"""Graph node model for the Markdown Knowledge Tree.

A ``MarkdownSection`` represents one heading-delimited section of a Markdown
document. Sections are stored as a flat node table (not a nested Pydantic
structure) with an explicit ``parent_section_id`` — the hierarchy is
materialised entirely as ``HAS_SUBSECTION`` graph edges, which lets an agent
navigate the tree with ordinary Cypher traversals.

These node/relation singletons are ingested directly via
:func:`genai_graph.kg.markdown.ingest.ingest_markdown_tree`, which bypasses
the generic Pydantic-nesting extraction (``extract_graph_data``) — the section
hierarchy is a self-referential structure that doesn't map cleanly onto that
mechanism — and instead builds nodes/relationships explicitly, then merges
them with the same Arrow/Ladybug primitives (`merge_nodes_batch`,
`merge_relationships_batch`) used everywhere else in genai-graph.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from genai_graph.kg.nodes.document import DocumentNode
from genai_graph.kg.schema.core import GraphNode, GraphRelation


class MarkdownSection(BaseModel):
    """A single heading-delimited section of a Markdown document."""

    section_id: str = Field(..., description="Primary key: f'{document_path}::{line_start}'")
    document_path: str = Field(..., description="Path of the owning Document node (foreign key)")
    parent_section_id: str | None = Field(
        default=None, description="section_id of the parent section, or None for a top-level (root) section"
    )
    title: str = Field(..., description="Heading text")
    level: int = Field(..., description="Heading level, 1 (H1) to 6 (H6)")
    line_start: int = Field(..., description="1-indexed source line of the heading")
    line_end: int = Field(..., description="1-indexed source line where the section ends (inclusive)")
    text: str = Field(..., description="Raw Markdown text of the section (heading line + body)")
    token_count: int = Field(..., description="Approximate token count")
    sequence: int = Field(..., description="0-based position of this section within its document, in document order")


# ---------------------------------------------------------------------------
# GraphNode / GraphRelation singletons
# ---------------------------------------------------------------------------
#
# `field_paths` are set explicitly to a no-op sentinel because the section
# hierarchy is self-referential and is populated by the direct ingestion path
# in `genai_graph.kg.markdown.ingest`, not by the generic
# Pydantic-nesting-based `extract_graph_data()` extraction.

SectionNode: GraphNode = GraphNode(
    node_class=MarkdownSection,
    name_from="title",
    key_from="section_id",
    description="A heading-delimited section of a Markdown document",
    explicitly_defined=True,
)

HAS_SECTION: GraphRelation = GraphRelation(
    name="HAS_SECTION",
    from_node=DocumentNode,
    to_node=SectionNode,
    description="Document has a top-level (root) section",
    field_paths=[("", "")],
)

HAS_SUBSECTION: GraphRelation = GraphRelation(
    name="HAS_SUBSECTION",
    from_node=SectionNode,
    to_node=SectionNode,
    description="Parent section contains a nested child section",
    field_paths=[("", "")],
)
