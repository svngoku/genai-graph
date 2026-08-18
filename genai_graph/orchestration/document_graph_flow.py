"""Prefect flow + workflow-engine step for building a Document Graph.

Wraps `genai_graph.kg.document_graph.ingest.ingest_document_graph` so it can be
referenced by dotted path from a genai-tk workflow YAML (`run:` /
`uses:`), exactly like `markdownize_flow` or `kg_create_step`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from genai_tk.workflow.registry import workflow
from loguru import logger
from prefect import flow

if TYPE_CHECKING:
    from collections.abc import Callable


@flow(name="document_graph")
def document_graph_flow(
    sources: list[str],
    db_path: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    recursive: bool = True,
    force_stage: str | None = None,
    delete_first: bool = False,
) -> dict[str, Any]:
    """Build (or update) a Document Graph at *db_path*.

    Args:
        sources: Directories, files, or `.zip` archives to ingest.
        db_path: Path to the (shared) Ladybug database file.
        include: Glob patterns to include (default `["*.md"]`).
        exclude: Glob patterns to exclude.
        recursive: Recurse into sub-directories.
        force_stage: One of `graph`/`all` (see `genai_tk.workflow.force`).
            `graph` (and above) rebuilds sections for documents already in the
            graph (handles heading/line-number drift on file edits).
        delete_first: Drop the Section tables before ingesting (full reset of the
            document graph; the shared Document table is preserved). Implies
            `force_stage="graph"` — sections are rebuilt for every document.

    Returns:
        Dict with `db_path`, `documents_processed`, `documents_skipped`,
        `documents_failed`, `sections_created`, `relationships_created`,
        `warnings`.
    """
    from genai_tk.workflow.force import ForceStage, stage_active

    from genai_graph.kg.backend import KuzuBackend
    from genai_graph.kg.document_graph.ingest import drop_document_graph, ingest_document_graph
    from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory

    backend = KuzuBackend()
    backend.connect(db_path)

    if delete_first:
        logger.info("Dropping existing Document Graph tables at {}", db_path)
        drop_document_graph(backend)

    factory = DocumentGraphFactory(
        sources=sources,
        include=include or ["*.md"],
        exclude=exclude or [],
        recursive=recursive,
    )

    # Dropping the Section tables leaves the Document nodes behind, so sections must be
    # rebuilt for them — otherwise the hash-based skip check makes the reset a no-op.
    force = delete_first or stage_active(force_stage, ForceStage.graph)
    result = ingest_document_graph(backend, factory, force=force)

    return {
        "db_path": db_path,
        "documents_processed": result.documents_processed,
        "documents_skipped": result.documents_skipped,
        "documents_failed": result.documents_failed,
        "sections_created": result.sections_created,
        "relationships_created": result.relationships_created,
        "warnings": result.warnings,
    }


@workflow(name="document_graph_build", description="Build a Document Graph from a corpus")
def document_graph_build_step(
    *,
    sources: list[str],
    db_path: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    recursive: bool = True,
    force_stage: str | None = None,
    delete_first: bool = False,
) -> dict[str, Any]:
    """Workflow-engine wrapper around `document_graph_flow` (see its docstring)."""
    return document_graph_flow(
        sources=sources,
        db_path=db_path,
        include=include,
        exclude=exclude,
        recursive=recursive,
        force_stage=force_stage,
        delete_first=delete_first,
    )


def make_source_already_ingested(db_path: str) -> "Callable[[str], bool]":
    """Return a callback usable as ``markdownize_flow(already_processed=...)``.

    The returned callable takes a source file's content hash and reports whether
    a Document derived from it is already in the graph at *db_path* — letting the
    markdownize step skip re-converting files whose output is already stored,
    without genai-tk depending on genai-graph.
    """
    from genai_graph.kg.backend import KuzuBackend

    backend = KuzuBackend()
    backend.connect(db_path)

    def _already(source_hash: str) -> bool:
        try:
            df = backend.execute_get_as_df(
                "MATCH (d:Document {content_hash: $h}) RETURN d.content_hash AS h LIMIT 1",
                {"h": source_hash},
                union=False,
            )
        except Exception as exc:  # noqa: BLE001
            if "does not exist" in str(exc):
                return False
            raise
        return not df.empty

    return _already
