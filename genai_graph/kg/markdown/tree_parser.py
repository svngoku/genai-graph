"""Markdown Knowledge Tree — parse Markdown text into a flat, ordered list of sections.

Uses ``markdown-it-py`` (a real CommonMark parser) instead of regex so that
headings inside fenced code blocks, inline code, or blockquotes are handled
correctly, and each heading's source line number is known precisely.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class FlatSection(BaseModel):
    """A single heading-delimited section, before hierarchy is resolved into graph edges."""

    title: str = Field(..., description="Heading text")
    level: int = Field(..., description="Heading level, 1 (H1) to 6 (H6)")
    line_start: int = Field(..., description="1-indexed source line of the heading")
    line_end: int = Field(..., description="1-indexed source line where the section ends (inclusive)")
    text: str = Field(..., description="Raw Markdown text of the section (heading line + body)")
    token_count: int = Field(..., description="Approximate token count (whitespace/punctuation based estimate)")
    parent_index: int | None = Field(
        default=None, description="Index of the parent section within the same flat list, or None for a root section"
    )


def _estimate_token_count(text: str) -> int:
    """Rough token-count estimate (word + punctuation split) — no tokenizer dependency."""
    return len(re.findall(r"\w+|[^\w\s]", text))


def parse_markdown_tree(raw: str) -> list[FlatSection]:
    """Parse *raw* Markdown into a flat, order-preserving list of sections.

    Each section's `line_end` extends to the line before the next section at
    the same or a shallower heading level (or end of file). Parent/child
    hierarchy is resolved with a level-based stack and stored as `parent_index`.

    Args:
        raw: Full Markdown document text.

    Returns:
        Flat list of `FlatSection` in document order. Empty when the document
        has no ATX/Setext headings.
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
            headings.append((title, level, line_start))
        depth += tok.nesting

    sections: list[FlatSection] = []
    for idx, (title, level, line_start) in enumerate(headings):
        line_end = total_lines
        for _next_title, next_level, next_line_start in headings[idx + 1 :]:
            if next_level <= level:
                line_end = next_line_start - 1
                break
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
