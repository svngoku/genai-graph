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

from genai_graph.kg.backend import KgBackend
from genai_graph.kg.manager import get_kg_manager


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
    backend: KgBackend,
    output_dir: UPath | None = None,
) -> HtmlExportResult:
    """Export an HTML visualization of the current KG and return its path.

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to export from
        output_dir: Optional custom output directory (if None, uses KG outcome manager)
    """
    from genai_graph.kg.export.html import generate_html

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
    from genai_graph.kg.schema import GraphRegistry, generate_schema_description

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    # Get all registered graphs and generate schema description
    registry = GraphRegistry.get_instance()
    selected_graphs = registry.list_graphs()

    schema_content = generate_schema_description(selected_graphs, print_enums=True)

    # Write schema to file using the specified config
    destination = manager.get_schema_path_for(config_name)
    destination.write_text(schema_content, encoding="utf-8")

    logger.debug("Exported KG schema to '%s'", destination)

    return destination


def export_schema_json(config_name: str) -> UPath:
    """Export the KG schema as a D3-friendly JSON file.

    The exported JSON is intended to be consumed directly by D3.js:
    - ``nodes`` is a list with stable string IDs
    - ``links`` references nodes by those IDs

    The JSON includes descriptions and per-field descriptions whenever available.

    Args:
        config_name: Name of the KG configuration.

    Returns:
        Path to the exported schema JSON file.
    """

    import warnings

    from genai_graph.kg.schema import GraphRegistry
    from genai_graph.kg.schema.schema_d3 import build_schema_d3_data

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    registry = GraphRegistry.get_instance()
    selected_graphs = registry.list_graphs()

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation:")
        schema = registry.build_combined_schema(selected_graphs)

    schema_data = build_schema_d3_data(schema, graph_names=selected_graphs)

    destination = manager.get_schema_json_path_for(config_name)
    destination.write_text(json.dumps(schema_data, indent=2), encoding="utf-8")

    logger.debug("Exported KG schema JSON to '%s'", destination)

    return destination


def export_schema_html(config_name: str) -> UPath:
    """Export the KG schema visualization as an HTML file.

    Args:
        config_name: Name of the KG configuration.

    Returns:
        Path to the exported schema HTML file.
    """

    from genai_graph.kg.schema.schema_html import generate_schema_html

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    schema_json_path = manager.get_schema_json_path_for(config_name)
    if schema_json_path.exists():
        schema_data = json.loads(schema_json_path.read_text(encoding="utf-8"))
    else:
        schema_data = json.loads(export_schema_json(config_name).read_text(encoding="utf-8"))

    destination = manager.get_schema_html_path_for(config_name)
    generate_schema_html(schema_data, destination_file_path=str(destination))

    logger.debug("Exported KG schema HTML visualization to '%s'", destination)

    return destination


def export_info(config_name: str, backend: KgBackend) -> UPath:
    """Export the KG info as a markdown file.

    Args:
        config_name: Name of the KG configuration
        backend: The graph backend to query from

    Returns:
        Path to the exported info file
    """
    import warnings

    from genai_graph.kg.backend import get_backend_storage_path_from_config
    from genai_graph.kg.schema import GraphRegistry, find_embedded_field_for_class, get_graph

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    # Build registry and get all graphs
    registry = GraphRegistry.get_instance()
    selected_graphs = registry.list_graphs()

    # Suppress validation warnings for combined schemas (type mismatches between
    # extended and base types are expected when merging different graphs)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation:")
            schema = registry.build_combined_schema(selected_graphs)
    except ValueError as exc:
        logger.error(f"Failed to build schema: {exc}")
        schema = None

    graph_title = ", ".join(selected_graphs) if selected_graphs else "ALL"

    # Start building markdown content
    lines: list[str] = []
    lines.append(f"# {graph_title} EKG Database Information")
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
    lines.append("- **Backend**: Cypher (via KgBackend abstraction)")
    lines.append("- **Storage**: Persistent File Storage")
    lines.append(f"- **Active KG Config**: `{active_cfg}@{manager.tag}`")
    lines.append(f"- **Default KG Config**: `{default_kg}`")
    lines.append(f"- **Graph(s)**: **{graph_title}**")
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

        if outcome_info.get("warnings_report"):
            report_info = outcome_info["warnings_report"]
            report_path = UPath(report_info["file"])
            report_name = report_path.name
            lines.append(f"- **Warnings Report**: [📊 {report_name}]({report_name})")

        lines.append("")
        lines.append("---")
        lines.append("")

    # Graph Factories section
    lines.append("## Graph Factories")
    lines.append("")
    lines.append("| Name | Type | Module |")
    lines.append("|------|------|--------|")
    for name in selected_graphs or registry.list_graphs():
        try:
            graph_impl = get_graph(name)
            factory_type = type(graph_impl).__name__
            factory_module = type(graph_impl).__module__
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
        lines.append(f"## Node Mapping for {graph_title}")
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
    backend: KgBackend,
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
    from genai_tk.utils.hashing import buffer_digest

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
        source_hash = buffer_digest(combined.encode(), algorithm="sha256")[:16]

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
    from genai_tk.utils.hashing import buffer_digest

    from genai_graph.kg.ingest.merge import ParquetCollector

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
        source_hash = buffer_digest(combined.encode(), algorithm="sha256")[:16]

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


