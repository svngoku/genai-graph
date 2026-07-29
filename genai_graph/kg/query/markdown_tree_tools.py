"""Navigation tools for the Markdown Knowledge Tree.

Exposes read-only Cypher-backed functions an agent can call to walk a
Document's heading hierarchy and fetch only the sections it needs — no
embeddings or vector search. Mirrors the pattern used by
`genai_graph.kg.query.agent.create_kg_cypher_tool`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from loguru import logger

from genai_graph.kg.backend import KgBackend, KuzuBackend
from genai_graph.kg.nodes.document import DocumentNode
from genai_graph.kg.nodes.markdown_tree import SectionNode

_DOCUMENT_LABEL = DocumentNode.node_class.__name__
_SECTION_LABEL = SectionNode.node_class.__name__


def _query_rows(backend: KgBackend, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a Cypher query and return rows as dicts, or `[]` if the table doesn't exist yet.

    A fresh (never-ingested) or just-dropped database legitimately has no
    Document/MarkdownSection tables — treat that as "no results" rather than
    an error.
    """
    try:
        df = backend.execute_get_as_df(query, parameters, union=False)
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            logger.debug("Markdown Knowledge Tree table not found (not yet ingested?): {}", exc)
            return []
        raise
    return df.to_dict(orient="records")


def list_documents(backend: KgBackend) -> list[dict[str, Any]]:
    """List every ingested document with its section count.

    Args:
        backend: Connected `KgBackend`.

    Returns:
        List of `{path, filename, section_count}` dicts, ordered by filename.
    """
    rows = _query_rows(
        backend, f"MATCH (d:{_DOCUMENT_LABEL}) RETURN d.path AS path, d.filename AS filename ORDER BY d.filename"
    )
    count_rows = _query_rows(
        backend, f"MATCH (s:{_SECTION_LABEL}) RETURN s.document_path AS document_path, count(s) AS section_count"
    )
    counts_by_path = {r["document_path"]: r["section_count"] for r in count_rows}
    for row in rows:
        row["section_count"] = int(counts_by_path.get(row["path"], 0))
    return rows


def get_document_toc(backend: KgBackend, document_path: str) -> list[dict[str, Any]]:
    """Return the table of contents (heading tree) for one document.

    No section body text is returned — this is the map an agent uses to
    decide which sections to fetch with `get_section_content`.

    Args:
        backend: Connected `KgBackend`.
        document_path: `Document.path` primary key.

    Returns:
        List of `{section_id, parent_section_id, title, level, line_start, sequence}`
        dicts, ordered by document position.
    """
    query = f"""
        MATCH (s:{_SECTION_LABEL} {{document_path: $document_path}})
        RETURN s.section_id AS section_id, s.parent_section_id AS parent_section_id,
               s.title AS title, s.level AS level, s.line_start AS line_start, s.sequence AS sequence
        ORDER BY s.sequence
    """
    return _query_rows(backend, query, {"document_path": document_path})


def get_section_content(backend: KgBackend, section_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch the raw Markdown text of one or more sections.

    Args:
        backend: Connected `KgBackend`.
        section_ids: `MarkdownSection.section_id` values to fetch.

    Returns:
        List of `{section_id, document_path, title, line_start, line_end, text}` dicts.
    """
    query = f"""
        MATCH (s:{_SECTION_LABEL})
        WHERE s.section_id IN $section_ids
        RETURN s.section_id AS section_id, s.document_path AS document_path, s.title AS title,
               s.line_start AS line_start, s.line_end AS line_end, s.text AS text
        ORDER BY s.document_path, s.line_start
    """
    return _query_rows(backend, query, {"section_ids": section_ids})


def search_sections(backend: KgBackend, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    """Cross-document keyword search over section titles and body text.

    Pure string matching — no embeddings involved.

    Args:
        backend: Connected `KgBackend`.
        keyword: Substring to search for (case-sensitive Cypher `CONTAINS`).
        limit: Maximum number of matches to return.

    Returns:
        List of `{document_path, section_id, title, level, line_start}` dicts.
    """
    query = f"""
        MATCH (s:{_SECTION_LABEL})
        WHERE s.title CONTAINS $keyword OR s.text CONTAINS $keyword
        RETURN s.document_path AS document_path, s.section_id AS section_id, s.title AS title,
               s.level AS level, s.line_start AS line_start
        ORDER BY s.document_path, s.line_start
        LIMIT $limit
    """
    return _query_rows(backend, query, {"keyword": keyword, "limit": limit})


def _connect(db_path: str) -> KgBackend:
    backend = KuzuBackend()
    backend.connect(db_path)
    return backend


def create_markdown_tree_tools(db_path: str) -> list[BaseTool]:
    """Build the LangChain tools an agent uses to navigate a Markdown Knowledge Tree.

    Args:
        db_path: Path to the Ladybug database holding the ingested tree.

    Returns:
        `[list_documents, get_document_toc, get_section_content, search_sections]` tools.
    """

    @tool("list_markdown_documents")
    def _list_documents() -> str:
        """List every ingested document with its section count."""
        try:
            rows = list_documents(_connect(db_path))
        except Exception as exc:  # noqa: BLE001
            return f"Error listing documents: {exc}"
        if not rows:
            return "No documents ingested yet."
        return "\n".join(f"- {r['filename']} ({r['section_count']} sections) — path: {r['path']}" for r in rows)

    @tool("get_markdown_document_toc")
    def _get_document_toc(document_path: str) -> str:
        """Get the table of contents (heading tree) for one document, given its path."""
        try:
            rows = get_document_toc(_connect(db_path), document_path)
        except Exception as exc:  # noqa: BLE001
            return f"Error fetching TOC: {exc}"
        if not rows:
            return f"No sections found for document: {document_path}"
        lines = []
        for r in rows:
            indent = "  " * max(int(r["level"]) - 1, 0)
            lines.append(f"{indent}- [{r['section_id']}] {r['title']} (line {r['line_start']})")
        return "\n".join(lines)

    @tool("get_markdown_section_content")
    def _get_section_content(section_ids: str) -> str:
        """Fetch the raw Markdown text of one or more sections. Comma-separated section_ids."""
        ids = [s.strip() for s in section_ids.split(",") if s.strip()]
        try:
            rows = get_section_content(_connect(db_path), ids)
        except Exception as exc:  # noqa: BLE001
            return f"Error fetching section content: {exc}"
        if not rows:
            return f"No sections found for ids: {section_ids}"
        return "\n\n---\n\n".join(f"### [{r['section_id']}] {r['title']}\n\n{r['text']}" for r in rows)

    @tool("search_markdown_sections")
    def _search_sections(keyword: str, limit: int = 20) -> str:
        """Cross-document keyword search over section titles and text (no embeddings)."""
        try:
            rows = search_sections(_connect(db_path), keyword, limit)
        except Exception as exc:  # noqa: BLE001
            return f"Error searching sections: {exc}"
        if not rows:
            return f"No sections matched keyword: {keyword!r}"
        return "\n".join(
            f"- [{r['section_id']}] {r['title']} (line {r['line_start']}) — doc: {r['document_path']}" for r in rows
        )

    return [_list_documents, _get_document_toc, _get_section_content, _search_sections]
