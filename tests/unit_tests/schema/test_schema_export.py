"""Tests for schema export to D3 JSON and HTML (schema_d3 / schema_html / ResolvedSchema rendering)."""

from __future__ import annotations

import json
from pathlib import Path

from genai_graph.kg.schema.core import GraphSchema
from genai_graph.kg.schema.schema_d3 import build_schema_d3_data
from genai_graph.kg.schema.schema_html import _d3_script_tag, generate_schema_html


class TestBuildSchemaD3Data:
    def test_structure_keys(self, simple_schema: GraphSchema) -> None:
        data = build_schema_d3_data(simple_schema)
        assert set(data.keys()) == {"meta", "nodes", "links", "vector_indexes"}

    def test_nodes_have_stable_ids_and_fields(self, simple_schema: GraphSchema) -> None:
        data = build_schema_d3_data(simple_schema)
        nodes = {n["id"]: n for n in data["nodes"]}
        assert {"Project", "Company", "Person"} <= set(nodes)

        project = nodes["Project"]
        assert project["primary_key"] == "title"
        assert project["name_from"] == "title"
        field_names = {f["name"] for f in project["fields"]}
        assert "title" in field_names
        assert "status" in field_names
        # 'client' is a relation target — excluded from node properties
        assert "client" not in field_names

    def test_links_reference_node_ids(self, simple_schema: GraphSchema) -> None:
        data = build_schema_d3_data(simple_schema)
        node_ids = {n["id"] for n in data["nodes"]}
        assert len(data["links"]) == 2
        for link in data["links"]:
            assert link["source"] in node_ids
            assert link["target"] in node_ids
        rel_names = {link["label"] for link in data["links"]}
        assert rel_names == {"FOR_CLIENT", "HAS_MEMBER"}

    def test_meta_records_graph_names(self, simple_schema: GraphSchema) -> None:
        data = build_schema_d3_data(simple_schema, graph_names=["g1", "g2"])
        assert data["meta"]["graphs"] == ["g1", "g2"]
        assert data["meta"]["root_model"] == "Project"

    def test_output_is_json_serializable(self, simple_schema: GraphSchema) -> None:
        data = build_schema_d3_data(simple_schema)
        json.dumps(data)  # must not raise


class TestGenerateSchemaHtml:
    def test_generates_html_with_embedded_data(self, simple_schema: GraphSchema) -> None:
        data = build_schema_d3_data(simple_schema)
        html = generate_schema_html(data)
        assert "<html" in html.lower()
        # The schema data JSON is embedded in the page
        assert "Project" in html
        assert "FOR_CLIENT" in html

    def test_writes_to_destination_file(self, simple_schema: GraphSchema, tmp_path: Path) -> None:
        data = build_schema_d3_data(simple_schema)
        out = tmp_path / "sub" / "schema.html"  # also exercises makedirs
        html = generate_schema_html(data, destination_file_path=str(out))
        assert out.exists()
        assert out.read_text(encoding="utf-8") == html

    def test_d3_script_tag_fallback_or_bundle(self) -> None:
        tag = _d3_script_tag()
        assert "<script" in tag
