"""Opportunity graph for EKG system.

Contains all opportunity-specific data model logic and BAML client integration.
This is the only module that imports BAML client types.
"""

from __future__ import annotations

from pydantic import BaseModel

from genai_graph.ekg.baml_client.types import ReviewedOpportunity
from genai_graph.kg.factories import JsonFileBackedFactory
from genai_graph.kg.schema import GraphSchema


class ReviewedOpportunityGraph(JsonFileBackedFactory, BaseModel):
    """Opportunity data graph using JSON files from BAML extract."""

    def get_model_class(self) -> type[BaseModel]:
        """Return the root model class for this graph factory."""
        return ReviewedOpportunity

    def build_schema(self) -> GraphSchema:
        """Build the graph schema configuration for opportunity data.

        Returns:
            GraphSchema with all node and relationship configurations
        """
        from genai_graph.ekg.baml_client.types import (
            CompetitiveLandscape,
            Competitor,
            FinancialMetrics,
            KeyStatementOfWorkElement,
            Opportunity,
            Person,
            RiskAnalysis,
            TechnicalApproach,
        )
        from genai_graph.ekg.schema.common_nodes import Customer, Geo, Partner
        from genai_graph.kg.schema import (
            GraphNode,
            GraphRelation,
        )

        # Note: We use BAML types directly here because ReviewedOpportunity's
        # fields reference these exact BAML types. The path introspection needs
        # to find the same types. Since all types share the same __name__,
        # they'll map to the same Kuzu tables for cross-factory deduplication.

        # Define nodes with descriptions
        nodes = [
            GraphNode(
                node_class=Opportunity,
                name_from="name",
                key_from="opportunity_id",
                description="Core opportunity information",
                index_fields=["name", "status"],
            ),
            GraphNode(
                node_class=Customer,
                name_from="name",
                key_from="name",
                description="Customer organization details",
                index_fields=["name"],
                explicitly_defined=True,  # Connected via multi-hop path (ReviewedOpportunity → Opportunity → Customer)
            ),
            GraphNode(
                node_class=Person,
                name_from="name",
                key_from="name",
                description="Individual contacts and team members",
            ),
            # Root node
            GraphNode(
                node_class=ReviewedOpportunity,
                extra_classes=[FinancialMetrics, CompetitiveLandscape, KeyStatementOfWorkElement],
                name_from=lambda data, _: "Review:"
                + str(data.get("opportunity", {}).get("opportunity_id", ""))
                + ":"
                + str(data.get("start_date", "")),
                key_from=lambda data, _: str(data.get("opportunity", {}).get("opportunity_id", "unknown")),
                description="Root node containing the complete reviewed opportunity",
            ),
            # Regular nodes - field paths auto-deduced
            GraphNode(
                node_class=RiskAnalysis,
                name_from=lambda data, _: getattr(data.get("risk_category"), "name", None)
                or str(data.get("risk_category", "other_risk")),
                key_from=lambda data, _: str(getattr(data.get("risk_category"), "name", None) or "Other Risks"),
                description="Risk assessment and mitigation details",
                index_fields=["risk_description"],
            ),
            GraphNode(
                node_class=TechnicalApproach,
                name_from=lambda data, base: data.get("technical_stack")
                or data.get("architecture")
                or f"{base}_default",
                key_from="AUTO_ID",  # Use auto-generated SERIAL id
                description="Technical implementation approach and stack",
                index_fields=["architecture", "technical_stack"],
            ),
            # GraphNode(
            #     node_class=CompetitiveLandscape,
            #     name_from=lambda data, base: data.get("competitive_position") or f"{base}_competitive_position",
            #     description="Competitive positioning and analysis",
            # ),
            GraphNode(
                node_class=Competitor,
                name_from=lambda data, base: data.get("known_as") or data.get("name") or f"{base}_competitor",
                key_from=lambda data, base: data.get("known_as") or data.get("name") or f"{base}_competitor",
                description="Competitor",
            ),
            GraphNode(
                node_class=Partner,
                name_from="name",
                key_from="name",
                description="Atos partner organization information",
            ),
            GraphNode(
                node_class=Geo,
                name_from=lambda data, base: data.get("geo_code") or data.get("country") or f"{base}_geo",
                key_from="name",  # Use computed name as PK (consistent with Stratnav Geo)
                description="Geographic location for delivery",
                index_fields=["country", "geo_code"],
                explicitly_defined=True,  # Connected via explicit field path in DELIVERED_IN relationship
            ),
        ]

        # Define relationships with descriptions
        # Field paths are automatically deduced from the model structure
        relations = [
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=Opportunity,
                name="REVIEWS",
                description="Review relationship to core opportunity",
            ),
            GraphRelation(
                from_node=Opportunity,
                to_node=Customer,
                name="HAS_CUSTOMER",
                description="Opportunity belongs to customer",
            ),
            GraphRelation(
                from_node=Customer,
                to_node=Person,
                name="HAS_CONTACT",
                description="Customer contact persons",
            ),
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=Person,
                name="HAS_TEAM_MEMBER",
                description="Internal team members",
                field_paths=[("", "team")],  # Use team field (not customer.employees)
            ),
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=Partner,
                name="HAS_PARTNER",
                description="Partner organizations involved",
            ),
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=Geo,
                name="DELIVERED_IN",
                description="Geographic locations where solution is delivered",
                field_paths=[("", "delivery_info.locations")],  # Explicit path to Geo objects
            ),
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=RiskAnalysis,
                name="HAS_RISK",
                description="Identified risks and mitigations",
            ),
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=TechnicalApproach,
                name="HAS_TECH_STACK",
                description="Technical implementation approach",
            ),
            # GraphRelationConfig(
            #     from_node=ReviewedOpportunity,
            #     to_node=CompetitiveLandscape,
            #     name="COMPETIIVE_LANDSCAPE",
            #     description="Competitive analysis",
            # ),
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=Competitor,
                name="HAS_COMPETITOR",
                description="Known competitors",
            ),
        ]
        return GraphSchema(root_model_class=ReviewedOpportunity, nodes=nodes, relations=relations)

    def get_sample_queries(self) -> list[str]:
        """Get list of sample Cypher queries for opportunity data."""
        return [
            "MATCH (n) RETURN labels(n)[0] as NodeType, count(n) as Count",
            "MATCH (o:Opportunity) RETURN o.name, o.status LIMIT 5",
            "MATCH (c:Customer)-[:HAS_CONTACT]->(p:Person) RETURN c.name, p.name, p.role LIMIT 5",
            "MATCH (ro:ReviewedOpportunity)-[:HAS_RISK]->(r:RiskAnalysis) RETURN r.risk_description, r.impact_level LIMIT 3",
            "MATCH (ro:ReviewedOpportunity)-[:HAS_PARTNER]->(partner:Partner) RETURN ro.start_date, partner.name, partner.role",
            "MATCH (o:Opportunity)-[:HAS_CUSTOMER]->(c:Customer) RETURN o.name, c.name, c.segment",
        ]

    # def get_entity_name_from_data(self, data: Any) -> str:
    #     """Extract a human-readable entity name from loaded data."""
    #     if hasattr(data, "ReviewedOpportunity") and hasattr(data.opportunity, "name"):
    #         return data.opportunity.name
    #     return "Unknown Entity"
