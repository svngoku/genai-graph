"""Schema definitions for Knowledge Graph construction.

This package provides:
- GraphNode: Node configuration for graph extraction
- GraphRelation: Relationship configuration
- GraphSchema: Complete schema definition
- GraphRegistry: Registry for graph factories
- generate_schema_description: LLM-friendly schema documentation
- ResolvedSchema: Canonical enriched schema with all render methods
"""

from genai_graph.kg.schema._helpers import _get_kuzu_type_for_field
from genai_graph.kg.schema.core import (
    GraphNode,
    GraphRelation,
    GraphSchema,
    _find_embedded_field_for_class,
    find_embedded_field_for_class,
)
from genai_graph.kg.schema.doc_generator import (
    generate_schema_description,
)
from genai_graph.kg.schema.registry import (
    GraphRegistry,
    get_graph,
    get_graph_registry,
    register_graph,
)
from genai_graph.kg.schema.resolved import (
    ResolvedSchema,
    VectorIndexInfo,
)

__all__ = [
    "GraphNode",
    "GraphRelation",
    "GraphSchema",
    "GraphRegistry",
    "_find_embedded_field_for_class",
    "find_embedded_field_for_class",
    "generate_schema_description",
    "_get_kuzu_type_for_field",
    "get_graph",
    "get_graph_registry",
    "register_graph",
    "ResolvedSchema",
    "VectorIndexInfo",
]
