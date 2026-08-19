"""Unit tests for the data lineage helpers in genai_graph.kg.ingest.lineage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from genai_graph.kg.factories.markdown_baml_factory import MarkdownBamlFactory
from genai_graph.kg.ingest.lineage import (
    _build_lineage_for_json,
    _build_lineage_for_markdown_factory,
    _detect_baml_version_mismatch,
    _resolve_related_path,
)
from genai_graph.kg.schema.core import GraphSchema


class TestDetectBamlVersionMismatch:
    def test_version_mismatch_with_numbers(self) -> None:
        # Message shape produced by baml_py.safe_import.raise_if_incompatible_version
        exc = ImportError(
            "baml-py is likely out of date.\n"
            "Version of baml_client generator (see generators.baml): 0.219.0\n"
            "Current version of baml-py: 0.220.0"
        )
        hint = _detect_baml_version_mismatch(exc)
        assert hint is not None
        assert "0.219.0" in hint
        assert "0.220.0" in hint
        assert "baml-cli generate" in hint

    def test_generic_baml_version_error(self) -> None:
        hint = _detect_baml_version_mismatch(RuntimeError("BAML client out of date"))
        assert hint is not None
        assert "baml-cli generate" in hint

    def test_unrelated_error_returns_none(self) -> None:
        assert _detect_baml_version_mismatch(ValueError("connection refused")) is None


class TestResolveRelatedPath:
    def test_resolves_markdown_from_manifest(self, tmp_path: Path) -> None:
        md_file = tmp_path / "report.md"
        md_file.write_text("# Report")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"source": str(md_file)}))

        json_file = tmp_path / "report.json"
        result = _resolve_related_path(manifest, json_file, exts=(".md", ".markdown"))
        assert result == md_file

    def test_prefers_candidate_matching_target_stem(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.md").write_text("a")
        (tmp_path / "beta.md").write_text("b")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"files": [str(tmp_path / "alpha.md"), str(tmp_path / "beta.md")]}))

        result = _resolve_related_path(manifest, tmp_path / "beta.json", exts=(".md",))
        assert result is not None
        assert result.name == "beta.md"

    def test_fallback_to_any_matching_extension(self, tmp_path: Path) -> None:
        (tmp_path / "unrelated.md").write_text("x")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"doc": str(tmp_path / "unrelated.md")}))

        result = _resolve_related_path(manifest, tmp_path / "target.json", exts=(".md",))
        assert result is not None
        assert result.name == "unrelated.md"

    def test_missing_manifest_returns_none(self, tmp_path: Path) -> None:
        assert _resolve_related_path(tmp_path / "nope.json", tmp_path / "x.json", exts=(".md",)) is None

    def test_skips_urls(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"doc": "https://example.com/file.md"}))
        assert _resolve_related_path(manifest, tmp_path / "x.json", exts=(".md",)) is None

    def test_relative_paths_resolved_against_manifest_dir(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("d")
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"doc": "doc.md"}))

        result = _resolve_related_path(manifest, tmp_path / "doc.json", exts=(".md",))
        assert result == tmp_path / "doc.md"

    def test_nested_structures_scanned(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"a": {"b": [{"c": "deep.md"}]}}))
        result = _resolve_related_path(manifest, tmp_path / "deep.json", exts=(".md",))
        assert result == tmp_path / "deep.md"


class TestBuildLineageForJson:
    def test_full_chain_json_to_md_to_pdf(self, tmp_path: Path) -> None:
        # Setup: json dir with manifest pointing to md; md dir with manifest pointing to pdf
        json_dir = tmp_path / "json"
        md_dir = tmp_path / "md"
        pdf_dir = tmp_path / "pdf"
        for d in (json_dir, md_dir, pdf_dir):
            d.mkdir()

        pdf = pdf_dir / "report.pdf"
        pdf.write_bytes(b"%PDF")
        md = md_dir / "report.md"
        md.write_text("# Report")
        js = json_dir / "report.json"
        js.write_text("{}")

        (json_dir / "manifest.json").write_text(json.dumps({"source": str(md)}))
        (md_dir / "manifest.json").write_text(json.dumps({"source": str(pdf)}))

        lineage = _build_lineage_for_json("prof", "sub:Factory", js)
        assert lineage is not None
        assert lineage.markdown_path == md
        assert lineage.source_path == pdf
        assert lineage.json_files[0].path == js
        assert lineage.json_files[0].subgraph == "sub:Factory"

    def test_no_manifest_no_config_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Without manifest and with no matching paths config, no lineage can be built
        import genai_graph.kg.ingest.lineage as lineage_mod

        class _FakeCfg:
            def get_dict(self, key: str):
                return {}

        monkeypatch.setattr("genai_tk.config_mgmt.config_mngr.global_config", lambda: _FakeCfg())
        js = tmp_path / "orphan.json"
        js.write_text("{}")
        assert lineage_mod._build_lineage_for_json("prof", "sub", js) is None


class _DummyModel(BaseModel):
    name: str = "dummy"


class _DummyMarkdownFactory(MarkdownBamlFactory):
    def extract_from_markdown(self, md_text: str) -> BaseModel:
        return _DummyModel()

    def build_schema(self) -> GraphSchema:
        return GraphSchema(nodes=[], relations=[], root_model_class=_DummyModel)


class TestBuildLineageForMarkdownFactory:
    def test_resolves_cached_json_and_source(self, tmp_path: Path) -> None:
        md_dir = tmp_path / "md"
        json_dir = tmp_path / "json"
        pdf_dir = tmp_path / "pdf"
        for d in (md_dir, json_dir, pdf_dir):
            d.mkdir()

        pdf = pdf_dir / "doc.pdf"
        pdf.write_bytes(b"%PDF")
        md = md_dir / "doc.md"
        md.write_text("# Doc")
        (md_dir / "manifest.json").write_text(json.dumps({"source": str(pdf)}))

        cache_path = json_dir / "_DummyModel" / "doc.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("{}")

        factory = _DummyMarkdownFactory(md_root=str(md_dir), json_cache_root=str(json_dir))

        lineages = _build_lineage_for_markdown_factory("prof", "sub:Factory", factory)

        assert len(lineages) == 1
        lineage = lineages[0]
        assert lineage.markdown_path == md
        assert lineage.source_path == pdf
        assert lineage.json_files[0].path == cache_path
        assert lineage.json_files[0].subgraph == "sub:Factory"

    def test_no_cached_json_yields_empty_json_files(self, tmp_path: Path) -> None:
        md_dir = tmp_path / "md"
        md_dir.mkdir()
        md = md_dir / "doc.md"
        md.write_text("# Doc")

        factory = _DummyMarkdownFactory(md_root=str(md_dir), json_cache_root=None)

        lineages = _build_lineage_for_markdown_factory("prof", "sub", factory)

        assert len(lineages) == 1
        assert lineages[0].markdown_path == md
        assert lineages[0].json_files == []

    def test_markdown_without_pdf(self, tmp_path: Path) -> None:
        md_dir = tmp_path / "md"
        md_dir.mkdir()
        md = md_dir / "doc.md"
        md.write_text("# Doc")
        js = tmp_path / "doc.json"
        js.write_text("{}")
        (tmp_path / "manifest.json").write_text(json.dumps({"source": str(md)}))

        lineage = _build_lineage_for_json("prof", "sub", js)
        assert lineage is not None
        assert lineage.markdown_path == md
        assert lineage.source_path is None  # md dir has no manifest