def _parse_struct_field_order(type_str: str) -> list[str]:
    """Parse field names from a Kuzu STRUCT type string, preserving order.
    
    Example:
        >>> _parse_struct_field_order("STRUCT(objectives STRING[], scope STRING, requirements STRING[])")
        ['objectives', 'scope', 'requirements']
    
    Args:
        type_str: Kuzu type string like "STRUCT(field1 TYPE1, field2 TYPE2, ...)"
    
    Returns:
        Ordered list of field names
    """
    if not type_str.startswith("STRUCT(") or not type_str.endswith(")"):
        return []
    
    # Extract the content between STRUCT( and )
    inner = type_str[len("STRUCT("):-1]
    
    # Parse fields: split on commas but respect nested brackets (e.g., STRING[])
    fields: list[str] = []
    depth = 0
    current = ""
    for ch in inner:
        if ch in "([":
            depth += 1
            current += ch
        elif ch in ")]":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            field_def = current.strip()
            if field_def:
                # Field name is the first word before the type
                field_name = field_def.split()[0]
                fields.append(field_name)
            current = ""
        else:
            current += ch
    
    # Don't forget the last field
    field_def = current.strip()
    if field_def:
        field_name = field_def.split()[0]
        fields.append(field_name)
    
    return fields


def _reorder_struct_dict(d: dict[str, Any], expected_fields: list[str]) -> dict[str, Any]:
    """Reorder dict keys to match expected struct field order.
    
    Keys in `expected_fields` come first (in that order), followed by any
    extra keys not in the expected list (preserving their original order).
    
    Args:
        d: Dictionary with potentially wrong key order
        expected_fields: Expected field order from Kuzu schema
    
    Returns:
        New dict with keys in correct order
    """
    result: dict[str, Any] = {}
    for field in expected_fields:
        if field in d:
            result[field] = d[field]
    # Add any remaining keys not in expected_fields
    for key in d:
        if key not in result:
            result[key] = d[key]
    return result


