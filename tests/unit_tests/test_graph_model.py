"""Unit tests for the shared graph-data model helpers in export/_graph_model.py."""

from __future__ import annotations

from genai_graph.kg.export._graph_model import (
    _get_node_color,
    _get_node_display_name,
    _get_node_raw_name,
    _normalize_graph_obj,
    _serialize_kuzu_id,
)


class TestGetNodeRawName:
    def test_name_field_wins(self) -> None:
        assert _get_node_raw_name({"name": "  Alice  ", "title": "Dr."}, "Person") == "Alice"

    def test_common_name_fields_fallback_order(self) -> None:
        assert _get_node_raw_name({"title": "T", "description": "D"}, "T") == "T"
        assert _get_node_raw_name({"description": "D", "label": "L"}, "X") == "D"
        assert _get_node_raw_name({"label": "L", "id": "42"}, "X") == "L"
        assert _get_node_raw_name({"id": "42"}, "X") == "42"

    def test_first_non_empty_string_field(self) -> None:
        assert _get_node_raw_name({"foo": "bar"}, "X") == "bar"

    def test_fallback_to_node_type(self) -> None:
        assert _get_node_raw_name({"count": 3}, "Widget") == "Widget"
        assert _get_node_raw_name({}, "Widget") == "Widget"

    def test_none_name_skipped(self) -> None:
        assert _get_node_raw_name({"name": None, "title": "T"}, "X") == "T"

    def test_technical_keys_not_used_as_fallback(self) -> None:
        # 'type' and 'id' must not be picked by the "any string field" fallback
        assert _get_node_raw_name({"type": "internal", "id": "9"}, "X") == "9"


class TestGetNodeDisplayName:
    def test_short_name_unchanged(self) -> None:
        assert _get_node_display_name({"name": "short"}, "X") == "short"

    def test_long_name_truncated(self) -> None:
        long_name = "a" * 50
        result = _get_node_display_name({"name": long_name}, "X", max_length=30)
        assert result == "a" * 30 + "..."
        assert len(result) == 33


class TestGetNodeColor:
    def test_deterministic(self) -> None:
        assert _get_node_color("Person") == _get_node_color("Person")

    def test_hex_format(self) -> None:
        color = _get_node_color("Person")
        assert color.startswith("#")
        assert len(color) == 7
        int(color[1:], 16)  # valid hex

    def test_custom_colors_override(self) -> None:
        assert _get_node_color("Person", {"Person": "#ff0000"}) == "#ff0000"

    def test_minimum_brightness(self) -> None:
        # Any generated color must have each channel >= 100 after brightening
        for node_type in ["A", "B", "SomeType", "z" * 20]:
            color = _get_node_color(node_type)
            r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
            assert r >= 100 or g >= 100 or b >= 100

    def test_different_types_different_colors(self) -> None:
        assert _get_node_color("Alpha") != _get_node_color("Beta")


class TestNormalizeGraphObj:
    def test_uppercase_internal_keys_lowercased(self) -> None:
        obj = {"_ID": 1, "_LABEL": "Person", "name": "Alice"}
        assert _normalize_graph_obj(obj) == {"_id": 1, "_label": "Person", "name": "Alice"}

    def test_lowercase_passthrough(self) -> None:
        obj = {"_id": 1, "name": "x"}
        assert _normalize_graph_obj(obj) == obj

    def test_non_dict_passthrough(self) -> None:
        assert _normalize_graph_obj("str") == "str"
        assert _normalize_graph_obj(None) is None


class TestSerializeKuzuId:
    def test_dict_id(self) -> None:
        assert _serialize_kuzu_id({"table": 2, "offset": 7}) == "2:7"

    def test_dict_id_missing_keys(self) -> None:
        assert _serialize_kuzu_id({}) == "0:0"

    def test_scalar_id(self) -> None:
        assert _serialize_kuzu_id(42) == "42"
        assert _serialize_kuzu_id("abc") == "abc"
