"""Document Graph — parse Markdown text into a flat, ordered list of sections.

Uses ``markdown-it-py`` (a real CommonMark parser) instead of regex so that
headings inside fenced code blocks, inline code, or blockquotes are handled
correctly, and each heading's source line number is known precisely.

Each section owns a *non-overlapping* slice of the document: its lines run from
its heading up to the line before the next heading of **any** level (a nested
subsection's lines therefore belong to the subsection, not the parent). Any text
before the first heading — or a document with no headings at all — is captured by
a synthetic level-0 root section. As a result every document yields at least one
section and can be reconstructed exactly by concatenating the sections'
``text`` in ``sequence`` order.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

# Title used for the synthetic level-0 section that captures a document's
# preamble (text before the first heading) or a heading-less document.
ROOT_SECTION_TITLE = "(document root)"

# Headings that are page markers from a PDF/Doc → Markdown conversion (e.g.
# "## Page 12"). These carry no structural meaning and must not become sections.
_PAGE_MARKER_RE = re.compile(r"^page\s+\d+$", re.IGNORECASE)

# Surrounding Markdown emphasis/whitespace, stripped before comparing a heading
# title to another for de-duplication (e.g. "**Advanced Micro Devices, Inc.**"
# and "Advanced Micro Devices, Inc." collapse together).
_DEDUP_EMPHASIS_RE = re.compile(r"[*_`]{1,3}")
_DEDUP_WS_RE = re.compile(r"\s+")

# Leading outline number of a heading, ignoring surrounding Markdown emphasis
# (e.g. "**3.4 Device life cycle**" → "3.4"). The depth (dot-separated component
# count) gives the heading's logical level in a numbered document.
_OUTLINE_NUMBER_RE = re.compile(r"^\**\s*(\d+(?:\.\d+)*)\b")


def _outline_depth(title: str) -> int | None:
    """Depth of a heading's leading outline number (``3.4`` → 2), or None if unnumbered."""
    match = _OUTLINE_NUMBER_RE.match(title)
    if not match:
        return None
    return match.group(1).count(".") + 1


def _normalize_title_for_dedup(title: str) -> str:
    """Normalize a heading title for repeat-detection (emphasis/whitespace-stripped, lowercased)."""
    stripped = _DEDUP_EMPHASIS_RE.sub("", title or "")
    return _DEDUP_WS_RE.sub(" ", stripped).strip().lower()


def _dedupe_page_header_headings(
    headings: list[tuple[str, int, int]], raw_lines: list[str]
) -> list[tuple[str, int, int]]:
    """Drop repeated page-header headings whose own body is empty.

    PDF/Office → Markdown conversions repeat a company-name or title line as a
    ``#`` heading before every statement (e.g. ``# **Advanced Micro Devices, Inc.**``
    ahead of each financial statement). Each such heading owns no body — the next
    heading follows on a nearby line — so it would become a near-empty section
    (the "empty sections" of the algorithmic path). Drop a heading when its body
    (the lines between it and the next heading) is entirely blank AND its
    normalized title has already appeared earlier in the document. Real
    sub-headings, first occurrences, and headings with body are always kept, so no
    content is lost and the document stays byte-for-byte reconstructable.
    """
    kept: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    n = len(headings)
    for i, (title, level, line_start) in enumerate(headings):
        next_start = headings[i + 1][2] if i + 1 < n else None
        norm = _normalize_title_for_dedup(title)
        is_repeat = bool(norm) and norm in seen
        empty_body = False
        if next_start is not None:
            # Body lines: 0-indexed slice from just after the heading line up to
            # (excluding) the next heading's line.
            body = raw_lines[line_start: next_start - 1]
            empty_body = all(not line.strip() for line in body)
        if is_repeat and empty_body:
            continue
        kept.append((title, level, line_start))
        seen.add(norm)
    return kept


def _infer_levels(titles: list[str], md_levels: list[int]) -> list[int]:
    """Derive logical heading levels for a numbered document from its outline numbers.

    PDF/Doc → Markdown conversions routinely emit inconsistent ``#`` levels (the
    same "3.1"/"3.5" heading may come out as H4 or H1). When a document is clearly
    numbered, the outline number is the reliable structure signal: a heading's level
    is its number's depth (``1`` → 1, ``1.1`` → 2), and an unnumbered heading nests
    one level below the most recent numbered heading. Documents without meaningful
    numbering keep their original Markdown levels.
    """
    depths = [_outline_depth(t) for t in titles]
    if sum(d is not None for d in depths) < 3:
        return md_levels

    levels: list[int] = []
    last_numbered_level = 0
    for depth in depths:
        if depth is not None:
            level = depth
            last_numbered_level = level
        else:
            level = last_numbered_level + 1
        levels.append(level)
    return levels


