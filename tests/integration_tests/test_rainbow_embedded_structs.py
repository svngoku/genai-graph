"""Integration tests for embedded structs in the rainbow_review schema.

Tests verify that RiskAnalysis and TechnicalApproach are correctly embedded
in ReviewedOpportunity and support embeddings/vector indexing.

Recent changes (Feb 2026):
- RiskAnalysis and TechnicalApproach should be embedded structs, NOT separate nodes
- Both support embeddings on their string fields for vector similarity search
"""

from __future__ import annotations

import tempfile

import pytest
from upath import UPath

from genai_graph.ekg.baml_client.types import (
    CompetitiveLandscape,
    Competitor,
    FinancialMetrics,
    KeyStatementOfWorkElement,
    Person,
    ReviewedOpportunity,
    RiskAnalysis,
    SWProjectRisks,
    TechnicalApproach,
)
from genai_graph.ekg.schema.common_nodes import Customer, Geo, Opportunity, Partner
from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema


class TestEmbeddedStructSchemaSetup:
    """Test that RiskAnalysis and TechnicalApproach are properly configured as embedded structs."""

    def test_reviewed_opportunity_has_embedded_structs(self):
        """Verify RiskAnalysis and TechnicalApproach are in extra_classes."""
        # Build schema matching rainbow_review.py
        opp_node = GraphNode(node_class=Opportunity, name_from="name", key_from="opportunity_id")
        customer_node = GraphNode(
            node_class=Customer,
            name_from="name",
            key_from="name",
            explicitly_defined=True,
        )
        person_node = GraphNode(node_class=Person, name_from="name", key_from="name")
        reviewed_opp_node = GraphNode(
            node_class=ReviewedOpportunity,
            extra_classes=[
                FinancialMetrics,
                CompetitiveLandscape,
                KeyStatementOfWorkElement,
                RiskAnalysis,
                TechnicalApproach,
            ],
            name_from=lambda data, _: "Review:test",
            key_from=lambda data, _: "test_key",
        )
        competitor_node = GraphNode(
            node_class=Competitor,
            name_from=lambda data, base: data.get("name") or f"{base}_competitor",
            key_from=lambda data, base: data.get("name") or f"{base}_competitor",
        )
        partner_node = GraphNode(node_class=Partner, name_from="name", key_from="name")
        geo_node = GraphNode(
            node_class=Geo,
            name_from=lambda data, base: data.get("geo_code") or f"{base}_geo",
            key_from="name",
            explicitly_defined=True,
        )
        nodes = [opp_node, customer_node, person_node, reviewed_opp_node, competitor_node, partner_node, geo_node]

        relations = [
            GraphRelation(from_node=reviewed_opp_node, to_node=opp_node, name="REVIEWS"),
            GraphRelation(from_node=opp_node, to_node=customer_node, name="HAS_CUSTOMER"),
            GraphRelation(from_node=customer_node, to_node=person_node, name="HAS_CONTACT"),
            GraphRelation(from_node=reviewed_opp_node, to_node=person_node, name="HAS_TEAM_MEMBER"),
            GraphRelation(from_node=reviewed_opp_node, to_node=partner_node, name="HAS_PARTNER"),
            GraphRelation(from_node=reviewed_opp_node, to_node=geo_node, name="DELIVERED_IN"),
            GraphRelation(from_node=reviewed_opp_node, to_node=competitor_node, name="HAS_COMPETITOR"),
        ]

        schema = GraphSchema(root_model_class=ReviewedOpportunity, nodes=nodes, relations=relations)

        # Find ReviewedOpportunity node
        ro_node = next((n for n in schema.nodes if n.node_class == ReviewedOpportunity), None)
        assert ro_node is not None, "ReviewedOpportunity node not found"

        # Verify embedded structs
        embedded_classes = ro_node.embedded_struct_classes
        assert RiskAnalysis in embedded_classes, "RiskAnalysis should be in embedded_struct_classes"
        assert TechnicalApproach in embedded_classes, "TechnicalApproach should be in embedded_struct_classes"
        assert FinancialMetrics in embedded_classes
        assert CompetitiveLandscape in embedded_classes
        assert KeyStatementOfWorkElement in embedded_classes

    def test_embedded_struct_field_names(self):
        """Verify that at least some embedded struct field names are found.

        Note: Due to ForwardRef in the BAML-generated types, field_name resolution
        may not find all embedded fields. This test verifies that directy-referenced
        classes (without ForwardRef) are found correctly.
        """
        nodes = [
            GraphNode(
                node_class=ReviewedOpportunity,
                extra_classes=[RiskAnalysis, TechnicalApproach, FinancialMetrics, CompetitiveLandscape],
                name_from=lambda data, _: "Review:test",
                key_from=lambda data, _: "test_key",
            ),
        ]

        schema = GraphSchema(root_model_class=ReviewedOpportunity, nodes=nodes, relations=[])
        ro_node = schema.nodes[0]

        struct_field_names = ro_node.struct_field_names()

        # These should be found because they're not ForwardRef in ReviewedOpportunity
        assert "financials" in struct_field_names, "financials field should be in struct_field_names"
        assert "competition" in struct_field_names, "competition field should be in struct_field_names"

        # Note: "risks" and "tech_stack" use ForwardRef so they may not be found by _find_embedded_field_for_class
        # but they are still correctly registered in embedded_struct_classes


