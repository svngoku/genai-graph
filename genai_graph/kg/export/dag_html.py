"""Generate an interactive left-to-right DAG visualization of a Cypher graph.

This is the DAG counterpart to :mod:`genai_graph.kg.export.html`. It reuses the
exact same data extraction (``_fetch_graph_data``) and model building
(``build_graph_model``) as the force-directed view, then injects the resulting
``nodes``/``links`` JSON into a page rendered with d3-dag's sugiyama layered
layout (default algorithm parameters), laid out left-to-right with roots
(nodes without incoming relationships) on the left.

d3-dag's IIFE bundle merges into the existing ``d3`` global, so it coexists
with the bundled d3 v5 used for zoom/pan/selection.

Usage example:
```python
from genai_graph.kg.export.dag_html import generate_dag_html

html_content = generate_dag_html(
    connection=graph_backend,
    destination_file_path="my_dag.html",
)
```
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from genai_graph.kg.backend import KgBackend
from genai_graph.kg.export._graph_model import (
    _fetch_graph_data,
    build_graph_model,
)
from genai_graph.kg.export.dag_html_template import (
    DAG_HTML_TEMPLATE,
    DAG_ORIENTATION,
    PX_PER_LAYER_UNIT,
    PX_PER_SIBLING_UNIT,
)

_D3_CDN_TAG = '<script src="https://d3js.org/d3.v5.min.js"></script>'
_D3_DAG_CDN_TAG = '<script src="https://cdn.jsdelivr.net/npm/d3-dag@1.1.0/bundle/d3-dag.iife.min.js"></script>'
_SCHEMA_DIR = Path(__file__).parent.parent / "schema"


def _embed_bundle(cdn_tag: str, bundle_name: str) -> str:
    """Return an inline ``<script>`` for *bundle_name* if present, else the CDN tag."""
    bundle = _SCHEMA_DIR / bundle_name
    if bundle.exists():
        return f"<script>{bundle.read_text(encoding='utf-8')}</script>"
    return cdn_tag


def _generate_dag_html_content(nodes_list: list[dict[str, Any]], links_list: list[dict[str, Any]]) -> str:
    """Generate DAG HTML content from nodes and links lists.

    Args:
        nodes_list: List of node dictionaries with id, name, type, color, and properties
        links_list: List of link dictionaries with source, target, relation, weight info

    Returns:
        HTML content as a string with embedded D3.js + d3-dag visualization
    """
    html_content = DAG_HTML_TEMPLATE.replace("{nodes}", json.dumps(nodes_list))
    html_content = html_content.replace("{links}", json.dumps(links_list))
    # Inject layout constants.
    html_content = html_content.replace("__ORIENTATION__", DAG_ORIENTATION)
    html_content = html_content.replace("__PX_LAYER__", str(PX_PER_LAYER_UNIT))
    html_content = html_content.replace("__PX_SIBLING__", str(PX_PER_SIBLING_UNIT))
    # Bundle d3 v5 then d3-dag offline (CDN fallback). Order matters: d3 v5 must
    # load first so the d3-dag IIFE merges into the existing ``d3`` global.
    html_content = html_content.replace(_D3_CDN_TAG, _embed_bundle(_D3_CDN_TAG, "d3.v5.min.js"))
    html_content = html_content.replace(_D3_DAG_CDN_TAG, _embed_bundle(_D3_DAG_CDN_TAG, "d3-dag.iife.min.js"))
    return html_content


def generate_dag_html(
    connection: KgBackend,
    destination_file_path: str | None = None,
    *,
    node_configs: list | None = None,
    relation_configs: list | None = None,
    custom_colors: dict[str, str] | None = None,
    query: str = "MATCH (n)-[r]->(m) RETURN n, r, m",
    union: bool = True,
    filter_orphan_nodes: bool = False,
    selected_node_types: list[str] | None = None,
) -> str:
    """Generate a left-to-right DAG HTML visualization from a graph connection/backend.

    Shares the same data pipeline as :func:`generate_html` (same query, same
    nodes/links model, same colors and orphan filtering) and differs only in the
    renderer: d3-dag sugiyama layout instead of a D3 force simulation. The graph
    is laid out left-to-right, starting from nodes that have no incoming
    relationship (detected from the edge structure, so no special Cypher is
    needed).

    Args:
        connection: Object exposing an execute() method (e.g. KgBackend or
            kuzu.Connection) connected to a database.
        destination_file_path: Optional path to write the HTML file. If omitted,
            the file will be saved as "dag_visualization.html" in the user's home directory.
        node_configs: Optional list of node configurations (legacy or new format)
        relation_configs: Optional list of relation configurations (legacy or new format)
        custom_colors: Optional mapping of node types to hex color codes
        query: Cypher query to fetch relationships and nodes (default:
            "MATCH (n)-[r]->(m) RETURN n, r, m"). Must return columns named
            'n', 'r', 'm' for source node, relationship, target node.
        union: If True and query contains multiple statements, union the results. Default True.
        filter_orphan_nodes: If True, remove nodes that don't appear in any
            relationship. Nodes whose type is in ``selected_node_types`` are
            always preserved regardless of this flag.
        selected_node_types: Node type labels the user explicitly chose to display.

    Returns:
        The HTML content as a string.
    """
    nodes_data, relationships_data = _fetch_graph_data(
        connection,
        node_configs,
        relation_configs,
        query,
        union,
        supplemental_node_types=selected_node_types,
    )

    nodes_list, links_list = build_graph_model(
        nodes_data,
        relationships_data,
        custom_colors=custom_colors,
        filter_orphan_nodes=filter_orphan_nodes,
        selected_node_types=selected_node_types,
    )

    html_content = _generate_dag_html_content(nodes_list, links_list)

    if not destination_file_path:
        home_dir = os.path.expanduser("~")
        destination_file_path = os.path.join(home_dir, "dag_visualization.html")

    os.makedirs(os.path.dirname(destination_file_path) or ".", exist_ok=True)
    with open(destination_file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_content
