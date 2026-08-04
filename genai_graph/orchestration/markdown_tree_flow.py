"""Prefect flow + workflow-engine step for building a Markdown Knowledge Tree.

Wraps `genai_graph.kg.markdown.ingest.ingest_markdown_tree` so it can be
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


@flow(name="markdown_tree")
def markdown_tree_flow(
    sources: list[str],
    db_path: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    recursive: bool = True,
    force_stage: str | None = None,
    delete_first: bool = False,
    embed_chunks: bool = False,
    embeddings_model: str | None = None,
) -> dict[str, Any]:
    """Build (or update) a Markdown Knowledge Tree graph at *db_path*.

    Args:
        sources: Directories, files, or `.zip` archives to ingest.
        db_path: Path to the (shared) Ladybug database file.
        include: Glob patterns to include (default `["*.md"]`).
        exclude: Glob patterns to exclude.
        recursive: Recurse into sub-directories.
        force_stage: One of `graph`/`embed`/`all` (see `genai_tk.workflow.force`).
            `graph` (and above) rebuilds sections/chunks for documents already in
            the graph (handles heading/line-number drift on file edits).
        delete_first: Drop the Section/Chunk tables before ingesting (full reset
            of the Markdown tree; the shared Document table is preserved).
        embed_chunks: Compute embeddings for newly-ingested chunks.
        embeddings_model: Embeddings model id (uses config default when omitted).

    Returns:
        Dict with `db_path`, `documents_processed`, `documents_skipped`,
        `documents_failed`, `sections_created`, `chunks_created`,
        `relationships_created`, `warnings`.
    """
    from genai_tk.workflow.force import ForceStage, stage_active

    from genai_graph.kg.backend import KuzuBackend
    from genai_graph.kg.factories.markdown_tree_factory import MarkdownTreeFactory
    from genai_graph.kg.markdown.ingest import drop_markdown_tree, ingest_markdown_tree

    backend = KuzuBackend()
    backend.connect(db_path)

    if delete_first:
        logger.info("Dropping existing Markdown Knowledge Tree tables at {}", db_path)
        drop_markdown_tree(backend)

    factory = MarkdownTreeFactory(
        sources=sources,
        include=include or ["*.md"],
        exclude=exclude or [],
        recursive=recursive,
        embed_chunks=embed_chunks,
        embeddings_model=embeddings_model,
    )

    result = ingest_markdown_tree(backend, factory, force=stage_active(force_stage, ForceStage.graph))

    return {
        "db_path": db_path,
        "documents_processed": result.documents_processed,
        "documents_skipped": result.documents_skipped,
        "documents_failed": result.documents_failed,
        "sections_created": result.sections_created,
        "chunks_created": result.chunks_created,
        "relationships_created": result.relationships_created,
        "warnings": result.warnings,
    }


@workflow(name="markdown_tree_build", description="Build a Markdown Knowledge Tree graph from a corpus")
def markdown_tree_build_step(
    *,
    sources: list[str],
    db_path: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    recursive: bool = True,
    force_stage: str | None = None,
    delete_first: bool = False,
    embed_chunks: bool = False,
    embeddings_model: str | None = None,
) -> dict[str, Any]:
    """Workflow-engine wrapper around `markdown_tree_flow` (see its docstring)."""
    return markdown_tree_flow(
        sources=sources,
        db_path=db_path,
        include=include,
        exclude=exclude,
        recursive=recursive,
        force_stage=force_stage,
        delete_first=delete_first,
        embed_chunks=embed_chunks,
        embeddings_model=embeddings_model,
    )


def make_source_already_ingested(db_path: str) -> "Callable[[str], bool]":
    """Return a callback usable as ``markdownize_flow(already_processed=...)``.

    The returned callable takes a source file's content hash and reports whether
    a `MarkdownDocument` derived from it is already in the graph at *db_path* —
    letting the markdownize step skip re-converting files whose output is
    already stored, without genai-tk depending on genai-graph.
    """
    from genai_graph.kg.backend import KuzuBackend

    backend = KuzuBackend()
    backend.connect(db_path)

    def _already(source_hash: str) -> bool:
        try:
            df = backend.execute_get_as_df(
                "MATCH (m:MarkdownDocument {source_hash: $h}) RETURN m.content_hash AS h LIMIT 1",
                {"h": source_hash},
                union=False,
            )
        except Exception as exc:  # noqa: BLE001
            if "does not exist" in str(exc):
                return False
            raise
        return not df.empty

    return _already
