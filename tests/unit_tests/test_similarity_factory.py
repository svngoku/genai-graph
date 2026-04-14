"""Unit tests for the SimilarityFactory and L3TechApproachMatcher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genai_graph.kg.factories.similarity import (
    SimilarityFactory,
    SimilarityResult,
    SimilaritySpec,
)

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class _MockMatcher(SimilarityFactory):
    """Minimal concrete SimilarityFactory for unit tests."""

    def build_schema(self):
        from pydantic import BaseModel

        from genai_graph.kg.schema.core import GraphNode, GraphRelation, GraphSchema

        class NodeA(BaseModel):
            code: str
            description: str

        class NodeB(BaseModel):
            id: str
            architecture: str

        node_a = GraphNode(node_class=NodeA, name_from="description", key_from="code")
        node_b = GraphNode(node_class=NodeB, name_from="architecture", key_from="AUTO_ID")
        relation = GraphRelation(
            from_node=node_b,
            to_node=node_a,
            name="SIMILAR_TO",
            properties={"similarity_score": float},
        )
        return GraphSchema(root_model_class=None, nodes=[node_b, node_a], relations=[relation])


def _make_matcher(threshold: float = 0.8, top_k: int = 5) -> _MockMatcher:
    return _MockMatcher(
        similarities=[
            SimilaritySpec(
                relationship="SIMILAR_TO",
                from_node="NodeB.architecture",
                to_node="NodeA.description",
                iterate_over="from",
                threshold=threshold,
                top_k=top_k,
            )
        ],
    )


# ---------------------------------------------------------------------------
# _resolve_field
# ---------------------------------------------------------------------------


class TestResolveField:
    def test_parses_table_and_field(self) -> None:
        table, field, index = SimilarityFactory._resolve_field("L3.description")
        assert table == "L3"
        assert field == "description"
        assert index == "description_index"

    def test_parses_camel_case_field(self) -> None:
        table, field, index = SimilarityFactory._resolve_field("TechnicalApproach.architecture")
        assert table == "TechnicalApproach"
        assert field == "architecture"
        assert index == "architecture_index"

    def test_strips_whitespace(self) -> None:
        table, field, _ = SimilarityFactory._resolve_field("  NodeA . my_field  ")
        assert table == "NodeA"
        assert field == "my_field"

    def test_raises_on_missing_dot(self) -> None:
        with pytest.raises(ValueError, match="NodeClass.field_name"):
            SimilarityFactory._resolve_field("no_dot_here")


# ---------------------------------------------------------------------------
# _combine_scores
# ---------------------------------------------------------------------------


class TestCombineScores:
    def test_first_single(self) -> None:
        assert SimilarityFactory._combine_scores([0.9], "first") == 0.9

    def test_first_uses_first_element(self) -> None:
        assert SimilarityFactory._combine_scores([0.8, 0.95], "first") == 0.8

    def test_max(self) -> None:
        assert SimilarityFactory._combine_scores([0.8, 0.95, 0.7], "max") == 0.95

    def test_avg(self) -> None:
        result = SimilarityFactory._combine_scores([0.8, 0.9], "avg")
        assert abs(result - 0.85) < 1e-9

    def test_empty_scores_returns_zero(self) -> None:
        assert SimilarityFactory._combine_scores([], "max") == 0.0


# ---------------------------------------------------------------------------
# _pk_field_for
# ---------------------------------------------------------------------------


class TestPkFieldFor:
    def test_string_key_from(self) -> None:
        matcher = _make_matcher()
        assert matcher._pk_field_for("NodeA") == "code"

    def test_auto_id_key_from(self) -> None:
        matcher = _make_matcher()
        assert matcher._pk_field_for("NodeB") == "id"

    def test_unknown_table_returns_id(self) -> None:
        matcher = _make_matcher()
        assert matcher._pk_field_for("UnknownTable") == "id"


# ---------------------------------------------------------------------------
# compute_similarities — happy path
# ---------------------------------------------------------------------------


class TestComputeSimilarities:
    def test_creates_relationship_above_threshold(self) -> None:
        """Relationships are inserted for pairs with similarity ≥ threshold."""
        from genai_graph.kg.backend import KuzuBackend

        matcher = _make_matcher(threshold=0.8)
        backend = MagicMock(spec=KuzuBackend)
        backend.ensure_vector_extension.return_value = None

        fake_embedding = [0.1] * 4
        # Source: one NodeB row (id, embedding)
        # Vector result: one NodeA row with cosine distance 0.1 → similarity 0.9
        # Third call: the CREATE relationship execute (returns None)
        backend.execute.side_effect = [
            [("ta-uuid-1", fake_embedding)],  # MATCH NodeB (iterate over from)
            [("L3-CODE-A", 0.1)],  # QUERY_VECTOR_INDEX → sim=0.9
            None,  # CREATE relationship
        ]

        result = matcher.compute_similarities(backend)

        assert result.relationships_created == 1
        assert result.pairs_evaluated == 1

        # The last execute call must contain a CREATE cypher with similarity_score
        create_call_args = backend.execute.call_args_list[-1][0][0]
        assert "CREATE" in create_call_args
        assert "SIMILAR_TO" in create_call_args
        assert "similarity_score" in create_call_args

    def test_skips_pairs_below_threshold(self) -> None:
        """Relationships are NOT inserted when similarity < threshold."""
        from genai_graph.kg.backend import KuzuBackend

        matcher = _make_matcher(threshold=0.9)
        backend = MagicMock(spec=KuzuBackend)
        backend.ensure_vector_extension.return_value = None

        fake_embedding = [0.1] * 4
        # cosine distance 0.2 → similarity 0.8, below threshold=0.9
        backend.execute.side_effect = [
            [("ta-uuid-1", fake_embedding)],
            [("L3-CODE-A", 0.2)],
        ]

        result = matcher.compute_similarities(backend)

        assert result.relationships_created == 0
        assert result.pairs_evaluated == 1

    def test_skips_null_embeddings(self) -> None:
        """Source rows with None embeddings are silently ignored."""
        from genai_graph.kg.backend import KuzuBackend

        matcher = _make_matcher()
        backend = MagicMock(spec=KuzuBackend)
        backend.ensure_vector_extension.return_value = None
        # embedding is None for this row
        backend.execute.side_effect = [[("ta-uuid-1", None)]]

        result = matcher.compute_similarities(backend)

        assert result.relationships_created == 0
        assert result.pairs_evaluated == 0

    def test_non_kuzu_backend_returns_empty_result(self) -> None:
        """Non-KuzuBackend backends are skipped with a warning."""
        from genai_graph.kg.backend import KgBackend

        matcher = _make_matcher()
        backend = MagicMock(spec=KgBackend)

        result = matcher.compute_similarities(backend)

        assert result.relationships_created == 0
        assert result.pairs_evaluated == 0
        backend.execute.assert_not_called()

    def test_multiple_source_nodes_multiple_results(self) -> None:
        """All source nodes are iterated and relationships are cumulative."""
        from genai_graph.kg.backend import KuzuBackend

        matcher = _make_matcher(threshold=0.8, top_k=3)
        backend = MagicMock(spec=KuzuBackend)
        backend.ensure_vector_extension.return_value = None

        emb = [0.1] * 4
        backend.execute.side_effect = [
            # Two NodeB rows
            [("ta-1", emb), ("ta-2", emb)],
            # Vector results for ta-1: one match (sim 0.9)
            [("CODE-1", 0.1)],
            # CREATE for ta-1/CODE-1
            None,
            # Vector results for ta-2: two matches (sim 0.85, 0.75)
            [("CODE-2", 0.15), ("CODE-3", 0.25)],
            # CREATE for ta-2/CODE-2 (passes threshold)
            None,
            # CODE-3 doesn't pass threshold, no CREATE
        ]

        result = matcher.compute_similarities(backend)

        assert result.pairs_evaluated == 2
        # Only 2 relationships pass (0.9 ≥ 0.8, 0.85 ≥ 0.8; 0.75 < 0.8)
        assert result.relationships_created == 2

    def test_iterate_over_to_swaps_iteration_side(self) -> None:
        """When iterate_over='to', the to-side nodes are iterated and from-side is indexed."""
        from genai_graph.kg.backend import KuzuBackend

        matcher = _MockMatcher(
            similarities=[
                SimilaritySpec(
                    relationship="SIMILAR_TO",
                    from_node="NodeB.architecture",
                    to_node="NodeA.description",
                    iterate_over="to",
                    threshold=0.8,
                    top_k=3,
                )
            ],
        )
        backend = MagicMock(spec=KuzuBackend)
        backend.ensure_vector_extension.return_value = None

        emb = [0.1] * 4
        backend.execute.side_effect = [
            # Fetch NodeA rows (iterate over to-side)
            [("CODE-X", emb)],
            # Vector index on NodeB.architecture_index: match above threshold
            [("ta-uuid-99", 0.05)],  # sim=0.95
            # CREATE
            None,
        ]
        result = matcher.compute_similarities(backend)

        assert result.relationships_created == 1
        # Verify that the MATCH cypher targets NodeA (the to-side)
        first_execute_args = backend.execute.call_args_list[0][0][0]
        assert "NodeA" in first_execute_args
        # Verify the CREATE cypher still has (from=NodeB, to=NodeA) direction
        create_args = backend.execute.call_args_list[-1][0][0]
        assert "NodeB" in create_args
        assert "NodeA" in create_args
        assert "SIMILAR_TO" in create_args


# ---------------------------------------------------------------------------
# SimilaritySpec
# ---------------------------------------------------------------------------


class TestSimilaritySpec:
    def test_from_alias(self) -> None:
        """SimilaritySpec accepts 'from' alias for from_node."""
        spec = SimilaritySpec.model_validate(
            {"relationship": "REL", "from": "A.f", "to": "B.g", "iterate_over": "from"}
        )
        assert spec.from_node == "A.f"
        assert spec.to_node == "B.g"

    def test_python_field_names(self) -> None:
        """SimilaritySpec can be constructed with Python names (populate_by_name=True)."""
        spec = SimilaritySpec(relationship="REL", from_node="A.f", to_node="B.g")
        assert spec.from_node == "A.f"
        assert spec.to_node == "B.g"

    def test_defaults(self) -> None:
        spec = SimilaritySpec(relationship="X", from_node="A.f", to_node="B.g")
        assert spec.threshold == 0.8
        assert spec.top_k == 5
        assert spec.iterate_over == "from"
        assert spec.combiner == "first"


# ---------------------------------------------------------------------------
# L3TechApproachMatcher schema
# ---------------------------------------------------------------------------


class TestL3TechApproachMatcherSchema:
    def _make_l3_matcher(self):
        from genai_graph.ekg.schema.learned_graph import L3TechApproachMatcher

        return L3TechApproachMatcher(
            similarities=[
                SimilaritySpec(
                    relationship="POSSIBLE_OFFERING",
                    from_node="TechnicalApproach.architecture",
                    to_node="L3.description",
                    iterate_over="from",
                )
            ],
        )

    def test_build_schema_contains_expected_nodes(self) -> None:
        matcher = self._make_l3_matcher()
        schema = matcher.build_schema()

        node_labels = {n.label for n in schema.nodes}
        assert "L3" in node_labels
        assert "TechnicalApproach" in node_labels

    def test_build_schema_has_possible_offering_relation(self) -> None:
        matcher = self._make_l3_matcher()
        schema = matcher.build_schema()

        assert len(schema.relations) == 1
        rel = schema.relations[0]
        assert rel.name == "POSSIBLE_OFFERING"
        # Direction: TechnicalApproach → POSSIBLE_OFFERING → L3
        assert rel.from_node.label == "TechnicalApproach"
        assert rel.to_node.label == "L3"

    def test_build_schema_relation_has_similarity_score_property(self) -> None:
        matcher = self._make_l3_matcher()
        schema = matcher.build_schema()
        rel = schema.relations[0]

        assert rel.properties is not None
        assert "similarity_score" in rel.properties
        assert rel.properties["similarity_score"] is float

    def test_get_struct_data_by_key_returns_none(self) -> None:
        matcher = self._make_l3_matcher()
        assert matcher.get_struct_data_by_key("any-key") is None


# ---------------------------------------------------------------------------
# compute_similarities_task
# ---------------------------------------------------------------------------


class TestComputeSimilaritiesTask:
    def test_skips_non_similarity_bundles(self) -> None:
        """Non-SimilarityFactory bundles are ignored."""
        from genai_graph.kg.backend import KuzuBackend
        from genai_graph.kg.factories import JsonFileBackedFactory
        from genai_graph.orchestration.models import GraphBundle
        from genai_graph.orchestration.tasks import compute_similarities_task

        non_sim_bundle = MagicMock(spec=GraphBundle)
        non_sim_bundle.factory = MagicMock(spec=JsonFileBackedFactory)

        backend = MagicMock(spec=KuzuBackend)
        results = compute_similarities_task.fn([non_sim_bundle], backend)
        assert results == []

    def test_calls_compute_similarities_for_each_sim_bundle(self) -> None:
        """compute_similarities is called once per SimilarityFactory bundle."""
        from genai_graph.kg.backend import KuzuBackend
        from genai_graph.orchestration.models import GraphBundle
        from genai_graph.orchestration.tasks import compute_similarities_task

        sim_bundle = MagicMock(spec=GraphBundle)
        sim_bundle.factory = MagicMock(spec=SimilarityFactory)
        sim_bundle.factory.name = "TestMatcher"
        fake_result = SimilarityResult(factory_name="TestMatcher", relationships_created=3, pairs_evaluated=5)
        sim_bundle.factory.compute_similarities.return_value = fake_result
        sim_bundle.config = {"factory": "test:TestMatcher"}

        backend = MagicMock(spec=KuzuBackend)
        results = compute_similarities_task.fn([sim_bundle], backend)

        sim_bundle.factory.compute_similarities.assert_called_once_with(backend)
        assert len(results) == 1
        assert results[0].relationships_created == 3
