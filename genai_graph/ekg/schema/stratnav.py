"""Stratnav graph factory for Neo4j JSONL exports.

This module provides the StratnavGraph factory that processes Neo4j exports
from the Stratnav system. It uses the Neo4jImportFactory base class with
schema-based mappings for rich documentation support.

Features:
- Complete node type mappings for all Stratnav entities
- Comprehensive relationship mappings with property support
- Type-safe Pydantic models for all nodes and relationships
- Index fields for vector search
- Relationship descriptions for schema documentation

Usage:
    factory = StratnavGraph(neo4j_export_file="path/to/export.jsonl")
    nodes, rels = factory.build_nodes_and_relationships()

    # Or use with the orchestration:
    # cli kg create --kg stratnav
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from genai_graph.ekg.schema.common_nodes import Customer, Geo, L3, Partner
from genai_graph.kg.factories import Neo4jImportFactory
from genai_graph.kg.factories.neo4j_factory import Neo4jNodeMapping, Neo4jRelationMapping

# =============================================================================
# Pydantic Models for Stratnav Graph Nodes
# =============================================================================

# Note: Customer is imported from common_nodes.py to ensure node unification
# across factories. The neo4j "Account" label is mapped to Customer.


class Ambition(BaseModel):
    """Strategic goal or vision for customer engagement."""

    name: str = Field(description="Ambition title")
    ambition_id: str | None = Field(default=None, description="Unique ambition code")
    text: str | None = Field(default=None, description="Full ambition description")
    user_email: str | None = Field(default=None, description="Author email")


class BL(BaseModel):
    """Business Line in the service catalog hierarchy."""

    name: str = Field(description="Business line name")
    longname: str | None = Field(default=None, description="Full business line name")
    status: str | None = Field(default=None, description="Status (Active/Deprecated)")


class L1(BaseModel):
    """Level 1 service portfolio division."""

    name: str = Field(description="L1 portfolio name")
    status: str | None = Field(default=None, description="Status (Active/Deprecated)")
    sales_portal_url: str | None = Field(default=None, description="Sales portal link")


class L2(BaseModel):
    """Level 2 service offering within L1."""

    name: str = Field(description="L2 service name")
    code: str | None = Field(default=None, description="Service code")
    description: str | None = Field(default=None, description="Service details")
    status: str | None = Field(default=None, description="Status (Active/Deprecated)")
    sales_portal_url: str | None = Field(default=None, description="Sales portal link")
    bl: str | None = Field(default=None, description="Parent business line")
    major_version: int | None = Field(default=None, description="Major version number")
    minor_version: int | None = Field(default=None, description="Minor version number")


class L4(BaseModel):
    """Level 4 service component - sub-service detail."""

    name: str = Field(description="Component name")


# =============================================================================
# Stratnav Graph Factory
# =============================================================================


class StratnavGraph(Neo4jImportFactory):
    """Graph factory for complete Neo4j Stratnav system imports.

    Processes Neo4j JSONL exports with full schema support:
    - All 9 node types (Customer, Ambition, BL, Counter, Geo, L1, L2, L3, L4, Partner)
    - All 20 relationship types with properties
    - Property renaming and filtering
    - Full documentation for LLM schema generation

    Note: Neo4j 'Account' nodes are mapped to the canonical 'Customer' type
    and 'TechnologyPartner' nodes to the canonical 'Partner' type,
    both from common_nodes.py, to ensure proper node unification across factories.
    """

    @property
    def name(self) -> str:
        """Factory name for registration."""
        return "StratnavGraph"

    def get_node_mappings(self) -> list[Neo4jNodeMapping]:
        """Define Neo4j to target node type and property mappings.

        Returns:
            List of Neo4jNodeMapping configurations with Pydantic classes
        """
        return [
            # Account -> Customer: Customer organization (maps to canonical Customer type)
            # Using 'name' as key_field for consistency with common_nodes.Customer
            Neo4jNodeMapping(
                neo4j_label="Account",
                node_class=Customer,
                property_mappings={
                    "name": "name",
                    "irisCode": "iris_code",
                    "country": "country",
                    "subMarket": "segment",
                    "businessLine": "business_line",
                    "revenue": "revenue",
                },
                name_field="name",
                key_field="name",  # Must match common_nodes for cross-factory deduplication
                index_fields=["name", "segment"],
            ),
            # Ambition: Strategic goal
            Neo4jNodeMapping(
                neo4j_label="Ambition",
                node_class=Ambition,
                property_mappings={
                    "ambition_id": "ambition_id",
                    "ambition_text": "text",
                    "text": "text",
                    "user_email": "user_email",
                },
                name_field="text",
                key_field="ambition_id",
                index_fields=["text"],
            ),
            # BL: Business Line
            Neo4jNodeMapping(
                neo4j_label="BL",
                node_class=BL,
                property_mappings={
                    "name": "name",
                    "longname": "longname",
                    "status": "status",
                },
                name_field="name",
                key_field="name",
            ),
            # GEO: Geographic region
            Neo4jNodeMapping(
                neo4j_label="GEO",
                node_class=Geo,
                property_mappings={
                    "name": "name",
                    "country": "country",
                },
                name_field="name",
                key_field="name",
            ),
            # L1: Level 1 portfolio
            Neo4jNodeMapping(
                neo4j_label="L1",
                node_class=L1,
                property_mappings={
                    "name": "name",
                    "status": "status",
                    "L1SalesPortalUrl": "sales_portal_url",
                },
                name_field="name",
                key_field="name",
                index_fields=["name"],
            ),
            # L2: Level 2 service
            Neo4jNodeMapping(
                neo4j_label="L2",
                node_class=L2,
                property_mappings={
                    "name": "name",
                    "L2Code": "code",
                    "desc": "description",
                    "status": "status",
                    "L2SalesPortalUrl": "sales_portal_url",
                    "bl": "bl",
                    "majorVersion": "major_version",
                    "minorVersion": "minor_version",
                },
                name_field="name",
                key_field="code",
                index_fields=["name", "description"],
            ),
            # L3: Level 3 service (primary)
            Neo4jNodeMapping(
                neo4j_label="L3",
                node_class=L3,
                property_mappings={
                    "name": "name",
                    "L3Code": "code",
                    "L3ServiceDescription": "description",
                    "L3ServiceType": "service_type",
                    "status": "status",
                    "L3Plm": "plm_stage",
                    "L3ServiceMaturityLevel": "maturity_level",
                    "L3id": "service_id",
                    "availableForNewDeals": "available_for_new_deals",
                    "allowInJourneys": "allow_in_journeys",
                    "showInCatalog": "show_in_catalog",
                    "L3PreSalesUrl": "pre_sales_url",
                    "SalesPortalUrl": "sales_portal_url",
                    "keyBuzzWords": "key_buzz_words",
                    "grd_definition": "grd_definition",
                    "deprecatedAt": "deprecated_at",
                    "deprecationReason": "deprecation_reason",
                    "gracePeriodEnds": "grace_period_ends",
                    "majorVersion": "major_version",
                    "minorVersion": "minor_version",
                },
                name_field="name",
                key_field="code",
                index_fields=["name", "description"],
            ),
            # L4: Level 4 component
            Neo4jNodeMapping(
                neo4j_label="L4",
                node_class=L4,
                property_mappings={
                    "name": "name",
                },
                name_field="name",
                key_field="name",
            ),
            # TechnologyPartner -> Partner: Vendor/Partner (maps to canonical Partner type)
            Neo4jNodeMapping(
                neo4j_label="TechnologyPartner",
                node_class=Partner,
                property_mappings={
                    "name": "name",
                },
                name_field="name",
                key_field="name",
            ),
        ]

    def get_included_rel_types(self) -> set[str] | None:
        """Get the set of Neo4j relationship types to include."""
        return {
            "LOCATED_IN__Account__GEO",
            "HAS__Account__Ambition",
            "RECOMMEND__Ambition__L3",
            "BL_OUTCOMES__BL__L1",
            "BL_OFFERINGS__L1__L2",
            "DELIVERS__BL__L2",
            "CONSISTS_OF__L2__L3",
            "CONSIST_OF__L3__L4",
            "SIMILAR_TO__L3__L3",
            "X_SELL__L3__L3",
            "ALL_X_SELL__L3__L3",
            "DELIVERY_LOCATION__L3__GEO",
            "ALLOWED_IN__L3__GEO",
            "EXCLUDED_FROM__L3__GEO",
            "CANNOT_DELIVER_FROM__L3__GEO",
            "DISCOVERED_PLATFORM_SYNERGY__L3__L3",
            "DISCOVERED_THEMATIC__L3__L3",
            "Journey__L3__L3",
            "PARTNER_WITH__L3__TechnologyPartner",
        }

    def get_relation_mappings(self) -> list[Neo4jRelationMapping]:
        """Define Neo4j to target relationship type mappings.

        Returns:
            List of Neo4jRelationMapping configurations with Pydantic classes
        """
        return [
            # Customer relationships (Account in neo4j maps to Customer)
            Neo4jRelationMapping(
                neo4j_type="LOCATED_IN__Account__GEO",
                target_rel="LOCATED_IN",
                from_node=Customer,
                to_node=Geo,
                description="Geographic location where customer operates",
            ),
            Neo4jRelationMapping(
                neo4j_type="HAS__Account__Ambition",
                target_rel="HAS_AMBITION",
                from_node=Customer,
                to_node=Ambition,
                description="Strategic ambition of the customer",
            ),
            # Ambition to L3 recommendation
            Neo4jRelationMapping(
                neo4j_type="RECOMMEND__Ambition__L3",
                target_rel="RECOMMEND",
                from_node=Ambition,
                to_node=L3,
                description="Recommended service for ambition",
                property_mappings={
                    "ai_justification": "ai_justification",
                    "confidence_score": "confidence_score",
                    "similarity_score": "similarity_score",
                    "priorityNumber": "priority_number",
                },
            ),
            # BL hierarchy
            Neo4jRelationMapping(
                neo4j_type="BL_OUTCOMES__BL__L1",
                target_rel="BL_OUTCOMES",
                from_node=BL,
                to_node=L1,
                description="Business outcome delivered by L1",
            ),
            Neo4jRelationMapping(
                neo4j_type="BL_OFFERINGS__L1__L2",
                target_rel="BL_OFFERINGS",
                from_node=L1,
                to_node=L2,
                description="L2 offering in L1 portfolio",
            ),
            Neo4jRelationMapping(
                neo4j_type="DELIVERS__BL__L2",
                target_rel="DELIVERS",
                from_node=BL,
                to_node=L2,
                description="L2 service delivering business line",
            ),
            # Service hierarchy
            Neo4jRelationMapping(
                neo4j_type="CONSISTS_OF__L2__L3",
                target_rel="CONSISTS_OF",
                from_node=L2,
                to_node=L3,
                description="L3 service composition of L2",
                property_mappings={
                    "majorVersion": "major_version",
                    "minorVersion": "minor_version",
                },
            ),
            Neo4jRelationMapping(
                neo4j_type="CONSIST_OF__L3__L4",
                target_rel="CONSIST_OF",
                from_node=L3,
                to_node=L4,
                description="L4 component of L3 service",
            ),
            # L3 similarity
            Neo4jRelationMapping(
                neo4j_type="SIMILAR_TO__L3__L3",
                target_rel="SIMILAR_TO",
                from_node=L3,
                to_node=L3,
                description="Similar service offerings",
                property_mappings={
                    "cosineSimilarity": "cosine_similarity",
                },
            ),
            # Cross-sell opportunities
            Neo4jRelationMapping(
                neo4j_type="X_SELL__L3__L3",
                target_rel="CROSS_SELL",
                from_node=L3,
                to_node=L3,
                description="Cross-sell opportunity between services",
                property_mappings={
                    "combined_value": "combined_value",
                    "overall_strength": "overall_strength",
                    "theme": "theme",
                    "weight": "weight",
                    "roi_multiplier": "roi_multiplier",
                },
            ),
            Neo4jRelationMapping(
                neo4j_type="ALL_X_SELL__L3__L3",
                target_rel="CROSS_SELL_ALL",
                from_node=L3,
                to_node=L3,
                description="All cross-sell candidates with extended properties",
                property_mappings={
                    "combined_value": "combined_value",
                    "overall_strength": "overall_strength",
                    "theme": "theme",
                    "weight": "weight",
                    "roi_multiplier": "roi_multiplier",
                    "integration_complexity": "integration_complexity",
                },
            ),
            # L3 geographic relationships
            Neo4jRelationMapping(
                neo4j_type="DELIVERY_LOCATION__L3__GEO",
                target_rel="DELIVERY_LOCATION",
                from_node=L3,
                to_node=Geo,
                description="Geographic location where L3 is delivered",
            ),
            Neo4jRelationMapping(
                neo4j_type="ALLOWED_IN__L3__GEO",
                target_rel="ALLOWED_IN",
                from_node=L3,
                to_node=Geo,
                description="L3 allowed for delivery in region",
            ),
            Neo4jRelationMapping(
                neo4j_type="EXCLUDED_FROM__L3__GEO",
                target_rel="EXCLUDED_FROM",
                from_node=L3,
                to_node=Geo,
                description="L3 explicitly excluded from region",
            ),
            Neo4jRelationMapping(
                neo4j_type="CANNOT_DELIVER_FROM__L3__GEO",
                target_rel="CANNOT_DELIVER_FROM",
                from_node=L3,
                to_node=Geo,
                description="L3 cannot be delivered in region",
            ),
            # L3 synergy relationships
            Neo4jRelationMapping(
                neo4j_type="DISCOVERED_PLATFORM_SYNERGY__L3__L3",
                target_rel="PLATFORM_SYNERGY",
                from_node=L3,
                to_node=L3,
                description="Platform synergy between services",
                property_mappings={
                    "discovered_at": "discovered_at",
                    "score": "score",
                    "rationale": "rationale",
                },
            ),
            Neo4jRelationMapping(
                neo4j_type="DISCOVERED_THEMATIC__L3__L3",
                target_rel="THEMATIC_SYNERGY",
                from_node=L3,
                to_node=L3,
                description="Thematic synergy between services",
                property_mappings={
                    "discovered_at": "discovered_at",
                    "score": "score",
                    "rationale": "rationale",
                },
            ),
            # Journey relationships
            Neo4jRelationMapping(
                neo4j_type="Journey__L3__L3",
                target_rel="JOURNEY_STEP",
                from_node=L3,
                to_node=L3,
                description="L3 sequencing in customer journey",
                property_mappings={
                    "journey_name": "journey_name",
                    "step_number": "step_number",
                    "account_iris_code": "account_iris_code",
                    "account_name": "account_name",
                },
            ),
            # Partner relationships
            Neo4jRelationMapping(
                neo4j_type="PARTNER_WITH__L3__TechnologyPartner",
                target_rel="PARTNER_WITH",
                from_node=L3,
                to_node=Partner,
                description="Technology partner for L3 service",
            ),
        ]

    def get_sample_queries(self) -> list[str]:
        """Get sample Cypher queries for the Stratnav graph.

        Returns:
            List of example query strings
        """
        return [
            "MATCH (n) RETURN labels(n)[0] as NodeType, count(n) as Count",
            "MATCH (c:Customer) RETURN c.name, c.iris_code, c.segment LIMIT 5",
            "MATCH (l:L3) RETURN l.name, l.code, l.status LIMIT 5",
            "MATCH (amb:Ambition) RETURN amb.text LIMIT 5",
            "MATCH (c:Customer)-[r:HAS_AMBITION]->(amb:Ambition) RETURN c.name, amb.text LIMIT 5",
            "MATCH (l:L3)-[r:CROSS_SELL]->(l2:L3) RETURN l.name, r.theme, l2.name LIMIT 5",
            "MATCH (l1:L1)-[r:BL_OFFERINGS]->(l2:L2) RETURN l1.name, l2.name LIMIT 5",
            "MATCH (l3:L3)-[r:DELIVERY_LOCATION]->(g:GEO) RETURN l3.name, g.country LIMIT 5",
        ]


# =============================================================================
# Standalone Testing
# =============================================================================

if __name__ == "__main__":
    from genai_tk.utils.config_mngr import global_config
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    console.print(Panel.fit("[bold cyan]Testing StratnavGraph[/bold cyan]", border_style="cyan"))

    # Get config paths
    cfg = global_config()
    stratnav_root = cfg.get_dir_path("paths.stratnav_db")

    # Test with subset file
    subset_file = stratnav_root / "subset" / "sn-subset.jsonl"

    if not subset_file.exists():
        console.print(f"[red]Test file not found: {subset_file}[/red]")
        console.print("[yellow]Run 'cli neo4j subset' first to create test data[/yellow]")
        exit(1)

    console.print(f"\n[bold blue]Test 1:[/bold blue] Loading Neo4j JSONL: {subset_file}")

    # Create the graph factory
    factory = StratnavGraph(neo4j_export_file=str(subset_file))

    # Check if initialized
    if not factory._initialized:
        console.print("[red]✗[/red] Failed to initialize factory")
        exit(1)

    console.print("[green]✓[/green] StratnavGraph initialized successfully")

    # Test 2: Check discovered schema
    console.print("\n[bold blue]Test 2:[/bold blue] Discovered schema from JSONL")

    schema_info = factory.get_schema_info()
    if schema_info:
        console.print(f"  Total nodes: {schema_info.total_nodes}")
        console.print(f"  Total relationships: {schema_info.total_relationships}")
        console.print(f"  Node labels: {list(schema_info.node_tables.keys())}")
        console.print(f"  Rel types: {list(schema_info.rel_tables.keys())}")

    # Test 3: Check raw node data
    console.print("\n[bold blue]Test 3:[/bold blue] Raw node data by label")
    for label in factory._node_data.keys():
        count = len(factory._node_data[label])
        console.print(f"  {label}: {count} nodes")

    # Test 4: Check raw relationship data
    console.print("\n[bold blue]Test 4:[/bold blue] Raw relationship data by type")
    for rel_type in factory._rel_data.keys():
        count = len(factory._rel_data[rel_type])
        console.print(f"  {rel_type}: {count} relationships")

    # Test 5: Build nodes and relationships
    console.print("\n[bold blue]Test 5:[/bold blue] Building mapped nodes and relationships")
    nodes_data, relationships = factory.build_nodes_and_relationships()

    console.print(f"  Total nodes: {nodes_data.total_count()}")
    for node_type, node_list in nodes_data.items():
        console.print(f"    {node_type}: {len(node_list)} nodes")
        if node_list:
            # Show first node as example
            first = node_list[0]
            console.print(f"      Example: {dict(list(first.items())[:4])}...")

    console.print(f"  Total relationships: {len(relationships)}")
    if relationships:
        # Group by type
        rel_counts: dict[str, int] = {}
        for rel in relationships:
            key = f"{rel.from_type}-[{rel.name}]->{rel.to_type}"
            rel_counts[key] = rel_counts.get(key, 0) + 1
        for key, count in rel_counts.items():
            console.print(f"    {key}: {count}")

    # Test 6: Sample queries
    console.print("\n[bold blue]Test 6:[/bold blue] Sample queries")
    for query in factory.get_sample_queries():
        console.print(f"  • {query}")

    console.print(Panel.fit("[bold green]All tests completed! ✓[/bold green]", border_style="green"))
