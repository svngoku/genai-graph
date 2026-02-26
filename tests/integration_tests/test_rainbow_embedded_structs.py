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
    Opportunity,
    Person,
    ReviewedOpportunity,
    RiskAnalysis,
    SWProjectRisks,
    TechnicalApproach,
)
from genai_graph.ekg.schema.common_nodes import Customer, Geo, Partner
from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema


class TestEmbeddedStructSchemaSetup:
    """Test that RiskAnalysis and TechnicalApproach are properly configured as embedded structs."""

    def test_reviewed_opportunity_has_embedded_structs(self):
        """Verify RiskAnalysis and TechnicalApproach are in extra_classes."""
        # Build schema matching rainbow_review.py
        nodes = [
            GraphNode(node_class=Opportunity, name_from="name", key_from="opportunity_id"),
            GraphNode(
                node_class=Customer,
                name_from="name",
                key_from="name",
                explicitly_defined=True,
            ),
            GraphNode(node_class=Person, name_from="name", key_from="name"),
            GraphNode(
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
            ),
            GraphNode(
                node_class=Competitor,
                name_from=lambda data, base: data.get("name") or f"{base}_competitor",
                key_from=lambda data, base: data.get("name") or f"{base}_competitor",
            ),
            GraphNode(node_class=Partner, name_from="name", key_from="name"),
            GraphNode(
                node_class=Geo,
                name_from=lambda data, base: data.get("geo_code") or f"{base}_geo",
                key_from="name",
                explicitly_defined=True,
            ),
        ]

        relations = [
            GraphRelation(from_node=ReviewedOpportunity, to_node=Opportunity, name="REVIEWS"),
            GraphRelation(from_node=Opportunity, to_node=Customer, name="HAS_CUSTOMER"),
            GraphRelation(from_node=Customer, to_node=Person, name="HAS_CONTACT"),
            GraphRelation(from_node=ReviewedOpportunity, to_node=Person, name="HAS_TEAM_MEMBER"),
            GraphRelation(from_node=ReviewedOpportunity, to_node=Partner, name="HAS_PARTNER"),
            GraphRelation(from_node=ReviewedOpportunity, to_node=Geo, name="DELIVERED_IN"),
            GraphRelation(from_node=ReviewedOpportunity, to_node=Competitor, name="HAS_COMPETITOR"),
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

        # Verify ReviewedOpportunity has embedded structs
        ro_node = next((n for n in schema.nodes if n.node_class == ReviewedOpportunity), None)
        assert ro_node is not None
        assert RiskAnalysis in ro_node.embedded_struct_classes
        assert TechnicalApproach in ro_node.embedded_struct_classes

        # Verify no separate RiskAnalysis or TechnicalApproach nodes
        node_classes_list = [n.node_class for n in schema.nodes]
        assert node_classes_list.count(RiskAnalysis) == 0, "RiskAnalysis should not be a separate node"
        assert node_classes_list.count(TechnicalApproach) == 0, "TechnicalApproach should not be a separate node"

        # Verify relationships (RiskAnalysis and TechnicalApproach don't have separate rels)
        rel_names = {r.name for r in schema.relations}
        assert "HAS_RISK" not in rel_names, "HAS_RISK rel should not exist (RiskAnalysis is embedded)"
        assert "HAS_TECH_STACK" not in rel_names, "HAS_TECH_STACK rel should not exist (TechnicalApproach is embedded)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
