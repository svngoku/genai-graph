"""Unit tests for pure summarization functions (no LLM, no database)."""

from __future__ import annotations

import pytest

from genai_graph.kg.document_graph.summarize import (
    DocumentIndex,
    SectionEntry,
    SectionPlan,
    SummarizationConfig,
    _build_annotated_document,
    _call_llm_with_retry,
    _clean_text,
    _is_length_limit_error,
    _plan_batches,
    _truncate_section_text,
    select_sections,
)


class TestSelectSections:
    def test_every_heading_section_gets_described(self) -> None:
        rows = [
            {"section_id": "a::1", "level": 1, "token_count": 50},
            {"section_id": "a::2", "level": 3, "token_count": 10},
        ]
        plans = select_sections(rows, max_level=6, summary_min_tokens=800)
        assert [p.section_id for p in plans] == ["a::1", "a::2"]
        assert not any(p.needs_summary for p in plans)

    def test_large_section_also_needs_summary(self) -> None:
        rows = [{"section_id": "a::1", "level": 1, "token_count": 900}]
        plans = select_sections(rows, max_level=6, summary_min_tokens=800)
        assert plans[0].needs_summary

    def test_root_section_is_skipped(self) -> None:
        rows = [{"section_id": "a::0", "level": 0, "token_count": 5000}]
        assert select_sections(rows, max_level=6, summary_min_tokens=800) == []

    def test_sections_deeper_than_max_level_are_skipped(self) -> None:
        rows = [
            {"section_id": "a::1", "level": 2, "token_count": 10},
            {"section_id": "a::2", "level": 4, "token_count": 10},
        ]
        plans = select_sections(rows, max_level=2, summary_min_tokens=800)
        assert [p.section_id for p in plans] == ["a::1"]


class TestCleanText:
    def test_strips_markdown_heading_and_flattens(self) -> None:
        raw = "## Database services\n\nService includes at least:\n\n- monitoring\n- backups"
        assert _clean_text(raw, 500) == "Database services Service includes at least: monitoring backups"

    def test_strips_page_markers(self) -> None:
        assert "Page 30" not in _clean_text("Contract terms.\n\n## Page 30\n", 500)

    def test_strips_emphasis(self) -> None:
        assert _clean_text("**12 Contract terms**", 500) == "12 Contract terms"

    def test_truncates_at_sentence_boundary(self) -> None:
        raw = "First sentence here. Second sentence that would overflow the configured limit."
        assert _clean_text(raw, 30) == "First sentence here."

    def test_truncates_at_word_boundary_when_no_sentence_break(self) -> None:
        result = _clean_text("alpha beta gamma delta epsilon zeta", 20)
        assert result.endswith("…")
        assert len(result) <= 21

    def test_short_text_is_unchanged(self) -> None:
        assert _clean_text("Already short.", 500) == "Already short."

    def test_handles_empty_input(self) -> None:
        assert _clean_text("", 500) == ""


class TestTruncateSectionText:
    def test_short_text_is_unchanged(self) -> None:
        text = "# Heading\n\nA short paragraph."
        assert _truncate_section_text(text, max_chars=4000, table_sample_rows=5) == text

    def test_long_prose_gets_truncated(self) -> None:
        text = "line\n" * 2000
        result = _truncate_section_text(text, max_chars=100, table_sample_rows=5)
        assert "_(... truncated ...)_" in result
        assert len(result) < len(text)

    def test_table_keeps_header_and_sample_rows(self) -> None:
        header, sep = "| Name | Value |", "| --- | --- |"
        rows = [f"| item{i} | {i} |" for i in range(20)]
        result = _truncate_section_text("\n".join([header, sep, *rows]), max_chars=4000, table_sample_rows=5)
        assert header in result
        assert "item4" in result
        assert "item5" not in result
        assert "15 more row(s) omitted" in result


