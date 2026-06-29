"""Tests for node identity and registry merge deduplication."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.unit


class TestTableNameIdentity:
    def test_label_defaults_to_class_name(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Widget(BaseModel):
            name: str

        node = GraphNode(node_class=Widget, name_from="name", key_from="name")
        assert node.label == "Widget"

    def test_explicit_table_name_overrides_label(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class WidgetV2(BaseModel):
            name: str

        node = GraphNode(node_class=WidgetV2, name_from="name", key_from="name", table_name="Widget")
        assert node.label == "Widget"

    def test_table_name_none_uses_class_name(self) -> None:
        from genai_graph.kg.schema import GraphNode

        class Widget(BaseModel):
            name: str

        node = GraphNode(node_class=Widget, name_from="name", key_from="name", table_name=None)
        assert node.label == "Widget"

    def test_two_classes_same_table_name_collide(self) -> None:
        from genai_graph.kg.schema import GraphNode, GraphSchema

        class Alpha(BaseModel):
            name: str

        class Beta(BaseModel):
            name: str

        node_a = GraphNode(node_class=Alpha, name_from="name", key_from="name")
        node_b = GraphNode(node_class=Beta, name_from="name", key_from="name", table_name="Alpha")
        schema = GraphSchema(root_model_class=None, nodes=[node_a, node_b], relations=[])
        warnings = schema.get_warnings()
        assert any("share the label" in w for w in warnings)

    def test_two_classes_different_table_names_no_collision(self) -> None:
        from genai_graph.kg.schema import GraphNode, GraphSchema

        class Alpha(BaseModel):
            name: str

        class AlphaLegacy(BaseModel):
            name: str

        node_a = GraphNode(node_class=Alpha, name_from="name", key_from="name")
        # Distinct table_name avoids using the class __name__
        node_b = GraphNode(
            node_class=AlphaLegacy, name_from="name", key_from="name", table_name="AlphaV1"
        )
        schema = GraphSchema(root_model_class=None, nodes=[node_a, node_b], relations=[])
        warnings = schema.get_warnings()
        assert not any("share the label" in w for w in warnings)


class TestRegistryMergeDeduplication:
    def test_nodes_deduped_by_label_across_schemas(self) -> None:
        from genai_graph.kg.schema import GraphNode, GraphSchema

        class Entity(BaseModel):
            name: str

        # Same class in two schemas — should merge to one node
        entity_node_1 = GraphNode(node_class=Entity, name_from="name", key_from="name")
        entity_node_2 = GraphNode(node_class=Entity, name_from="name", key_from="name")

        schema_a = GraphSchema(root_model_class=None, nodes=[entity_node_1], relations=[])
        schema_b = GraphSchema(root_model_class=None, nodes=[entity_node_2], relations=[])

        # Simulate merge (what GraphRegistry.build_combined_schema does)
        merged_nodes: list = []
        seen: set[str] = set()
        for schema in [schema_a, schema_b]:
            for node in schema.nodes:
                if node.label not in seen:
                    seen.add(node.label)
                    merged_nodes.append(node)

        assert len(merged_nodes) == 1

    def test_nodes_with_explicit_table_name_deduped_correctly(self) -> None:
        from genai_graph.kg.schema import GraphNode, GraphSchema

        class EntityV1(BaseModel):
            name: str

        class EntityV2(BaseModel):
            name: str

        # Both map to "Entity" table — should merge to 1
        node_v1 = GraphNode(node_class=EntityV1, name_from="name", key_from="name", table_name="Entity")
        node_v2 = GraphNode(node_class=EntityV2, name_from="name", key_from="name", table_name="Entity")

        schema_a = GraphSchema(root_model_class=None, nodes=[node_v1], relations=[])
        schema_b = GraphSchema(root_model_class=None, nodes=[node_v2], relations=[])

        merged: list = []
        seen: set[str] = set()
        for schema in [schema_a, schema_b]:
            for node in schema.nodes:
                if node.label not in seen:
                    seen.add(node.label)
                    merged.append(node)

        assert len(merged) == 1
        assert merged[0].label == "Entity"

    def test_relations_deduped_by_label_triple(self) -> None:
        from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

        class A(BaseModel):
            name: str

        class B(BaseModel):
            name: str

        a = GraphNode(node_class=A, name_from="name", key_from="name")
        b = GraphNode(node_class=B, name_from="name", key_from="name")
        rel1 = GraphRelation(from_node=a, to_node=b, name="LINKS")
        rel2 = GraphRelation(from_node=a, to_node=b, name="LINKS")

        schema_a = GraphSchema(root_model_class=None, nodes=[a, b], relations=[rel1])
        schema_b = GraphSchema(root_model_class=None, nodes=[a, b], relations=[rel2])

        merged_rels: list = []
        seen_rels: set = set()
        for schema in [schema_a, schema_b]:
            for rel in schema.relations:
                key = (rel.from_node.label, rel.to_node.label, rel.name)
                if key not in seen_rels:
                    seen_rels.add(key)
                    merged_rels.append(rel)

        assert len(merged_rels) == 1
