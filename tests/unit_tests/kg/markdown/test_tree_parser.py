"""Unit tests for the Markdown Knowledge Tree parser."""

from __future__ import annotations

import pytest

from genai_graph.kg.markdown.tree_parser import parse_markdown_tree


@pytest.mark.unit
class TestParseMarkdownTree:
    def test_empty_document_has_no_sections(self) -> None:
        assert parse_markdown_tree("") == []
        assert parse_markdown_tree("Just a paragraph, no headings.") == []

    def test_flat_headings(self) -> None:
        raw = "# Title\n\nIntro text.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.\n"
        sections = parse_markdown_tree(raw)

        assert [s.title for s in sections] == ["Title", "Section A", "Section B"]
        assert [s.level for s in sections] == [1, 2, 2]
        # Section A and B are siblings under Title
        assert sections[0].parent_index is None
        assert sections[1].parent_index == 0
        assert sections[2].parent_index == 0

    def test_nested_headings_parent_index(self) -> None:
        raw = "# H1\n## H2a\n### H3\ntext\n## H2b\n"
        sections = parse_markdown_tree(raw)

        assert [s.title for s in sections] == ["H1", "H2a", "H3", "H2b"]
        assert sections[0].parent_index is None  # H1 root
        assert sections[1].parent_index == 0  # H2a -> H1
        assert sections[2].parent_index == 1  # H3 -> H2a
        assert sections[3].parent_index == 0  # H2b -> H1 (pops H3 and H2a off the stack)

    def test_line_start_and_line_end(self) -> None:
        raw = "\n".join(
            [
                "# Title",  # line 1
                "intro",  # line 2
                "## Section A",  # line 3
                "body a line 1",  # line 4
                "body a line 2",  # line 5
                "## Section B",  # line 6
                "body b",  # line 7
            ]
        )
        sections = parse_markdown_tree(raw)
        title, section_a, section_b = sections

        assert title.line_start == 1
        # "Title" is H1; its section spans until the next H1-or-shallower heading,
        # i.e. the whole rest of the document since no other H1 follows.
        assert title.line_end == 7
        assert section_a.line_start == 3
        assert section_a.line_end == 5  # ends right before "## Section B"
        assert section_b.line_start == 6
        assert section_b.line_end == 7  # end of file

    def test_code_fence_does_not_produce_false_headings(self) -> None:
        raw = "# Real Heading\n\n```markdown\n# Not a heading\n## Also not one\n```\n\n## Real Section\n"
        sections = parse_markdown_tree(raw)

        assert [s.title for s in sections] == ["Real Heading", "Real Section"]

    def test_heading_inside_blockquote_is_ignored(self) -> None:
        raw = "# Top\n\n> # Quoted heading\n\n## Bottom\n"
        sections = parse_markdown_tree(raw)

        assert [s.title for s in sections] == ["Top", "Bottom"]

    def test_token_count_is_positive(self) -> None:
        raw = "# Title\n\nSome words here for counting tokens.\n"
        sections = parse_markdown_tree(raw)
        assert sections[0].token_count > 0

    def test_untitled_heading_gets_placeholder_title(self) -> None:
        raw = "#\n\nbody\n"
        sections = parse_markdown_tree(raw)
        assert sections[0].title == "(untitled H1)"
