"""Direct ingestion of a Document Graph into a graph backend.

Builds a ``Folder → Document → MarkdownSection`` tree. The section hierarchy is
self-referential (`MarkdownSection -> MarkdownSection`), which doesn't map onto
the generic Pydantic-nesting extraction used elsewhere in genai-graph
(`extract_graph_data`). This module instead builds `NodeDataCollection` /
`RelationshipRecord` objects directly from a `DocumentGraphFactory` and merges
them with the same Arrow/Ladybug primitives (`merge_nodes_batch`,
`merge_relationships_batch`) used by the rest of the ingestion pipeline.

Documents are keyed by content hash, so re-ingesting an unchanged corpus is a
MERGE no-op. Before parsing sections for a document, the DB is checked for an
existing Document with the same ``markdown_hash`` — when present, section
creation is skipped entirely, avoiding costly recomputation.
"""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.backend import KgBackend
from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory
from genai_graph.kg.ingest.extract import RelationshipRecord, create_schema
from genai_graph.kg.ingest.merge import (
    NodeDataCollection,
    NodeTypeRegistry,
    merge_nodes_batch,
    merge_relationships_batch,
)
from genai_graph.kg.nodes.document import (
    CONTAINS_DOC,
    HAS_SUBFOLDER,
    DocumentNode,
    FolderNode,
)
from genai_graph.kg.nodes.document_section import HAS_SECTION, HAS_SUBSECTION, SectionNode

_FOLDER_TYPE = FolderNode.node_class.__name__
_DOCUMENT_TYPE = DocumentNode.node_class.__name__
_SECTION_TYPE = SectionNode.node_class.__name__


class DocumentGraphIngestResult(BaseModel):
    """Outcome of a Document Graph ingestion run."""

    documents_processed: int = 0
    documents_failed: int = 0
    documents_skipped: int = 0
    sections_created: int = 0
    sections_summarized: int = 0
    relationships_created: int = 0
    warnings: list[str] = Field(default_factory=list)


def _document_exists(backend: KgBackend, markdown_hash: str) -> bool:
    """Return True if a Document with this markdown_hash is already in the graph."""
    try:
        df = backend.execute_get_as_df(
            f"MATCH (d:{_DOCUMENT_TYPE} {{markdown_hash: $h}}) RETURN d.markdown_hash AS h LIMIT 1",
            {"h": markdown_hash},
            union=False,
        )
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            return False
        raise
    return not df.empty


def _sections_described(backend: KgBackend, markdown_hash: str) -> bool:
    """Return True if any section of this document already carries a description.

    Used by the LLM build path to decide whether an already-ingested document's
    sections were built algorithmically (no descriptions) and so should be rebuilt
    to pick up the LLM outline's descriptions/summaries.
    """
    try:
        df = backend.execute_get_as_df(
            f"MATCH (s:{_SECTION_TYPE} {{markdown_hash: $h}}) WHERE s.description IS NOT NULL "
            "RETURN count(s) AS c",
            {"h": markdown_hash},
            union=False,
        )
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            return False
        raise
    if df.empty:
        return False
    return int(df.iloc[0]["c"]) > 0


