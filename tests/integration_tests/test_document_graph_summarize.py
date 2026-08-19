"""Integration tests for Document Graph summarization.

Ingests a small Markdown corpus into a throwaway Ladybug database (no mocks for
the graph itself), then exercises `summarize_document`/`summarize_graph` with
the LLM call boundary (`_call_llm`) monkeypatched — the only true external
system dependency in this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.document_graph.ingest import ingest_document_graph
from genai_graph.kg.document_graph.summarize import (
    DocumentIndex,
    SectionEntry,
    SummarizationConfig,
    summarize_document,
    summarize_graph,
)
from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory
from genai_graph.kg.query.document_graph_tools import (
    build_toc_tree,
    document_toc_yaml,
    folder_toc_yaml,
    get_document,
    get_document_toc,
)

DOC_ALPHA = """# Alpha Guide

Intro paragraph for alpha.

## Installation

{filler}

### Advanced install

Extra flags.

## Usage

Run the CLI.
"""

DOC_BETA = """# Beta Manual

Beta intro text about connectors.

## Configuration

Set the knobs.
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
        document_description=f"Description of {filename}.",
        document_summary=f"Summary of {filename}.",
        sections=[
            SectionEntry(
                section_id=p.section_id,
                description=f"Description of {p.section_id}.",
                summary=f"Summary of {p.section_id}." if p.needs_summary else None,
            )
            for p in plans
        ],
    )


@pytest.fixture
def md_corpus(tmp_path: Path) -> Path:
    (tmp_path / "alpha.md").write_text(DOC_ALPHA.format(filler="Install with pip. " * 60), encoding="utf-8")
    (tmp_path / "beta.md").write_text(DOC_BETA, encoding="utf-8")
    return tmp_path


@pytest.fixture
def ingested_backend(graph_backend: KuzuBackend, md_corpus: Path) -> KuzuBackend:
    ingest_document_graph(graph_backend, DocumentGraphFactory(sources=[str(md_corpus)]))
    return graph_backend


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("genai_graph.kg.document_graph.summarize._call_llm", _fake_call_llm)


@pytest.mark.integration
@pytest.mark.usefixtures("fake_llm")
class TestSummarizeDocument:
    def test_every_section_gets_a_description(self, ingested_backend: KuzuBackend) -> None:
        result = summarize_document(ingested_backend, "alpha.md")

        assert result.llm_calls == 1
        assert result.sections_described >= 3

        rows = get_document_toc(ingested_backend, "alpha.md")
        described = [r for r in rows if int(r["level"]) > 0]  # type: ignore[union-attr]
        assert all(r["description"] for r in described)
        assert all(r["summary_source"] == "llm" for r in described)

    def test_no_section_summary_holds_raw_markdown(self, ingested_backend: KuzuBackend) -> None:
        """The old implementation dumped raw section text into `summary`; it must not come back."""
        summarize_document(ingested_backend, "alpha.md")

        for row in get_document_toc(ingested_backend, "alpha.md"):  # type: ignore[union-attr]
            for field in ("description", "summary"):
                value = row.get(field) or ""
                assert "#" not in value
                assert "\n" not in value

    def test_short_sections_get_no_summary(self, ingested_backend: KuzuBackend) -> None:
        summarize_document(ingested_backend, "beta.md", SummarizationConfig(summary_min_tokens=100_000))

        rows = get_document_toc(ingested_backend, "beta.md")
        described = [r for r in rows if int(r["level"]) > 0]  # type: ignore[union-attr]
        assert described
        assert all(r["summary"] is None for r in described)
        assert all(r["description"] for r in described)

    def test_large_sections_also_get_a_summary(self, ingested_backend: KuzuBackend) -> None:
        summarize_document(ingested_backend, "alpha.md", SummarizationConfig(summary_min_tokens=1))

        rows = get_document_toc(ingested_backend, "alpha.md")
        described = [r for r in rows if int(r["level"]) > 0]  # type: ignore[union-attr]
        assert all(r["summary"] for r in described)

    def test_document_description_and_summary_are_set(self, ingested_backend: KuzuBackend) -> None:
        summarize_document(ingested_backend, "alpha.md")

        doc = get_document(ingested_backend, "alpha.md")
        assert doc is not None
        assert doc["description"] == "Description of alpha.md."
        assert doc["summary"] == "Summary of alpha.md."

    def test_already_summarized_is_skipped_without_force(self, ingested_backend: KuzuBackend) -> None:
        assert not summarize_document(ingested_backend, "alpha.md").already_summarized
        second = summarize_document(ingested_backend, "alpha.md")
        assert second.already_summarized
        assert second.llm_calls == 0

    def test_force_resummarizes(self, ingested_backend: KuzuBackend) -> None:
        summarize_document(ingested_backend, "alpha.md")
        forced = summarize_document(ingested_backend, "alpha.md", force=True)
        assert not forced.already_summarized
        assert forced.llm_calls == 1

    def test_dry_run_makes_no_writes(self, ingested_backend: KuzuBackend) -> None:
        result = summarize_document(ingested_backend, "alpha.md", dry_run=True)
        assert result.sections_described > 0

        doc = get_document(ingested_backend, "alpha.md")
        assert doc is not None
        assert doc["description"] is None


