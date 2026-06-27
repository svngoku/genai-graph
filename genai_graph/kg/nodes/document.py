"""Generic Document and Chunk node models.

Document
--------
Represents a source file from which graph data was extracted (or ingested).
Serves as provenance anchor for all entities extracted from a file.

Chunk
-----
A text chunk derived from a Document, typically produced by a semantic or
fixed-size chunker. Chunks carry an optional embedding vector for similarity
search.

Relationships
-------------
- CONTAINS_DOC : Document → Chunk  (a document contains its chunks)
- NEXT_CHUNK   : Chunk → Chunk     (sequential order within the document)

These GraphNode / GraphRelation singletons are ready for use in any factory's
build_schema() call:

    from genai_graph.kg.nodes import DocumentNode, ChunkNode, CONTAINS_DOC, NEXT_CHUNK

    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=...,
            nodes=[DocumentNode, ChunkNode, ...],
            relations=[CONTAINS_DOC, NEXT_CHUNK, ...],
        )
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from genai_graph.kg.schema.core import GraphNode, GraphRelation

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Document(BaseModel):
    """Source document node — tracks the file from which graph data was extracted.

    Serves as the provenance anchor for all entities and chunks derived from a file.
    Access-control fields are intentionally simple and can be extended downstream.
    """

    path: str = Field(..., description="Absolute path to the source file (primary key)")
    filename: str = Field(..., description="Base filename without directory")
    file_size: int | None = Field(default=None, description="File size in bytes")
    mime_type: str | None = Field(default=None, description="MIME type inferred from extension")
    modified_at: str | None = Field(default=None, description="Last-modified timestamp (ISO 8601)")
    content_hash: str | None = Field(default=None, description="xxHash XXH3-64 digest for deduplication")
    # Access control — basic; can be extended in domain-specific projects
    access_level: str = Field(default="public", description="Access level: public | restricted | confidential")
    allowed_roles: list[str] = Field(default_factory=list, description="Roles permitted to access this document")
    allowed_users: list[str] = Field(default_factory=list, description="Users permitted to access this document")


class Chunk(BaseModel):
    """A text chunk derived from a Document.

    Produced by a chunker (e.g. chonkie semantic chunker) during document
    ingestion. The ``embedding`` field holds a dense vector for similarity search.
    """

    chunk_id: str = Field(..., description="Unique chunk identifier (primary key) — document path + chunk index")
    document_path: str = Field(..., description="Path to the parent Document node (foreign key)")
    text: str = Field(..., description="Raw text content of this chunk")
    chunk_index: int = Field(..., description="Zero-based position within the document")
    start_offset: int | None = Field(default=None, description="Character start offset in the source document")
    end_offset: int | None = Field(default=None, description="Character end offset in the source document")
    token_count: int | None = Field(default=None, description="Number of tokens (model-dependent)")
    embedding: list[float] | None = Field(default=None, description="Dense embedding vector for similarity search")


# ---------------------------------------------------------------------------
# GraphNode singletons — plug these into GraphSchema.nodes
# ---------------------------------------------------------------------------

DocumentNode: GraphNode = GraphNode(
    node_class=Document,
    name_from="filename",
    key_from="path",
    description="Source document from which graph data was extracted",
    explicitly_defined=True,
)

ChunkNode: GraphNode = GraphNode(
    node_class=Chunk,
    name_from="chunk_id",
    key_from="chunk_id",
    description="Text chunk derived from a source document",
    index_fields=["embedding"],
)

# ---------------------------------------------------------------------------
# GraphRelation singletons — plug these into GraphSchema.relations
# ---------------------------------------------------------------------------

CONTAINS_DOC: GraphRelation = GraphRelation(
    name="CONTAINS",
    from_node=DocumentNode,
    to_node=ChunkNode,
    description="Document contains a text chunk",
)

NEXT_CHUNK: GraphRelation = GraphRelation(
    name="NEXT",
    from_node=ChunkNode,
    to_node=ChunkNode,
    description="Sequential order of chunks within a document",
)