def ingest_document_graph(
    backend: KgBackend,
    factory: DocumentGraphFactory,
    *,
    force: bool = False,
) -> DocumentGraphIngestResult:
    """Ingest a Markdown corpus (via *factory*) into *backend* as a hash-keyed tree.

    Idempotent: unchanged files MERGE in place. A Document already present in the
    graph (matched by ``markdown_hash``) has its sections reused — they are not
    re-parsed into new nodes — unless ``force=True``, which deletes and rebuilds
    them.

    Args:
        backend: Connected `KgBackend` (already `.connect()`ed).
        factory: `DocumentGraphFactory` describing the corpus to ingest.
        force: Rebuild sections for documents already in the graph.

    Returns:
        `DocumentGraphIngestResult` with counts and any warnings.
    """
    result = DocumentGraphIngestResult()

    schema = factory.build_schema()
    create_schema(backend, schema.nodes, schema.relations)
    registry = NodeTypeRegistry.from_graph_nodes(schema.nodes)

    nodes = NodeDataCollection()
    relationships: list[RelationshipRecord] = []
    seen_folders: set[str] = set()
    seen_folder_edges: set[tuple[str, str]] = set()
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

        document = bundle.document
        md_hash = document.markdown_hash or ""

        # --- structural nodes (always MERGE; cheap and idempotent) ---------
        for folder in bundle.folders:
            if folder.folder_id not in seen_folders:
                seen_folders.add(folder.folder_id)
                folder_dict = folder.model_dump()
                folder_dict["name"] = folder.name
                nodes.add(_FOLDER_TYPE, folder_dict)

        for parent_folder, child_folder in zip(bundle.folders, bundle.folders[1:], strict=False):
            edge = (parent_folder.folder_id, child_folder.folder_id)
            if edge not in seen_folder_edges:
                seen_folder_edges.add(edge)
                relationships.append(
                    RelationshipRecord(_FOLDER_TYPE, edge[0], _FOLDER_TYPE, edge[1], HAS_SUBFOLDER.name, {})
                )

        doc_dict = document.model_dump()
        doc_dict["name"] = document.filename
        nodes.add(_DOCUMENT_TYPE, doc_dict)

        leaf_folder = bundle.folders[-1]
        relationships.append(
            RelationshipRecord(
                _FOLDER_TYPE, leaf_folder.folder_id, _DOCUMENT_TYPE, document.content_hash, CONTAINS_DOC.name, {}
            )
        )

        # --- section reuse: skip if already ingested -----------------------
        # In-batch dedup first: the same markdown already queued for merge this run.
        if md_hash in seen_markdown:
            result.documents_skipped += 1
            result.documents_processed += 1
            continue

        in_db = _document_exists(backend, md_hash)
        if force:
            rebuild = True
        elif factory.outline_config is not None:
            # LLM build path: rebuild sections that were built without descriptions
            # (algorithmically, or by a prior degraded run) so a re-run after `--llm`
            # enriches them with the outline's descriptions/summaries.
            rebuild = in_db and not _sections_described(backend, md_hash)
        else:
            rebuild = False

        if in_db and not rebuild:
            result.documents_skipped += 1
            result.documents_processed += 1
            continue
        seen_markdown.add(md_hash)

        if rebuild:
            _delete_document_sections(backend, md_hash)

        for section in bundle.sections:
            section_dict = section.model_dump()
            section_dict["name"] = section.title
            nodes.add(_SECTION_TYPE, section_dict)

            if section.parent_section_id is None:
                relationships.append(
                    RelationshipRecord(
                        _DOCUMENT_TYPE, document.content_hash, _SECTION_TYPE, section.section_id, HAS_SECTION.name, {}
                    )
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

        result.sections_created += len(bundle.sections)
        result.sections_summarized += sum(1 for s in bundle.sections if s.summary)
        result.documents_processed += 1

    merge_result = merge_nodes_batch(backend, nodes, registry)
    result.relationships_created = merge_relationships_batch(backend, relationships, registry, merge_result.id_mapping)

    logger.info(
        "Document Graph ingest: {} processed ({} skipped), {} failed, {} section(s), {} rel(s)",
        result.documents_processed,
        result.documents_skipped,
        result.documents_failed,
        result.sections_created,
        result.relationships_created,
    )
    return result


def _delete_document_sections(backend: KgBackend, markdown_hash: str) -> None:
    """Delete existing sections for a document (used on force)."""
    try:
        backend.execute(f"MATCH (n:{_SECTION_TYPE} {{markdown_hash: $h}}) DETACH DELETE n", {"h": markdown_hash})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not clear stale {} for {}: {}", _SECTION_TYPE, markdown_hash, exc)


def drop_document_graph(backend: KgBackend, *, drop_documents: bool = False) -> None:
    """Drop the Document Graph structure tables (sections + their relationships).

    By default only drops the Section table and its relationships, leaving
    Folder/Document metadata intact (since they may be shared with other
    factories). This means `list_documents()` will still return entries. Pass
    `drop_documents=True` for a full reset.

    Args:
        backend: Connected `KgBackend`.
        drop_documents: Also drop Folder/Document tables. Leave `False` when those
            are shared with other factories; set `True` for a complete reset.
    """
    for rel in (HAS_SUBSECTION.name, HAS_SECTION.name):
        backend.drop_table(rel)
    backend.drop_table(_SECTION_TYPE)
    if drop_documents:
        backend.drop_table(CONTAINS_DOC.name)
        backend.drop_table(_DOCUMENT_TYPE)
        backend.drop_table(HAS_SUBFOLDER.name)
        backend.drop_table(_FOLDER_TYPE)
