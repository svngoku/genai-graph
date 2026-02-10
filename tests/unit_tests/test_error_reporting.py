"""Test improved error reporting for common KG creation issues."""

import pytest
from pydantic import BaseModel

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
