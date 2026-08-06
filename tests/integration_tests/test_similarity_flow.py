"""Integration tests for SimilarityFactory.compute_similarities with a real database.

These tests replace the former MagicMock-based unit tests: nodes with synthetic
embeddings are created in a throwaway Ladybug database, a real HNSW vector index
is built, and relationships are verified with real Cypher queries.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.factories.similarity import SimilarityFactory, SimilaritySpec
from genai_graph.kg.schema.core import GraphNode, GraphRelation, GraphSchema

_EM_DIM = 4


class Technique(BaseModel):
    """Source node — iterated over in 'from' mode."""

    id: str
    architecture: str
    architecture_embedding: list[float] | None = None


class Component(BaseModel):
    """Target node — HNSW-indexed in 'from' mode."""

    code: str
    description: str
    description_embedding: list[float] | None = None


class TechComponentMatcher(SimilarityFactory):
    """Concrete matcher linking Technique → Component via embedding similarity."""

    def build_schema(self) -> GraphSchema:
        technique = GraphNode(node_class=Technique, name_from="architecture", key_from="id")
        component = GraphNode(node_class=Component, name_from="description", key_from="code")
        relation = GraphRelation(
            from_node=technique,
            to_node=component,
            name="SIMILAR_TO",
            properties={"similarity_score": float},
        )
        return GraphSchema(root_model_class=None, nodes=[technique, component], relations=[relation])


def _make_matcher(threshold: float = 0.8, top_k: int = 5, iterate_over: str = "from") -> TechComponentMatcher:
    return TechComponentMatcher(
        similarities=[
            SimilaritySpec(
                relationship="SIMILAR_TO",
                from_node="Technique.architecture",
                to_node="Component.description",
                iterate_over=iterate_over,  # type: ignore[arg-type]
                threshold=threshold,
                top_k=top_k,
            )
        ],
    )


def _setup_schema(backend: KuzuBackend) -> None:
    """Create node/rel tables matching the TechComponentMatcher schema."""
    backend.execute(
        f"CREATE NODE TABLE Technique("
        f"id STRING, architecture STRING, architecture_embedding FLOAT[{_EM_DIM}], PRIMARY KEY(id))"
    )
    backend.execute(
        f"CREATE NODE TABLE Component("
        f"code STRING, description STRING, description_embedding FLOAT[{_EM_DIM}], PRIMARY KEY(code))"
    )
    backend.execute("CREATE REL TABLE SIMILAR_TO(FROM Technique TO Component, similarity_score DOUBLE)")


def _insert_technique(backend: KuzuBackend, id: str, architecture: str, embedding: list[float] | None) -> None:
    backend.execute(
        "CREATE (t:Technique {id: $id, architecture: $arch, architecture_embedding: $emb})",
        {"id": id, "arch": architecture, "emb": embedding},
    )


def _insert_component(backend: KuzuBackend, code: str, description: str, embedding: list[float] | None) -> None:
    # Note: the parameter name must not be a reserved word ($desc would clash with DESC).
    backend.execute(
        "CREATE (c:Component {code: $code, description: $descr, description_embedding: $emb})",
        {"code": code, "descr": description, "emb": embedding},
    )


def _create_index(backend: KuzuBackend, table: str, field: str) -> None:
    backend.create_vector_index(table_name=table, field_name=f"{field}_embedding", index_name=f"{field}_index")


def _count_relationships(backend: KuzuBackend) -> int:
    df = backend.execute_get_as_df("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r) AS cnt", union=False)
    return int(df["cnt"].iloc[0])


def _relationship_scores(backend: KuzuBackend) -> list[tuple[str, str, float]]:
    df = backend.execute_get_as_df(
        "MATCH (t:Technique)-[r:SIMILAR_TO]->(c:Component) RETURN t.id AS src, c.code AS dst, r.similarity_score AS score",
        union=False,
    )
    return [(row.src, row.dst, row.score) for row in df.itertuples()]


@pytest.fixture
def sim_backend(graph_backend: KuzuBackend) -> KuzuBackend:
    """Backend with the similarity schema pre-created."""
    _setup_schema(graph_backend)
    return graph_backend


@pytest.mark.integration
class TestComputeSimilarities:
    def test_creates_relationship_above_threshold(self, sim_backend: KuzuBackend) -> None:
        """A pair whose cosine similarity exceeds the threshold gets a real relationship."""
        _insert_technique(sim_backend, "ta-1", "microservices", [1.0, 0.0, 0.0, 0.0])
        _insert_component(sim_backend, "COMP-A", "service mesh", [1.0, 0.0, 0.0, 0.0])
        _create_index(sim_backend, "Component", "description")

        result = _make_matcher(threshold=0.8).compute_similarities(sim_backend)

        assert result.relationships_created == 1
        assert result.pairs_evaluated == 1
        assert result.factory_name == "TechComponentMatcher"
        assert _count_relationships(sim_backend) == 1
        src, dst, score = _relationship_scores(sim_backend)[0]
        assert (src, dst) == ("ta-1", "COMP-A")
        assert score == pytest.approx(1.0, abs=1e-6)

    def test_skips_pairs_below_threshold(self, sim_backend: KuzuBackend) -> None:
        """Orthogonal embeddings (similarity 0) never produce a relationship."""
        _insert_technique(sim_backend, "ta-1", "microservices", [1.0, 0.0, 0.0, 0.0])
        _insert_component(sim_backend, "COMP-A", "unrelated", [0.0, 1.0, 0.0, 0.0])
        _create_index(sim_backend, "Component", "description")

        result = _make_matcher(threshold=0.9).compute_similarities(sim_backend)

        assert result.relationships_created == 0
        assert result.pairs_evaluated == 1
        assert _count_relationships(sim_backend) == 0

    def test_skips_null_embeddings(self, sim_backend: KuzuBackend) -> None:
        """Source rows with NULL embeddings are silently ignored by the MATCH query."""
        _insert_technique(sim_backend, "ta-1", "no embedding", None)
        _insert_component(sim_backend, "COMP-A", "service mesh", [1.0, 0.0, 0.0, 0.0])
        _create_index(sim_backend, "Component", "description")

        result = _make_matcher().compute_similarities(sim_backend)

        assert result.relationships_created == 0
        assert result.pairs_evaluated == 0

    def test_multiple_sources_selective_threshold(self, sim_backend: KuzuBackend) -> None:
        """Each source node is evaluated; only above-threshold matches are created."""
        _insert_technique(sim_backend, "ta-1", "microservices", [1.0, 0.0, 0.0, 0.0])
        _insert_technique(sim_backend, "ta-2", "event driven", [0.0, 0.0, 1.0, 0.0])
        _insert_component(sim_backend, "COMP-A", "service mesh", [1.0, 0.0, 0.0, 0.0])
        _insert_component(sim_backend, "COMP-B", "message broker", [0.0, 0.0, 0.9, 0.1])
        _insert_component(sim_backend, "COMP-C", "batch job", [0.0, 1.0, 0.0, 0.0])
        _create_index(sim_backend, "Component", "description")

        result = _make_matcher(threshold=0.8, top_k=3).compute_similarities(sim_backend)

        assert result.pairs_evaluated == 2
        assert result.relationships_created == 2
        assert {(s, d) for s, d, _ in _relationship_scores(sim_backend)} == {("ta-1", "COMP-A"), ("ta-2", "COMP-B")}

    def test_iterate_over_to_preserves_direction(self, sim_backend: KuzuBackend) -> None:
        """iterate_over='to' iterates the to-side but the relationship direction stays from→to."""
        _insert_technique(sim_backend, "ta-1", "microservices", [1.0, 0.0, 0.0, 0.0])
        _insert_component(sim_backend, "COMP-A", "service mesh", [1.0, 0.0, 0.0, 0.0])
        # In 'to' mode the index queried is on the FROM side (Technique.architecture)
        _create_index(sim_backend, "Technique", "architecture")

        result = _make_matcher(threshold=0.8, iterate_over="to").compute_similarities(sim_backend)

        assert result.relationships_created == 1
        src, dst, _ = _relationship_scores(sim_backend)[0]
        assert (src, dst) == ("ta-1", "COMP-A")

    def test_missing_table_is_skipped_not_raised(self, graph_backend: KuzuBackend) -> None:
        """A spec referencing a non-existent table logs an error and is skipped."""
        # Only create the Component side; Technique table is missing entirely.
        graph_backend.execute(
            f"CREATE NODE TABLE Component("
            f"code STRING, description STRING, description_embedding FLOAT[{_EM_DIM}], PRIMARY KEY(code))"
        )

        result = _make_matcher().compute_similarities(graph_backend)

        assert result.relationships_created == 0
        assert result.pairs_evaluated == 0


@pytest.mark.integration
class TestComputeSimilaritiesTask:
    def test_skips_non_similarity_bundles(self, sim_backend: KuzuBackend) -> None:
        """Non-SimilarityFactory bundles are ignored (real bundle, no mock)."""
        from genai_graph.kg.factories.base import KgFactory
        from genai_graph.orchestration.models import GraphBundle
        from genai_graph.orchestration.tasks import compute_similarities_task

        class _DummyFactory(KgFactory):
            def build_schema(self) -> GraphSchema:
                return GraphSchema(nodes=[], relations=[])

            def get_struct_data_by_key(self, key: str) -> BaseModel | None:
                return None

        bundle = GraphBundle(config={"factory": "test:Dummy"}, factory=_DummyFactory())
        results = compute_similarities_task.fn([bundle], sim_backend)
        assert results == []

    def test_runs_similarity_bundles_end_to_end(self, sim_backend: KuzuBackend) -> None:
        """compute_similarities_task drives a real factory against a real backend."""
        from genai_graph.orchestration.models import GraphBundle
        from genai_graph.orchestration.tasks import compute_similarities_task

        _insert_technique(sim_backend, "ta-1", "microservices", [1.0, 0.0, 0.0, 0.0])
        _insert_component(sim_backend, "COMP-A", "service mesh", [1.0, 0.0, 0.0, 0.0])
        _create_index(sim_backend, "Component", "description")

        bundle = GraphBundle(config={"factory": "test:TechComponentMatcher"}, factory=_make_matcher())
        results = compute_similarities_task.fn([bundle], sim_backend)

        assert len(results) == 1
        assert results[0].relationships_created == 1
        assert results[0].pairs_evaluated == 1
        assert _count_relationships(sim_backend) == 1