@pytest.mark.integration
@pytest.mark.usefixtures("fake_llm")
class TestSummarizeGraph:
    def test_summarizes_every_document_then_skips_on_rerun(self, ingested_backend: KuzuBackend) -> None:
        result = summarize_graph(ingested_backend)
        assert result.documents_processed == 2
        assert result.documents_failed == 0

        assert summarize_graph(ingested_backend).documents_skipped == 2


@pytest.mark.integration
class TestTocYaml:
    def test_build_toc_tree_nests_by_parent(self, ingested_backend: KuzuBackend) -> None:
        tree = build_toc_tree(get_document_toc(ingested_backend, "alpha.md"))  # type: ignore[arg-type]

        assert {n["title"] for n in tree} == {"Alpha Guide"}
        alpha = tree[0]
        assert {c["title"] for c in alpha["sections"]} == {"Installation", "Usage"}
        installation = next(c for c in alpha["sections"] if c["title"] == "Installation")
        assert any(gc["title"] == "Advanced install" for gc in installation["sections"])

    def test_max_level_prunes_deep_sections(self, ingested_backend: KuzuBackend) -> None:
        tree = build_toc_tree(get_document_toc(ingested_backend, "alpha.md"), max_level=2)  # type: ignore[arg-type]
        installation = next(c for c in tree[0]["sections"] if c["title"] == "Installation")
        assert "sections" not in installation  # the level-3 child is pruned

    def test_document_toc_yaml_includes_descriptions(self, ingested_backend: KuzuBackend, fake_llm: None) -> None:
        summarize_document(ingested_backend, "alpha.md")
        payload = yaml.safe_load(document_toc_yaml(ingested_backend, "alpha.md"))

        assert payload["document"] == "alpha.md"
        assert payload["description"] == "Description of alpha.md."
        assert payload["sections"][0]["description"].startswith("Description of ")

    def test_document_toc_yaml_omits_summaries_by_default(self, ingested_backend: KuzuBackend, fake_llm: None) -> None:
        summarize_document(ingested_backend, "alpha.md", SummarizationConfig(summary_min_tokens=1))

        default = yaml.safe_load(document_toc_yaml(ingested_backend, "alpha.md"))
        assert "summary" not in default["sections"][0]

        verbose = yaml.safe_load(document_toc_yaml(ingested_backend, "alpha.md", include_summaries=True))
        assert verbose["sections"][0]["summary"].startswith("Summary of ")

    def test_folder_toc_yaml_lists_documents_without_sections(
        self, ingested_backend: KuzuBackend, fake_llm: None
    ) -> None:
        summarize_graph(ingested_backend)
        payload = yaml.safe_load(folder_toc_yaml(ingested_backend, None))

        assert {d["name"] for d in payload["documents"]} == {"alpha.md", "beta.md"}
        for entry in payload["documents"]:
            assert "toc" not in entry  # orientation view: no sections inlined
            assert entry["description"].startswith("Description of ")

    def test_folder_toc_yaml_can_inline_sections(self, ingested_backend: KuzuBackend, fake_llm: None) -> None:
        summarize_graph(ingested_backend)
        payload = yaml.safe_load(folder_toc_yaml(ingested_backend, None, include_sections=True))
        assert all("toc" in entry for entry in payload["documents"])

    def test_folder_toc_is_much_smaller_than_the_sectioned_view(
        self, ingested_backend: KuzuBackend, fake_llm: None
    ) -> None:
        summarize_graph(ingested_backend)
        compact = folder_toc_yaml(ingested_backend, None)
        full = folder_toc_yaml(ingested_backend, None, include_sections=True, include_summaries=True)
        assert len(compact) < len(full) / 2
