"""Architecture document graph for EKG system.

Contains all architecture-specific data model logic and BAML client integration.
Builds a knowledge graph for Software Architecture documents with technical components
and solutions as nodes, and their relationships and purposes as edges.
"""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from genai_graph.ekg.baml_client.types import SWArchitectureDocument
from genai_graph.kg.factories import JsonFileBackedFactory
from genai_graph.kg.schema import GraphSchema


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
        from genai_graph.ekg.baml_client.types import (
            Customer,
            Opportunity,
            Person,
            Solution,
            SWArchitectureDocument,
            TechnicalComponent,
        )
        from genai_graph.ekg.schema.common_nodes import L3
        from genai_graph.kg.schema import (
            GraphNode,
            GraphRelation,
        )

        # Note: We use BAML types directly here (not extended types from common_nodes)
        # because SWArchitectureDocument's fields reference these BAML types.

        # Define nodes with descriptions
        nodes = [
            # BAML types that SWArchitectureDocument references
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
            ),
            GraphNode(
                node_class=Person,
                name_from="name",
                key_from="name",
                description="Individual contacts and team members",
            ),
            # Root node - the architecture document itself
            GraphNode(
                node_class=SWArchitectureDocument,
                name_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
                key_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
                description="Root node containing the complete architecture document with technical stack and solutions",
            ),
            # Technical Component nodes - individual technologies and tools
            GraphNode(
                node_class=TechnicalComponent,
                name_from="name",
                key_from="name",  # Use name field as primary key for deduplication
                description="Individual technology, framework, platform, tool, or infrastructure component",
                index_fields=["name", "type"],
            ),
            # Solution nodes - managed services, products, and OSS solutions
            GraphNode(
                node_class=Solution,
                name_from="name",
                key_from="name",  # Use name field as primary key for deduplication
                description="Specific product, managed service, or OSS solution used in the architecture",
                index_fields=["name", "vendor", "type"],
            ),
            # L3 service nodes from Stratnav catalog
            # Note: L3 nodes are primarily imported from Stratnav (with code as PK).
            # In architecture docs, L3 is referenced by name. Using name as key here
            # ensures deduplication within this factory; cross-factory merging with
            # Stratnav L3 nodes happens at the graph merge level.
            GraphNode(
                node_class=L3,
                name_from="name",
                key_from="name",
                description="Level 3 service offering from service catalog",
                index_fields=["name", "description"],
            ),
        ]

        # Define relationships with descriptions
        # BAML properties matching p_*_ pattern (e.g., p_purpose_) are automatically
        # converted to edge properties
        relations = [
            # Document to project
            GraphRelation(
                from_node=SWArchitectureDocument,  # Top class
                to_node=Opportunity,
                name="SOFWARE_ARCHITECURE",
                description="Architecture document for the opportunity/project",
            ),
            # Document to technical components in the stack
            GraphRelation(
                from_node=SWArchitectureDocument,
                to_node=TechnicalComponent,
                name="USED_TECHNOLOGY",
                description="Architecture includes this technology component.",
            ),
            # Document to solutions
            GraphRelation(
                from_node=SWArchitectureDocument,
                to_node=Solution,
                name="USED_SOLUTION",
                description="Architecture leverages this solution. ",
            ),
            GraphRelation(
                from_node=Opportunity,
                to_node=Customer,
                name="HAS_CUSTOMER",
                description="Opportunity belongs to customer",
            ),
            GraphRelation(
                from_node=Customer, to_node=Person, name="HAS_CONTACT", description="Customer contact persons"
            ),
            # Solution to L3 service catalog mapping
            GraphRelation(
                from_node=Solution,
                to_node=L3,
                name="MAPS_TO_SERVICE",
                description="Solution maps to or is delivered by this L3 service offering",
            ),
            # Component to component relationships (dependencies/integration)
        ]

        return GraphSchema(root_model_class=self.TOP_CLASS, nodes=nodes, relations=relations)

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
