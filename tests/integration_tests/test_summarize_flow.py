"""Smoke test for the `document_graph_summarize` Prefect flow / workflow step."""

from __future__ import annotations

from pathlib import Path

import pytest

from genai_graph.kg.document_graph.ingest import ingest_document_graph
from genai_graph.kg.document_graph.summarize import DocumentIndex, SectionEntry, SummarizationConfig
from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory
from genai_graph.orchestration.summarize_flow import summarize_document_graph_flow

DOC = """# Guide

## Setup

{filler}
"""


def _fake_call_llm(
    *,
    llm_id: str,
    filename: str,
    toc_outline: str,
    annotated_document: str,
    plans: list,
    config: SummarizationConfig,
    max_tokens: int | None = None,
) -> DocumentIndex:
    return DocumentIndex(
        document_description="Fake description.",
        document_summary="Fake abstract.",
        sections=[SectionEntry(section_id=p.section_id, description=f"Description of {p.section_id}.") for p in plans],
    )


@pytest.mark.integration
def test_summarize_flow_end_to_end(temp_db_path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "guide.md").write_text(DOC.format(filler="Setup instructions. " * 60), encoding="utf-8")

    from genai_graph.kg.backend import KuzuBackend

    backend = KuzuBackend()
    backend.connect(temp_db_path)
    ingest_document_graph(backend, DocumentGraphFactory(sources=[str(tmp_path)]))

    monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", _fake_call_llm)

    result = summarize_document_graph_flow(temp_db_path)

    assert result["documents_processed"] == 1
    assert result["documents_failed"] == 0
    assert result["total_llm_calls"] >= 1
