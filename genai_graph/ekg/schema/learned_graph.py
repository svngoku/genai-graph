"""EKG learned graph: similarity-based relationships between TechnicalApproach and L3 nodes.

``L3TechApproachMatcher`` creates ``POSSIBLE_OFFERING`` relationships from
:class:`TechnicalApproach` nodes (opportunity architecture text) to
:class:`L3` service-offering nodes, based on cosine similarity between their
architecture and description embeddings.

Relationship direction: ``(TechnicalApproach)-[:POSSIBLE_OFFERING]->(L3)``.

This factory produces *no new nodes* — it relies on nodes already ingested
by the ``stratnav_subset_rainbow_crm`` import chain — and is executed after all
HNSW vector indexes are ready (``compute_similarities_task`` in the Prefect flow).
"""

from __future__ import annotations

from genai_graph.ekg.schema.canonical_nodes import L3Node
from genai_graph.ekg.schema.rainbow_review import TechnicalApproachNode
from genai_graph.kg.factories.similarity import SimilarityFactory
from genai_graph.kg.schema.core import GraphRelation, GraphSchema


class L3TechApproachMatcher(SimilarityFactory):
    """Create POSSIBLE_OFFERING relationships from TechnicalApproach to L3 nodes.

    Iterates over :class:`TechnicalApproach` nodes (the ``from`` side with fewer
    instances), queries the L3 HNSW ``description_index``, and creates a
    ``POSSIBLE_OFFERING`` relationship for each pair whose cosine similarity
    meets the configured threshold.

    Relationship direction: ``(TechnicalApproach)-[:POSSIBLE_OFFERING]->(L3)``.

    Configuration example (from ``ekg.yaml``):

    ```yaml
    - factory: 'genai_graph.ekg.schema.learned_graph.L3TechApproachMatcher'
      similarities:
        - relationship: POSSIBLE_OFFERING
          from: TechnicalApproach.architecture   # source; fewer nodes -> iterate
          to: L3.description                     # target; HNSW-indexed
          iterate_over: from
          threshold: 0.8
          top_k: 5
          combiner: first
    ```
    """

    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=None,
            nodes=[TechnicalApproachNode, L3Node],
            relations=[
                GraphRelation(
                    from_node=TechnicalApproachNode,
                    to_node=L3Node,
                    name="POSSIBLE_OFFERING",
                    description="Technical approach that may be served by the L3 offering",
                    properties={"similarity_score": float},
                )
            ],
        )
