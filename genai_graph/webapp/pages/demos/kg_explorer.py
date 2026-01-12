"""Knowledge Graph Explorer - Navigation Page.

This page has been split into two specialized pages for better user experience:

- **KG Query**: Query the knowledge graph using Cypher or natural language
- **KG Visualization**: Visualize the graph with interactive filtering

Please navigate to one of these pages using the links below.
"""

import streamlit as st


def main() -> None:
    """Show navigation page directing users to the new split pages."""
    st.set_page_config(
        page_title="Knowledge Graph Explorer",
        page_icon="🕸️",
        layout="wide",
    )

    st.title("🕸️ Knowledge Graph Explorer")

    st.info(
        """
        **Note:** This page has been split into two specialized pages for improved usability.
        Please select one of the options below:
        """
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🔍 KG Query")
        st.markdown(
            """
            **Query the Knowledge Graph**
            
            Features:
            - Execute Cypher queries with examples
            - Natural language to Cypher conversion
            - View results as DataFrame
            - Export results to CSV
            - Select KG configuration
            """
        )
        st.page_link(
            "pages/demos/kg_query.py",
            label="Go to KG Query →",
            icon="🔍",
        )

    with col2:
        st.markdown("### 🕸️ KG Visualization")
        st.markdown(
            """
            **Visualize the Graph Structure**
            
            Features:
            - Interactive HTML graph visualization
            - Filter by node types
            - Filter by relationship types
            - Adjustable result limits
            - Select KG configuration
            """
        )
        st.page_link(
            "pages/demos/kg_visualization.py",
            label="Go to KG Visualization →",
            icon="🕸️",
        )

    st.markdown("---")

    st.markdown("### 📚 Documentation")
    st.markdown(
        """
        - [Cypher Query Language](https://neo4j.com/docs/cypher-manual/current/)
        - [Graph Patterns](https://neo4j.com/docs/cypher-manual/current/patterns/)
        - [Knowledge Graph Documentation](https://github.com/tclatos/genai-graph)
        """
    )


if __name__ == "__main__":
    main()
