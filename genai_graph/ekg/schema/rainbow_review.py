"""Opportunity graph for EKG system.

Contains all opportunity-specific data model logic and BAML client integration.
This is the only module that imports BAML client types.
"""

from __future__ import annotations

from pydantic import BaseModel

from genai_graph.ekg.baml_client.types import (
    CompetitiveLandscape,
    Competitor,
    FinancialMetrics,
    KeyStatementOfWorkElement,
    ReviewedOpportunity,
    RiskAnalysis,
    TechnicalApproach,
)
from genai_graph.ekg.schema.canonical_nodes import CustomerNode, OpportunityNode, PartnerNode, PersonNode
from genai_graph.ekg.schema.common_nodes import Geo
from genai_graph.kg.factories import JsonFileBackedFactory
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

# ---------------------------------------------------------------------------
# Module-scope node singletons
# Canonical shared types (Opportunity, Customer, Person, Partner) are imported
# from canonical_nodes.  Rainbow-specific types are defined here.
# ---------------------------------------------------------------------------

ReviewedOpportunityNode: GraphNode = GraphNode(
    node_class=ReviewedOpportunity,
    extra_classes=[FinancialMetrics, CompetitiveLandscape, KeyStatementOfWorkElement],
    name_from=lambda data, _: (
        "Review."
        + str(data.get("opportunity", {}).get("opportunity_id", ""))
        + "."
        + str(data.get("start_date", ""))
    ),
    key_from=lambda data, _: str(data.get("opportunity", {}).get("opportunity_id", "unknown")),
    description="Root node containing the complete reviewed opportunity",
)

RiskAnalysisNode: GraphNode = GraphNode(
    node_class=RiskAnalysis,
    name_from=lambda data, _: (
        getattr(data.get("risk_category"), "name", None) or str(data.get("risk_category", "other_risk"))
    ),
    key_from=lambda data, _: str(getattr(data.get("risk_category"), "name", None) or "Other Risks"),
    description="Risk assessment and mitigation details",
)

TechnicalApproachNode: GraphNode = GraphNode(
    node_class=TechnicalApproach,
    name_from=lambda data, base: (
        data.get("technical_stack") or data.get("architecture") or f"{base}_default"
    ),
    key_from="AUTO_ID",
    description="Technical implementation approach and stack",
    index_fields=["architecture", "technical_stack"],
)

CompetitorNode: GraphNode = GraphNode(
    node_class=Competitor,
    name_from=lambda data, base: data.get("known_as") or data.get("name") or f"{base}_competitor",
    key_from=lambda data, base: data.get("known_as") or data.get("name") or f"{base}_competitor",
    description="Competitor",
)

# Geo in rainbow uses a computed name (geo_code / country) — different from canonical GeoNode
GeoDeliveryNode: GraphNode = GraphNode(
    node_class=Geo,
    name_from=lambda data, base: data.get("geo_code") or data.get("country") or f"{base}_geo",
    key_from="name",  # computed name is stored as PK (consistent with Stratnav Geo)
    description="Geographic location for delivery",
    explicitly_defined=True,  # linked via explicit field_path in DELIVERED_IN
)


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
        nodes = [
            OpportunityNode,
            CustomerNode,
            PersonNode,
            ReviewedOpportunityNode,
            RiskAnalysisNode,
            TechnicalApproachNode,
            CompetitorNode,
            PartnerNode,
            GeoDeliveryNode,
        ]

        relations = [
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=OpportunityNode,
                name="REVIEWS",
                description="Review relationship to core opportunity",
            ),
            GraphRelation(
                from_node=OpportunityNode,
                to_node=CustomerNode,
                name="HAS_CUSTOMER",
                description="Opportunity belongs to customer",
            ),
            GraphRelation(
                from_node=CustomerNode,
                to_node=PersonNode,
                name="HAS_CONTACT",
                description="Customer contact persons",
            ),
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=PersonNode,
                name="HAS_TEAM_MEMBER",
                description="Internal team members",
                field_paths=[("", "team")],  # Use team field (not customer.employees)
            ),
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=PartnerNode,
                name="HAS_PARTNER",
                description="Partner organizations involved",
            ),
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=GeoDeliveryNode,
                name="DELIVERED_IN",
                description="Geographic locations where solution is delivered",
                field_paths=[("", "delivery_info.locations")],
            ),
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=RiskAnalysisNode,
                name="HAS_RISK",
                description="Identified risks and mitigations",
            ),
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=TechnicalApproachNode,
                name="HAS_TECH_STACK",
                description="Technical implementation approach",
            ),
            GraphRelation(
                from_node=ReviewedOpportunityNode,
                to_node=CompetitorNode,
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
