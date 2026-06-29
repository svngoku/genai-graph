"""Shared test fixtures for schema unit tests.

Generic Pydantic domain models for testing GraphNode/GraphRelation/GraphSchema
without any project-specific concepts.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Generic domain models — reused across all schema test modules
# ---------------------------------------------------------------------------


class Address(BaseModel):
    city: str
    country: str = "Unknown"


class Company(BaseModel):
    name: str
    industry: str | None = None
    headquarters: Address | None = None


class Person(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None


class Project(BaseModel):
    """Root model: a project owned by a company, with a team."""

    title: str
    status: str = "active"
    client: Company
    lead: Person | None = None
    team: list[Person] = Field(default_factory=list)


class Task(BaseModel):
    """A task within a project."""

    name: str
    assignee: Person | None = None
    done: bool = False


class ProjectWithTasks(BaseModel):
    """Root model with nested tasks."""

    title: str
    client: Company
    tasks: list[Task] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company_node():
    from genai_graph.kg.schema import GraphNode

    return GraphNode(node_class=Company, name_from="name", key_from="name")


@pytest.fixture
def person_node():
    from genai_graph.kg.schema import GraphNode

    return GraphNode(node_class=Person, name_from="name", key_from="name")


@pytest.fixture
def project_node():
    from genai_graph.kg.schema import GraphNode

    return GraphNode(node_class=Project, name_from="title", key_from="title")


@pytest.fixture
def simple_schema(project_node, company_node, person_node):
    """A minimal schema: Project → FOR_CLIENT → Company, Project → HAS_MEMBER → Person."""
    from genai_graph.kg.schema import GraphRelation, GraphSchema

    return GraphSchema(
        root_model_class=Project,
        nodes=[project_node, company_node, person_node],
        relations=[
            GraphRelation(from_node=project_node, to_node=company_node, name="FOR_CLIENT"),
            GraphRelation(from_node=project_node, to_node=person_node, name="HAS_MEMBER"),
        ],
    )
