"""Export utilities for Knowledge Graph artifacts.

This module provides functions to export various KG artifacts:
- HTML visualization
- Schema documentation (text file)
- KG info report (markdown file)
"""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel
from upath import UPath

from genai_graph.core.graph_backend import GraphBackend
from genai_graph.core.kg_manager import get_kg_manager


class HtmlExportResult(BaseModel):
    """Result of HTML export task."""

    config_name: str
    output_path: UPath


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

    # Write info to file
    destination = manager.info_path
    destination.write_text("\n".join(lines), encoding="utf-8")

    logger.debug("Exported KG info to '%s'", destination)

    return destination
