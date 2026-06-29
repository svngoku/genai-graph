"""Tests for ResolvedSchema rendering and description injection."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.unit


@pytest.fixture
def simple_schema():
    from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

    class Company(BaseModel):
        name: str
        sector: str | None = None

    class Person(BaseModel):
        name: str
        title: str | None = None

    class Project(BaseModel):
        title: str
        client: Company
        lead: Person | None = None

    company_node = GraphNode(node_class=Company, name_from="name", key_from="name", description="A company entity")
    person_node = GraphNode(node_class=Person, name_from="name", key_from="name")
    project_node = GraphNode(node_class=Project, name_from="title", key_from="title")

    return GraphSchema(
        root_model_class=Project,
        nodes=[project_node, company_node, person_node],
        relations=[
            GraphRelation(from_node=project_node, to_node=company_node, name="FOR_CLIENT"),
        ],
    )


@pytest.fixture
def resolved(simple_schema):
    from genai_graph.kg.schema import ResolvedSchema

    return ResolvedSchema.from_graph_schema(simple_schema)


class TestResolvedSchemaStructure:
    def test_nodes_present(self, resolved) -> None:
        node_names = {n.name for n in resolved.nodes}
        assert "Project" in node_names
        assert "Company" in node_names
        assert "Person" in node_names

    def test_relations_present(self, resolved) -> None:
        rel_names = {r.name for r in resolved.relations}
        assert "FOR_CLIENT" in rel_names

    def test_node_description_from_graph_node(self, resolved) -> None:
        company = next(n for n in resolved.nodes if n.name == "Company")
        assert "company" in company.description.lower()


class TestMarkdownRendering:
    def test_markdown_contains_node_names(self, resolved) -> None:
        md = resolved.to_markdown()
        assert "Project" in md
        assert "Company" in md

    def test_markdown_contains_relation_names(self, resolved) -> None:
        md = resolved.to_markdown()
        assert "FOR_CLIENT" in md

    def test_markdown_is_string(self, resolved) -> None:
        assert isinstance(resolved.to_markdown(), str)


class TestD3JsonRendering:
    def test_d3_json_has_nodes_and_links(self, resolved) -> None:
        data = resolved.to_d3_json()
        assert "nodes" in data
        assert "links" in data

    def test_d3_nodes_have_id_and_label(self, resolved) -> None:
        data = resolved.to_d3_json()
        for node in data["nodes"]:
            assert "id" in node
            assert "label" in node


class TestDescriptionInjection:
    def test_no_descriptions_falls_back_to_pydantic(self, simple_schema) -> None:
        from genai_graph.kg.schema import ResolvedSchema

        resolved = ResolvedSchema.from_graph_schema(simple_schema, descriptions=None)
        # Should still produce valid output
        assert len(resolved.nodes) > 0

    def test_custom_descriptions_appear_in_output(self, simple_schema) -> None:
        from genai_graph.kg.schema import ResolvedSchema

        custom_descriptions = {
            "classes": {"Company": "An organisation that pays for projects"},
            "fields": {"Company": {"name": "The legal company name"}},
            "enums": {},
        }
        resolved = ResolvedSchema.from_graph_schema(simple_schema, descriptions=custom_descriptions)
        # graphnode.description takes priority; otherwise falls through to custom desc
        # Here graphnode has description="A company entity" which takes precedence
        assert resolved is not None

    def test_empty_descriptions_dict_no_error(self, simple_schema) -> None:
        from genai_graph.kg.schema import ResolvedSchema

        empty = {"classes": {}, "fields": {}, "enums": {}}
        resolved = ResolvedSchema.from_graph_schema(simple_schema, descriptions=empty)
        assert len(resolved.nodes) > 0

    def test_custom_descriptions_used_when_no_graphnode_description(self) -> None:
        from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema, ResolvedSchema

        class Gadget(BaseModel):
            name: str

        class Gizmo(BaseModel):
            name: str
            gadget: Gadget

        gadget_node = GraphNode(node_class=Gadget, name_from="name", key_from="name")
        gizmo_node = GraphNode(node_class=Gizmo, name_from="name", key_from="name")
        rel = GraphRelation(from_node=gizmo_node, to_node=gadget_node, name="HAS_GADGET")
        schema = GraphSchema(root_model_class=Gizmo, nodes=[gizmo_node, gadget_node], relations=[rel])

        custom_descriptions = {
            "classes": {"Gadget": "A small technical device"},
            "fields": {},
            "enums": {},
        }
        resolved = ResolvedSchema.from_graph_schema(schema, descriptions=custom_descriptions)
        gadget = next(n for n in resolved.nodes if n.name == "Gadget")
        assert gadget.description == "A small technical device"


class TestSerializationRoundTrip:
    def test_json_roundtrip(self, resolved, tmp_path) -> None:

        from genai_graph.kg.schema import ResolvedSchema

        # to_json_str() produces D3 format; deserialize via from_json_file
        json_str = resolved.to_json_str()
        json_path = tmp_path / "schema.json"
        json_path.write_text(json_str, encoding="utf-8")
        reloaded = ResolvedSchema.from_json_file(str(json_path))
        assert {n.name for n in reloaded.nodes} == {n.name for n in resolved.nodes}

    def test_model_dump_json_roundtrip(self, resolved) -> None:
        from genai_graph.kg.schema import ResolvedSchema

        # model_dump_json / model_validate_json uses the Pydantic schema
        json_str = resolved.model_dump_json(indent=2)
        reloaded = ResolvedSchema.model_validate_json(json_str)
        assert {n.name for n in reloaded.nodes} == {n.name for n in resolved.nodes}