class FlatSection(BaseModel):
    """A single section, before hierarchy is resolved into graph edges."""

    title: str = Field(..., description="Heading text (or the root-section title)")
    level: int = Field(..., description="Heading level, 0 (synthetic root) or 1 (H1) to 6 (H6)")
    line_start: int = Field(..., description="1-indexed source line where this section's own text starts")
    line_end: int = Field(..., description="1-indexed source line where this section's own text ends (inclusive)")
    text: str = Field(..., description="Own Markdown text: heading line + body up to the next heading (any level)")
    token_count: int = Field(..., description="Approximate token count (whitespace/punctuation based estimate)")
    parent_index: int | None = Field(
        default=None, description="Index of the parent section within the same flat list, or None for a root section"
    )
    description: str | None = Field(default=None, description="One-sentence routing description of the section")
    summary: str | None = Field(default=None, description="Short paragraph summary (substantial sections only)")
    summary_source: str | None = Field(
        default=None, description="How description/summary were produced (e.g. 'llm'), or None when not yet set"
    )


def _estimate_token_count(text: str) -> int:
    """Rough token-count estimate (word + punctuation split) — no tokenizer dependency."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def detect_headings(raw: str) -> list[tuple[str, int, int]]:
    """Find the document's top-level headings and their logical levels.

    Uses ``markdown-it-py`` so headings inside fenced code blocks, inline code, or
    blockquotes are ignored, and each heading's source line number is known
    precisely. Page-marker headings (``Page 12``) are dropped — they are PDF/Doc
    conversion artifacts with no structural meaning. For numbered documents the
    unreliable source ``#`` levels are re-derived from the outline numbers.

    Returns:
        ``(title, level, line_start)`` tuples in document order, where
        ``line_start`` is 1-indexed. Empty when the document has no headings.
    """
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")
    tokens = md.parse(raw)

    headings: list[tuple[str, int, int]] = []  # (title, level, line_start 1-indexed)
    depth = 0
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and depth == 0:
            level = int(tok.tag[1:])  # "h2" -> 2
            line_start = (tok.map[0] if tok.map else 0) + 1
            title = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                title = tokens[i + 1].content.strip()
            # Drop page-marker headings ("Page 12"): their text stays inline.
            if not _PAGE_MARKER_RE.match(title):
                headings.append((title, level, line_start))
        depth += tok.nesting

    inferred_levels = _infer_levels([h[0] for h in headings], [h[1] for h in headings])
    inferred = [
        (title, inferred_levels[idx], line_start)
        for idx, (title, _, line_start) in enumerate(headings)
    ]
    return _dedupe_page_header_headings(inferred, raw.splitlines())


def slice_sections(raw: str, headings: list[tuple[str, int, int]]) -> list[FlatSection]:
    """Slice *raw* into non-overlapping sections anchored at *headings*.

    Each heading's section owns its heading line plus body up to the line before
    the next heading of ANY level (a nested subsection's lines belong to the
    subsection, not the parent). Any text before the first heading — or a document
    with no headings at all — is captured by a synthetic level-0 root section.
    ``headings`` is ``(title, level, line_start)`` in document order, with
    ``line_start`` 1-indexed. The result is never empty, and concatenating the
    sections' ``text`` in order reconstructs *raw*.
    """
    lines = raw.splitlines()
    total_lines = len(lines)

    sections: list[FlatSection] = []

    first_heading_line = headings[0][2] if headings else None
    if first_heading_line is None or first_heading_line > 1:
        root_end = (first_heading_line - 1) if first_heading_line is not None else total_lines
        root_end = max(root_end, 1)
        root_text = "\n".join(lines[0:root_end])
        sections.append(
            FlatSection(
                title=ROOT_SECTION_TITLE,
                level=0,
                line_start=1,
                line_end=root_end,
                text=root_text,
                token_count=_estimate_token_count(root_text),
            )
        )

    for idx, (title, level, line_start) in enumerate(headings):
        next_line = headings[idx + 1][2] if idx + 1 < len(headings) else total_lines + 1
        line_end = next_line - 1
        text = "\n".join(lines[line_start - 1 : line_end])
        sections.append(
            FlatSection(
                title=title or f"(untitled H{level})",
                level=level,
                line_start=line_start,
                line_end=max(line_end, line_start),
                text=text,
                token_count=_estimate_token_count(text),
            )
        )

    stack: list[int] = []
    for idx, section in enumerate(sections):
        while stack and sections[stack[-1]].level >= section.level:
            stack.pop()
        section.parent_index = stack[-1] if stack else None
        stack.append(idx)

    return sections


def parse_markdown_tree(raw: str) -> list[FlatSection]:
    """Parse *raw* Markdown into a flat, order-preserving list of sections.

    Every document yields at least one section. Section line ranges partition the
    document with no overlap, so concatenating the sections' ``text`` in
    ``sequence`` order reconstructs the original document.

    Args:
        raw: Full Markdown document text.

    Returns:
        Flat list of `FlatSection` in document order (never empty). Parent/child
        hierarchy is resolved with a level-based stack and stored as `parent_index`.
    """
    return slice_sections(raw, detect_headings(raw))