class TestPlanBatches:
    def test_empty_input(self) -> None:
        assert _plan_batches([], SummarizationConfig()) == []

    def test_small_document_is_one_batch(self) -> None:
        plans = [SectionPlan(section_id=f"d::{i}") for i in range(20)]
        assert len(_plan_batches(plans, SummarizationConfig())) == 1

    def test_large_document_splits_and_preserves_order(self) -> None:
        plans = [SectionPlan(section_id=f"d::{i}") for i in range(500)]
        batches = _plan_batches(plans, SummarizationConfig())
        assert len(batches) > 1
        assert [p.section_id for b in batches for p in b] == [p.section_id for p in plans]

    def test_summary_sections_cost_more_so_batches_are_smaller(self) -> None:
        config = SummarizationConfig()
        plain = _plan_batches([SectionPlan(section_id=f"d::{i}") for i in range(200)], config)
        with_summaries = _plan_batches(
            [SectionPlan(section_id=f"d::{i}", needs_summary=True) for i in range(200)], config
        )
        assert len(with_summaries) > len(plain)


class TestBuildAnnotatedDocument:
    def test_markers_precede_each_section_in_sequence_order(self) -> None:
        rows = [
            {"section_id": "a::1", "sequence": 1, "text": "## Second"},
            {"section_id": "a::0", "sequence": 0, "text": "# First"},
        ]
        result = _build_annotated_document(rows, SummarizationConfig())
        assert result.index("[[a::0]]") < result.index("[[a::1]]")
        assert "[[a::0]]\n# First" in result


class TestIsLengthLimitError:
    def test_matches_openai_length_finish_reason_message(self) -> None:
        exc = RuntimeError("Could not parse response content as the length limit was reached - CompletionUsage(...)")
        assert _is_length_limit_error(exc)

    def test_unrelated_error_does_not_match(self) -> None:
        assert not _is_length_limit_error(ValueError("some other failure"))


def _retry_kwargs(max_tokens: int | None) -> dict:
    return {
        "llm_id": "fake@fake",
        "filename": "doc.md",
        "toc_outline": "- [a::1] Title",
        "annotated_document": "[[a::1]]\n# Title\n\nbody",
        "plans": [SectionPlan(section_id="a::1")],
        "config": SummarizationConfig(llm_max_tokens=max_tokens),
        "max_tokens": max_tokens,
        "context": "doc.md batch 1/1 (1 section(s))",
    }


def _ok_result(**kwargs) -> DocumentIndex:
    return DocumentIndex(
        document_description="A doc.",
        document_summary="A doc summary.",
        sections=[SectionEntry(section_id=p.section_id, description="d") for p in kwargs["plans"]],
    )


class TestCallLlmWithRetry:
    def test_success_on_first_attempt_no_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int | None] = []

        def fake_call_llm(*, max_tokens: int | None, **kwargs) -> DocumentIndex:
            calls.append(max_tokens)
            return _ok_result(**kwargs)

        monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)
        warnings: list[str] = []
        assert _call_llm_with_retry(**_retry_kwargs(None), warnings=warnings) is not None
        assert calls == [None]
        assert warnings == []

    def test_retries_once_with_bigger_budget_on_length_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int | None] = []

        def fake_call_llm(*, max_tokens: int | None, **kwargs) -> DocumentIndex:
            calls.append(max_tokens)
            if len(calls) == 1:
                raise RuntimeError("Could not parse response content as the length limit was reached")
            return _ok_result(**kwargs)

        monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)
        warnings: list[str] = []
        assert _call_llm_with_retry(**_retry_kwargs(None), warnings=warnings) is not None
        assert calls == [None, 32_000]
        assert "retrying with max_tokens=32000" in warnings[0]

    def test_non_length_limit_error_does_not_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int | None] = []

        def fake_call_llm(*, max_tokens: int | None, **kwargs) -> DocumentIndex:
            calls.append(max_tokens)
            raise ValueError("boom")

        monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)
        warnings: list[str] = []
        assert _call_llm_with_retry(**_retry_kwargs(None), warnings=warnings) is None
        assert calls == [None]
        assert "LLM call failed" in warnings[0]
