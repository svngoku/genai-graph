"""Generic graph node types for the genai-graph knowledge graph framework.

These types are domain-agnostic and can be used in any graph-building application.
Domain-specific node types (e.g. EKG-specific Opportunity, Customer, L3) live in
the downstream project that imports genai-graph.
"""

from genai_graph.kg.nodes.document import CONTAINS_DOC, NEXT_CHUNK, Chunk, ChunkNode, Document, DocumentNode

__all__ = [
    "Document",
    "Chunk",
    "DocumentNode",
    "ChunkNode",
    "CONTAINS_DOC",
    "NEXT_CHUNK",
]
