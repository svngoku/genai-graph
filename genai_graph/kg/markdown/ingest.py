"""Direct ingestion of a Markdown Knowledge Tree into a graph backend.

The section hierarchy is self-referential (`MarkdownSection -> MarkdownSection`),
which doesn't map onto the generic Pydantic-nesting extraction used elsewhere in
genai-graph (`extract_graph_data`). This module instead builds
`NodeDataCollection` / `RelationshipRecord` objects directly from a
`MarkdownTreeFactory` and merges them with the same Arrow/Ladybug primitives
(`merge_nodes_batch`, `merge_relationships_batch`) used by the rest of the
ingestion pipeline.
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
from genai_graph.kg.nodes.document import DocumentNode
from genai_graph.kg.nodes.markdown_tree import HAS_SECTION, HAS_SUBSECTION, SectionNode


class MarkdownTreeIngestResult(BaseModel):
    """Outcome of a Markdown Knowledge Tree ingestion run."""

    documents_processed: int = 0
    documents_failed: int = 0
    sections_created: int = 0
    relationships_created: int = 0
    warnings: list[str] = Field(default_factory=list)


def ingest_markdown_tree(
    backend: KgBackend,
    factory: MarkdownTreeFactory,
    *,
    force: bool = False,
) -> MarkdownTreeIngestResult:
    """Ingest a Markdown corpus (via *factory*) into *backend* as a Document+Section tree.

    Idempotent: re-running with unchanged files updates existing nodes in place
    (MERGE semantics). When ``force=True``, existing sections for each
    re-ingested document are deleted first — needed because a ``section_id``
    embeds the heading's line number, so edited headings produce new IDs and
    would otherwise leave stale orphan sections behind.

    Args:
        backend: Connected `KgBackend` (e.g. `KuzuBackend`, already `.connect()`ed).
        factory: `MarkdownTreeFactory` describing the corpus to ingest.
        force: Delete existing sections for re-ingested documents before merging.

    Returns:
        `MarkdownTreeIngestResult` with counts and any warnings.
    """
    result = MarkdownTreeIngestResult()

    schema = factory.build_schema()
    create_schema(backend, schema.nodes, schema.relations)

    node_type_registry = NodeTypeRegistry.from_graph_nodes(schema.nodes)

    document_type = DocumentNode.node_class.__name__
    section_type = SectionNode.node_class.__name__

    nodes = NodeDataCollection()
    relationships: list[RelationshipRecord] = []

    for key in factory.get_keys():
        try:
            tree = factory.get_struct_data_by_key(key)
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to parse {key}: {exc}"
            logger.error(msg)
            result.warnings.append(msg)
            result.documents_failed += 1
            continue

        if tree is None:
            result.documents_failed += 1
            continue

        document_path = tree.document.path

        if force:
            try:
                backend.execute(
                    f"MATCH (s:{section_type} {{document_path: $path}}) DETACH DELETE s",
                    {"path": document_path},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not clear stale sections for {}: {}", document_path, exc)

        doc_dict = tree.document.model_dump()
        doc_dict["name"] = tree.document.filename
        nodes.add(document_type, doc_dict)

        for section in tree.sections:
            section_dict = section.model_dump()
            section_dict["name"] = section.title
            nodes.add(section_type, section_dict)

            if section.parent_section_id is None:
                relationships.append(
                    RelationshipRecord(
                        from_type=document_type,
                        from_id=document_path,
                        to_type=section_type,
                        to_id=section.section_id,
                        name=HAS_SECTION.name,
                        properties={},
                    )
                )
            else:
                relationships.append(
                    RelationshipRecord(
                        from_type=section_type,
                        from_id=section.parent_section_id,
                        to_type=section_type,
                        to_id=section.section_id,
                        name=HAS_SUBSECTION.name,
                        properties={},
                    )
                )

        result.sections_created += len(tree.sections)
        result.documents_processed += 1

    merge_result = merge_nodes_batch(backend, nodes, node_type_registry)
    result.relationships_created = merge_relationships_batch(
        backend, relationships, node_type_registry, merge_result.id_mapping
    )

    logger.info(
        "Markdown Knowledge Tree ingest: {} document(s) processed, {} failed, {} section(s), {} relationship(s)",
        result.documents_processed,
        result.documents_failed,
        result.sections_created,
        result.relationships_created,
    )
    return result


def drop_markdown_tree(backend: KgBackend, *, drop_documents: bool = False) -> None:
    """Drop the Markdown Knowledge Tree tables (sections + their relationships).

    Args:
        backend: Connected `KgBackend`.
        drop_documents: Also drop the (shared) Document table. Leave `False`
            when the Document table is shared with other factories in the same DB.
    """
    backend.drop_table(HAS_SUBSECTION.name)
    backend.drop_table(HAS_SECTION.name)
    backend.drop_table(SectionNode.node_class.__name__)
    if drop_documents:
        backend.drop_table(DocumentNode.node_class.__name__)
