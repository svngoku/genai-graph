"""Streamlit page for Knowledge Graph HTML visualization.

Provides an interface to visualize the Knowledge Graph with:
- Interactive HTML graph visualization using D3.js
- Filter by node types, specific nodes, and relationships
- Configurable LIMIT parameter
- KG configuration selector

Usage:
    Navigate to this page in the Streamlit app to visualize the KG.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st
import streamlit.components.v1 as components
from genai_tk.utils.config_mngr import global_config
from loguru import logger
from streamlit import session_state as sss

from genai_graph.core.graph_backend import create_backend_from_config
from genai_graph.core.graph_html import generate_html
from genai_graph.core.kg_manager import get_kg_manager

if TYPE_CHECKING:
    from genai_graph.core.graph_backend import GraphBackend


def get_available_kg_configs() -> list[str]:
    """Get list of available KG configurations from ekg.yaml.

    Returns:
        List of KG configuration names
    """
    try:
        manager = get_kg_manager()
        return sorted(manager.ekg_config.kg_configs.keys())
    except Exception as e:
        logger.warning(f"Could not load KG configurations: {e}")
        return ["default"]


def get_node_types(backend: "GraphBackend") -> list[str]:
    """Get all node types (labels) in the graph.

    Args:
        backend: GraphBackend instance

    Returns:
        List of node type names
    """
    try:
        df = backend.execute_get_as_df("CALL show_tables() RETURN name, type", union=False)
        # Filter for NODE tables only
        node_tables = df[df["type"] == "NODE"]["name"].tolist()
        return sorted(node_tables)
    except Exception as e:
        logger.warning(f"Could not fetch node types: {e}")
        return []


def get_relationship_types(backend: "GraphBackend") -> list[str]:
    """Get all relationship types in the graph.

    Args:
        backend: GraphBackend instance

    Returns:
        List of relationship type names
    """
    try:
        df = backend.execute_get_as_df("CALL show_tables() RETURN name, type", union=False)
        # Filter for REL tables only
        rel_tables = df[df["type"] == "REL"]["name"].tolist()
        return sorted(rel_tables)
    except Exception as e:
        logger.warning(f"Could not fetch relationship types: {e}")
        return []


def get_nodes_of_type(backend: "GraphBackend", node_type: str, limit: int = 200) -> list[str]:
    """Get list of nodes of a specific type with their display names.

    Args:
        backend: GraphBackend instance
        node_type: Node type to query
        limit: Maximum number of nodes to return

    Returns:
        List of node display names
    """
    try:
        # Try to get a name field - common names are 'name', '_name', or the first non-internal field
        query = f"MATCH (n:{node_type}) RETURN n LIMIT {limit}"
        df = backend.execute_get_as_df(query, union=False)

        node_names = []
        for _, row in df.iterrows():
            node_data = row["n"]
            if isinstance(node_data, dict):
                # Try common name fields
                name = (
                    node_data.get("name")
                    or node_data.get("_name")
                    or node_data.get("title")
                    or node_data.get("id")
                    or str(node_data.get("_id", ""))[:30]
                )
                if name:
                    node_names.append(str(name))

        return sorted(set(node_names))
    except Exception as e:
        logger.warning(f"Could not fetch nodes of type {node_type}: {e}")
        return []


def build_filtered_cypher_query(
    node_types: list[str],
    node_name: str | None,
    relationship_types: list[str],
    limit: int,
    excluded_node_types: list[str] | None = None,
) -> str:
    """Build a Cypher query based on selected filters.

    Args:
        node_types: Selected node types (empty means all)
        node_name: Selected specific node name (None means all of that type)
        relationship_types: Selected relationship types (empty means all)
        limit: Maximum number of results
        excluded_node_types: Node types to exclude from results (None/empty means no exclusion)

    Returns:
        Cypher query string (comma-separated for union execution)
    """
    # Build relationship type filter
    if relationship_types:
        rel_patterns = "|".join(relationship_types)
        rel_filter = f"[r:{rel_patterns}]"
    else:
        rel_filter = "[r]"

    # Build exclusion filter for WHERE clause
    exclusion_filter = ""
    if excluded_node_types:
        exclusion_conditions = []
        for exc_type in excluded_node_types:
            exclusion_conditions.append(f"label(n) <> '{exc_type}'")
            exclusion_conditions.append(f"label(m) <> '{exc_type}'")
        exclusion_filter = " AND ".join(exclusion_conditions)

    # Build query based on filters
    if node_types and node_name:
        # Specific node and type - filter by name (use first selected type for node name filter)
        # Include both outgoing and incoming relationships (semicolon-separated for Kuzu)
        name_escaped = node_name.replace("'", "\\'")
        node_type = node_types[0]  # Use first type for specific node selection
        half_limit = limit // 2
        if exclusion_filter:
            query = (
                f"MATCH (n:{node_type})-{rel_filter}->(m) WHERE n.name = '{name_escaped}' AND {exclusion_filter} RETURN n, r, m LIMIT {half_limit}; "
                f"MATCH (n)-{rel_filter}->(m:{node_type}) WHERE m.name = '{name_escaped}' AND {exclusion_filter} RETURN n, r, m LIMIT {half_limit}"
            )
        else:
            query = (
                f"MATCH (n:{node_type})-{rel_filter}->(m) WHERE n.name = '{name_escaped}' RETURN n, r, m LIMIT {half_limit}; "
                f"MATCH (n)-{rel_filter}->(m:{node_type}) WHERE m.name = '{name_escaped}' RETURN n, r, m LIMIT {half_limit}"
            )
    elif node_types:
        # Node type filter - include both directions for each type
        # There are type issues it seems with UNION in Kuzu, so the merge
        # is done via comma-separated queries and dataframe merge afterwards
        queries = []
        per_type_limit = max(10, limit // (len(node_types) * 2))  # Split limit across types and directions
        for node_type in node_types:
            if exclusion_filter:
                queries.append(
                    f"MATCH (n:{node_type})-{rel_filter}->(m) WHERE {exclusion_filter} RETURN n, r, m LIMIT {per_type_limit}"
                )
                queries.append(
                    f"MATCH (n)-{rel_filter}->(m:{node_type}) WHERE {exclusion_filter} RETURN n, r, m LIMIT {per_type_limit}"
                )
            else:
                queries.append(f"MATCH (n:{node_type})-{rel_filter}->(m) RETURN n, r, m LIMIT {per_type_limit}")
                queries.append(f"MATCH (n)-{rel_filter}->(m:{node_type}) RETURN n, r, m LIMIT {per_type_limit}")
        query = "; ".join(queries)
    else:
        # No node type filter
        if exclusion_filter:
            query = f"MATCH (n)-{rel_filter}->(m) WHERE {exclusion_filter} RETURN n, r, m LIMIT {limit}"
        else:
            query = f"MATCH (n)-{rel_filter}->(m) RETURN n, r, m LIMIT {limit}"

    return query


def initialize_session_state() -> None:
    """Initialize session state variables."""
    if "kg_config_selected" not in sss:
        # Get default config
        try:
            manager = get_kg_manager()
            sss.kg_config_selected = manager.ekg_config.kg_config
        except Exception:
            sss.kg_config_selected = "default"

    if "graph_html" not in sss:
        sss.graph_html = None
    if "viz_limit" not in sss:
        sss.viz_limit = 500
    if "selected_node_types" not in sss:
        sss.selected_node_types = []
    if "selected_node_name" not in sss:
        sss.selected_node_name = None
    if "selected_rel_types" not in sss:
        sss.selected_rel_types = []
    if "excluded_node_types" not in sss:
        sss.excluded_node_types = []
    if "current_query" not in sss:
        sss.current_query = None


def main() -> None:
    """Main Streamlit app for KG visualization."""
    st.set_page_config(
        page_title="Knowledge Graph Visualization",
        page_icon="🕸️",
        layout="wide",
    )

    initialize_session_state()

    st.title("🕸️ Knowledge Graph Visualization")

    # Sidebar - Configuration and Filters
    with st.sidebar:
        available_configs = get_available_kg_configs()
        selected_config = st.selectbox(
            "### ⚙️ KG Configuration",
            options=available_configs,
            index=available_configs.index(sss.kg_config_selected) if sss.kg_config_selected in available_configs else 0,
            help="Select the Knowledge Graph configuration to visualize",
        )

        # Update session state if config changed
        if selected_config != sss.kg_config_selected:
            sss.kg_config_selected = selected_config
            sss.graph_html = None  # Clear cached visualization

            # Invalidate KgManager singleton to pick up new config
            config = global_config()
            config.set("kg_config", selected_config)

            get_kg_manager.invalidate()  # pyright: ignore[reportFunctionMemberAccess]

            st.rerun()

        # Get database connection for filters
        try:
            backend = create_backend_from_config("default", selected_config)
            if not backend:
                st.error("❌ No Knowledge Graph database found")
                return
        except Exception as e:
            st.error(f"Failed to connect to database: {e}")
            return

        st.markdown("### 🎯 Filters")

        # Track previous filter values to detect changes
        if "prev_node_types" not in sss:
            sss.prev_node_types = []
        if "prev_node_name" not in sss:
            sss.prev_node_name = None
        if "prev_rel_types" not in sss:
            sss.prev_rel_types = []
        if "prev_viz_limit" not in sss:
            sss.prev_viz_limit = 500
        if "prev_excluded_node_types" not in sss:
            sss.prev_excluded_node_types = []

        # Node type filter (multiselect)
        available_node_types = get_node_types(backend)
        if available_node_types:
            selected_node_types = st.multiselect(
                "Node Types",
                options=available_node_types,
                default=[],
                help="Select node types to include (empty = all types)",
            )
            sss.selected_node_types = selected_node_types
        else:
            st.info("No node types available")
            sss.selected_node_types = []

        # Exclude node type filter (multiselect)
        if available_node_types:
            # Filter out the selected include node types to prevent conflict
            exclude_options = [t for t in available_node_types if t not in sss.selected_node_types]
            selected_exclude_types = st.multiselect(
                "Exclude Node Types",
                options=exclude_options,
                default=[],
                help="Select node types to exclude from the visualization",
            )
            sss.excluded_node_types = selected_exclude_types
        else:
            sss.excluded_node_types = []

        # Specific node selector (only if exactly one node type is selected)
        if len(sss.selected_node_types) == 1:
            node_type = sss.selected_node_types[0]
            available_nodes = get_nodes_of_type(backend, node_type)
            if available_nodes:
                node_options = ["(All)"] + available_nodes
                selected_node_display = st.selectbox(
                    f"{node_type} Nodes",
                    options=node_options,
                    index=0,
                    help=f"Select a specific {node_type} node to focus on",
                )
                sss.selected_node_name = None if selected_node_display == "(All)" else selected_node_display
            else:
                st.info(f"No {node_type} nodes found")
                sss.selected_node_name = None
        else:
            sss.selected_node_name = None

        # Relationship type filter
        available_rel_types = get_relationship_types(backend)
        if available_rel_types:
            selected_rel_types = st.multiselect(
                "Relationship Types",
                options=available_rel_types,
                default=[],
                help="Select relationship types to display (empty = all types)",
            )
            sss.selected_rel_types = selected_rel_types
        else:
            st.info("No relationship types available")
            sss.selected_rel_types = []

        # Limit parameter
        limit = st.number_input(
            "Result Limit",
            min_value=10,
            max_value=10000,
            value=500,
            step=50,
            help="Maximum number of relationships to display",
        )
        sss.viz_limit = limit

        # Detect filter changes and clear cached visualization
        if (
            sss.selected_node_types != sss.prev_node_types
            or sss.selected_node_name != sss.prev_node_name
            or sss.selected_rel_types != sss.prev_rel_types
            or sss.viz_limit != sss.prev_viz_limit
            or sss.excluded_node_types != sss.prev_excluded_node_types
        ):
            sss.graph_html = None  # Clear cached graph when filters change
            sss.prev_node_types = sss.selected_node_types.copy() if sss.selected_node_types else []
            sss.prev_node_name = sss.selected_node_name
            sss.prev_rel_types = sss.selected_rel_types.copy() if sss.selected_rel_types else []
            sss.prev_viz_limit = sss.viz_limit
            sss.prev_excluded_node_types = sss.excluded_node_types.copy() if sss.excluded_node_types else []

        st.markdown("---")

        # Generate button
        if st.button("🎨 Generate Visualization", type="primary", use_container_width=True):
            with st.spinner("Generating visualization..."):
                try:
                    # Build query based on filters
                    query = build_filtered_cypher_query(
                        sss.selected_node_types,
                        sss.selected_node_name,
                        sss.selected_rel_types,
                        sss.viz_limit,
                        sss.excluded_node_types,
                    )

                    # Store the query in session state
                    sss.current_query = query

                    # Generate HTML visualization
                    html_content = generate_html(
                        backend,
                        query=query,
                    )
                    sss.graph_html = html_content
                    st.success("✅ Visualization generated!")
                except Exception as e:
                    st.error(f"Failed to generate visualization: {e}")
                    logger.exception("Visualization generation failed")

    if sss.graph_html:
        # Display current filters and query side by side
        filter_info = []
        if sss.selected_node_types:
            if sss.selected_node_name:
                filter_info.append(f"Node: {sss.selected_node_types[0]}/{sss.selected_node_name}")
            else:
                filter_info.append(f"Node Types: {', '.join(sss.selected_node_types)}")
        if sss.excluded_node_types:
            filter_info.append(f"Excluding: {', '.join(sss.excluded_node_types)}")
        if sss.selected_rel_types:
            filter_info.append(f"Relationships: {', '.join(sss.selected_rel_types)}")
        filter_info.append(f"Limit: {sss.viz_limit}")

        col1, col2 = st.columns([1, 2])
        with col1:
            st.caption("Filters: " + " | ".join(filter_info))
        with col2:
            if sss.current_query:
                st.caption(f"Query: `{sss.current_query}`")

        # Display the graph
        with st.container():
            components.html(sss.graph_html, height=600, scrolling=True)
    else:
        st.info("👈 Click 'Generate Visualization' in the sidebar to display the graph with your selected filters.")


if __name__ == "__main__":
    main()
