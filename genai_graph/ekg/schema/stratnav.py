"""Stratnav graph factory for Neo4j JSONL exports.

This module provides the StratnavGraph factory that processes Neo4j exports
from the Stratnav system. It uses the Neo4jImportFactory base class for
direct import with configurable mappings.

Features:
- Node type renaming: Account → Customer, L3 → L3Service, etc.
- Property renaming: irisCode → iris_code, L3Code → code, etc.
- Filtering: Only import specified node/relationship types
- Direct import: Bypasses hierarchical extraction for efficiency

Usage:
    factory = StratnavGraph(neo4j_export_file="path/to/export.jsonl")
    nodes, rels = factory.build_nodes_and_relationships()

    # Or use with the orchestration:
    # cli kg create --kg simple_neo4j
"""

from __future__ import annotations

from genai_graph.core.subgraph_factories import Neo4jImportFactory


class StratnavGraph(Neo4jImportFactory):
    """Graph factory for Neo4j JSONL exports from Stratnav system.

    Processes Neo4j exports and maps them to a cleaner schema:
    - Account → Customer (with property mapping)
    - L3 → L3Service (with property mapping)
    - Ambition → Ambition (with property mapping)
    - ... and all relationships between them

    The factory can be configured to include/exclude specific node types
    and relationships by overriding get_included_node_types() and
    get_included_rel_types().
    """

    @property
    def name(self) -> str:
        """Factory name for registration."""
        return "StratnavGraph"

    def get_node_mappings(self) -> dict[str, tuple[str, dict[str, str]]]:
        """Define Neo4j to Kuzu node type and property mappings.

        Format: {neo4j_label: (target_type, {neo4j_prop: target_prop, ...})}

        If a property mapping is empty {}, all properties are copied as-is.
        If a property mapping has entries, only mapped properties are included.

        Returns:
            Node type and property mapping configuration
        """
        return {
            # Account → Customer with property renaming
            "Account": (
                "Customer",
                {
                    "name": "name",
                    "irisCode": "iris_code",
                    "country": "country",
                    "subMarket": "segment",
                    "businessLine": "business_line",
                    "revenue": "revenue",
                },
            ),
            # L3 → L3Service with property renaming
            "L3": (
                "L3Service",
                {
                    "L3Code": "code",
                    "name": "name",
                    "L3ServiceDescription": "description",
                    "L3ServiceType": "service_type",
                    "status": "status",
                    "L3Plm": "plm_stage",
                    "L3ServiceMaturityLevel": "maturity_level",
                },
            ),
            # Ambition → Ambition with property renaming
            "Ambition": (
                "Ambition",
                {
                    "ambition_id": "ambition_id",
                    "id": "ambition_id",  # Fallback if id is used instead
                    "ambition_text": "text",
                    "text": "text",  # Fallback
                    "created_at": "created_at",
                },
            ),
            # Add more mappings as needed:
            # "Opportunity": ("Opportunity", {...}),
            # "Contact": ("Contact", {...}),
        }

    def get_relationship_mappings(self) -> dict[str, str]:
        """Define Neo4j to Kuzu relationship type mappings.

        Format: {neo4j_rel_type: target_rel_type}

        Returns:
            Relationship type mapping configuration
        """
        return {
            # Keep relationship names as-is or rename them
            "HAS_AMBITION": "HAS_AMBITION",
            "HAS_L3": "HAS_L3",
            "USES": "USES_SERVICE",
            "BELONGS_TO": "BELONGS_TO",
            # Add more mappings as needed
        }

    def get_included_node_types(self) -> set[str] | None:
        """Define which Neo4j node types to include.

        Returns None to include all discovered types, or a set of
        specific Neo4j labels to include.

        Returns:
            Set of Neo4j labels to include, or None for all
        """
        # Start with a focused set for testing, expand as needed
        return {"Account", "L3", "Ambition"}

    def get_included_rel_types(self) -> set[str] | None:
        """Define which Neo4j relationship types to include.

        Returns None to include all discovered types, or a set of
        specific relationship types to include.

        Returns:
            Set of Neo4j rel types to include, or None for all
        """
        # Include all relationships for now
        # Can filter to specific types if needed:
        # return {"HAS_AMBITION", "HAS_L3", "USES"}
        return None

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
