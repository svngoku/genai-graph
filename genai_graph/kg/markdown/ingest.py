"""Direct ingestion of a Markdown Knowledge Tree into a graph backend.

Builds a ``Repository → Document → MarkdownDocument → Section → Chunk`` tree. The
section hierarchy is self-referential (`MarkdownSection -> MarkdownSection`),
which doesn't map onto the generic Pydantic-nesting extraction used elsewhere in
genai-graph (`extract_graph_data`). This module instead builds
`NodeDataCollection` / `RelationshipRecord` objects directly from a
`MarkdownTreeFactory` and merges them with the same Arrow/Ladybug primitives
(`merge_nodes_batch`, `merge_relationships_batch`) used by the rest of the
ingestion pipeline.

Documents and Markdown documents are keyed by content hash, so re-ingesting an
unchanged corpus is a MERGE no-op. Before parsing sections and (optionally)
computing embeddings for a document, the DB is checked for an existing
``MarkdownDocument`` with the same hash — when present, section/chunk creation
and embedding are skipped entirely, avoiding costly recomputation.
"""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.backend import KgBackend
from genai_graph.kg.factories.markdown_tree_factory import MarkdownTreeFactory
from genai_graph.kg.ingest.extract import RelationshipRecord, create_schema
from genai_graph.kg.ingest.merge import (
    NodeDataCollection,
    NodeTypeRegistry,
    merge_nodes_batch,
    merge_relationships_batch,
)
from genai_graph.kg.nodes.document import (
    CONTAINS_DOC,
    HAS_DOCUMENT,
    MARKDOWNIZED_AS,
    NEXT_CHUNK,
    ChunkNode,
    DocumentNode,
    MarkdownDocumentNode,
    RepositoryNode,
)
from genai_graph.kg.nodes.markdown_tree import HAS_CHUNK, HAS_SECTION, HAS_SUBSECTION, SectionNode

_REPOSITORY_TYPE = RepositoryNode.node_class.__name__
_DOCUMENT_TYPE = DocumentNode.node_class.__name__
_MARKDOWN_TYPE = MarkdownDocumentNode.node_class.__name__
_SECTION_TYPE = SectionNode.node_class.__name__
_CHUNK_TYPE = ChunkNode.node_class.__name__


class MarkdownTreeIngestResult(BaseModel):
    """Outcome of a Markdown Knowledge Tree ingestion run."""

    documents_processed: int = 0
    documents_failed: int = 0
    documents_skipped: int = 0
    sections_created: int = 0
    chunks_created: int = 0
    relationships_created: int = 0
    warnings: list[str] = Field(default_factory=list)


def _markdown_document_exists(backend: KgBackend, markdown_hash: str) -> bool:
    """Return True if a MarkdownDocument with this hash is already in the graph."""
    try:
        df = backend.execute_get_as_df(
            f"MATCH (m:{_MARKDOWN_TYPE} {{content_hash: $h}}) RETURN m.content_hash AS h LIMIT 1",
            {"h": markdown_hash},
            union=False,
        )
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            return False
        raise
    return not df.empty


