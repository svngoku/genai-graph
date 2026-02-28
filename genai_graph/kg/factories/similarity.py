"""Abstract base factory for creating relationships based on embedding similarity.

A ``SimilarityFactory`` does not load new node data — it reads embeddings from
already-ingested nodes and creates typed relationships between pairs whose
cosine similarity exceeds the configured threshold.

It plugs into the standard ``create_kg_flow`` pipeline and is executed by
``compute_similarities_task``, which runs *after* ``create_vector_indexes_task``
so that all HNSW indexes are ready before any similarity queries are issued.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.backend import KgBackend, KuzuBackend
from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.schema.core import GraphSchema


class SimilaritySpec(BaseModel):
    """Configuration for one similarity-based relationship.

    Declares both the **graph semantics** (which node is the relationship
    source/target) and the **query strategy** (which side to iterate over).

    Field format: ``"NodeClass.field_name"`` using the Pydantic/Kuzu
    snake_case field name.  The factory appends ``_embedding`` and ``_index``
    suffixes automatically.

    Relationship direction is always ``(from)-[:relationship]->(to)``,
    regardless of ``iterate_over``.

    ``iterate_over`` controls *query performance*: set it to the side with
    fewer node instances so the outer loop is short and the HNSW index on
    the other (larger) side does the heavy lifting.

    Example (TechnicalApproach → L3; TechnicalApproach has fewer nodes):

    ```yaml
    - relationship: POSSIBLE_OFFERING
      from: TechnicalApproach.architecture   # source node + embedding field
      to: L3.description                     # target node + HNSW-indexed field
      iterate_over: from                     # loop over fewer TechnicalApproach nodes
      threshold: 0.8
      top_k: 5
    ```

    The ``combiner`` field is scaffolded for future multi-field score fusion
    (when multiple specs share a relationship); a single spec always uses
    ``"first"``.
    """

    relationship: str
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    iterate_over: Literal["from", "to"] = "from"
    threshold: float = 0.8
    top_k: int = 5
    matcher: Literal["embeddings"] = "embeddings"
    combiner: Literal["max", "avg", "first"] = "first"

    model_config = {"populate_by_name": True}


class SimilarityResult(BaseModel):
    """Statistics from a single similarity computation run."""

    factory_name: str
    relationships_created: int = 0
    pairs_evaluated: int = 0


class SimilarityFactory(KgFactory):
    """Abstract base for factories that create relationships via embedding similarity.

    Unlike data-source factories, a ``SimilarityFactory`` produces no new nodes.
    It reads embeddings from already-ingested nodes and creates typed
    relationships between pairs whose cosine similarity exceeds the configured
    threshold.

    Subclasses must implement :meth:`build_schema` to declare the node types
    and the target relationship (with ``properties={"similarity_score": float}``
    to declare the DOUBLE column in Kuzu).

    Similarity computation is triggered by :meth:`compute_similarities` after
    all HNSW vector indexes have been created (i.e. after
    ``create_vector_indexes_task`` in the Prefect flow).

    Example YAML config:

    ```yaml
    graphs:
      - factory: 'mypackage.schema.my_graph:MyMatcher'
        similarities:
          - relationship: SIMILAR_TO
            from: NodeB.architecture   # source node (fewer instances -> iterate)
            to: NodeA.description      # target node (HNSW-indexed)
            iterate_over: from
            threshold: 0.8
            top_k: 5
    ```
    """

    similarities: list[SimilaritySpec]

    # No source data — this factory only wires existing nodes.
    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        return None

    @abstractmethod
    def build_schema(self) -> GraphSchema: ...

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_field(field_str: str) -> tuple[str, str, str]:
        """Parse ``"Table.field"`` into ``(table_name, column_name, index_name)``.

        Example: ``"L3.description"`` -> ``("L3", "description", "description_index")``.
        """
        if "." not in field_str:
            raise ValueError(f"Similarity field must be in 'NodeClass.field_name' format, got: {field_str!r}")
        table, field = field_str.split(".", 1)
        return table.strip(), field.strip(), f"{field.strip()}_index"

    def _pk_field_for(self, table_name: str) -> str:
        """Derive the Kuzu primary key field name for a node class from the schema.

        ``AUTO_ID`` and callable ``key_from`` use the internal ``id`` field;
        a plain string ``key_from`` is used directly (e.g. ``"code"`` for L3).
        """
        for node in self.build_schema().nodes:
            if node.node_class.__name__ == table_name:
                if node.key_from == "AUTO_ID" or callable(node.key_from):
                    return "id"
                if isinstance(node.key_from, str):
                    return node.key_from
        return "id"  # fallback

    @staticmethod
    def _combine_scores(scores: list[float], combiner: str) -> float:
        """Combine per-field similarity scores into a single value.

        Args:
            scores: Per-field cosine similarity scores (higher is more similar).
            combiner: One of ``"first"``, ``"max"``, ``"avg"``.

        Returns:
            Combined similarity score.
        """
        if not scores:
            return 0.0
        if combiner == "max":
            return max(scores)
        if combiner == "avg":
            return sum(scores) / len(scores)
        # "first" (default) — use the first / only score
        return scores[0]

    # ------------------------------------------------------------------
    # Core similarity logic
    # ------------------------------------------------------------------

    def compute_similarities(self, backend: KgBackend) -> SimilarityResult:
        """Create relationship records based on embedding similarity.

        For each :class:`SimilaritySpec`, the logic depends on ``iterate_over``:

        - ``iterate_over="from"``: loop over *from* nodes, query HNSW on *to*,
          create ``(from)-[:REL {similarity_score}]->(to)``
        - ``iterate_over="to"``: loop over *to* nodes, query HNSW on *from*,
          create ``(from)-[:REL {similarity_score}]->(to)`` (direction unchanged)

        In both cases the graph relationship direction is always
        ``(from)-[:relationship]->(to)`` as declared in the spec.

        Kuzu reports cosine *distance* (``1 - similarity``); this method
        converts it back to cosine *similarity* before applying the threshold.

        Args:
            backend: Active KgBackend.  Must be a :class:`KuzuBackend`.

        Returns:
            :class:`SimilarityResult` with creation statistics.
        """
        if not isinstance(backend, KuzuBackend):
            logger.warning("SimilarityFactory.compute_similarities requires KuzuBackend; skipping {}", self.name)
            return SimilarityResult(factory_name=self.name)

        total_created = 0
        total_evaluated = 0

        # Build pk lookup once to avoid repeated build_schema() calls
        _schema = self.build_schema()
        _pk_map: dict[str, str] = {}
        for _node in _schema.nodes:
            kf = _node.key_from
            _pk_map[_node.node_class.__name__] = kf if isinstance(kf, str) and kf != "AUTO_ID" else "id"

        # Ensure the vector extension is loaded once before any queries
        backend.ensure_vector_extension()

        for spec in self.similarities:
            rel_name = spec.relationship

            from_table, from_field, from_index = self._resolve_field(spec.from_node)
            from_pk = _pk_map.get(from_table, "id")

            to_table, to_field, to_index = self._resolve_field(spec.to_node)
            to_pk = _pk_map.get(to_table, "id")

            # Decide which side to iterate (outer loop) and which to index-query
            if spec.iterate_over == "from":
                iter_table, iter_field, iter_pk = from_table, from_field, from_pk
                idx_table, idx_index, idx_pk = to_table, to_index, to_pk
                iter_is_from = True  # iter node is the FROM side of the relationship
            else:
                iter_table, iter_field, iter_pk = to_table, to_field, to_pk
                idx_table, idx_index, idx_pk = from_table, from_index, from_pk
                iter_is_from = False  # iter node is the TO side of the relationship

            iter_embedding_col = f"{iter_field}_embedding"

            # Fetch all iter-side nodes that have a computed embedding
            fetch_cypher = (
                f"MATCH (n:{iter_table}) "
                f"WHERE n.{iter_embedding_col} IS NOT NULL "
                f"RETURN n.{iter_pk}, n.{iter_embedding_col}"
            )
            try:
                iter_rows = list(backend.execute(fetch_cypher))
            except Exception as exc:
                logger.error("Failed to fetch {} embeddings for similarity: {}", iter_table, exc)
                continue

            logger.info(
                "Computing '{}' similarities: iterating {} {} nodes, querying {} HNSW index '{}'",
                rel_name,
                len(iter_rows),
                iter_table,
                idx_table,
                idx_index,
            )

            for row in iter_rows:
                iter_node_id, embedding = row[0], row[1]
                if not embedding:
                    continue

                total_evaluated += 1

                try:
                    vector_cypher = (
                        f"CALL QUERY_VECTOR_INDEX("
                        f"    '{idx_table}', '{idx_index}', $query_vector, {spec.top_k}, efs := 200"
                        f") RETURN node.{idx_pk}, distance;"
                    )
                    result_rows = list(backend.execute(vector_cypher, {"query_vector": embedding}))
                except Exception as exc:
                    logger.warning("Vector index query failed for {} node {}: {}", iter_table, iter_node_id, exc)
                    continue

                for vrow in result_rows:
                    idx_node_id, distance = vrow[0], vrow[1]
                    if distance is None or idx_node_id is None:
                        continue

                    # Kuzu reports cosine distance (1 - similarity); convert back
                    similarity = self._combine_scores([1.0 - float(distance)], spec.combiner)
                    if similarity < spec.threshold:
                        continue

                    # Resolve which node is FROM and which is TO for the relationship
                    if iter_is_from:
                        actual_from_id, actual_from_pk, actual_from_table = iter_node_id, iter_pk, iter_table
                        actual_to_id, actual_to_pk, actual_to_table = idx_node_id, idx_pk, idx_table
                    else:
                        actual_from_id, actual_from_pk, actual_from_table = idx_node_id, idx_pk, idx_table
                        actual_to_id, actual_to_pk, actual_to_table = iter_node_id, iter_pk, iter_table

                    try:
                        self._insert_similarity_rel(
                            backend=backend,
                            rel_name=rel_name,
                            from_table=actual_from_table,
                            from_node_id=actual_from_id,
                            from_pk_field=actual_from_pk,
                            to_table=actual_to_table,
                            to_node_id=actual_to_id,
                            to_pk_field=actual_to_pk,
                            similarity_score=similarity,
                        )
                        total_created += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to insert {} ({}:{} -> {}:{}): {}",
                            rel_name,
                            actual_from_table,
                            actual_from_id,
                            actual_to_table,
                            actual_to_id,
                            exc,
                        )

        result = SimilarityResult(
            factory_name=self.name,
            relationships_created=total_created,
            pairs_evaluated=total_evaluated,
        )
        logger.info(
            "{}: evaluated {} source nodes, created {} relationships",
            self.name,
            total_evaluated,
            total_created,
        )
        return result

    @staticmethod
    def _insert_similarity_rel(
        backend: KuzuBackend,
        rel_name: str,
        from_table: str,
        from_node_id: Any,
        from_pk_field: str,
        to_table: str,
        to_node_id: Any,
        to_pk_field: str,
        similarity_score: float,
    ) -> None:
        """Insert a single similarity relationship using a MATCH ... CREATE pattern.

        Args:
            backend: KuzuBackend instance.
            rel_name: Relationship type name.
            from_table: Source node table name.
            from_node_id: Primary key value of the source node.
            from_pk_field: Primary key field name for the source table.
            to_table: Target node table name.
            to_node_id: Primary key value of the target node.
            to_pk_field: Primary key field name for the target table.
            similarity_score: Cosine similarity score stored as ``similarity_score`` property.
        """
        from_id_esc = str(from_node_id).replace("'", "\\'")
        to_id_esc = str(to_node_id).replace("'", "\\'")
        cypher = (
            f"MATCH (src:{from_table} {{{from_pk_field}: '{from_id_esc}'}}), "
            f"      (tgt:{to_table} {{{to_pk_field}: '{to_id_esc}'}})\n"
            f"CREATE (src)-[:{rel_name} {{similarity_score: {similarity_score}}}]->(tgt);"
        )
        backend.execute(cypher)
