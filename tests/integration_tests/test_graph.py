#!/usr/bin/env python3
"""Integration test / demo for KG creation from Pydantic models.

Demonstrates the complete workflow:
  - Define generic Pydantic domain models
  - Declare a GraphSchema (auto-deduces field paths, excluded fields)
  - Create a Ladybug graph, insert records, run Cypher queries

Run directly:
    uv run python tests/integration_tests/test_graph.py
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field
from rich.console import Console

from genai_graph.kg.ingest import create_graph, restart_database
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

console = Console()


# ---------------------------------------------------------------------------
# Generic domain models (no project-specific concepts)
# ---------------------------------------------------------------------------


class Address(BaseModel):
    city: str
    country: str = "Unknown"


class Company(BaseModel):
    name: str
    industry: str | None = None
    location: Address | None = None


class Person(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None


class Risk(BaseModel):
    description: str
    impact: str = "medium"


class TechStack(BaseModel):
    architecture: str
    languages: list[str] = Field(default_factory=list)


class Project(BaseModel):
    """Root model: a project review with company, team, risks, and tech."""

    title: str
    status: str = "active"
    client: Company
    team: list[Person] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    tech: TechStack | None = None


# ---------------------------------------------------------------------------
# Test fixture data
# ---------------------------------------------------------------------------

SAMPLE_PROJECT = Project(
    title="Cloud Migration Alpha",
    status="in-progress",
    client=Company(name="Acme Corp", industry="Retail", location=Address(city="Paris", country="France")),
    team=[
        Person(name="Alice Martin", role="Lead", email="alice@example.com"),
        Person(name="Bob Chen", role="Engineer", email="bob@example.com"),
    ],
    risks=[
        Risk(description="Data loss during migration", impact="high"),
        Risk(description="Timeline overrun", impact="medium"),
    ],
    tech=TechStack(architecture="Kubernetes + Kafka", languages=["Python", "Go"]),
)


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------


def build_schema() -> GraphSchema:
    project_node = GraphNode(node_class=Project, name_from="title", key_from="title")
    company_node = GraphNode(node_class=Company, name_from="name", key_from="name")
    person_node = GraphNode(node_class=Person, name_from="name", key_from="name")
    risk_node = GraphNode(node_class=Risk, name_from="description", key_from="AUTO_ID")
    tech_node = GraphNode(node_class=TechStack, name_from="architecture", key_from="AUTO_ID")

    nodes = [project_node, company_node, person_node, risk_node, tech_node]

    relations = [
        GraphRelation(from_node=project_node, to_node=company_node, name="FOR_CLIENT"),
        GraphRelation(from_node=project_node, to_node=person_node, name="HAS_MEMBER"),
        GraphRelation(from_node=company_node, to_node=person_node, name="HAS_CONTACT"),
        GraphRelation(from_node=project_node, to_node=risk_node, name="HAS_RISK"),
        GraphRelation(from_node=project_node, to_node=tech_node, name="USES_TECH"),
    ]

    return GraphSchema(root_model_class=Project, nodes=nodes, relations=relations)


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------


@pytest.fixture
def schema() -> GraphSchema:
    return build_schema()


@pytest.mark.integration
class TestSchemaDefinition:
    def test_schema_has_expected_nodes(self, schema: GraphSchema) -> None:
        labels = {n.label for n in schema.nodes}
        assert "Project" in labels
        assert "Company" in labels
        assert "Person" in labels
        assert "Risk" in labels
        assert "TechStack" in labels

    def test_schema_has_expected_relations(self, schema: GraphSchema) -> None:
        rel_names = {r.name for r in schema.relations}
        assert "FOR_CLIENT" in rel_names
        assert "HAS_MEMBER" in rel_names
        assert "HAS_RISK" in rel_names
        assert "USES_TECH" in rel_names

    def test_no_schema_warnings(self, schema: GraphSchema) -> None:
        warnings = schema.get_warnings()
        assert not warnings, f"Unexpected schema warnings: {warnings}"

    def test_field_paths_deduced(self, schema: GraphSchema) -> None:
        company_node = next(n for n in schema.nodes if n.node_class is Company)
        # Company reachable from Project.client
        assert any("client" in p for p in company_node.field_paths)

    def test_client_excluded_from_project_fields(self, schema: GraphSchema) -> None:
        project_node = next(n for n in schema.nodes if n.node_class is Project)
        # 'client' field is a relationship target — must be excluded from node properties
        assert "client" in project_node.excluded_fields


# ---------------------------------------------------------------------------
# Runnable demo
# ---------------------------------------------------------------------------


def main() -> None:
    import tempfile

    console.print("[bold magenta]KG Demo — Generic domain models[/bold magenta]")

    schema = build_schema()
    console.print(f"Schema: {len(schema.nodes)} nodes, {len(schema.relations)} relations")
    schema.print_schema_summary()

    with tempfile.TemporaryDirectory() as tmp:
        from genai_graph.kg.backend import create_backend_from_config

        db_path = f"{tmp}/demo.lbug"
        backend = create_backend_from_config("default", db_path=db_path)
        restart_database(backend)
        create_graph(backend, SAMPLE_PROJECT, schema)

        for label in ["Project", "Company", "Person", "Risk", "TechStack"]:
            df = backend.execute(f"MATCH (n:{label}) RETURN count(n) AS cnt").get_as_df()
            console.print(f"  {label}: {df['cnt'].iloc[0]}")

    console.print("[green]Done.[/green]")


if __name__ == "__main__":
    main()
