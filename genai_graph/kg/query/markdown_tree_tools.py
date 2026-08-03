"""Navigation tools for the Markdown Knowledge Tree.

Exposes read-only Cypher-backed functions an agent (or the `cli tree` command)
can call to walk a document's heading hierarchy, fetch individual sections, and
reconstruct a whole document from its sections. Documents are addressed by the
source Document content hash, the MarkdownDocument content hash (full or prefix),
or the filename.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from loguru import logger

from genai_graph.kg.backend import KgBackend, KuzuBackend
from genai_graph.kg.nodes.document import MarkdownDocumentNode
from genai_graph.kg.nodes.markdown_tree import SectionNode

_MARKDOWN_LABEL = MarkdownDocumentNode.node_class.__name__
_SECTION_LABEL = SectionNode.node_class.__name__


def _query_rows(
    backend: KgBackend, query: str, parameters: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], str]:
    """Run a Cypher query and return (rows, query_string).

    A fresh (never-ingested) or just-dropped database legitimately has no
    MarkdownDocument/MarkdownSection tables — treat that as "no results".
    """
    try:
        df = backend.execute_get_as_df(query, parameters, union=False)
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            logger.debug("Markdown Knowledge Tree table not found (not yet ingested?): {}", exc)
            return [], query
        raise
    # pandas renders SQL/Cypher NULLs (e.g. a root section's parent_section_id) as
    # float NaN rather than None — normalize so callers can compare with `is None`.
    return df.astype(object).where(df.notna(), None).to_dict(orient="records"), query


def _resolve_markdown_hash(backend: KgBackend, document_id: str) -> str | None:
    """Resolve a document reference to a MarkdownDocument.content_hash.

    Accepts a MarkdownDocument hash (full or prefix), a filename, a source
    Document hash (full or prefix), or a source Document path.
    """
    query = (
        f"MATCH (m:{_MARKDOWN_LABEL}) "
        "WHERE m.content_hash = $id OR m.content_hash STARTS WITH $id OR m.filename = $id "
        "RETURN m.content_hash AS h LIMIT 1"
    )
    rows, _ = _query_rows(backend, query, {"id": document_id})
    if rows:
        return rows[0]["h"]

    query = (
        f"MATCH (d:Document)-[:MARKDOWNIZED_AS]->(m:{_MARKDOWN_LABEL}) "
        "WHERE d.content_hash = $id OR d.content_hash STARTS WITH $id OR d.filename = $id OR d.path = $id "
        "RETURN m.content_hash AS h LIMIT 1"
    )
    rows, _ = _query_rows(backend, query, {"id": document_id})
    if rows:
        return rows[0]["h"]

    return None


def list_documents(backend: KgBackend) -> list[dict[str, Any]]:
    """List every ingested Markdown document with its section count and hashes.

    Returns:
        List of `{markdown_hash, source_hash, filename, section_count, path}` dicts.
    """
    rows, _ = _query_rows(
        backend,
        f"MATCH (m:{_MARKDOWN_LABEL}) "
        "OPTIONAL MATCH (d:Document {content_hash: m.source_hash}) "
        "RETURN m.content_hash AS markdown_hash, m.source_hash AS source_hash, m.filename AS filename, "
        "m.section_count AS section_count, d.path AS path ORDER BY m.filename",
    )
    for row in rows:
        row["section_count"] = int(row.get("section_count") or 0)
    return rows


def get_document_toc(
    backend: KgBackend, document_id: str, return_query: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], str]:
    """Return the table of contents (heading tree) for one document.

    No section body text is returned — this is the map an agent uses to decide
    which sections to fetch with `get_section_content`.
    """
    markdown_hash = _resolve_markdown_hash(backend, document_id)
    if not markdown_hash:
        result: list[dict[str, Any]] = []
        query = f"-- No document found matching: {document_id}"
        return (result, query) if return_query else result

    query = f"""
        MATCH (s:{_SECTION_LABEL} {{markdown_hash: $markdown_hash}})
        RETURN s.section_id AS section_id, s.parent_section_id AS parent_section_id,
               s.title AS title, s.level AS level, s.line_start AS line_start, s.sequence AS sequence
        ORDER BY s.sequence
    """
    rows, _ = _query_rows(backend, query, {"markdown_hash": markdown_hash})
    return (rows, query) if return_query else rows


def get_section_content(
    backend: KgBackend, section_ids: list[str], return_query: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], str]:
    """Fetch the raw Markdown text of one or more sections.

    Each entry in *section_ids* may be a full ``section_id`` (``{markdown_hash}::{sequence}``)
    or a prefix of one — e.g. a bare (or truncated) document hash matches every section of
    that document, so `cli tree list`'s short hash column can be used directly.
    """
    query = f"""
        UNWIND $section_ids AS sid
        MATCH (s:{_SECTION_LABEL})
        WHERE s.section_id = sid OR s.section_id STARTS WITH sid
        RETURN DISTINCT s.section_id AS section_id, s.markdown_hash AS markdown_hash, s.title AS title,
               s.line_start AS line_start, s.line_end AS line_end, s.sequence AS sequence, s.text AS text
        ORDER BY markdown_hash, sequence
    """
    rows, _ = _query_rows(backend, query, {"section_ids": section_ids})
    return (rows, query) if return_query else rows


def reconstruct_document(
    backend: KgBackend, document_id: str, return_query: bool = False
) -> str | None | tuple[str | None, str]:
    """Rebuild a document's full Markdown text by concatenating its sections.

    Sections partition the document's lines without overlap, so concatenating
    their ``text`` in ``sequence`` order reproduces the original document.
    """
    query = f"MATCH (s:{_SECTION_LABEL} {{markdown_hash: $markdown_hash}}) RETURN s.text AS text ORDER BY s.sequence"
    markdown_hash = _resolve_markdown_hash(backend, document_id)
    if not markdown_hash:
        return (None, query) if return_query else None
    rows, _ = _query_rows(backend, query, {"markdown_hash": markdown_hash})
    text = "\n".join(r["text"] for r in rows)
    return (text, query) if return_query else text


def search_sections(
    backend: KgBackend, keyword: str, limit: int = 20, return_query: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], str]:
    """Cross-document keyword search over section titles and body text (no embeddings)."""
    query = f"""
        MATCH (s:{_SECTION_LABEL})
        WHERE s.title CONTAINS $keyword OR s.text CONTAINS $keyword
        RETURN s.markdown_hash AS markdown_hash, s.section_id AS section_id, s.title AS title,
               s.level AS level, s.line_start AS line_start
        ORDER BY s.markdown_hash, s.line_start
        LIMIT $limit
    """
    rows, _ = _query_rows(backend, query, {"keyword": keyword, "limit": limit})
    return (rows, query) if return_query else rows


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
        """List every ingested Markdown document with its section count."""
        try:
            rows = list_documents(_connect(db_path))
        except Exception as exc:  # noqa: BLE001
            return f"Error listing documents: {exc}"
        if not rows:
            return "No documents ingested yet."
        return "\n".join(
            f"- {r['filename']} ({r['section_count']} sections) — md_hash: {r['markdown_hash']}" for r in rows
        )

    @tool("get_markdown_document_toc")
    def _get_document_toc(document_id: str) -> str:
        """Get the table of contents (heading tree) for one document (hash or filename)."""
        try:
            rows = get_document_toc(_connect(db_path), document_id)
        except Exception as exc:  # noqa: BLE001
            return f"Error fetching TOC: {exc}"
        if not rows:
            return f"No sections found for document: {document_id}"
        lines = []
        for r in rows:  # type: ignore[union-attr]
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
        return "\n\n---\n\n".join(f"### [{r['section_id']}] {r['title']}\n\n{r['text']}" for r in rows)  # type: ignore[union-attr]

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
            f"- [{r['section_id']}] {r['title']} (line {r['line_start']}) — md_hash: {r['markdown_hash']}"
            for r in rows  # type: ignore[union-attr]
        )

    return [_list_documents, _get_document_toc, _get_section_content, _search_sections]

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
