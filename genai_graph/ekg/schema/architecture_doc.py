"""Architecture document graph for EKG system.

Contains all architecture-specific data model logic and BAML client integration.
Builds a knowledge graph for Software Architecture documents with technical components
and solutions as nodes, and their relationships and purposes as edges.
"""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from genai_graph.ekg.baml_client.types import Solution, SWArchitectureDocument, TechnicalComponent
from genai_graph.ekg.schema.canonical_nodes import CustomerNode, OpportunityNode, PersonNode
from genai_graph.kg.factories import JsonFileBackedFactory
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

# ---------------------------------------------------------------------------
# Module-scope node singletons
# ---------------------------------------------------------------------------

SWArchitectureDocumentNode: GraphNode = GraphNode(
    node_class=SWArchitectureDocument,
    name_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
    key_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
    description="Root node containing the complete architecture document with technical stack and solutions",
)

TechnicalComponentNode: GraphNode = GraphNode(
    node_class=TechnicalComponent,
    name_from="name",
    key_from="name",
    description="Individual technology, framework, platform, tool, or infrastructure component",
)

SolutionNode: GraphNode = GraphNode(
    node_class=Solution,
    name_from="name",
    key_from="name",
    description="Specific product, managed service, or OSS solution used in the architecture",
)


class ArchitectureDocumentGraph(JsonFileBackedFactory, BaseModel):
    """Architecture document data graph using JSON files from BAML extract."""

    TOP_CLASS: Type[BaseModel] = SWArchitectureDocument

    @property
    def name(self) -> str:
        """Name of the graph in the registry."""
        return "ArchitectureDocument"

    def build_schema(self) -> GraphSchema:
        """Build the graph schema configuration for architecture document data.

        Returns:
            GraphSchema with all node and relationship configurations
        """
        nodes = [
            OpportunityNode,
            CustomerNode,
            PersonNode,
            SWArchitectureDocumentNode,
            TechnicalComponentNode,
            SolutionNode,
        ]

        # BAML properties matching p_*_ pattern (e.g., p_purpose_) are automatically
        # converted to edge properties
        relations = [
            GraphRelation(
                from_node=SWArchitectureDocumentNode,
                to_node=OpportunityNode,
                name="SOFWARE_ARCHITECURE",
                description="Architecture document for the opportunity/project",
            ),
            GraphRelation(
                from_node=SWArchitectureDocumentNode,
                to_node=TechnicalComponentNode,
                name="USED_TECHNOLOGY",
                description="Architecture includes this technology component.",
            ),
            GraphRelation(
                from_node=SWArchitectureDocumentNode,
                to_node=SolutionNode,
                name="USED_SOLUTION",
                description="Architecture leverages this solution.",
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
        ]
        doc_nodes, doc_relations = self.get_document_schema_elements(SWArchitectureDocumentNode)
        return GraphSchema(
            root_model_class=self.TOP_CLASS,
            nodes=nodes + doc_nodes,
            relations=relations + doc_relations,
        )

    def get_sample_queries(self) -> list[str]:
        """Get list of sample Cypher queries for architecture data."""
        return [
            # Node type summary
            "MATCH (n) RETURN labels(n)[0] as NodeType, count(n) as Count",
            # List all technologies in the stack
            (
                "MATCH (doc:SWArchitectureDocument)-[r:USED_TECHNOLOGY]->(tech:TechnicalComponent) "
                "RETURN tech.name, tech.type, r.p_purpose_ LIMIT 10"
            ),
            # List all solutions in the architecture
            (
                "MATCH (doc:SWArchitectureDocument)-[r:USED_SOLUTION]->(sol:Solution) "
                "RETURN sol.name, sol.vendor, sol.type, r.p_purpose_ LIMIT 10"
            ),
            # Find L3 services mapped to solutions
            (
                "MATCH (sol:Solution)-[r:MAPS_TO_SERVICE]->(l3:L3) "
                "RETURN sol.name as Solution, l3.name as L3Service, l3.code as ServiceCode LIMIT 10"
            ),
        ]

    # def get_entity_name_from_data(self, data: Any) -> str:
    #     """Extract a human-readable entity name from loaded data."""
    #     if hasattr(data, "opportunity") and hasattr(data.opportunity, "name"):
    #         return f"Architecture: {data.opportunity.name}"
    #     if hasattr(data, "document_date"):
    #         return f"Architecture: {data.document_date}"
    #     return "Architecture Document"
