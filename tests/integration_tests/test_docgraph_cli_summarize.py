"""End-to-end tests of `cli docgraph summarize` (Typer CLI layer).

`_call_llm` is monkeypatched — the only true external dependency — so the tests
exercise the full CLI parsing → config → summarize_graph path, including the
automatic retry after a reasoning model exhausts its completion budget
(`openai.LengthFinishReasonError`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from genai_graph.core.commands_docgraph import DocGraphCommands
from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.document_graph.ingest import ingest_document_graph
from genai_graph.kg.document_graph.summarize import DocumentIndex, SectionEntry
from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory

DOC = """# Guide

## Setup

{filler}
"""


def _cli_app() -> typer.Typer:
    app = typer.Typer()
    DocGraphCommands().register(app)
    return app


@pytest.fixture
def ingested_db(temp_db_path: str, tmp_path: Path) -> str:
    (tmp_path / "guide.md").write_text(DOC.format(filler="Setup instructions. " * 60), encoding="utf-8")
    backend = KuzuBackend()
    backend.connect(temp_db_path)
    ingest_document_graph(backend, DocumentGraphFactory(sources=[str(tmp_path)]))
    # The CLI opens its own Ladybug Database on this path; close the ingest
    # backend so only one Database object holds the file during the run.
    backend.close()
    return temp_db_path


@pytest.fixture
def ingested_two_doc_db(temp_db_path: str, tmp_path: Path) -> str:
    (tmp_path / "alpha.md").write_text(DOC.format(filler="Alpha setup notes. " * 60), encoding="utf-8")
    (tmp_path / "beta.md").write_text("# Beta\n\n## Overview\n\nShort beta doc.\n", encoding="utf-8")
    backend = KuzuBackend()
    backend.connect(temp_db_path)
    ingest_document_graph(backend, DocumentGraphFactory(sources=[str(tmp_path)]))
    backend.close()
    return temp_db_path


def _index_for(plans: list) -> DocumentIndex:
    return DocumentIndex(
        document_description="Fake description.",
        document_summary="Fake abstract.",
        sections=[SectionEntry(section_id=p.section_id, description=f"Description of {p.section_id}.") for p in plans],
    )


@pytest.mark.integration
def test_summarize_cli_recovers_from_reasoning_budget_exhaustion(
    ingested_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int | None] = []

    def fake_call_llm(*, max_tokens: int | None, **kwargs) -> DocumentIndex:
        calls.append(max_tokens)
        if len(calls) == 1:
            # The failure mode reported against rfq_pricing: reasoning tokens consumed
            # the whole completion budget before any answer was produced.
            raise RuntimeError(
                "Could not parse response content as the length limit was reached - "
                "CompletionUsage(completion_tokens=8192, reasoning_tokens=8192)"
            )
        return _index_for(kwargs["plans"])

    monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)

    result = CliRunner().invoke(_cli_app(), ["docgraph", "summarize", "--db", ingested_db])

    assert result.exit_code == 0, result.output
    assert "hit the completion token limit" in result.output
    assert "retrying with max_tokens=32000" in result.output
    assert "LLM call failed" not in result.output
    assert calls == [None, 32_000]


@pytest.mark.integration
def test_summarize_cli_llm_max_tokens_option_is_threaded_through(
    ingested_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int | None] = []

    def fake_call_llm(*, max_tokens: int | None, **kwargs) -> DocumentIndex:
        seen.append(max_tokens)
        return _index_for(kwargs["plans"])

    monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)

    result = CliRunner().invoke(_cli_app(), ["docgraph", "summarize", "--db", ingested_db, "--llm-max-tokens", "50000"])

    assert result.exit_code == 0, result.output
    assert seen == [50_000]


@pytest.mark.integration
def test_summarize_cli_multiple_documents_run_in_parallel(
    ingested_two_doc_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_call_llm(*, plans: list, **kwargs) -> DocumentIndex:
        return _index_for(plans)

    monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)

    result = CliRunner().invoke(
        _cli_app(),
        [
            "docgraph",
            "summarize",
            "--db",
            ingested_two_doc_db,
            "--document",
            "alpha.md",
            "--document",
            "beta.md",
            "--workers",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Processed" in result.output
    # Both documents summarized with no failures or warnings.
    assert "⚠" not in result.output


@pytest.mark.integration
def test_summarize_cli_workers_option_is_accepted(ingested_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_call_llm(*, plans: list, **kwargs) -> DocumentIndex:
        return _index_for(plans)

    monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", fake_call_llm)

    result = CliRunner().invoke(_cli_app(), ["docgraph", "summarize", "--db", ingested_db, "--workers", "3"])

    assert result.exit_code == 0, result.output