def import_from_parquet(
    config_name: str,
    backend: KgBackend,
) -> tuple[int, int]:
    """Import nodes and relationships from parquet files into the graph.

    Uses LOAD FROM df MERGE for efficient batch loading.

    Args:
        config_name: Name of the KG configuration to import from
        backend: The graph backend to import into

    Returns:
        Tuple of (nodes_imported, rels_imported)
    """
    from genai_graph.kg.ingest.merge import get_parquet_collector

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

            # Check for missing columns in the table schema and add them if needed
            # This handles schema evolution when importing from different sources
            struct_col_fields: dict[str, list[str]] = {}  # col_name -> expected field order
            try:
                info_result = kuzu_conn.execute(f"CALL table_info('{node_type}') RETURN *")
                info_df = info_result.get_as_df() if hasattr(info_result, "get_as_df") else pd.DataFrame()
                existing_cols = set(info_df["name"].tolist()) if "name" in info_df.columns else set()

                # Parse struct column field order from Kuzu schema types
                if "type" in info_df.columns and "name" in info_df.columns:
                    for _, row in info_df.iterrows():
                        col_type = str(row["type"])
                        if col_type.startswith("STRUCT("):
                            expected_fields = _parse_struct_field_order(col_type)
                            if expected_fields:
                                struct_col_fields[row["name"]] = expected_fields

                for col in df.columns:
                    if col not in existing_cols:
                        # Infer Kuzu type from pandas dtype
                        dtype = df[col].dtype
                        if dtype == "object":
                            kuzu_type = "STRING"
                        elif dtype == "int64":
                            kuzu_type = "INT64"
                        elif dtype == "float64":
                            kuzu_type = "DOUBLE"
                        elif dtype == "bool":
                            kuzu_type = "BOOL"
                        else:
                            kuzu_type = "STRING"  # fallback

                        try:
                            kuzu_conn.execute(f"ALTER TABLE {node_type} ADD {col} {kuzu_type}")
                            logger.debug(f"Added column {col} ({kuzu_type}) to {node_type}")
                        except Exception as alter_exc:
                            logger.debug(f"Could not add column {col} to {node_type}: {alter_exc}")
            except Exception as schema_exc:
                logger.debug(f"Could not check schema for {node_type}: {schema_exc}")

            # Convert numpy arrays to Python lists/values for Kuzu compatibility
            import numpy as np

            def convert_numpy_recursive(obj):
                """Recursively convert numpy types to Python native types."""
                if isinstance(obj, np.ndarray):
                    return [convert_numpy_recursive(item) for item in obj.tolist()]
                elif isinstance(obj, dict):
                    return {k: convert_numpy_recursive(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [convert_numpy_recursive(item) for item in obj]
                elif isinstance(obj, (np.integer,)):
                    return int(obj)
                elif isinstance(obj, (np.floating,)):
                    return float(obj)
                elif isinstance(obj, np.bool_):
                    return bool(obj)
                return obj

            for col in df.columns:
                if df[col].dtype == "object" and len(df) > 0:
                    # Check first non-null value to see if conversion is needed
                    sample_idx = df[col].first_valid_index()
                    if sample_idx is not None:
                        sample = df[col].loc[sample_idx]
                        if isinstance(sample, (np.ndarray, dict, list)):
                            # Convert numpy types to Python native types
                            df[col] = df[col].apply(lambda x: convert_numpy_recursive(x) if x is not None else None)

            # Reorder struct column dict keys to match Kuzu's expected field order.
            # PyArrow may alphabetize struct fields when writing parquet, but Kuzu
            # requires them in the exact schema order. See docs/cache_invalidation_strategy.md
            for col, expected_fields in struct_col_fields.items():
                if col in df.columns and df[col].dtype == "object" and len(df) > 0:
                    df[col] = df[col].apply(
                        lambda x: _reorder_struct_dict(x, expected_fields) if isinstance(x, dict) else x
                    )
                    logger.debug(f"Reordered struct fields for {node_type}.{col}: {expected_fields}")

            # Determine primary key from schema info or fallback to 'id' or first column
            target_pk = pk_info.get(node_type)
            pk_field = target_pk or ("id" if "id" in df.columns else df.columns[0])

            # Verify the pk_field exists in the dataframe
            if pk_field not in df.columns:
                logger.warning(
                    f"Primary key '{pk_field}' not found in parquet columns "
                    f"for {node_type}: {list(df.columns)}"
                )
                pk_field = "id" if "id" in df.columns else df.columns[0]

            # Filter out nodes with NULL or empty primary keys (Fix 1: BAML extraction issue)
            initial_count = len(df)
            df = df[df[pk_field].notna() & (df[pk_field] != "")]
            filtered_count = initial_count - len(df)
            if filtered_count > 0:
                logger.warning(
                    f"Filtered out {filtered_count} {node_type} node(s) with NULL or empty primary key '{pk_field}'"
                )

            if df.empty:
                logger.debug(f"No valid {node_type} nodes to import after filtering")
                continue

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
            # Check for struct field order mismatch error
            err_msg = str(exc)
            if "STRUCT" in err_msg and "but expected STRUCT" in err_msg and "Implicit cast is not supported" in err_msg:
                logger.error(
                    f"Failed to import {node_type} nodes: Schema mismatch detected. "
                    f"This usually happens when the BAML schema changed but cached parquet files have the old structure. "
                    f"\n\n� Solution: Run 'cli kg create --clear-all-caches' to regenerate all caches."
                )
                logger.error(f"Technical details: {exc}")
            else:
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


def export_warnings(config_name: str, warnings: list[str]) -> UPath:
    """Export warnings to a structured Markdown report.

    Args:
        config_name: Name of the KG configuration
        warnings: List of warning messages collected during KG creation

    Returns:
        Path to the exported warnings markdown file
    """
    from genai_graph.kg.export.warnings_report import generate_warnings_markdown

    manager = get_kg_manager()
    manager.ensure_directories_for(config_name)

    # Generate markdown report
    markdown_content = generate_warnings_markdown(warnings)

    # Write to file
    destination = manager.get_warnings_md_path_for(config_name)
    destination.write_text(markdown_content, encoding="utf-8")

    logger.info("Exported warnings report to '%s'", destination)

    return destination


def clear_all_parquet_caches() -> int:
    """Clear all parquet caches from kg_outputs directories.
    
    This removes all parquet/ subdirectories to force complete regeneration
    of cached data. Useful when schema changes cause incompatibilities.
    
    Returns:
        Number of cache directories cleared
    """
    import shutil
    
    from genai_tk.utils.config_mngr import global_config
    
    try:
        kg_outputs = global_config().get_dir_path("paths.kg_outputs", create_if_not_exists=False)
    except Exception:
        return 0
    
    if not kg_outputs.exists():
        return 0
    
    cleared = 0
    for kg_dir in kg_outputs.iterdir():
        if kg_dir.is_dir():
            parquet_dir = kg_dir / "parquet"
            if parquet_dir.exists():
                shutil.rmtree(str(parquet_dir))
                cleared += 1
                logger.info(f"Cleared parquet cache: {parquet_dir}")
    
    return cleared
