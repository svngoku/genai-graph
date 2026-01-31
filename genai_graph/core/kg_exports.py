"""Export utilities for Knowledge Graph artifacts.

This module provides functions to export various KG artifacts:
- HTML visualization
- Schema documentation (text file)
- KG info report (markdown file)
- Parquet export for KG data (for import mechanism)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger
from pydantic import BaseModel
from upath import UPath

from genai_graph.core.graph_backend import GraphBackend
from genai_graph.core.kg_manager import get_kg_manager


class HtmlExportResult(BaseModel):
    """Result of HTML export task."""

    config_name: str
    output_path: UPath


class ParquetExportResult(BaseModel):
    """Result of parquet export task."""

    config_name: str
    nodes_path: UPath
    rels_path: UPath
    manifest_path: UPath
    node_count: int
    rel_count: int

    model_config = {"arbitrary_types_allowed": True}


class ParquetManifest(BaseModel):
    """Manifest file for parquet export."""

    config_name: str
    exported_at: str
    node_tables: list[str]
    rel_tables: list[str]
    node_count: int
    rel_count: int
    source_files: list[str] = []
    source_files_hash: str | None = None

    model_config = {"arbitrary_types_allowed": True}


def export_html(
    config_name: str,
    backend: GraphBackend,
    output_dir: UPath | None = None,
) -> HtmlExportResult:
    """Export an HTML visualization of the current KG and return its path.

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to export from
        output_dir: Optional custom output directory (if None, uses KG outcome manager)
    """
    from genai_graph.core.graph_html import generate_html

    if output_dir is None:
        # Use KgManager for organized output with the specified config
        manager = get_kg_manager()
        destination = manager.get_html_path_for(config_name)
        manager.ensure_directories_for(config_name)
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
    manager.ensure_directories_for(config_name)

    # Get all registered subgraphs and generate schema description
    registry = GraphRegistry.get_instance()
    selected_subgraphs = registry.listsubgraphs()

    schema_content = generate_schema_description(selected_subgraphs, print_enums=True)

    # Write schema to file using the specified config
    destination = manager.get_schema_path_for(config_name)
    destination.write_text(schema_content, encoding="utf-8")

    logger.debug("Exported KG schema to '%s'", destination)

    return destination


def export_schema_json(config_name: str) -> UPath:
    """Export the KG schema as a JSON file with type mappings.

    This exports a machine-readable schema that includes:
    - Node types with their field names and Kuzu types
    - Embedded struct field types for proper type coercion
    - Relationship types with their properties

    Args:
        config_name: Name of the KG configuration

    Returns:
        Path to the exported JSON schema file
    """
    import json
    import warnings

    from genai_graph.core.graph_registry import GraphRegistry
    from genai_graph.core.schema_doc_generator import _get_kuzu_type_for_field

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    # Build the combined schema from all registered subgraphs
    # Suppress validation warnings for combined schemas (type mismatches between
    # extended and base types are expected when merging different subgraphs)
    registry = GraphRegistry.get_instance()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation:")
        schema = registry.build_combined_schema([])

    schema_dict: dict[str, Any] = {
        "nodes": {},
        "relationships": {},
        "embedded_structs": {},
    }

    # Process nodes
    for node in schema.nodes:
        node_name = node.node_class.__name__
        fields: dict[str, str] = {}

        # Get field types from Pydantic model
        if hasattr(node.node_class, "model_fields"):
            for field_name, field_info in node.node_class.model_fields.items():
                if field_name not in node.excluded_fields:
                    kuzu_type = _get_kuzu_type_for_field(field_info.annotation)
                    fields[field_name] = kuzu_type

        # Process embedded struct classes
        embedded_fields: dict[str, dict[str, str]] = {}
        for emb_class in getattr(node, "embedded_struct_classes", []) or []:
            emb_name = emb_class.__name__
            emb_fields: dict[str, str] = {}
            if hasattr(emb_class, "model_fields"):
                for emb_field_name, emb_field_info in emb_class.model_fields.items():
                    kuzu_type = _get_kuzu_type_for_field(emb_field_info.annotation)
                    emb_fields[emb_field_name] = kuzu_type
            embedded_fields[emb_name] = emb_fields

            # Also add to top-level embedded_structs for cross-reference
            if emb_name not in schema_dict["embedded_structs"]:
                schema_dict["embedded_structs"][emb_name] = emb_fields

        schema_dict["nodes"][node_name] = {
            "fields": fields,
            "embedded": embedded_fields,
        }

    # Process relationships
    for rel in schema.relations:
        rel_name = rel.relation_name
        rel_props: dict[str, str] = {}

        if rel.relation_properties:
            for prop_name, prop_info in rel.relation_properties.model_fields.items():
                kuzu_type = _get_kuzu_type_for_field(prop_info.annotation)
                rel_props[prop_name] = kuzu_type

        schema_dict["relationships"][rel_name] = {
            "from": rel.from_node.node_class.__name__,
            "to": rel.to_node.node_class.__name__,
            "properties": rel_props,
        }

    # Write JSON schema
    destination = manager.get_output_path_for(config_name).with_suffix(".schema.json")
    destination.write_text(json.dumps(schema_dict, indent=2), encoding="utf-8")

    logger.debug("Exported KG JSON schema to '%s'", destination)

    return destination


def export_info(config_name: str, backend: GraphBackend) -> UPath:
    """Export the KG info as a markdown file.

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to query from

    Returns:
        Path to the exported info file
    """
    import warnings

    from genai_graph.core.graph_backend import get_backend_storage_path_from_config
    from genai_graph.core.graph_registry import GraphRegistry, get_subgraph
    from genai_graph.core.graph_schema import find_embedded_field_for_class

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    # Build registry and get all subgraphs
    registry = GraphRegistry.get_instance()
    selected_subgraphs = registry.listsubgraphs()

    # Suppress validation warnings for combined schemas (type mismatches between
    # extended and base types are expected when merging different subgraphs)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation:")
            schema = registry.build_combined_schema(selected_subgraphs)
    except ValueError as exc:
        logger.error(f"Failed to build schema: {exc}")
        schema = None

    subgraph_title = ", ".join(selected_subgraphs) if selected_subgraphs else "ALL"

    # Start building markdown content
    lines: list[str] = []
    lines.append(f"# {subgraph_title} EKG Database Information")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Database Information section
    lines.append("## Database Information")
    lines.append("")
    db_path = get_backend_storage_path_from_config("default", config_name)
    active_cfg = manager.profile
    default_kg = manager.ekg_config.kg_config

    lines.append(f"- **Database Path**: `{db_path}`")
    lines.append("- **Database Type**: Cypher Graph Database")
    lines.append("- **Backend**: Cypher (via GraphBackend abstraction)")
    lines.append("- **Storage**: Persistent File Storage")
    lines.append(f"- **Active KG Config**: `{active_cfg}@{manager.tag}`")
    lines.append(f"- **Default KG Config**: `{default_kg}`")
    lines.append(f"- **Subgraph(s)**: **{subgraph_title}**")
    lines.append("")

    # KG Outputs & Outcomes section
    outcome_info = manager.get_info()
    if outcome_info.get("exists"):
        lines.append("## KG Outputs & Outcomes")
        lines.append("")
        lines.append(f"- **Base Path**: `{outcome_info['base_path']}`")

        if outcome_info.get("database"):
            db_info = outcome_info["database"]
            lines.append(f"- **Database Size**: **{db_info['size_mb']:.2f} MB**")

        if outcome_info.get("html_export"):
            html_info = outcome_info["html_export"]
            lines.append(f"- **HTML Export**: **{html_info['size_mb']:.2f} MB**")

        if outcome_info.get("outcomes"):
            out_info = outcome_info["outcomes"]
            lines.append(f"- **Logged Outcomes**: **{out_info['count']}** events")

        if outcome_info.get("warnings"):
            warn_info = outcome_info["warnings"]
            lines.append(f"- **Logged Warnings**: **{warn_info['count']}** warnings")

        lines.append("")
        lines.append("---")
        lines.append("")

    # Subgraph Factories section
    lines.append("## Subgraph Factories")
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
            lines.append("## Schema Overview")
            lines.append("")
            lines.append(f"- **Node Tables**: **{len(node_tables)}**")
            lines.append(f"- **Relationship Tables**: **{len(rel_tables)}**")
            lines.append("")
            lines.append("---")
            lines.append("")

            # Node Counts
            if node_tables:
                lines.append("### Node Counts")
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
                        lines.append(f"| **{node_type}** | Error: {exc} |")
                lines.append("")

            # Relationship Counts
            if rel_tables:
                lines.append("### Relationship Counts")
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
                        lines.append(f"| **{rel_type}** | Error: {exc} |")
                lines.append("")
                lines.append("---")
                lines.append("")

        except Exception as exc:
            lines.append(f"*Error retrieving schema statistics: {exc}*")
            lines.append("")

        # Node Mapping
        lines.append(f"## Node Mapping for {subgraph_title}")
        lines.append("")
        lines.append("| Node Type | Description | Primary Key |")
        lines.append("|-----------|-------------|-------------|")
        for node in schema.nodes:
            node_type = node.node_class.__name__
            description = node.description or ""

            if node.key_from == "AUTO_ID":
                key_label = "`id` (SERIAL auto-generated)"
            elif isinstance(node.key_from, str):
                key_label = f"`{node.key_from}`"
            else:
                key_label = "`id` (computed)"

            lines.append(f"| **{node_type}** | {description} | {key_label} |")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Relationship Mapping
        lines.append("## Relationship Mapping")
        lines.append("")
        lines.append("| Relationship | From -> To | Meaning | Field Paths |")
        lines.append("|--------------|-----------|---------|-------------|")
        for relation in schema.relations:
            rel_type = relation.name
            direction = f"**{relation.from_node.__name__}** -> **{relation.to_node.__name__}**"
            meaning = relation.description or ""

            if relation.field_paths:
                paths_display = "; ".join(f"`{fp or '(root)'} -> {tp or '(root)'}`" for fp, tp in relation.field_paths)
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
        lines.append("## Embedded Fields")
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

    # Write info to file using the specified config
    destination = manager.get_info_path_for(config_name)
    destination.write_text("\n".join(lines), encoding="utf-8")

    logger.debug("Exported KG info to '%s'", destination)

    return destination


def get_parquet_export_dir(config_name: str) -> UPath:
    """Get the directory path for parquet exports of a KG config.

    Args:
        config_name: Name of the KG configuration

    Returns:
        Path to the parquet export directory
    """
    manager = get_kg_manager()
    return manager.get_base_path_for(config_name) / "parquet"


def export_parquet(
    config_name: str,
    backend: GraphBackend,
    source_files: list[str] | None = None,
) -> ParquetExportResult:
    """Export all nodes and relationships from a KG to parquet files.

    This creates:
    - nodes/<NodeType>.parquet for each node table
    - rels/<RelType>.parquet for each relationship table
    - manifest.json with metadata

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to export from
        source_files: Optional list of source files (for hash-based caching)

    Returns:
        ParquetExportResult with export details
    """
    import hashlib

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    export_dir = get_parquet_export_dir(config_name)
    nodes_dir = export_dir / "nodes"
    rels_dir = export_dir / "rels"

    # Create directories
    nodes_dir.mkdir(parents=True, exist_ok=True)
    rels_dir.mkdir(parents=True, exist_ok=True)

    # Get all tables
    tables_df = backend.execute_get_as_df("CALL show_tables() RETURN *", union=False)

    node_tables: list[str] = []
    rel_tables: list[str] = []

    for _, row in tables_df.iterrows():
        if row.get("type") == "NODE":
            node_tables.append(row["name"])
        elif row.get("type") == "REL":
            rel_tables.append(row["name"])

    total_nodes = 0
    total_rels = 0

    # Export node tables
    for node_type in node_tables:
        try:
            df = backend.execute_get_as_df(f"MATCH (n:{node_type}) RETURN n.*", union=False)
            if not df.empty:
                # Clean column names (remove 'n.' prefix)
                df.columns = [c.replace("n.", "") for c in df.columns]
                parquet_path = nodes_dir / f"{node_type}.parquet"
                df.to_parquet(str(parquet_path), index=False)
                total_nodes += len(df)
                logger.debug(f"Exported {len(df)} {node_type} nodes to parquet")
        except Exception as exc:
            logger.warning(f"Failed to export {node_type} nodes: {exc}")

    # Export relationship tables
    for rel_type in rel_tables:
        try:
            # Get relationships with source and target IDs
            df = backend.execute_get_as_df(
                f"MATCH (a)-[r:{rel_type}]->(b) RETURN id(a) as _from_id, id(b) as _to_id, r.*",
                union=False,
            )
            if not df.empty:
                # Clean column names (remove 'r.' prefix)
                df.columns = [c.replace("r.", "") if c.startswith("r.") else c for c in df.columns]
                parquet_path = rels_dir / f"{rel_type}.parquet"
                df.to_parquet(str(parquet_path), index=False)
                total_rels += len(df)
                logger.debug(f"Exported {len(df)} {rel_type} relationships to parquet")
        except Exception as exc:
            logger.warning(f"Failed to export {rel_type} relationships: {exc}")

    # Calculate source files hash if provided
    source_hash = None
    if source_files:
        combined = "|".join(sorted(source_files))
        source_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]

    # Write manifest
    manifest = ParquetManifest(
        config_name=config_name,
        exported_at=datetime.now().isoformat(),
        node_tables=node_tables,
        rel_tables=rel_tables,
        node_count=total_nodes,
        rel_count=total_rels,
        source_files=source_files or [],
        source_files_hash=source_hash,
    )

    manifest_path = export_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    logger.info(f"Exported KG '{config_name}' to parquet: {total_nodes} nodes, {total_rels} rels")

    return ParquetExportResult(
        config_name=config_name,
        nodes_path=nodes_dir,
        rels_path=rels_dir,
        manifest_path=manifest_path,
        node_count=total_nodes,
        rel_count=total_rels,
    )


def save_parquet_from_collector(
    config_name: str,
    collector: Any,
    source_files: list[str] | None = None,
) -> ParquetExportResult:
    """Save collected DataFrames from merge operations to parquet files.

    This is the preferred method for parquet export as it captures the
    exact data being merged, avoiding the need to query it back out
    (which can hit Kuzu bugs with relationships).

    Args:
        config_name: Name of the KG configuration
        collector: ParquetCollector with accumulated node/relationship DataFrames
        source_files: Optional list of source files (for hash-based caching)

    Returns:
        ParquetExportResult with export details
    """
    import hashlib

    from genai_graph.core.graph_merge import ParquetCollector

    # Validate collector type
    if not isinstance(collector, ParquetCollector):
        raise TypeError(f"Expected ParquetCollector, got {type(collector)}")

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    export_dir = get_parquet_export_dir(config_name)
    nodes_dir = export_dir / "nodes"
    rels_dir = export_dir / "rels"

    # Create directories
    nodes_dir.mkdir(parents=True, exist_ok=True)
    rels_dir.mkdir(parents=True, exist_ok=True)

    total_nodes = 0
    total_rels = 0
    node_tables: list[str] = []
    rel_tables: list[str] = []

    # Export node DataFrames
    for node_type, df in collector.nodes.items():
        if df.empty:
            continue
        try:
            parquet_path = nodes_dir / f"{node_type}.parquet"
            df.to_parquet(str(parquet_path), index=False)
            total_nodes += len(df)
            node_tables.append(node_type)
            logger.debug(f"Saved {len(df)} {node_type} nodes to parquet from collector")
        except Exception as exc:
            logger.warning(f"Failed to save {node_type} nodes to parquet: {exc}")

    # Export relationship DataFrames
    for rel_type, df in collector.relationships.items():
        if df.empty:
            continue
        try:
            parquet_path = rels_dir / f"{rel_type}.parquet"
            df.to_parquet(str(parquet_path), index=False)
            total_rels += len(df)
            rel_tables.append(rel_type)
            logger.debug(f"Saved {len(df)} {rel_type} relationships to parquet from collector")
        except Exception as exc:
            logger.warning(f"Failed to save {rel_type} relationships to parquet: {exc}")

    # Calculate source files hash if provided
    source_hash = None
    if source_files:
        combined = "|".join(sorted(source_files))
        source_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]

    # Write manifest
    manifest = ParquetManifest(
        config_name=config_name,
        exported_at=datetime.now().isoformat(),
        node_tables=node_tables,
        rel_tables=rel_tables,
        node_count=total_nodes,
        rel_count=total_rels,
        source_files=source_files or [],
        source_files_hash=source_hash,
    )

    manifest_path = export_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    logger.info(f"Saved KG '{config_name}' to parquet from collector: {total_nodes} nodes, {total_rels} rels")

    return ParquetExportResult(
        config_name=config_name,
        nodes_path=nodes_dir,
        rels_path=rels_dir,
        manifest_path=manifest_path,
        node_count=total_nodes,
        rel_count=total_rels,
    )


def load_parquet_manifest(config_name: str) -> ParquetManifest | None:
    """Load the parquet manifest for a KG config if it exists.

    Args:
        config_name: Name of the KG configuration

    Returns:
        ParquetManifest or None if not found
    """
    export_dir = get_parquet_export_dir(config_name)
    manifest_path = export_dir / "manifest.json"

    if not manifest_path.exists():
        return None

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return ParquetManifest(**data)
    except Exception as exc:
        logger.warning(f"Failed to load parquet manifest for {config_name}: {exc}")
        return None


def import_from_parquet(
    config_name: str,
    backend: GraphBackend,
) -> tuple[int, int]:
    """Import nodes and relationships from parquet files into the graph.

    Uses LOAD FROM df MERGE for efficient batch loading.

    Args:
        config_name: Name of the KG configuration to import from
        backend: The graph backend to import into

    Returns:
        Tuple of (nodes_imported, rels_imported)
    """
    from genai_graph.core.graph_merge import get_parquet_collector

    export_dir = get_parquet_export_dir(config_name)
    nodes_dir = export_dir / "nodes"
    rels_dir = export_dir / "rels"

    manifest = load_parquet_manifest(config_name)
    if manifest is None:
        raise FileNotFoundError(f"No parquet export found for KG '{config_name}'")

    nodes_imported = 0
    rels_imported = 0

    # Get the active parquet collector (if any) to also collect imported data
    collector = get_parquet_collector()

    # Get kuzu connection
    kuzu_conn = backend.conn if hasattr(backend, "conn") else backend

    # Get primary key info for each table by querying the database schema
    pk_info: dict[str, str] = {}
    try:
        # Query the schema to get primary key columns
        tables_result = kuzu_conn.execute("CALL show_tables() RETURN *")
        tables_df = tables_result.get_as_df() if hasattr(tables_result, "get_as_df") else pd.DataFrame()

        for _, row in tables_df.iterrows():
            if row.get("type") == "NODE":
                table_name = row["name"]
                try:
                    # Get table info including primary key
                    info_result = kuzu_conn.execute(f"CALL table_info('{table_name}') RETURN *")
                    info_df = info_result.get_as_df() if hasattr(info_result, "get_as_df") else pd.DataFrame()
                    # Find the primary key column - column is named "primary key" with a space
                    pk_col_name = "primary key" if "primary key" in info_df.columns else "is_primary_key"
                    if pk_col_name in info_df.columns:
                        pk_rows = info_df[info_df[pk_col_name]]
                        if not pk_rows.empty:
                            pk_info[table_name] = pk_rows.iloc[0]["name"]
                except Exception as exc:
                    logger.debug(f"Could not get table info for {table_name}: {exc}")
    except Exception as exc:
        logger.debug(f"Could not query schema for primary keys: {exc}")

    # Import nodes
    for node_type in manifest.node_tables:
        parquet_path = nodes_dir / f"{node_type}.parquet"
        if not parquet_path.exists():
            logger.warning(f"Node parquet file not found: {parquet_path}")
            continue

        try:
            df = pd.read_parquet(str(parquet_path))
            if df.empty:
                continue

            # Determine primary key from schema info or fallback to 'id' or first column
            pk_field = pk_info.get(node_type) or ("id" if "id" in df.columns else df.columns[0])

            # Verify the pk_field exists in the dataframe
            if pk_field not in df.columns:
                logger.warning(
                    f"Primary key '{pk_field}' not found in parquet columns for {node_type}: {list(df.columns)}"
                )
                # Try to find a suitable key
                pk_field = "id" if "id" in df.columns else df.columns[0]

            # Build MERGE query - merge on primary key, set all other fields
            other_cols = [c for c in df.columns if c != pk_field]
            on_create_set = ", ".join([f"n.{c} = {c}" for c in other_cols]) if other_cols else ""
            on_match_set = ", ".join([f"n.{c} = {c}" for c in other_cols]) if other_cols else ""

            if on_create_set:
                merge_query = f"""
                    LOAD FROM df
                    MERGE (n:{node_type} {{{pk_field}: {pk_field}}})
                    ON CREATE SET {on_create_set}
                    ON MATCH SET {on_match_set}
                """
            else:
                merge_query = f"""
                    LOAD FROM df
                    MERGE (n:{node_type} {{{pk_field}: {pk_field}}})
                """

            kuzu_conn.execute(merge_query)
            nodes_imported += len(df)
            logger.debug(f"Imported {len(df)} {node_type} nodes from parquet (pk={pk_field})")

            # Also add to collector if active (so the KG's parquet export includes imported data)
            if collector is not None:
                collector.add_nodes(node_type, df)

        except Exception as exc:
            logger.error(f"Failed to import {node_type} nodes: {exc}")

    # Import relationships
    for rel_type in manifest.rel_tables:
        parquet_path = rels_dir / f"{rel_type}.parquet"
        if not parquet_path.exists():
            logger.warning(f"Relationship parquet file not found: {parquet_path}")
            continue

        try:
            df = pd.read_parquet(str(parquet_path))
            if df.empty:
                continue

            # Check if this is a collector-saved parquet with metadata columns
            if "_from_type" in df.columns and "_to_type" in df.columns:
                # Group by from_type/to_type combination (there should typically be one)
                for (from_type, to_type), group_df in df.groupby(["_from_type", "_to_type"]):
                    from_key = group_df["_from_key_field"].iloc[0] if "_from_key_field" in group_df.columns else "id"
                    to_key = group_df["_to_key_field"].iloc[0] if "_to_key_field" in group_df.columns else "id"

                    # Remove metadata columns for the merge
                    merge_df = group_df.drop(
                        columns=["_from_type", "_to_type", "_from_key_field", "_to_key_field"], errors="ignore"
                    )

                    # Build property columns (excluding from_id, to_id)
                    prop_cols = [c for c in merge_df.columns if c not in ("from_id", "to_id")]
                    if prop_cols:
                        props_str = " {" + ", ".join([f"{c}: {c}" for c in prop_cols]) + "}"
                    else:
                        props_str = ""

                    # Create relationships using MATCH + CREATE
                    create_query = f"""
                        LOAD FROM merge_df
                        MATCH (a:{from_type} {{{from_key}: from_id}}), (b:{to_type} {{{to_key}: to_id}})
                        CREATE (a)-[:{rel_type}{props_str}]->(b)
                    """

                    kuzu_conn.execute(create_query)
                    rels_imported += len(merge_df)
                    logger.debug(
                        f"Imported {len(merge_df)} {rel_type} relationships from parquet ({from_type}->{to_type})"
                    )

                    # Also add to collector if active (so the KG's parquet export includes imported data)
                    if collector is not None:
                        # Re-add the metadata columns for proper export
                        export_df = merge_df.copy()
                        export_df["_from_type"] = from_type
                        export_df["_to_type"] = to_type
                        export_df["_from_key_field"] = from_key
                        export_df["_to_key_field"] = to_key
                        collector.add_relationships(rel_type, export_df)

        except Exception as exc:
            logger.error(f"Failed to import {rel_type} relationships: {exc}")

    logger.info(f"Imported from parquet '{config_name}': {nodes_imported} nodes, {rels_imported} rels")

    return nodes_imported, rels_imported
