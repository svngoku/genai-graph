"""Stratnav graph factory for Neo4j JSONL exports.

This module provides the StratnavGraph factory that processes Neo4j exports
from the Stratnav system. It uses the Neo4jImportFactory base class with
schema-based mappings for rich documentation support.

Features:
- Node type renaming: Account → Customer, L3 → L3Service, etc.
- Property renaming: irisCode → iris_code, L3Code → code, etc.
- Type-safe Pydantic models for nodes and relationships
- Index fields for vector search
- Relationship descriptions for schema documentation

Usage:
    factory = StratnavGraph(neo4j_export_file="path/to/export.jsonl")
    nodes, rels = factory.build_nodes_and_relationships()

    # Or use with the orchestration:
    # cli kg create --kg simple_neo4j
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from genai_graph.kg.factories import Neo4jImportFactory
from genai_graph.kg.factories.neo4j_factory import Neo4jNodeMapping, Neo4jRelationMapping

# -----------------------------------------------------------------------------
# Pydantic Models for Stratnav Graph Nodes
# -----------------------------------------------------------------------------


class Customer(BaseModel):
    """Customer organization with business context and financials."""

    id: str = Field(description="Unique identifier")
    name: str = Field(description="Customer organization name")
    iris_code: str | None = Field(default=None, description="Unique IRIS system identifier")
    country: str | None = Field(default=None, description="Country where customer is headquartered")
    segment: str | None = Field(default=None, description="Market segment classification")
    business_line: str | None = Field(default=None, description="Primary business line")
    revenue: str | None = Field(default=None, description="Annual revenue figure")


class L3Service(BaseModel):
    """Level 3 service offering in the service catalog."""

    id: str = Field(description="Unique identifier")
    name: str = Field(description="Service offering name")
    code: str | None = Field(default=None, description="Unique service code identifier")
    description: str | None = Field(default=None, description="Detailed service description")
    service_type: str | None = Field(default=None, description="Type of service (e.g., Managed, Consulting)")
    status: str | None = Field(default=None, description="Current service status (Active, Deprecated)")
    plm_stage: str | None = Field(default=None, description="Product lifecycle management stage")
    maturity_level: str | None = Field(default=None, description="Service maturity level")


class Ambition(BaseModel):
    """Strategic ambition or goal for a customer relationship."""

    id: str = Field(description="Unique identifier")
    name: str = Field(description="Display name")
    ambition_id: str | None = Field(default=None, description="Unique ambition identifier")
    text: str | None = Field(default=None, description="Description of the strategic ambition")
    created_at: str | None = Field(default=None, description="Date when ambition was created")


class GEO(BaseModel):
    """Geographic region or country."""

    id: str = Field(description="Unique identifier")
    name: str = Field(description="Geographic region code")
    country: str | None = Field(default=None, description="Full country name")


class StratnavGraph(Neo4jImportFactory):
    """Graph factory for Neo4j JSONL exports from Stratnav system.

    Processes Neo4j exports and maps them to a cleaner schema with
    full documentation support for LLM schema generation:
    - Account → Customer (with property mapping)
    - L3 → L3Service (with property mapping)
    - Ambition → Ambition (with property mapping)
    - GEO → GEO (geographic regions)

    Uses type-safe Pydantic models (Customer, L3Service, etc.) for both
    node and relationship mappings.
    """

    @property
    def name(self) -> str:
        """Factory name for registration."""
        return "StratnavGraph"

    def get_node_mappings(self) -> list[Neo4jNodeMapping]:
        """Define Neo4j to Kuzu node type and property mappings.

        Returns:
            List of Neo4jNodeMapping configurations with Pydantic classes
        """
        return [
            # Account → Customer with property renaming
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
                key_field="iris_code",
                index_fields=["name", "segment"],
            ),
            # L3 → L3Service with property renaming
            Neo4jNodeMapping(
                neo4j_label="L3",
                node_class=L3Service,
                property_mappings={
                    "L3Code": "code",
                    "name": "name",
                    "L3ServiceDescription": "description",
                    "L3ServiceType": "service_type",
                    "status": "status",
                    "L3Plm": "plm_stage",
                    "L3ServiceMaturityLevel": "maturity_level",
                },
                name_field="name",
                key_field="code",
                index_fields=["name", "description"],
            ),
            # Ambition → Ambition with property renaming
            Neo4jNodeMapping(
                neo4j_label="Ambition",
                node_class=Ambition,
                property_mappings={
                    "ambition_id": "ambition_id",
                    "id": "ambition_id",
                    "ambition_text": "text",
                    "text": "text",
                    "created_at": "created_at",
                },
                name_field="text",
                key_field="ambition_id",
                index_fields=["text"],
            ),
            # GEO → GEO geographic region
            Neo4jNodeMapping(
                neo4j_label="GEO",
                node_class=GEO,
                property_mappings={
                    "name": "name",
                    "country": "country",
                },
                name_field="name",
                key_field="name",
            ),
        ]

    def get_included_rel_types(self) -> set[str] | None:
        """Get the set of Neo4j relationship types to include."""
        return {"LOCATED_IN", "SIMILAR_TO", "X_SELL"}

    def get_relation_mappings(self) -> list[Neo4jRelationMapping]:
        """Define Neo4j to Kuzu relationship type mappings.

        Note: These mappings must match actual relationship types in the Neo4j data.
        Check the JSONL file for available relationship types using:
            grep '"type": "relationship"' file.jsonl | grep -o '"label": "[^"]*"' | sort | uniq -c

        Returns:
            List of Neo4jRelationMapping configurations with Pydantic classes
        """
        return [
            # Account → GEO: Geographic location of customer
            Neo4jRelationMapping(
                neo4j_type="LOCATED_IN",
                from_node=Customer,
                to_node=GEO,
                description="Geographic location where the customer operates",
            ),
            # L3 → L3: Similar services
            Neo4jRelationMapping(
                neo4j_type="SIMILAR_TO",
                from_node=L3Service,
                to_node=L3Service,
                description="Indicates similar L3 service offerings",
            ),
            # L3 → L3: Cross-sell opportunities
            Neo4jRelationMapping(
                neo4j_type="X_SELL",
                target_rel="CROSS_SELL",
                from_node=L3Service,
                to_node=L3Service,
                description="Cross-selling opportunity between services",
            ),
        ]

    def get_sample_queries(self) -> list[str]:
        """Get sample Cypher queries for the Stratnav graph.

        Returns:
            List of example query strings
        """
        return [
            "MATCH (n) RETURN labels(n)[0] as NodeType, count(n) as Count",
            "MATCH (c:Customer) RETURN c.name, c.segment, c.iris_code LIMIT 5",
            "MATCH (l:L3Service) RETURN l.name, l.code, l.status LIMIT 5",
            "MATCH (a:Ambition) RETURN a.ambition_id, a.text LIMIT 5",
            "MATCH (c:Customer)-[r:HAS_AMBITION]->(a:Ambition) RETURN c.name, a.text LIMIT 5",
        ]


# -----------------------------------------------------------------------------
# Standalone Testing
# -----------------------------------------------------------------------------

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
