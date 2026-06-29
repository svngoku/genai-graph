"""Tests for node callable behavior (name_from, key_from callables)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.unit


class TestCallableNameFrom:
    def test_string_name_from_returns_field_value(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Widget(BaseModel):
            label: str

        node = GraphNode(node_class=Widget, name_from="label", key_from="AUTO_ID")
        data = {"label": "my-widget"}
        assert node.get_name_value(data, "Widget") == "my-widget"

    def test_callable_name_from_invoked_with_data_and_base(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Widget(BaseModel):
            a: str | None = None
            b: str | None = None

        node = GraphNode(
            node_class=Widget,
            name_from=lambda data, base: data.get("a") or data.get("b") or base,
            key_from="AUTO_ID",
        )
        assert node.get_name_value({"a": "alpha", "b": "beta"}, "Widget") == "alpha"
        assert node.get_name_value({"a": None, "b": "beta"}, "Widget") == "beta"
        assert node.get_name_value({"a": None, "b": None}, "Widget") == "Widget"


class TestCallableKeyFrom:
    def test_auto_id_generates_uuid(self) -> None:
        import uuid

        from genai_graph.kg.schema import GraphNode

        class Doc(BaseModel):
            title: str

        node = GraphNode(node_class=Doc, name_from="title", key_from="AUTO_ID")
        key = node.get_key_value({"title": "hello"}, "Doc")
        # Should be a valid UUID4
        uuid.UUID(key, version=4)

    def test_field_key_from_returns_field_value(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Product(BaseModel):
            sku: str
            name: str

        node = GraphNode(node_class=Product, name_from="name", key_from="sku")
        assert node.get_key_value({"sku": "PROD-001", "name": "Widget"}, "Product") == "PROD-001"

    def test_callable_key_from_returning_none_returns_none(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Item(BaseModel):
            code: str | None = None

        node = GraphNode(
            node_class=Item,
            name_from="code",
            key_from=lambda data, base: data.get("code"),
        )
        # When callable returns None, get_key_value should propagate None
        result = node.get_key_value({"code": None}, "Item")
        assert result is None

    def test_callable_key_from_with_value(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Item(BaseModel):
            code: str

        node = GraphNode(
            node_class=Item,
            name_from="code",
            key_from=lambda data, base: f"{base}::{data.get('code', '')}",
        )
        result = node.get_key_value({"code": "X1"}, "Item")
        assert result == "Item::X1"


class TestIndexFieldSpecs:
    def test_string_index_field_normalised(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Doc(BaseModel):
            content: str

        node = GraphNode(
            node_class=Doc,
            name_from="content",
            key_from="AUTO_ID",
            index_fields=["content"],
        )
        specs = node.index_field_specs
        assert len(specs) == 1
        field, model = specs[0]
        assert field == "content"
        assert model is None

    def test_tuple_index_field_preserves_model(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Doc(BaseModel):
            content: str

        node = GraphNode(
            node_class=Doc,
            name_from="content",
            key_from="AUTO_ID",
            index_fields=[("content", "ada_002@openai")],
        )
        specs = node.index_field_specs
        field, model = specs[0]
        assert field == "content"
        assert model == "ada_002@openai"

    def test_compute_embeddings_true_when_index_fields_set(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Doc(BaseModel):
            content: str

        node = GraphNode(
            node_class=Doc,
            name_from="content",
            key_from="AUTO_ID",
            index_fields=["content"],
        )
        assert node.compute_embeddings is True

    def test_compute_embeddings_false_without_index_fields(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Doc(BaseModel):
            content: str

        node = GraphNode(node_class=Doc, name_from="content", key_from="AUTO_ID")
        assert node.compute_embeddings is False
