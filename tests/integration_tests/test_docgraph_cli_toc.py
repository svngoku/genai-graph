"""End-to-end test of `cli docgraph toc` (Typer CLI layer).

Regression test for a real bug: `toc <folder> --yaml` silently ignored `--yaml`
and printed the same plain listing as without it, because the folder branch of
the command returned before ever checking the flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from genai_graph.core.commands_docgraph import DocGraphCommands
from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.document_graph.ingest import ingest_document_graph
from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory

DOC = """# Guide A

## Setup

Setup instructions.
"""


def _cli_app() -> typer.Typer:
    app = typer.Typer()
    DocGraphCommands().register(app)
    return app


@pytest.fixture
def ingested_db(temp_db_path: str, tmp_path: Path) -> str:
    (tmp_path / "a.md").write_text(DOC, encoding="utf-8")
    backend = KuzuBackend()
    backend.connect(temp_db_path)
    ingest_document_graph(backend, DocumentGraphFactory(sources=[str(tmp_path)]))
    return temp_db_path


def _folder_id(db_path: str) -> str:
    backend = KuzuBackend()
    backend.connect(db_path)
    df = backend.execute_get_as_df("MATCH (f:Folder) RETURN f.folder_id AS id LIMIT 1", union=False)
    return str(df["id"].iloc[0])


@pytest.mark.integration
class TestTocCli:
    def test_toc_folder_without_yaml_prints_plain_listing(self, ingested_db: str) -> None:
        folder_id = _folder_id(ingested_db)
        runner = CliRunner()
        result = runner.invoke(_cli_app(), ["docgraph", "toc", folder_id, "--db", ingested_db])

        assert result.exit_code == 0, result.output
        assert "a.md" in result.output
        assert "documents:" not in result.output  # not YAML

    def test_toc_folder_with_yaml_returns_nested_sections(self, ingested_db: str) -> None:
        folder_id = _folder_id(ingested_db)
        runner = CliRunner()
        result = runner.invoke(_cli_app(), ["docgraph", "toc", folder_id, "--db", ingested_db, "--yaml"])

        assert result.exit_code == 0, result.output
        payload = yaml.safe_load(result.output)
        assert payload["documents"][0]["name"] == "a.md"
        titles = {s["title"] for s in payload["documents"][0]["toc"]}
        assert "Guide A" in titles

    def test_toc_document_with_yaml_still_works(self, ingested_db: str) -> None:
        runner = CliRunner()
        result = runner.invoke(_cli_app(), ["docgraph", "toc", "a.md", "--db", ingested_db, "--yaml"])

        assert result.exit_code == 0, result.output
        payload = yaml.safe_load(result.output)
        assert payload["document"] == "a.md"

    def test_toc_document_without_yaml_still_works(self, ingested_db: str) -> None:
        runner = CliRunner()
        result = runner.invoke(_cli_app(), ["docgraph", "toc", "a.md", "--db", ingested_db])

        assert result.exit_code == 0, result.output
        assert "Guide A" in result.output
        assert "document:" not in result.output  # not YAML
