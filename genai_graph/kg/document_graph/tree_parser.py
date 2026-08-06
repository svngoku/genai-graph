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


def _estimate_token_count(text: str) -> int:
    """Rough token-count estimate (word + punctuation split) — no tokenizer dependency."""
    return len(re.findall(r"\w+|[^\w\s]", text))


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
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")
    tokens = md.parse(raw)

    lines = raw.splitlines()
    total_lines = len(lines)

    # Collect only top-level headings (nesting depth 0 — not inside blockquotes/lists).
    headings: list[tuple[str, int, int]] = []  # (title, level, line_start 1-indexed)
    depth = 0
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and depth == 0:
            level = int(tok.tag[1:])  # "h2" -> 2
            line_start = (tok.map[0] if tok.map else 0) + 1
            title = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                title = tokens[i + 1].content.strip()
            # Drop page-marker headings ("Page 12"): they are conversion artifacts,
            # not document structure. Their text stays inline in the enclosing section.
            if not _PAGE_MARKER_RE.match(title):
                headings.append((title, level, line_start))
        depth += tok.nesting

    # In numbered documents the source '#' levels are unreliable (PDF artifacts);
    # re-derive each heading's logical level from its outline number.
    inferred_levels = _infer_levels([h[0] for h in headings], [h[1] for h in headings])
    headings = [(title, inferred_levels[idx], line_start) for idx, (title, _, line_start) in enumerate(headings)]

    sections: list[FlatSection] = []

    # Synthetic root section: needed when the document has no headings, or when
    # there is preamble content before the first heading.
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

    # Heading sections: own text runs up to the line before the next heading of ANY level.
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

    # Resolve parent_index: nearest preceding section with a strictly smaller level.
    stack: list[int] = []
    for idx, section in enumerate(sections):
        while stack and sections[stack[-1]].level >= section.level:
            stack.pop()
        section.parent_index = stack[-1] if stack else None
        stack.append(idx)

    return sections
