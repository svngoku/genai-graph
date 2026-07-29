"""Prefect flow + workflow-engine step for building a Markdown Knowledge Tree.

Wraps `genai_graph.kg.markdown.ingest.ingest_markdown_tree` so it can be
referenced by dotted path from a genai-tk workflow YAML (`run:` /
`uses:`), exactly like `markdownize_flow` or `kg_create_step`.
"""

from __future__ import annotations

from typing import Any

from genai_tk.workflow.registry import workflow
from loguru import logger
from prefect import flow


@flow(name="markdown_tree")
def markdown_tree_flow(
    sources: list[str],
    db_path: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    recursive: bool = True,
    force: bool = False,
    delete_first: bool = False,
) -> dict[str, Any]:
    """Build (or update) a Markdown Knowledge Tree graph at *db_path*.

    Args:
        sources: Directories, files, or `.zip` archives to ingest.
        db_path: Path to the (shared) Ladybug database file.
        include: Glob patterns to include (default `["*.md"]`).
        exclude: Glob patterns to exclude.
        recursive: Recurse into sub-directories.
        force: Delete stale sections for re-ingested documents before merging
            (handles heading/line-number drift on file edits).
        delete_first: Drop the Section tables before ingesting (full reset of
            the Markdown tree; the shared Document table is preserved).

    Returns:
        Dict with `db_path`, `documents_processed`, `documents_failed`,
        `sections_created`, `relationships_created`, `warnings`.
    """
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
    )

    result = ingest_markdown_tree(backend, factory, force=force)

    return {
        "db_path": db_path,
        "documents_processed": result.documents_processed,
        "documents_failed": result.documents_failed,
        "sections_created": result.sections_created,
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
    force: bool = False,
    delete_first: bool = False,
) -> dict[str, Any]:
    """Workflow-engine wrapper around `markdown_tree_flow` (see its docstring)."""
    return markdown_tree_flow(
        sources=sources,
        db_path=db_path,
        include=include,
        exclude=exclude,
        recursive=recursive,
        force=force,
        delete_first=delete_first,
    )
