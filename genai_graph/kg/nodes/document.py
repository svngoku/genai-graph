"""Generic source-structure node models for the knowledge graph.

Repository
----------
A logical base location (a local directory, a ``.zip`` archive, or — later —
a SharePoint site) from which source files are read. Storing a Repository lets
Documents carry a short ``relative_path`` instead of a full absolute path.

Document
--------
A source file from which graph data is extracted or ingested. Its primary key
is the **content hash** (``content_hash``) — two byte-identical files collapse to
one Document node. The file's ``path`` is kept only as informative metadata.

MarkdownDocument
----------------
The Markdown rendering of a source Document (produced by markdownization, or the
document itself when it is already Markdown). Keyed by the hash of the Markdown
text so re-ingesting identical content is a no-op MERGE.

Chunk
-----
A text chunk carrying an optional embedding vector for similarity search. Chunks
are produced either directly from a Document (RAG pipeline, via ``CONTAINS``) or
from a Markdown ``Section`` (Markdown tree, via ``HAS_CHUNK``).

Relationships
-------------
- HAS_DOCUMENT    : Repository → Document       (a repository holds source files)
- MARKDOWNIZED_AS : Document → MarkdownDocument  (source rendered to Markdown)
- CONTAINS_DOC    : Document → Chunk             (RAG: a document contains its chunks)
- NEXT_CHUNK      : Chunk → Chunk                (sequential order within a document)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from genai_graph.kg.schema.core import GraphNode, GraphRelation

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Repository(BaseModel):
    """A base location that source Documents are read from."""

    repo_id: str = Field(..., description="Primary key: stable identifier for the repository")
    uri: str = Field(..., description="Base URI/path of the repository (directory, .zip, or remote site)")
    kind: Literal["directory", "zip", "sharepoint"] = Field(default="directory", description="Repository backend kind")
    name: str = Field(..., description="Human-friendly repository name")


class Document(BaseModel):
    """Source document node, keyed by content hash.

    Serves as the provenance anchor for everything derived from a file. The
    ``content_hash`` is the primary key, so identical files map to a single node
    regardless of where they live. ``path`` is informative metadata only;
    ``repository_id`` + ``relative_path`` locate the file within a Repository.
    """

    content_hash: str = Field(..., description="xxHash XXH3-64 digest of the file content (primary key)")
    filename: str = Field(..., description="Base filename without directory")
    repository_id: str | None = Field(default=None, description="Owning Repository.repo_id (foreign key)")
    relative_path: str | None = Field(default=None, description="Path relative to the repository base")
    path: str | None = Field(default=None, description="Absolute source path (informative only)")
    file_size: int | None = Field(default=None, description="File size in bytes")
    mime_type: str | None = Field(default=None, description="MIME type inferred from extension")
    modified_at: str | None = Field(default=None, description="Last-modified timestamp (ISO 8601)")
    # Access control — basic; can be extended in domain-specific projects
    access_level: str = Field(default="public", description="Access level: public | restricted | confidential")
    allowed_roles: list[str] = Field(default_factory=list, description="Roles permitted to access this document")
    allowed_users: list[str] = Field(default_factory=list, description="Users permitted to access this document")


class MarkdownDocument(BaseModel):
    """The Markdown rendering of a source Document, keyed by its content hash."""

    content_hash: str = Field(..., description="xxHash XXH3-64 digest of the Markdown text (primary key)")
    source_hash: str = Field(..., description="content_hash of the source Document (foreign key)")
    filename: str = Field(..., description="Base filename of the source document")
    token_count: int = Field(default=0, description="Approximate token count of the whole Markdown document")
    section_count: int = Field(default=0, description="Number of sections parsed from this document")


class Chunk(BaseModel):
    """A text chunk derived from a Document or a Markdown Section.

    The ``embedding`` field holds a dense vector for similarity search. For RAG
    chunks ``document_path`` links back to the source; for Markdown-tree chunks
    ``section_id`` and ``markdown_hash`` are populated instead.
    """

    chunk_id: str = Field(..., description="Unique chunk identifier (primary key)")
    text: str = Field(..., description="Raw text content of this chunk")
    chunk_index: int = Field(..., description="Zero-based position within its parent (document or section)")
    document_path: str | None = Field(default=None, description="Path to the parent Document (RAG pipeline)")
    section_id: str | None = Field(default=None, description="Parent MarkdownSection.section_id (Markdown tree)")
    markdown_hash: str | None = Field(default=None, description="Parent MarkdownDocument.content_hash (Markdown tree)")
    start_offset: int | None = Field(default=None, description="Character start offset in the source text")
    end_offset: int | None = Field(default=None, description="Character end offset in the source text")
    token_count: int | None = Field(default=None, description="Number of tokens (model-dependent)")
    embedding: list[float] | None = Field(default=None, description="Dense embedding vector for similarity search")


# ---------------------------------------------------------------------------
# GraphNode singletons — plug these into GraphSchema.nodes
# ---------------------------------------------------------------------------

RepositoryNode: GraphNode = GraphNode(
    node_class=Repository,
    name_from="name",
    key_from="repo_id",
    description="Base location that source documents are read from",
    explicitly_defined=True,
)

DocumentNode: GraphNode = GraphNode(
    node_class=Document,
    name_from="filename",
    key_from="content_hash",
    description="Source document from which graph data was extracted (keyed by content hash)",
    explicitly_defined=True,
)

MarkdownDocumentNode: GraphNode = GraphNode(
    node_class=MarkdownDocument,
    name_from="filename",
    key_from="content_hash",
    description="Markdown rendering of a source document (keyed by content hash)",
    explicitly_defined=True,
)

ChunkNode: GraphNode = GraphNode(
    node_class=Chunk,
    name_from="chunk_id",
    key_from="chunk_id",
    description="Text chunk derived from a source document or Markdown section",
    index_fields=["embedding"],
)

# ---------------------------------------------------------------------------
# GraphRelation singletons — plug these into GraphSchema.relations
# ---------------------------------------------------------------------------

HAS_DOCUMENT: GraphRelation = GraphRelation(
    name="HAS_DOCUMENT",
    from_node=RepositoryNode,
    to_node=DocumentNode,
    description="Repository holds a source document",
)

MARKDOWNIZED_AS: GraphRelation = GraphRelation(
    name="MARKDOWNIZED_AS",
    from_node=DocumentNode,
    to_node=MarkdownDocumentNode,
    description="Source document rendered into a Markdown document",
)

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
