"""Prefect tasks for orchestrating knowledge graph creation.

These tasks wrap the existing core KG creation primitives into Prefect tasks,
so that we can build observable, resilient flows while preserving the
underlying behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from genai_tk.utils.config_mngr import import_from_qualified
from loguru import logger
from prefect import get_run_logger, task
from prefect.cache_policies import NO_CACHE
from prefect.exceptions import MissingContextError
from pydantic import BaseModel
from upath import UPath

from genai_graph.core.graph_backend import (
    GraphBackend,
    create_backend_from_config,
    delete_backend_storage_from_config,
    get_backend_storage_path_from_config,
)
from genai_graph.core.graph_core import create_schema as core_create_schema
from genai_graph.core.graph_documents import DocumentStats, add_documents_to_graph
from genai_graph.core.graph_html import generate_html
from genai_graph.core.graph_schema import GraphSchema
from genai_graph.core.kg_manager import get_kg_manager
from genai_graph.core.subgraph_factories import (
    JsonFileBackedSubgraphFactory,
    SubgraphFactory,
    TableBackedSubgraphFactory,
)


def _get_prefect_logger_or_default() -> Any:
    """Return a Prefect run logger when in a flow, else fall back to loguru logger.

    This allows the same task functions to be reused both inside Prefect flows
    and in direct local execution (e.g. from the CLI) without requiring a
    running Prefect server.
    """

    try:
        return get_run_logger()
    except MissingContextError:
        return logger


class SubgraphBundle(BaseModel):
    """In-memory representation of a configured subgraph during KG creation."""

    config: dict[str, Any]
    factory: SubgraphFactory
    # Schema type is kept as Any to avoid circular imports in type checkers
    schema_obj: GraphSchema | None = None


class HtmlExportResult(BaseModel):
    """Result of HTML export task."""

    config_name: str
    output_path: Path


class KgRunResult(BaseModel):
    """Aggregated result of a KG creation run."""

    config_name: str
    db_path: Path
    stats: DocumentStats
    warnings: list[str]
    html_export: HtmlExportResult | None = None


@task
def resolve_config_task(config_name: str | None) -> tuple[str, dict[str, Any]]:
    """Resolve KG profile and return its configuration dictionary via KgManager."""

    logger_pf = _get_prefect_logger_or_default()

    from genai_graph.core.kg_manager import get_kg_manager

    manager = get_kg_manager()
    effective, _ = manager.activate()
    kg_cfg = manager.get_profile_dict()

    logger_pf.debug(
        "Loaded KG config '%s', subgraphs=%d.",
        effective,
        len(kg_cfg.get("subgraphs", [])),
    )

    return effective, kg_cfg


@task
def initialize_backend_task(config_key: str = "default", kg_config_name: str | None = None) -> GraphBackend:
    """Create and return the graph backend instance.

    The flow is expected to run with a single-process task runner so that the
    embedded Kuzu backend is never accessed concurrently from multiple
    processes.

    Args:
        config_key: Key in graph_db config section
        kg_config_name: Optional KG configuration name for organized output folders
    """

    logger_pf = _get_prefect_logger_or_default()

    backend = create_backend_from_config(config_key, kg_config_name)
    db_path = get_backend_storage_path_from_config(config_key, kg_config_name)

    logger_pf.debug("Initialized backend '%s' at path '%s'", config_key, db_path)
    return backend


@task
def load_factories_task(kg_cfg: dict[str, Any]) -> list[SubgraphBundle]:
    """Load and instantiate subgraph factories from KG configuration."""

    logger_pf = _get_prefect_logger_or_default()
    manager = get_kg_manager()
    subgraphs_cfg = kg_cfg.get("subgraphs", [])

    bundles: list[SubgraphBundle] = []
    for subgraph_cfg in subgraphs_cfg:
        if not isinstance(subgraph_cfg, dict):
            continue

        factory_path = subgraph_cfg.get("factory")
        if not factory_path:
            continue

        try:
            imported = import_from_qualified(factory_path)
            if isinstance(imported, SubgraphFactory):
                subgraph_impl = imported
            elif isinstance(imported, type) and issubclass(imported, SubgraphFactory):
                constructor_kwargs = {
                    k: v for k, v in subgraph_cfg.items() if k not in {"factory", "initial_load", "trigger"}
                }
                subgraph_impl = imported(**constructor_kwargs)  # type: ignore[misc]
            else:
                msg = f"Factory {factory_path} is not a SubgraphFactory"
                logger.warning(msg)
                manager.add_warning(msg)
                continue

            logger_pf.debug("Loaded subgraph factory: %s", subgraph_impl.name)
            bundles.append(SubgraphBundle(config=subgraph_cfg, factory=subgraph_impl))
        except Exception as exc:  # pragma: no cover - defensive logging
            import traceback

            msg = f"Failed to import factory {factory_path}: {exc}"
            logger.error(msg)
            logger.error(traceback.format_exc())
            manager.add_warning(msg)

    return bundles


def create_schema(
    bundles: list[SubgraphBundle],
    backend: GraphBackend,
) -> list[SubgraphBundle]:
    """Create graph schema for all loaded subgraphs (Pass 1)."""

    manager = get_kg_manager()

    for bundle in bundles:
        subgraph_impl = bundle.factory
        subgraph_impl.register()

        schema = subgraph_impl.build_schema()
        try:
            core_create_schema(backend, schema.nodes, schema.relations, manager)
            schema.validate_with_context(manager)
            logger.info(
                "Created schema for subgraph '%s'",
                getattr(subgraph_impl, "name", "<unknown>"),
            )
        except Exception as exc:  # pragma: no cover - defensive
            import traceback

            msg = f"Schema creation failed for subgraph {getattr(subgraph_impl, 'name', '<unknown>')}: {exc}"
            logger.error(msg)
            logger.error(traceback.format_exc())
            manager.add_warning(msg)

        bundle.schema_obj = schema

    return bundles


@task(cache_policy=NO_CACHE)
def ingest_subgraphs_task(
    bundles: list[SubgraphBundle],
    backend: GraphBackend,
) -> DocumentStats:
    """Ingest documents for all configured subgraphs (Pass 2)."""

    logger_pf = _get_prefect_logger_or_default()
    manager = get_kg_manager()

    total_stats = DocumentStats()

    for bundle in bundles:
        subgraph_cfg = bundle.config
        subgraph_impl = bundle.factory
        schema = bundle.schema_obj

        factory_path = subgraph_cfg.get("factory", "<unknown>")

        # For table-backed subgraphs configured with `pull`, do not
        # load all rows by default. They act as an on-demand source.
        pull_cfg = getattr(subgraph_impl, "pull", None)
        keys = subgraph_cfg.get("initial_load", [])

        if not keys and pull_cfg and isinstance(subgraph_impl, TableBackedSubgraphFactory):
            logger_pf.debug(
                "Skipping automatic ingestion for pull-only subgraph: %s",
                getattr(subgraph_impl, "name", factory_path),
            )
            continue

        # Handle JsonFileBackedSubgraphFactory - get file paths
        if not keys and isinstance(subgraph_impl, JsonFileBackedSubgraphFactory):
            try:
                file_paths = subgraph_impl.get_all_file_paths()
                keys = [str(fp) for fp in file_paths]
                logger_pf.debug(
                    "Retrieved %d file paths from JSON-backed factory",
                    len(keys),
                )
            except Exception as exc:  # pragma: no cover - defensive
                msg = f"Failed to get file paths from JSON factory {factory_path}: {exc}"
                logger.warning(msg)
                manager.add_warning(msg)
                keys = []

        # Handle TableBackedSubgraphFactory - get all keys from DB
        if not keys and isinstance(subgraph_impl, TableBackedSubgraphFactory):
            try:
                keys = subgraph_impl.get_all_keys()
                logger_pf.debug(
                    "Retrieved %d keys from table-backed factory",
                    len(keys),
                )
            except Exception as exc:  # pragma: no cover - defensive
                msg = f"Failed to get keys from table for {factory_path}: {exc}"
                logger.warning(msg)
                manager.add_warning(msg)
                keys = []

        if not keys:
            continue

        try:
            assert schema is not None, "Schema must be created before ingestion"
            stats = add_documents_to_graph(keys, subgraph_impl, backend, schema, manager)
            logger_pf.debug(
                "Ingest stats for %s: processed=%d failed=%d nodes=%d rels=%d",
                factory_path,
                stats.total_processed,
                stats.total_failed,
                stats.nodes_created,
                stats.relationships_created,
            )
            total_stats.total_processed += stats.total_processed
            total_stats.total_failed += stats.total_failed
            total_stats.nodes_created += stats.nodes_created
            total_stats.relationships_created += stats.relationships_created
        except Exception as exc:  # pragma: no cover - defensive
            import traceback

            msg = f"Ingestion error for {factory_path}: {exc}"
            logger.error(msg)
            logger.error(traceback.format_exc())
            manager.add_warning(msg)
            # Assume all keys failed when we cannot be more precise
            total_stats.total_failed += len(keys)

            logger_pf.debug(
                "Total ingest stats: processed=%d failed=%d nodes=%d rels=%d",
                total_stats.total_processed,
                total_stats.total_failed,
                total_stats.nodes_created,
                total_stats.relationships_created,
            )
    return total_stats


@task
def delete_backend_task(config_key: str = "default", kg_config_name: str | None = None) -> None:
    """Delete the knowledge graph backend storage for a given config key.

    Args:
        config_key: Key in graph_db config section
        kg_config_name: Optional KG configuration name for organized output folders
    """

    logger_pf = _get_prefect_logger_or_default()
    path = get_backend_storage_path_from_config(config_key, kg_config_name)

    if path.exists():
        logger_pf.info(
            "Deleting backend storage at '%s' for config '%s'",
            path,
            config_key,
        )
        delete_backend_storage_from_config(config_key, kg_config_name)
    else:
        logger_pf.info(
            "No backend storage found at '%s' for config '%s'",
            path,
            config_key,
        )


def export_html(
    config_name: str,
    backend: GraphBackend,
    output_dir: Path | None = None,
) -> HtmlExportResult:
    """Export an HTML visualization of the current KG and return its path.

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to export from
        output_dir: Optional custom output directory (if None, uses KG outcome manager)
    """

    if output_dir is None:
        # Use KgManager for organized output
        manager = get_kg_manager()
        manager.activate()
        destination = manager.html_path
        manager.ensure_directories()
    else:
        # Custom output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"{config_name}_graph.html"

    generate_html(backend, destination_file_path=str(destination))
    logger.debug("Exported KG HTML visualization to '%s'", destination)

    return HtmlExportResult(config_name=config_name, output_path=destination)


def export_schema(config_name: str) -> UPath:
    """Export the KG schema as a text file.

    Args:
        config_name: Name of the KG configuration

    Returns:
        Path to the exported schema file
    """

    from genai_graph.core.graph_registry import GraphRegistry
    from genai_graph.core.schema_doc_generator import generate_schema_description

    manager = get_kg_manager()
    manager.activate()
    manager.ensure_directories()

    # Get all registered subgraphs and generate schema description
    registry = GraphRegistry.get_instance()
    selected_subgraphs = registry.listsubgraphs()

    schema_content = generate_schema_description(selected_subgraphs, print_enums=True)

    # Write schema to file
    destination = manager.schema_path
    destination.write_text(schema_content, encoding="utf-8")

    logger.debug("Exported KG schema to '%s'", destination)

    return destination


def export_info(config_name: str, backend: GraphBackend) -> UPath:
    """Export the KG info as a markdown file.

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to query from

    Returns:
        Path to the exported info file
    """

    from genai_graph.core.graph_backend import get_backend_storage_path_from_config
    from genai_graph.core.graph_registry import GraphRegistry, get_subgraph
    from genai_graph.core.graph_schema import find_embedded_field_for_class

    manager = get_kg_manager()
    manager.activate()
    manager.ensure_directories()

    # Build registry and get all subgraphs
    registry = GraphRegistry.get_instance()
    selected_subgraphs = registry.listsubgraphs()

    try:
        schema = registry.build_combined_schema(selected_subgraphs)
    except ValueError as exc:
        logger.error(f"Failed to build schema: {exc}")
        schema = None

    subgraph_title = ", ".join(selected_subgraphs) if selected_subgraphs else "ALL"

    # Start building markdown content
    lines: list[str] = []
    lines.append(f"# 🗄️ {subgraph_title} EKG Database Information")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Database Information section
    lines.append("## 💾 Database Information")
    lines.append("")
    db_path = get_backend_storage_path_from_config("default", config_name)
    active_cfg = manager.profile
    default_kg = manager.ekg_config.kg_config

    lines.append(f"- **📂 Database Path**: `{db_path}`")
    lines.append("- **🔷 Database Type**: Cypher Graph Database")
    lines.append("- **⚙️ Backend**: Cypher (via GraphBackend abstraction)")
    lines.append("- **💿 Storage**: Persistent File Storage")
    lines.append(f"- **✅ Active KG Config**: `{active_cfg}@{manager.tag}`")
    lines.append(f"- **🎯 Default KG Config**: `{default_kg}`")
    lines.append(f"- **📊 Subgraph(s)**: **{subgraph_title}**")
    lines.append("")

    # KG Outputs & Outcomes section
    outcome_info = manager.get_info()
    if outcome_info.get("exists"):
        lines.append("## 📤 KG Outputs & Outcomes")
        lines.append("")
        lines.append(f"- **📁 Base Path**: `{outcome_info['base_path']}`")

        if outcome_info.get("database"):
            db_info = outcome_info["database"]
            lines.append(f"- **💾 Database Size**: **{db_info['size_mb']:.2f} MB**")

        if outcome_info.get("html_export"):
            html_info = outcome_info["html_export"]
            lines.append(f"- **🌐 HTML Export**: **{html_info['size_mb']:.2f} MB**")

        if outcome_info.get("outcomes"):
            out_info = outcome_info["outcomes"]
            lines.append(f"- **📝 Logged Outcomes**: **{out_info['count']}** events")

        if outcome_info.get("warnings"):
            warn_info = outcome_info["warnings"]
            lines.append(f"- **⚠️ Logged Warnings**: **{warn_info['count']}** warnings")

        lines.append("")
        lines.append("---")
        lines.append("")

    # Subgraph Factories section
    lines.append("## 🏭 Subgraph Factories")
    lines.append("")
    lines.append("| Name | Type | Module |")
    lines.append("|------|------|--------|")
    for name in selected_subgraphs or registry.listsubgraphs():
        try:
            subgraph_impl = get_subgraph(name)
            factory_type = type(subgraph_impl).__name__
            factory_module = type(subgraph_impl).__module__
            lines.append(f"| **{name}** | `{factory_type}` | `{factory_module}` |")
        except ValueError:
            lines.append(f"| **{name}** | *Not Found* | - |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Schema Overview and Statistics
    if schema:
        try:
            tables_df = backend.execute_get_as_df("CALL show_tables() RETURN *", union=False)

            node_tables: list[str] = []
            rel_tables: list[str] = []

            for _, row in tables_df.iterrows():
                if row.get("type") == "NODE":
                    node_tables.append(row["name"])
                elif row.get("type") == "REL":
                    rel_tables.append(row["name"])

            allowed_node_labels = {n.node_class.__name__ for n in schema.nodes}
            allowed_rel_types = {r.name for r in schema.relations}

            node_tables = [t for t in node_tables if t in allowed_node_labels]
            rel_tables = [t for t in rel_tables if t in allowed_rel_types]

            # Schema Overview
            lines.append("## 📋 Schema Overview")
            lines.append("")
            lines.append(f"- **🔵 Node Tables**: **{len(node_tables)}**")
            lines.append(f"- **🔗 Relationship Tables**: **{len(rel_tables)}**")
            lines.append("")
            lines.append("---")
            lines.append("")

            # Node Counts
            if node_tables:
                lines.append("### 🔵 Node Counts")
                lines.append("")
                lines.append("| Node Type | Count |")
                lines.append("|-----------|------:|")
                for node_type in sorted(node_tables):
                    try:
                        result_df = backend.execute_get_as_df(
                            f"MATCH (n:{node_type}) RETURN count(n) as count", union=False
                        )
                        count = result_df.iloc[0]["count"]
                        lines.append(f"| **{node_type}** | **{count:,}** |")
                    except Exception as exc:
                        lines.append(f"| **{node_type}** | ⚠️ Error: {exc} |")
                lines.append("")

            # Relationship Counts
            if rel_tables:
                lines.append("### 🔗 Relationship Counts")
                lines.append("")
                lines.append("| Relationship Type | Count |")
                lines.append("|-------------------|------:|")
                for rel_type in sorted(rel_tables):
                    try:
                        result_df = backend.execute_get_as_df(
                            f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as count", union=False
                        )
                        count = result_df.iloc[0]["count"]
                        lines.append(f"| **{rel_type}** | **{count:,}** |")
                    except Exception as exc:
                        lines.append(f"| **{rel_type}** | ⚠️ Error: {exc} |")
                lines.append("")
                lines.append("---")
                lines.append("")

        except Exception as exc:
            lines.append(f"⚠️ *Error retrieving schema statistics: {exc}*")
            lines.append("")

        # Node Mapping
        lines.append(f"## 🔵 Node Mapping for {subgraph_title}")
        lines.append("")
        lines.append("| Node Type | Description | Dedup Key | Alt Names Field |")
        lines.append("|-----------|-------------|-----------|-----------------|")
        for node in schema.nodes:
            node_type = node.node_class.__name__
            description = node.description or ""

            if node.deduplication_key is None:
                dedup_label = "`_name` (default)"
            elif isinstance(node.deduplication_key, str):
                dedup_label = f"`{node.deduplication_key}`"
            else:
                dedup_label = "🔧 callable"

            alt_label = "`alternate_names`" if node.deduplication_key else ""

            lines.append(f"| **{node_type}** | {description} | {dedup_label} | {alt_label} |")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Relationship Mapping
        lines.append("## 🔗 Relationship Mapping")
        lines.append("")
        lines.append("| Relationship | From → To | Meaning | Field Paths |")
        lines.append("|--------------|-----------|---------|-------------|")
        for relation in schema.relations:
            rel_type = relation.name
            direction = f"**{relation.from_node.__name__}** → **{relation.to_node.__name__}**"
            meaning = relation.description or ""

            if relation.field_paths:
                paths_display = "; ".join(f"`{fp or '(root)'} → {tp or '(root)'}`" for fp, tp in relation.field_paths)
            else:
                paths_display = "*(none)*"

            # Escape pipe characters in the content
            meaning = meaning.replace("|", "\\|")
            paths_display = paths_display.replace("|", "\\|")

            lines.append(f"| **{rel_type}** | {direction} | {meaning} | {paths_display} |")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Embedded Fields
        lines.append("## 📦 Embedded Fields")
        lines.append("")
        has_embedded = False
        embedded_rows = []
        for node in schema.nodes:
            for embedded_class in getattr(node, "embedded_struct_classes", []) or []:
                field_name = find_embedded_field_for_class(node.node_class, embedded_class)
                if field_name:
                    has_embedded = True
                    embedded_rows.append(
                        f"| **{node.node_class.__name__}** | `{field_name}` | `{embedded_class.__name__}` |"
                    )

        if has_embedded:
            lines.append("| Parent Node | Embedded Field | Embedded Class |")
            lines.append("|-------------|----------------|----------------|")
            lines.extend(embedded_rows)
        else:
            lines.append("*No embedded fields configured*")
        lines.append("")

    # Write info to file
    destination = manager.info_path
    destination.write_text("\n".join(lines), encoding="utf-8")

    logger.debug("Exported KG info to '%s'", destination)

    return destination


def summarize_warnings(config_name: str | None = None) -> list[str]:
    """Return collected warnings from KgManager and log them if config_name provided.

    Args:
        config_name: Optional KG configuration name to log warnings to file
    """

    manager = get_kg_manager()
    warnings = manager.get_warnings()

    if warnings:
        logger.warning(
            "KG creation completed with %d warning(s)",
            len(warnings),
        )

        # Log warnings to file if config_name is provided
        if config_name:
            manager.activate()
            manager.log_warnings(warnings)
    else:
        logger.info("KG creation completed with no warnings")

    return warnings