def ingest_markdown_tree(
    backend: KgBackend,
    factory: MarkdownTreeFactory,
    *,
    force: bool = False,
) -> MarkdownTreeIngestResult:
    """Ingest a Markdown corpus (via *factory*) into *backend* as a hash-keyed tree.

    Idempotent: unchanged files MERGE in place. A ``MarkdownDocument`` already
    present in the graph (matched by content hash) has its sections/chunks reused
    — they are neither re-parsed into new nodes nor re-embedded — unless
    ``force=True``, which deletes and rebuilds them.

    Args:
        backend: Connected `KgBackend` (already `.connect()`ed).
        factory: `MarkdownTreeFactory` describing the corpus to ingest.
        force: Rebuild sections/chunks for documents already in the graph.

    Returns:
        `MarkdownTreeIngestResult` with counts and any warnings.
    """
    result = MarkdownTreeIngestResult()

    schema = factory.build_schema()
    create_schema(backend, schema.nodes, schema.relations)
    registry = NodeTypeRegistry.from_graph_nodes(schema.nodes)

    nodes = NodeDataCollection()
    relationships: list[RelationshipRecord] = []
    seen_repos: set[str] = set()
    seen_markdown: set[str] = set()

    for key in factory.get_keys():
        try:
            bundle = factory.get_struct_data_by_key(key)
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to parse {key}: {exc}"
            logger.error(msg)
            result.warnings.append(msg)
            result.documents_failed += 1
            continue

        if bundle is None:
            result.documents_failed += 1
            continue

        repo = bundle.repository
        document = bundle.document
        markdown = bundle.markdown_document
        md_hash = markdown.content_hash

        # --- structural nodes (always MERGE; cheap and idempotent) ---------
        if repo.repo_id not in seen_repos:
            seen_repos.add(repo.repo_id)
            repo_dict = repo.model_dump()
            repo_dict["name"] = repo.name
            nodes.add(_REPOSITORY_TYPE, repo_dict)

        doc_dict = document.model_dump()
        doc_dict["name"] = document.filename
        nodes.add(_DOCUMENT_TYPE, doc_dict)

        md_dict = markdown.model_dump()
        md_dict["name"] = markdown.filename
        nodes.add(_MARKDOWN_TYPE, md_dict)

        relationships.append(
            RelationshipRecord(
                _REPOSITORY_TYPE, repo.repo_id, _DOCUMENT_TYPE, document.content_hash, HAS_DOCUMENT.name, {}
            )
        )
        relationships.append(
            RelationshipRecord(_DOCUMENT_TYPE, document.content_hash, _MARKDOWN_TYPE, md_hash, MARKDOWNIZED_AS.name, {})
        )

        # --- section/chunk reuse: skip if already ingested -----------------
        already_ingested = md_hash in seen_markdown or (not force and _markdown_document_exists(backend, md_hash))
        if already_ingested:
            result.documents_skipped += 1
            result.documents_processed += 1
            continue
        seen_markdown.add(md_hash)

        if force:
            _delete_markdown_content(backend, md_hash)

        # Embeddings are computed only for newly ingested Markdown documents.
        factory.compute_embeddings(bundle.chunks)

        for section in bundle.sections:
            section_dict = section.model_dump()
            section_dict["name"] = section.title
            nodes.add(_SECTION_TYPE, section_dict)

            if section.parent_section_id is None:
                relationships.append(
                    RelationshipRecord(_MARKDOWN_TYPE, md_hash, _SECTION_TYPE, section.section_id, HAS_SECTION.name, {})
                )
            else:
                relationships.append(
                    RelationshipRecord(
                        _SECTION_TYPE,
                        section.parent_section_id,
                        _SECTION_TYPE,
                        section.section_id,
                        HAS_SUBSECTION.name,
                        {},
                    )
                )

        for chunk in bundle.chunks:
            chunk_dict = chunk.model_dump()
            chunk_dict["name"] = chunk.chunk_id
            nodes.add(_CHUNK_TYPE, chunk_dict)
            if chunk.section_id is not None:
                relationships.append(
                    RelationshipRecord(_SECTION_TYPE, chunk.section_id, _CHUNK_TYPE, chunk.chunk_id, HAS_CHUNK.name, {})
                )

        for prev_chunk, next_chunk in zip(bundle.chunks, bundle.chunks[1:], strict=False):
            relationships.append(
                RelationshipRecord(
                    _CHUNK_TYPE, prev_chunk.chunk_id, _CHUNK_TYPE, next_chunk.chunk_id, NEXT_CHUNK.name, {}
                )
            )

        result.sections_created += len(bundle.sections)
        result.chunks_created += len(bundle.chunks)
        result.documents_processed += 1

    merge_result = merge_nodes_batch(backend, nodes, registry)
    result.relationships_created = merge_relationships_batch(backend, relationships, registry, merge_result.id_mapping)

    logger.info(
        "Markdown Knowledge Tree ingest: {} processed ({} skipped), {} failed, {} section(s), {} chunk(s), {} rel(s)",
        result.documents_processed,
        result.documents_skipped,
        result.documents_failed,
        result.sections_created,
        result.chunks_created,
        result.relationships_created,
    )
    return result


def _delete_markdown_content(backend: KgBackend, markdown_hash: str) -> None:
    """Delete existing sections + chunks for a Markdown document (used on force)."""
    for label in (_CHUNK_TYPE, _SECTION_TYPE):
        try:
            backend.execute(f"MATCH (n:{label} {{markdown_hash: $h}}) DETACH DELETE n", {"h": markdown_hash})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not clear stale {} for {}: {}", label, markdown_hash, exc)


def drop_markdown_tree(backend: KgBackend, *, drop_documents: bool = False) -> None:
    """Drop the Markdown Knowledge Tree tables (sections, chunks + their relationships).

    Args:
        backend: Connected `KgBackend`.
        drop_documents: Also drop the (shared) Repository/Document/MarkdownDocument
            tables. Leave `False` when those are shared with other factories.
    """
    for rel in (HAS_CHUNK.name, HAS_SUBSECTION.name, HAS_SECTION.name, NEXT_CHUNK.name):
        backend.drop_table(rel)
    backend.drop_table(_CHUNK_TYPE)
    backend.drop_table(_SECTION_TYPE)
    if drop_documents:
        for rel in (MARKDOWNIZED_AS.name, HAS_DOCUMENT.name, CONTAINS_DOC.name):
            backend.drop_table(rel)
        backend.drop_table(_MARKDOWN_TYPE)
        backend.drop_table(_DOCUMENT_TYPE)
        backend.drop_table(_REPOSITORY_TYPE)
