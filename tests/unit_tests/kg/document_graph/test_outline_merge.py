"""Unit tests for the LLM-outline -> Markdown merge (no LLM, no database)."""

from __future__ import annotations

import pytest

from genai_graph.kg.document_graph.outline_extract import DocumentOutline, OutlineEntry
from genai_graph.kg.document_graph.outline_merge import merge_outline
from genai_graph.kg.document_graph.tree_parser import detect_headings, slice_sections


def _outline(*entries: OutlineEntry, doc_desc: str = "Doc desc.", doc_sum: str = "Doc sum.") -> DocumentOutline:
    return DocumentOutline(document_description=doc_desc, document_summary=doc_sum, sections=list(entries))


@pytest.mark.unit
class TestMergeOutline:
    def test_matched_anchors_attach_description_and_summary(self) -> None:
        raw = "# Title\n\nIntro.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.\n"
        algo = detect_headings(raw)
        outline = _outline(
            OutlineEntry(title="Title", level=1, description="The title."),
            OutlineEntry(title="Section A", level=2, description="A section."),
            OutlineEntry(title="Section B", level=2, description="B section.", summary="B summary."),
        )

        sections = merge_outline(raw, outline, algo)

        descs = {s.title: s.description for s in sections if s.level > 0}
        assert descs == {"Title": "The title.", "Section A": "A section.", "Section B": "B section."}
        sums = {s.title: s.summary for s in sections if s.level > 0}
        assert sums["Title"] is None
        assert sums["Section B"] == "B summary."
        assert all(s.summary_source == "llm" for s in sections if s.level > 0)

    def test_result_is_byte_for_byte_reconstructable(self) -> None:
        raw = "# Title\n\nIntro.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.\n"
        algo = detect_headings(raw)
        outline = _outline(
            OutlineEntry(title="Title", level=1, description="t"),
            OutlineEntry(title="Section A", level=2, description="a"),
            OutlineEntry(title="Section B", level=2, description="b"),
        )

        sections = merge_outline(raw, outline, algo)

        assert "\n".join(s.text for s in sections) == raw.rstrip("\n")

    def test_algorithmic_level_is_authoritative(self) -> None:
        raw = "# Title\n\n## Section A\n\nbody\n"
        algo = detect_headings(raw)  # Section A is level 2 algorithmically
        outline = _outline(
            OutlineEntry(title="Title", level=1, description="t"),
            OutlineEntry(title="Section A", level=3, description="a"),  # LLM says 3
        )

        sections = merge_outline(raw, outline, algo)

        section_a = next(s for s in sections if s.title == "Section A")
        assert section_a.level == 2  # detected level wins, not the LLM's

    def test_unmatched_entry_folds_into_preceding_section(self) -> None:
        raw = "# Title\n\nIntro.\n\n## Section A\n\nBody A.\n"
        algo = detect_headings(raw)
        outline = _outline(
            OutlineEntry(title="Title", level=1, description="The title."),
            OutlineEntry(title="Phantom", level=2, description="Phantom desc."),
            OutlineEntry(title="Section A", level=2, description="A section."),
        )

        sections = merge_outline(raw, outline, algo)

        title_section = next(s for s in sections if s.title == "Title")
        assert "Phantom desc." in (title_section.description or "")
        # The phantom produced no extra section.
        assert "Phantom" not in {s.title for s in sections}

    def test_algo_fallback_anchors_when_no_line_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        raw = "Preamble.\n\n## Section A\n\nbody\n"
        algo = detect_headings(raw)
        # Two entries vs one heading -> count mismatch -> fallback path; the
        # phantom (no description) only forces the mismatch and folds harmlessly.
        outline = _outline(
            OutlineEntry(title="Section A", level=2, description="A desc."),
            OutlineEntry(title="Phantom", level=2, description=""),
        )

        # Force the line scan to fail so the algorithmic-heading fallback is exercised.
        monkeypatch.setattr(
            "genai_graph.kg.document_graph.outline_merge._find_heading_line",
            lambda lines, title, cursor: None,
        )

        sections = merge_outline(raw, outline, algo)

        section_a = next(s for s in sections if s.title == "Section A")
        assert section_a.description == "A desc."
        assert section_a.summary_source == "llm"

    def test_zero_matches_degrades_to_algorithmic_structure(self) -> None:
        raw = "# Title\n\n## Section A\n\nbody\n"
        algo = detect_headings(raw)
        # Three entries vs two headings -> count mismatch -> fallback path; none
        # of the titles match a line or an algo heading -> total reconciliation
        # failure -> degrade to the algorithmic structure with no summaries.
        outline = _outline(
            OutlineEntry(title="Nope1", level=1, description="x"),
            OutlineEntry(title="Nope2", level=2, description="y"),
            OutlineEntry(title="Nope3", level=2, description="z"),
        )

        sections = merge_outline(raw, outline, algo)

        algo_sections = slice_sections(raw, algo)
        assert [s.title for s in sections] == [s.title for s in algo_sections]
        assert all(s.description is None for s in sections)
        assert all(s.summary is None for s in sections)
        assert all(s.summary_source is None for s in sections)

    def test_synthetic_root_does_not_carry_description(self) -> None:
        raw = "Preamble.\n\n## Section A\n\nbody\n"
        algo = detect_headings(raw)
        outline = _outline(OutlineEntry(title="Section A", level=2, description="A desc."))

        sections = merge_outline(raw, outline, algo)

        root = sections[0]
        assert root.level == 0
        assert root.description is None
        assert root.summary is None
        assert root.summary_source is None
