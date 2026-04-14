"""Test improved error reporting for common KG creation issues."""

import pytest
from pydantic import BaseModel
from unittest.mock import MagicMock

from genai_graph.kg.schema import GraphNode


class SimpleNode(BaseModel):
    """Test node with specific fields."""

    name: str
    valid_field: str


def test_missing_key_field_error_message():
    """Test that missing key field shows available fields."""
    node = GraphNode(
        node_class=SimpleNode,
        name_from="name",
        key_from="missing_field",  # This field doesn't exist
        description="Test node",
    )

    data = {
        "name": "Test",
        "valid_field": "value",
        "extra_field1": "data1",
        "extra_field2": "data2",
    }

    with pytest.raises(ValueError) as exc_info:
        node.get_key_value(data, "SimpleNode")

    error_msg = str(exc_info.value)
    # Check that error message includes helpful information
    assert "Key field 'missing_field' not found or empty" in error_msg
    assert "Available fields:" in error_msg
    assert "name" in error_msg  # Should show available fields
    assert "key_from='AUTO_ID'" in error_msg  # Should suggest AUTO_ID


def test_missing_key_field_shows_field_preview():
    """Test that error message previews available fields."""
    node = GraphNode(
        node_class=SimpleNode,
        name_from="name",
        key_from="id",
        description="Test node",
    )

    # Create data with many fields (16 total: 15 + name)
    data = {f"field_{i}": f"value_{i}" for i in range(15)}
    data["name"] = "Test"

    with pytest.raises(ValueError) as exc_info:
        node.get_key_value(data, "SimpleNode")

    error_msg = str(exc_info.value)
    # Should show preview of fields (first 10)
    assert "Available fields:" in error_msg
    assert "(and 6 more)" in error_msg  # 16 total - 10 shown = 6 more


def test_auto_id_never_fails():
    """Test that AUTO_ID always generates a valid key."""
    node = GraphNode(
        node_class=SimpleNode,
        name_from="name",
        key_from="AUTO_ID",
        description="Test node",
    )

    # Even with empty data (except name), AUTO_ID should work
    data = {"name": "Test"}

    key = node.get_key_value(data, "SimpleNode")
    assert key  # Should generate a UUID
    assert len(key) == 36  # UUID format


def test_computed_key_empty_error():
    """Test that computed key shows helpful error when empty."""
    node = GraphNode(
        node_class=SimpleNode,
        name_from="name",
        key_from=lambda data, node_type: data.get("missing_field", ""),
        description="Test node",
    )

    data = {"name": "Test"}

    with pytest.raises(ValueError) as exc_info:
        node.get_key_value(data, "SimpleNode")

    error_msg = str(exc_info.value)
    assert "Computed key is empty" in error_msg


# ---------------------------------------------------------------------------
# merge_nodes_batch error-enhancement path
# ---------------------------------------------------------------------------


class _SchemaNode(BaseModel):
    id: str
    name: str
    score: float


class TestMergeNodesBatchErrorHandler:
    """Cover the 'Cannot find property' error-enhancement branch in merge_nodes_batch.

    This branch reads config.field_names; a regression would raise AttributeError
    instead of the expected RuntimeError from the DB call.
    """

    def _make_conn(self, side_effect: Exception) -> MagicMock:
        """Build a mock KgBackend whose inner .conn.execute raises side_effect."""
        inner = MagicMock()
        inner.execute.side_effect = side_effect
        conn = MagicMock()
        conn.conn = inner
        return conn

    def test_cannot_find_property_logs_schema_fields(self):
        """Error-enhancement path must not raise AttributeError on config.field_names.

        Regression test: before the fix, the except block accessed config.field_types
        (removed attribute) and raised AttributeError, masking the real DB error.
        """
        from genai_graph.kg.ingest.merge import (
            NodeDataCollection,
            NodeTypeConfig,
            NodeTypeRegistry,
            _build_node_arrow_schema,
            merge_nodes_batch,
        )

        schema = _build_node_arrow_schema(_SchemaNode, primary_key_field="id")
        config = NodeTypeConfig(node_type="_SchemaNode", primary_key_field="id", arrow_schema=schema)
        registry = NodeTypeRegistry()
        registry.register(config)

        nodes = NodeDataCollection()
        nodes.add("_SchemaNode", {"id": "n1", "name": "A", "score": 1.0})

        conn = self._make_conn(RuntimeError("Cannot find property bad_field in node _SchemaNode"))

        # Must re-raise the original RuntimeError, NOT an AttributeError
        with pytest.raises(RuntimeError, match="Cannot find property"):
            merge_nodes_batch(conn, nodes, registry)

    def test_generic_error_logged_without_attribute_error(self):
        """Generic (non-property) errors must propagate without AttributeError."""
        from genai_graph.kg.ingest.merge import (
            NodeDataCollection,
            NodeTypeConfig,
            NodeTypeRegistry,
            merge_nodes_batch,
        )

        config = NodeTypeConfig(node_type="_SchemaNode", primary_key_field="id")
        registry = NodeTypeRegistry()
        registry.register(config)

        nodes = NodeDataCollection()
        nodes.add("_SchemaNode", {"id": "n1", "name": "A", "score": 1.0})

        conn = self._make_conn(RuntimeError("Some unrelated DB failure"))

        with pytest.raises(RuntimeError, match="Some unrelated DB failure"):
            merge_nodes_batch(conn, nodes, registry)
