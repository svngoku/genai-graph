"""Navigation tools for the Document Graph.

Exposes read-only Cypher-backed functions an agent (or the `cli docgraph`
command) can call to walk a document's heading hierarchy, fetch individual
sections, and reconstruct a whole document from its sections. Documents are
addressed by the Document content hash (full or prefix), its ``markdown_hash``,
the filename, or the source path.
"""

from __future__ import annotations

from typing import Any

import yaml
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
        List of `{content_hash, markdown_hash, filename, section_count, token_count, description,
        summary, path, folder_id}` dicts.
    """
    if folder_id is None:
        query = (
            f"MATCH (d:{_DOCUMENT_LABEL}) "
            "RETURN d.content_hash AS content_hash, d.markdown_hash AS markdown_hash, d.filename AS filename, "
            "d.section_count AS section_count, d.token_count AS token_count, "
            "d.description AS description, d.summary AS summary, "
            "d.path AS path, d.folder_id AS folder_id ORDER BY d.filename"
        )
        params: dict[str, Any] = {}
    else:
        query = f"""
            MATCH (root:{_FOLDER_LABEL} {{folder_id: $folder_id}})-[:HAS_SUBFOLDER*0..30]->(f:{_FOLDER_LABEL})
            MATCH (f)-[:CONTAINS]->(d:{_DOCUMENT_LABEL})
            RETURN d.content_hash AS content_hash, d.markdown_hash AS markdown_hash, d.filename AS filename,
                   d.section_count AS section_count, d.token_count AS token_count,
                   d.description AS description, d.summary AS summary,
                   d.path AS path, d.folder_id AS folder_id ORDER BY d.filename
        """
        params = {"folder_id": folder_id}

    rows, _ = _query_rows(backend, query, params)
    for row in rows:
        row["section_count"] = int(row.get("section_count") or 0)
        row["token_count"] = int(row.get("token_count") or 0)
    return rows


def get_document(backend: KgBackend, document_id: str) -> dict[str, Any] | None:
    """Return one Document's full metadata, including `token_count` and `summary`.

    Accepts the same references as `get_document_toc`: a content hash (full or
    prefix), a `markdown_hash`, a filename, or a source path.
    """
    query = (
        f"MATCH (d:{_DOCUMENT_LABEL}) "
        "WHERE d.content_hash = $id OR d.content_hash STARTS WITH $id OR d.markdown_hash = $id "
        "OR d.markdown_hash STARTS WITH $id OR d.filename = $id OR d.path = $id "
        "RETURN d.content_hash AS content_hash, d.markdown_hash AS markdown_hash, d.filename AS filename, "
        "d.section_count AS section_count, d.token_count AS token_count, "
        "d.description AS description, d.summary AS summary, "
        "d.path AS path, d.folder_id AS folder_id LIMIT 1"
    )
    rows, _ = _query_rows(backend, query, {"id": document_id})
    if not rows:
        return None
    row = rows[0]
    row["section_count"] = int(row.get("section_count") or 0)
    row["token_count"] = int(row.get("token_count") or 0)
    return row


def apply_section_summaries(backend: KgBackend, rows: list[dict[str, Any]]) -> int:
    """Write `description`/`summary`/`summary_source` onto MarkdownSection nodes.

    Args:
        rows: `{section_id, description, summary, summary_source}` dicts, one per section.

    Returns:
        Number of sections updated.
    """
    if not rows:
        return 0
    query = (
        "UNWIND $rows AS row "
        f"MATCH (s:{_SECTION_LABEL} {{section_id: row.section_id}}) "
        "SET s.description = row.description, s.summary = row.summary, s.summary_source = row.summary_source"
    )
    backend.execute(query, {"rows": rows})
    return len(rows)


def apply_document_summary(
    backend: KgBackend, markdown_hash: str, *, description: str | None = None, summary: str | None = None
) -> None:
    """Write the document-level description/abstract onto every Document sharing *markdown_hash*."""
    backend.execute(
        f"MATCH (d:{_DOCUMENT_LABEL} {{markdown_hash: $markdown_hash}}) "
        "SET d.description = $description, d.summary = $summary",
        {"markdown_hash": markdown_hash, "description": description, "summary": summary},
    )


def get_document_toc(
    backend: KgBackend, document_id: str, return_query: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], str]:
    """Return the table of contents (heading tree) for one document.

    Section body text is not returned, but `token_count` and `summary` are — this
    is the map an agent uses to decide which sections to fetch in full with
    `get_section_content`.
    """
    markdown_hash = _resolve_markdown_hash(backend, document_id)
    if not markdown_hash:
        result: list[dict[str, Any]] = []
        query = f"-- No document found matching: {document_id}"
        return (result, query) if return_query else result

    query = f"""
        MATCH (s:{_SECTION_LABEL} {{markdown_hash: $markdown_hash}})
        RETURN s.section_id AS section_id, s.parent_section_id AS parent_section_id,
               s.title AS title, s.level AS level, s.line_start AS line_start, s.sequence AS sequence,
               s.token_count AS token_count, s.description AS description, s.summary AS summary,
               s.summary_source AS summary_source
        ORDER BY s.sequence
    """
    rows, _ = _query_rows(backend, query, {"markdown_hash": markdown_hash})
    return (rows, query) if return_query else rows


def build_toc_tree(
    toc_rows: list[dict[str, Any]], *, include_summaries: bool = False, max_level: int | None = None
) -> list[dict[str, Any]]:
    """Nest flat `get_document_toc` rows into a tree by `parent_section_id`.

    Each node is `{id, title, description?, summary?, sections?}`. Heading level and
    token count are deliberately not emitted: level is redundant with the tree's own
    nesting, and token count adds nothing an agent can act on once `description`
    already answers "is this worth opening?". The synthetic level-0 root section is
    unwrapped — it is a container for preamble text, not a heading an agent would
    navigate to.

    Args:
        include_summaries: Also emit the fuller `summary` where one exists. Off by
            default: `description` alone is what an agent needs to pick a section, and
            adding summaries roughly triples the size of the tree.
        max_level: Drop sections deeper than this heading level.
    """
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for row in toc_rows:
        by_parent.setdefault(row.get("parent_section_id"), []).append(row)

    def build(parent_id: str | None) -> list[dict[str, Any]]:
        nodes = []
        for row in sorted(by_parent.get(parent_id, []), key=lambda r: r["sequence"]):
            if max_level is not None and int(row["level"]) > max_level:
                continue
            node: dict[str, Any] = {
                "id": row["section_id"],
                "title": row["title"],
            }
            if row.get("description"):
                node["description"] = row["description"]
            if include_summaries and row.get("summary"):
                node["summary"] = row["summary"]
            children = build(row["section_id"])
            if children:
                node["sections"] = children
            nodes.append(node)
        return nodes

    root_rows = [r for r in toc_rows if r.get("level") == 0]
    if root_rows:
        return build(root_rows[0]["section_id"])
    return build(None)


def render_toc_outline(toc_rows: list[dict[str, Any]]) -> str:
    """Render a document's TOC as compact indented text (`- [id] Title`), for LLM prompts and the CLI."""
    lines = []
    for row in toc_rows:
        if int(row.get("level") or 0) == 0:
            continue  # synthetic root: not a navigable heading
        indent = "  " * max(int(row["level"]) - 1, 0)
        lines.append(f"{indent}- [{row['section_id']}] {row['title']} (line {row['line_start']})")
    return "\n".join(lines)


