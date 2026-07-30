"""Generic graph node types for the genai-graph knowledge graph framework.

These types are domain-agnostic and can be used in any graph-building application.
Domain-specific node types (e.g. EKG-specific Opportunity, Customer, L3) live in
the downstream project that imports genai-graph.
"""

from genai_graph.kg.nodes.document import (
    CONTAINS_DOC,
    HAS_DOCUMENT,
    MARKDOWNIZED_AS,
    NEXT_CHUNK,
    Chunk,
    ChunkNode,
    Document,
    DocumentNode,
    MarkdownDocument,
    MarkdownDocumentNode,
    Repository,
    RepositoryNode,
)
from genai_graph.kg.nodes.markdown_tree import (
    HAS_CHUNK,
    HAS_SECTION,
    HAS_SUBSECTION,
    MarkdownSection,
    SectionNode,
)

__all__ = [
    "Document",
    "Chunk",
    "Repository",
    "MarkdownDocument",
    "DocumentNode",
    "ChunkNode",
    "RepositoryNode",
    "MarkdownDocumentNode",
    "CONTAINS_DOC",
    "NEXT_CHUNK",
    "HAS_DOCUMENT",
    "MARKDOWNIZED_AS",
    "MarkdownSection",
    "SectionNode",
    "HAS_SECTION",
    "HAS_SUBSECTION",
    "HAS_CHUNK",
]
