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
from loguru import logger
from streamlit import session_state as sss

from genai_graph.core.graph_backend import create_backend_from_config
from genai_graph.core.graph_html import generate_html

if TYPE_CHECKING:
    from genai_graph.core.graph_backend import GraphBackend


def get_available_kg_configs() -> list[str]:
    """Get list of available KG configurations from ekg.yaml.

    Returns:
        List of KG configuration names
    """
    try:
        from genai_graph.core.kg_manager import get_kg_manager

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
        result = backend.execute("CALL show_tables() RETURN name, type")
        df = result.get_as_df()
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
        result = backend.execute("CALL show_tables() RETURN name, type")
        df = result.get_as_df()
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
        result = backend.execute(query)
        df = result.get_as_df()

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
    node_type: str | None,
    node_name: str | None,
    relationship_types: list[str],
    limit: int,
) -> str:
    """Build a Cypher query based on selected filters.

    Args:
        node_type: Selected node type (None means all)
        node_name: Selected specific node name (None means all of that type)
        relationship_types: Selected relationship types (empty means all)
        limit: Maximum number of results

    Returns:
        Cypher query string
    """
    # Build relationship type filter
    if relationship_types:
        rel_patterns = "|".join(relationship_types)
        rel_filter = f"[r:{rel_patterns}]"
    else:
        rel_filter = "[r]"

    # Build query based on filters
    if node_type and node_name:
        # Specific node and type - filter by name
        name_escaped = node_name.replace("'", "\\'")
        query = f"MATCH (n:{node_type})-{rel_filter}->(m) WHERE n.name = '{name_escaped}' RETURN n, r, m LIMIT {limit}"
    elif node_type:
        # Just node type filter
        query = f"MATCH (n:{node_type})-{rel_filter}->(m) RETURN n, r, m LIMIT {limit}"
    else:
        # No node type filter
        query = f"MATCH (n)-{rel_filter}->(m) RETURN n, r, m LIMIT {limit}"

    return query


def initialize_session_state() -> None:
    """Initialize session state variables."""
    if "kg_config_selected" not in sss:
        # Get default config
        try:
            from genai_graph.core.kg_manager import get_kg_manager

            manager = get_kg_manager()
            sss.kg_config_selected = manager.ekg_config.kg_config
        except Exception:
            sss.kg_config_selected = "default"

    if "graph_html" not in sss:
        sss.graph_html = None
    if "viz_limit" not in sss:
        sss.viz_limit = 500
    if "selected_node_type" not in sss:
        sss.selected_node_type = None
    if "selected_node_name" not in sss:
        sss.selected_node_name = None
    if "selected_rel_types" not in sss:
        sss.selected_rel_types = []


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
        st.markdown("### ⚙️ Configuration")

        available_configs = get_available_kg_configs()
        selected_config = st.selectbox(
            "KG Configuration",
            options=available_configs,
            index=available_configs.index(sss.kg_config_selected) if sss.kg_config_selected in available_configs else 0,
            help="Select the Knowledge Graph configuration to visualize",
        )

        # Update session state if config changed
        if selected_config != sss.kg_config_selected:
            sss.kg_config_selected = selected_config
            sss.graph_html = None  # Clear cached visualization
            st.rerun()

        st.markdown("---")

        # Get database connection for filters
        try:
            backend = create_backend_from_config("default", sss.kg_config_selected)
            if not backend:
                st.error("❌ No Knowledge Graph database found")
                return
        except Exception as e:
            st.error(f"Failed to connect to database: {e}")
            return

        st.markdown("### 🎯 Filters")

        # Node type filter (single select)
        available_node_types = get_node_types(backend)
        if available_node_types:
            # Add "All" option at the beginning
            node_type_options = ["(All)"] + available_node_types
            selected_node_type_display = st.selectbox(
                "Node Type",
                options=node_type_options,
                index=0,
                help="Select a node type to filter by",
            )
            # Store the actual node type (None if "All" selected)
            sss.selected_node_type = None if selected_node_type_display == "(All)" else selected_node_type_display
        else:
            st.info("No node types available")
            sss.selected_node_type = None

        # Specific node selector (only if a node type is selected)
        if sss.selected_node_type:
            available_nodes = get_nodes_of_type(backend, sss.selected_node_type)
            if available_nodes:
                node_options = ["(All)"] + available_nodes
                selected_node_display = st.selectbox(
                    f"{sss.selected_node_type} Nodes",
                    options=node_options,
                    index=0,
                    help=f"Select a specific {sss.selected_node_type} node to focus on",
                )
                sss.selected_node_name = None if selected_node_display == "(All)" else selected_node_display
            else:
                st.info(f"No {sss.selected_node_type} nodes found")
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

        st.markdown("---")

        # Generate button
        if st.button("🎨 Generate Visualization", type="primary", use_container_width=True):
            with st.spinner("Generating visualization..."):
                try:
                    # Build query based on filters
                    query = build_filtered_cypher_query(
                        sss.selected_node_type,
                        sss.selected_node_name,
                        sss.selected_rel_types,
                        sss.viz_limit,
                    )

                    st.info(f"Executing query: `{query}`")

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

        st.markdown("---")
        st.markdown("### 🔗 Resources")
        st.markdown("""
        - [Graph Visualization Guide](https://neo4j.com/docs/cypher-manual/current/patterns/)
        - [D3.js Documentation](https://d3js.org/)
        """)

    # Main content area
    st.markdown("### 📊 Interactive Graph")

    if sss.graph_html is None:
        # Show default visualization on first load
        with st.spinner("Loading default graph visualization..."):
            try:
                query = f"MATCH (n)-[r]->(m) RETURN n, r, m LIMIT {sss.viz_limit}"
                html_content = generate_html(
                    backend,
                    query=query,
                )
                sss.graph_html = html_content
            except Exception as e:
                st.error(f"Failed to generate default visualization: {e}")
                st.info("Use the sidebar to configure filters and generate a custom visualization.")
                return

    if sss.graph_html:
        # Display current filters
        filter_info = []
        if sss.selected_node_type:
            if sss.selected_node_name:
                filter_info.append(f"Node: {sss.selected_node_type}/{sss.selected_node_name}")
            else:
                filter_info.append(f"Node Type: {sss.selected_node_type}")
        if sss.selected_rel_types:
            filter_info.append(f"Relationships: {', '.join(sss.selected_rel_types)}")
        filter_info.append(f"Limit: {sss.viz_limit}")

        st.caption("Current filters: " + " | ".join(filter_info))

        # Display the graph
        with st.container():
            components.html(sss.graph_html, height=800, scrolling=True)

        st.info("💡 Tip: Use the filters in the sidebar to focus on specific parts of the graph.")
    else:
        st.info("Use the sidebar to generate a visualization.")


if __name__ == "__main__":
    main()