def document_toc_yaml(
    backend: KgBackend, document_id: str, *, include_summaries: bool = False, max_level: int | None = None
) -> str:
    """Return one document's table of contents as a YAML string.

    Section `description`s are always included (they are the routing signal); pass
    `include_summaries=True` for the fuller per-section summaries as well.
    """
    doc = get_document(backend, document_id)
    toc_rows = get_document_toc(backend, document_id)
    if doc is None or not toc_rows:
        return yaml.safe_dump({"error": f"No document found matching: {document_id}"}, sort_keys=False)
    payload: dict[str, Any] = {
        "document": doc["filename"],
        "id": doc["content_hash"],
    }
    if doc.get("description"):
        payload["description"] = doc["description"]
    if doc.get("summary"):
        payload["summary"] = doc["summary"]
    payload["sections"] = build_toc_tree(
        toc_rows,  # type: ignore[arg-type]
        include_summaries=include_summaries,
        max_level=max_level,
    )
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def folder_toc_yaml(
    backend: KgBackend,
    folder_id: str | None,
    *,
    include_sections: bool = False,
    include_summaries: bool = False,
    max_level: int | None = None,
) -> str:
    """Return the documents under a folder's subtree as one YAML string.

    Sections are omitted by default — this is the *orientation* view an agent reads
    first to pick a document, and inlining every section of every document defeats
    the point (and blows the context window on a large corpus). Call
    `document_toc_yaml` for the chosen document, or pass `include_sections=True`.
    """
    docs = list_documents(backend, folder_id=folder_id)
    if not docs:
        return yaml.safe_dump({"documents": []}, sort_keys=False)
    payload: dict[str, Any] = {"documents": []}
    for doc in docs:
        entry: dict[str, Any] = {
            "id": doc["content_hash"],
            "name": doc["filename"],
            "sections": doc["section_count"],
        }
        if doc.get("description"):
            entry["description"] = doc["description"]
        if include_summaries and doc.get("summary"):
            entry["summary"] = doc["summary"]
        if include_sections:
            toc_rows = get_document_toc(backend, doc["markdown_hash"])
            entry["toc"] = build_toc_tree(
                toc_rows,  # type: ignore[arg-type]
                include_summaries=include_summaries,
                max_level=max_level,
            )
        payload["documents"].append(entry)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


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
        `[list_documents, get_document_toc, get_folder_toc, get_section_content, search_sections]` tools.
    """

    @tool("list_documents")
    def _list_documents() -> str:
        """List every ingested document with its section count and one-line description."""
        try:
            rows = list_documents(_connect(db_path))
        except Exception as exc:  # noqa: BLE001
            return f"Error listing documents: {exc}"
        if not rows:
            return "No documents ingested yet."
        lines = []
        for r in rows:
            line = f"- [{r['content_hash']}] {r['filename']} ({r['section_count']} sections, {r['token_count']} tokens)"
            if r.get("description"):
                line += f"\n  {r['description']}"
            lines.append(line)
        return "\n".join(lines)

    @tool("get_folder_toc")
    def _get_folder_toc(folder_id: str | None = None) -> str:
        """Start here. List the documents in a folder, each with an id and a one-line description.

        Sections are NOT included — pick a document from this list, then call
        `get_document_toc` with its id to see that document's sections.
        Omit `folder_id` to cover every ingested document.
        """
        try:
            backend = _connect(db_path)
            resolved = resolve_folder_id(backend, folder_id) if folder_id else None
            return folder_toc_yaml(backend, resolved)
        except Exception as exc:  # noqa: BLE001
            return f"Error fetching folder TOC: {exc}"

    @tool("get_document_toc")
    def _get_document_toc(document_id: str, include_summaries: bool = False, max_level: int | None = None) -> str:
        """Get one document's section tree as YAML: each section's id, title, size and description.

        Use the section ids from here with `get_section_content` to read the actual text.
        `document_id` is a content hash (full or prefix), a filename, or a source path.
        Set `include_summaries=True` for fuller per-section summaries, or `max_level` to
        show only top-level sections of a very long document.
        """
        try:
            return document_toc_yaml(
                _connect(db_path), document_id, include_summaries=include_summaries, max_level=max_level
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error fetching TOC: {exc}"

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

    return [_get_folder_toc, _get_document_toc, _get_section_content, _search_sections, _list_documents]
