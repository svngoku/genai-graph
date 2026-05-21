"""RFQ analysis graph for EKG system.

Contains all RFQ-specific data model logic and BAML client integration.
This is the only module that imports RFQ-related BAML client types.
"""

from __future__ import annotations

from pydantic import BaseModel

from genai_graph.ekg.baml_client.types import (
    ComplianceItem,
    ContractualCondition,
    Deliverable,
    EvaluationCriterion,
    Milestone,
    PricingItem,
    Requirement,
    RFQAnalysis,
    RiskItem,
    SubmissionInstructions,
)
from genai_graph.ekg.schema.canonical_nodes import CustomerNode, PersonNode
from genai_graph.kg.factories import JsonFileBackedFactory
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

# ---------------------------------------------------------------------------
# Module-scope node singletons
# Canonical shared types (Customer, Person) are imported from canonical_nodes.
# RFQ-specific types are defined here.
# ---------------------------------------------------------------------------

RFQAnalysisNode: GraphNode = GraphNode(
    node_class=RFQAnalysis,
    extra_classes=[PricingItem, SubmissionInstructions],
    name_from=lambda data, _: (
        data.get("rfq_title") or data.get("rfq_reference_number") or data.get("issuing_organization") or "rfq_unknown"
    ),
    key_from=lambda data, _: data.get("rfq_reference_number") or data.get("rfq_title") or "rfq_unknown",
    description="Root node containing the complete RFQ triage analysis",
    index_fields=["executive_summary"],
)

RequirementNode: GraphNode = GraphNode(
    node_class=Requirement,
    name_from=lambda data, base: data.get("title") or f"{base}_requirement",
    key_from="AUTO_ID",
    description="Individual requirement extracted from the RFQ",
    index_fields=["description"],
)

ContractualConditionNode: GraphNode = GraphNode(
    node_class=ContractualCondition,
    name_from=lambda data, _: getattr(data.get("area"), "name", None) or str(data.get("area", "other")),
    key_from="AUTO_ID",
    description="Contractual or commercial clause extracted from the RFQ",
)

MilestoneNode: GraphNode = GraphNode(
    node_class=Milestone,
    name_from=lambda data, base: data.get("name") or f"{base}_milestone",
    key_from="AUTO_ID",
    description="Timeline milestone extracted from the RFQ",
)

RiskItemNode: GraphNode = GraphNode(
    node_class=RiskItem,
    name_from=lambda data, base: data.get("p_title") or f"{base}_risk",
    key_from="AUTO_ID",
    description="Top risk item identified during RFQ triage",
)

EvaluationCriterionNode: GraphNode = GraphNode(
    node_class=EvaluationCriterion,
    name_from=lambda data, base: data.get("name") or f"{base}_criterion",
    key_from="AUTO_ID",
    description="Evaluation or scoring criterion from the RFQ",
)

DeliverableNode: GraphNode = GraphNode(
    node_class=Deliverable,
    name_from=lambda data, base: data.get("name") or f"{base}_deliverable",
    key_from="AUTO_ID",
    description="Expected deliverable extracted from the RFQ",
)

ComplianceItemNode: GraphNode = GraphNode(
    node_class=ComplianceItem,
    name_from=lambda data, base: data.get("topic") or f"{base}_compliance",
    key_from="AUTO_ID",
    description="Compliance or security obligation extracted from the RFQ",
)


class RFQAnalysisGraph(JsonFileBackedFactory, BaseModel):
    """RFQ analysis graph using JSON files from BAML extract."""

    def get_model_class(self) -> type[BaseModel]:
        """Return the root model class for this graph factory."""
        return RFQAnalysis

    def build_schema(self) -> GraphSchema:
        """Build the graph schema configuration for RFQ analysis data.

        Returns:
            GraphSchema with all node and relationship configurations
        """
        nodes = [
            RFQAnalysisNode,
            CustomerNode,
            PersonNode,
            RequirementNode,
            ContractualConditionNode,
            MilestoneNode,
            RiskItemNode,
            EvaluationCriterionNode,
            DeliverableNode,
            ComplianceItemNode,
        ]

        relations = [
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=CustomerNode,
                name="HAS_CUSTOMER",
                description="Customer or issuing organization identified in the RFQ",
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=PersonNode,
                name="HAS_STAKEHOLDER",
                description="Stakeholders and named contacts from the RFQ process",
                field_paths=[("", "stakeholders")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=RequirementNode,
                name="HAS_REQUIREMENT",
                description="Requirements extracted from the RFQ",
                field_paths=[("", "main_requirements")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=ContractualConditionNode,
                name="HAS_CONTRACTUAL_CONDITION",
                description="Contractual and commercial clauses extracted from the RFQ",
                field_paths=[("", "contractual_conditions")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=MilestoneNode,
                name="HAS_MILESTONE",
                description="Timeline milestones and deadlines from the RFQ",
                field_paths=[("", "timeline")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=RiskItemNode,
                name="HAS_RISK",
                description="Top risks identified during RFQ triage",
                field_paths=[("", "top_risks")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=EvaluationCriterionNode,
                name="HAS_EVALUATION_CRITERION",
                description="Evaluation criteria and scoring weights from the RFQ",
                field_paths=[("", "evaluation_criteria")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=DeliverableNode,
                name="HAS_DELIVERABLE",
                description="Expected deliverables defined in the RFQ",
                field_paths=[("", "deliverables")],
            ),
            GraphRelation(
                from_node=RFQAnalysisNode,
                to_node=ComplianceItemNode,
                name="HAS_COMPLIANCE_REQUIREMENT",
                description="Compliance and security obligations from the RFQ",
                field_paths=[("", "compliance_requirements")],
            ),
        ]

        return GraphSchema(root_model_class=RFQAnalysis, nodes=nodes, relations=relations)

    def get_sample_queries(self) -> list[str]:
        """Get list of sample Cypher queries for RFQ analysis data."""
        return [
            "MATCH (n) RETURN labels(n)[0] as NodeType, count(n) as Count",
            "MATCH (r:RFQAnalysis) RETURN r.rfq_title, r.project_type, r.executive_summary LIMIT 5",
            "MATCH (r:RFQAnalysis)-[:HAS_CUSTOMER]->(c:Customer) RETURN r.rfq_title, c.name LIMIT 10",
            "MATCH (r:RFQAnalysis)-[:HAS_RISK]->(ri:RiskItem) WHERE ri.risk_category IS NOT NULL RETURN r.rfq_title, ri.p_title, ri.risk_category LIMIT 10",
            "MATCH (r:RFQAnalysis)-[:HAS_REQUIREMENT]->(req:Requirement) WHERE req.priority = 'MUST' RETURN r.rfq_title, req.title, req.type LIMIT 10",
            "MATCH (r:RFQAnalysis)-[:HAS_CONTRACTUAL_CONDITION]->(cc:ContractualCondition) WHERE cc.red_flag = true RETURN r.rfq_title, cc.area, cc.summary LIMIT 10",
            "MATCH (r:RFQAnalysis)-[:HAS_MILESTONE]->(m:Milestone) RETURN r.rfq_title, m.name, m.date ORDER BY m.date LIMIT 10",
        ]
