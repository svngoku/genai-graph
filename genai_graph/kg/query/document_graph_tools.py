"""Navigation tools for the Document Graph.

Exposes read-only Cypher-backed functions an agent (or the `cli docgraph`
command) can call to walk a document's heading hierarchy, fetch individual
sections, and reconstruct a whole document from its sections. Documents are
addressed by the Document content hash (full or prefix), its ``markdown_hash``,
the filename, or the source path.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from loguru import logger

from genai_graph.kg.backend import KgBackend, KuzuBackend
from genai_graph.kg.nodes.document import DocumentNode, FolderNode
from genai_graph.kg.nodes.document_section import SectionNode

_DOCUMENT_LABEL = DocumentNode.node_class.__name__
_SECTION_LABEL = SectionNode.node_class.__name__
_FOLDER_LABEL = FolderNode.node_class.__name__


def _query_rows(
    backend: KgBackend, query: str, parameters: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], str]:
    """Run a Cypher query and return (rows, query_string).

    A fresh (never-ingested) or just-dropped database legitimately has no
    Document/MarkdownSection tables — treat that as "no results".
    """
    try:
        df = backend.execute_get_as_df(query, parameters, union=False)
    except Exception as exc:  # noqa: BLE001
        if "does not exist" in str(exc):
            logger.debug("Document Graph table not found (not yet ingested?): {}", exc)
            return [], query
        raise
    # pandas renders SQL/Cypher NULLs (e.g. a root section's parent_section_id) as
    # float NaN rather than None — normalize so callers can compare with `is None`.
    return df.astype(object).where(df.notna(), None).to_dict(orient="records"), query


def _resolve_markdown_hash(backend: KgBackend, document_id: str) -> str | None:
    """Resolve a document reference to a Document.markdown_hash.

    Accepts a Document content hash (full or prefix), a ``markdown_hash``, a
    filename, or a source Document path.
    """
    query = (
        f"MATCH (d:{_DOCUMENT_LABEL}) "
        "WHERE d.content_hash = $id OR d.content_hash STARTS WITH $id OR d.markdown_hash = $id "
        "OR d.markdown_hash STARTS WITH $id OR d.filename = $id OR d.path = $id "
        "RETURN d.markdown_hash AS h LIMIT 1"
    )
    rows, _ = _query_rows(backend, query, {"id": document_id})
    if rows:
        return rows[0]["h"]
    return None


def resolve_folder_id(backend: KgBackend, folder_ref: str) -> str | None:
    """Resolve a folder reference (hash, hash prefix, or name) to a Folder.folder_id."""
    query = (
        f"MATCH (f:{_FOLDER_LABEL}) "
        "WHERE f.folder_id = $id OR f.folder_id STARTS WITH $id OR f.name = $id "
        "RETURN f.folder_id AS id LIMIT 1"
    )
    rows, _ = _query_rows(backend, query, {"id": folder_ref})
    if rows:
        return rows[0]["id"]
    return None


def get_folder_path(backend: KgBackend, folder_id: str) -> list[dict[str, Any]]:
    """Return the ancestor chain (root-first, ``folder_id``-last) for a folder, for breadcrumb display."""
    chain: list[dict[str, Any]] = []
    current: str | None = folder_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        rows, _ = _query_rows(
            backend,
            f"MATCH (f:{_FOLDER_LABEL} {{folder_id: $id}}) "
            "RETURN f.folder_id AS folder_id, f.parent_folder_id AS parent_folder_id, f.name AS name, "
            "f.kind AS kind, f.uri AS uri",
            {"id": current},
        )
        if not rows:
            break
        chain.append(rows[0])
        current = rows[0]["parent_folder_id"]
    chain.reverse()
    return chain


def get_folder_tree(backend: KgBackend, root_folder_id: str | None = None) -> list[dict[str, Any]]:
    """Return the folder hierarchy (subfolders + direct document counts) rooted at *root_folder_id*.

    Each row is `{folder_id, parent_folder_id, name, kind, doc_count}`. When
    `root_folder_id` is None, returns every top-level source folder (those with
    no parent) plus their full descendant subtree.
    """
    if root_folder_id is None:
        root_rows, _ = _query_rows(
            backend, f"MATCH (r:{_FOLDER_LABEL}) WHERE r.parent_folder_id IS NULL RETURN r.folder_id AS folder_id"
        )
        root_ids = [r["folder_id"] for r in root_rows]
    else:
        root_ids = [root_folder_id]

    by_folder_id: dict[str, dict[str, Any]] = {}
    for root_id in root_ids:
        query = f"""
            MATCH (root:{_FOLDER_LABEL} {{folder_id: $root_id}})-[:HAS_SUBFOLDER*0..30]->(f:{_FOLDER_LABEL})
            OPTIONAL MATCH (f)-[:CONTAINS]->(d:{_DOCUMENT_LABEL})
            RETURN f.folder_id AS folder_id, f.parent_folder_id AS parent_folder_id, f.name AS name,
                   f.kind AS kind, count(d) AS doc_count
        """
        rows, _ = _query_rows(backend, query, {"root_id": root_id})
        for row in rows:
            row["doc_count"] = int(row.get("doc_count") or 0)
            by_folder_id[row["folder_id"]] = row

    return sorted(by_folder_id.values(), key=lambda r: r["name"])


def list_documents(backend: KgBackend, folder_id: str | None = None) -> list[dict[str, Any]]:
    """List ingested documents with their section count and hashes.

    Args:
        folder_id: When given, only return documents under this folder's subtree
            (the folder itself or any nested subfolder).

    Returns:
        List of `{content_hash, markdown_hash, filename, section_count, path, folder_id}` dicts.
    """
    if folder_id is None:
        query = (
            f"MATCH (d:{_DOCUMENT_LABEL}) "
            "RETURN d.content_hash AS content_hash, d.markdown_hash AS markdown_hash, d.filename AS filename, "
            "d.section_count AS section_count, d.path AS path, d.folder_id AS folder_id ORDER BY d.filename"
        )
        params: dict[str, Any] = {}
    else:
        query = f"""
            MATCH (root:{_FOLDER_LABEL} {{folder_id: $folder_id}})-[:HAS_SUBFOLDER*0..30]->(f:{_FOLDER_LABEL})
            MATCH (f)-[:CONTAINS]->(d:{_DOCUMENT_LABEL})
            RETURN d.content_hash AS content_hash, d.markdown_hash AS markdown_hash, d.filename AS filename,
                   d.section_count AS section_count, d.path AS path, d.folder_id AS folder_id ORDER BY d.filename
        """
        params = {"folder_id": folder_id}

    rows, _ = _query_rows(backend, query, params)
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
    that document, so `cli docgraph list`'s short hash column can be used directly.
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


def _collect_subtree_section_ids(toc_rows: list[dict[str, Any]], root_section_id: str) -> list[str]:
    """Return *root_section_id* plus every descendant section_id (any depth)."""
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for row in toc_rows:
        by_parent.setdefault(row["parent_section_id"], []).append(row)

    ids = [root_section_id]

    def collect(parent_id: str) -> None:
        for row in by_parent.get(parent_id, []):
            ids.append(row["section_id"])
            collect(row["section_id"])

    collect(root_section_id)
    return ids


def reconstruct_section(
    backend: KgBackend, section_id: str, return_query: bool = False
) -> str | None | tuple[str | None, str]:
    """Rebuild the Markdown text of one section plus all of its nested subsections.

    Accepts a full ``section_id`` (``{markdown_hash}::{sequence}``) or a prefix of one.
    """
    query = (
        f"MATCH (s:{_SECTION_LABEL}) WHERE s.section_id = $id OR s.section_id STARTS WITH $id "
        "RETURN s.section_id AS section_id, s.markdown_hash AS markdown_hash LIMIT 1"
    )
    rows, _ = _query_rows(backend, query, {"id": section_id})
    if not rows:
        return (None, query) if return_query else None

    resolved_id = rows[0]["section_id"]
    markdown_hash = rows[0]["markdown_hash"]

    toc = get_document_toc(backend, markdown_hash)
    subtree_ids = _collect_subtree_section_ids(toc, resolved_id)  # type: ignore[arg-type]

    content_rows = get_section_content(backend, subtree_ids)
    content_rows.sort(key=lambda r: r["sequence"])  # type: ignore[union-attr]
    text = "\n".join(r["text"] for r in content_rows)  # type: ignore[union-attr]
    return (text, query) if return_query else text


def search_sections(
    backend: KgBackend, keyword: str, limit: int = 20, folder_id: str | None = None, return_query: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], str]:
    """Cross-document keyword search over section titles and body text (no embeddings).

    Args:
        folder_id: When given, restrict the search to documents under this folder's subtree.
    """
    if folder_id is None:
        query = f"""
            MATCH (s:{_SECTION_LABEL})
            WHERE s.title CONTAINS $keyword OR s.text CONTAINS $keyword
            RETURN s.markdown_hash AS markdown_hash, s.section_id AS section_id, s.title AS title,
                   s.level AS level, s.line_start AS line_start
            ORDER BY s.markdown_hash, s.line_start
            LIMIT $limit
        """
        params: dict[str, Any] = {"keyword": keyword, "limit": limit}
    else:
        query = f"""
            MATCH (root:{_FOLDER_LABEL} {{folder_id: $folder_id}})-[:HAS_SUBFOLDER*0..30]->(f:{_FOLDER_LABEL})
            MATCH (f)-[:CONTAINS]->(d:{_DOCUMENT_LABEL})
            MATCH (s:{_SECTION_LABEL} {{markdown_hash: d.markdown_hash}})
            WHERE s.title CONTAINS $keyword OR s.text CONTAINS $keyword
            RETURN s.markdown_hash AS markdown_hash, s.section_id AS section_id, s.title AS title,
                   s.level AS level, s.line_start AS line_start
            ORDER BY s.markdown_hash, s.line_start
            LIMIT $limit
        """
        params = {"keyword": keyword, "limit": limit, "folder_id": folder_id}
    rows, _ = _query_rows(backend, query, params)
    return (rows, query) if return_query else rows


def _connect(db_path: str) -> KgBackend:
    backend = KuzuBackend()
    backend.connect(db_path)
    return backend


def create_document_graph_tools(db_path: str) -> list[BaseTool]:
    """Build the LangChain tools an agent uses to navigate a Document Graph.

    Args:
        db_path: Path to the Ladybug database holding the ingested graph.

    Returns:
        `[list_documents, get_document_toc, get_section_content, search_sections]` tools.
    """

    @tool("list_documents")
    def _list_documents() -> str:
        """List every ingested document with its section count."""
        try:
            rows = list_documents(_connect(db_path))
        except Exception as exc:  # noqa: BLE001
            return f"Error listing documents: {exc}"
        if not rows:
            return "No documents ingested yet."
        return "\n".join(
            f"- {r['filename']} ({r['section_count']} sections) — md_hash: {r['markdown_hash']}" for r in rows
        )

    @tool("get_document_toc")
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

    @tool("get_section_content")
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

    @tool("search_sections")
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
