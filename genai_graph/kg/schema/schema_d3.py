"""Build D3-friendly JSON representations of KG schemas."""

from __future__ import annotations

from typing import Any

from genai_graph.kg.schema.core import GraphSchema


def build_schema_d3_data(schema: GraphSchema, graph_names: list[str] | None = None) -> dict[str, Any]:
    """Build a D3-ready JSON model for a graph schema.

    The resulting data structure is optimized for direct use with D3 force-layout
    (or other graph layouts): nodes are a list with stable string IDs, and edges
    reference nodes by those IDs.

    Delegates to :class:`~genai_graph.kg.schema.resolved.ResolvedSchema` which
    is the single source of truth for schema rendering.

    Args:
        schema: Schema to export.
        graph_names: Optional list of graph factory names used to build the schema.

    Returns:
        Dictionary with keys: meta, nodes, links, vector_indexes.
    """
    from genai_graph.kg.schema.resolved import ResolvedSchema

    return ResolvedSchema.from_graph_schema(schema, graph_names=graph_names).to_d3_json()