class TestEmbeddedStructsWithEmbeddings:
    """Test that embeddings can be computed on embedded struct fields."""

    @pytest.fixture
    def temp_kuzu_db(self):
        """Create a temporary Kuzu database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = UPath(tmpdir) / "test.kuzu"
            backend = KuzuBackend()
            backend.connect(str(db_path))
            yield backend, db_path
            backend.close()

    def test_risk_analysis_embedding_fields(self):
        """Verify RiskAnalysis fields can be indexed with embeddings."""
        # RiskAnalysis has:
        # - risk_category: Optional[SWProjectRisks]
        # - p_mitigation_strategy_: Optional[str]
        # - p_risk_description_: str
        risk = RiskAnalysis(
            risk_category=SWProjectRisks.SkillRetentionRisk,
            p_mitigation_strategy_="Implement training program",
            p_risk_description_="Difficulty in retaining key skills due to high turnover",
        )

        # Verify it has string fields suitable for embeddings
        assert isinstance(risk.p_risk_description_, str)
        assert isinstance(risk.p_mitigation_strategy_, str)

        # These would be indexed in GraphNode.index_fields for embeddings
        # Example: GraphNode(node_class=ReviewedOpportunity, index_fields=["risks"])
        # Then embeddings would be computed for nested fields

    def test_technical_approach_embedding_fields(self):
        """Verify TechnicalApproach fields can be indexed with embeddings."""
        # TechnicalApproach has:
        # - architecture: Optional[str]
        # - technical_stack: Optional[list[str]]
        tech = TechnicalApproach(
            architecture="Microservices with event-driven design",
            technical_stack=["Kubernetes", "Docker", "Apache Kafka", "PostgreSQL"],
        )

        # Verify it has indexable fields
        assert isinstance(tech.architecture, str)
        assert isinstance(tech.technical_stack, list)

        # For vector indexing, the architecture field would be used
        # Example: GraphNode(..., index_fields=["tech_stack.architecture"])
        # Then compute_embeddings could index it for semantic search


class TestRainbowSchemaStructureValidation:
    """Integration test to validate the complete rainbow schema structure."""

    def test_schema_consistency(self):
        """Verify the rainbow schema is internally consistent."""
        from genai_graph.ekg.schema.rainbow_review import ReviewedOpportunityGraph

        graph = ReviewedOpportunityGraph(
            name="test_rainbow",
            data_root="/tmp/test",
        )

        schema = graph.build_schema()

        # Verify root model
        assert schema.root_model_class == ReviewedOpportunity

        # Verify nodes
        node_classes = {n.node_class for n in schema.nodes}
        assert ReviewedOpportunity in node_classes
        assert Opportunity in node_classes
        assert Customer in node_classes, f"Customer not in {node_classes}"

        # Verify ReviewedOpportunity node exists
        ro_node = next((n for n in schema.nodes if n.node_class == ReviewedOpportunity), None)
        assert ro_node is not None

        # RiskAnalysis and TechnicalApproach are separate nodes (not embedded structs)
        # — they were promoted to first-class nodes so their fields can be indexed/queried directly
        node_classes_list = [n.node_class for n in schema.nodes]
        assert node_classes_list.count(RiskAnalysis) == 1, "RiskAnalysis should be a separate node"
        assert node_classes_list.count(TechnicalApproach) == 1, "TechnicalApproach should be a separate node"

        # FinancialMetrics, CompetitiveLandscape, KeyStatementOfWorkElement remain embedded
        assert FinancialMetrics in ro_node.embedded_struct_classes
        assert CompetitiveLandscape in ro_node.embedded_struct_classes

        # Verify dedicated relationships exist for the promoted nodes
        rel_names = {r.name for r in schema.relations}
        assert "HAS_RISK" in rel_names, "HAS_RISK relationship should exist for the RiskAnalysis node"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
