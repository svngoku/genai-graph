"""Stratnav subgraph factory for Neo4j JSONL exports.

This module provides the StratnavSubgraph factory that processes Neo4j exports
from the Stratnav system and maps them to the EKG schema.
"""

from __future__ import annotations

from typing import Any, Type

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.core.graph_schema import GraphNode, GraphSchema
from genai_graph.core.subgraph_factories import Neo4jSubgraphFactory
from genai_graph.ekg.schema.common_nodes import Customer, GeoLocation, L3Service

# -----------------------------------------------------------------------------
# Node-to-Model mapping configuration
# -----------------------------------------------------------------------------


class Neo4jNodeMapping(BaseModel):
    """Mapping configuration from Neo4j node label to Pydantic model."""

    neo4j_label: str
    target_class: Type[BaseModel]
    field_mapping: dict[str, str] = Field(default_factory=dict)
    """Maps Neo4j property names to Pydantic field names."""


class Neo4jRelMapping(BaseModel):
    """Mapping configuration for Neo4j relationships."""

    neo4j_type: str
    from_label: str
    to_label: str
    name: str
    """Name of the relationship in the graph schema."""
    property_mapping: dict[str, str] = Field(default_factory=dict)
    """Maps Neo4j relationship property names to schema property names."""


# -----------------------------------------------------------------------------
# StratnavSubgraph factory
# -----------------------------------------------------------------------------


class StratnavSubgraph(Neo4jSubgraphFactory, BaseModel):
    """Subgraph factory for Neo4j JSONL exports.

    This factory processes Neo4j exports and maps Account nodes to Customer
    with embedded location and services.
    """

    # Node mappings from Neo4j labels to Pydantic models
    _node_mappings: list[Neo4jNodeMapping] = [
        Neo4jNodeMapping(
            neo4j_label="Account",
            target_class=Customer,
            field_mapping={
                "name": "name",
                "irisCode": "iris_code",
                "subMarket": "segment",
            },
        ),
        Neo4jNodeMapping(
            neo4j_label="GEO",
            target_class=GeoLocation,
            field_mapping={
                "name": "name",
                "country": "country",
            },
        ),
        Neo4jNodeMapping(
            neo4j_label="L3",
            target_class=L3Service,
            field_mapping={
                "L3Code": "code",
                "name": "name",
                "L3ServiceDescription": "description",
                "L3ServiceType": "service_type",
            },
        ),
    ]

    def build_schema(self) -> GraphSchema:
        """Build the graph schema for imported data.

        Returns:
            GraphSchema with Customer as the root node.
            Location and services are embedded in Customer.
        """
        nodes = [
            GraphNode(
                node_class=Customer,
                name_from="name",
                key_from=lambda data, _: data.get("iris_code") or data.get("name", "unknown"),
                description="Customer account with location and services",
                index_fields=["name"],
            ),
        ]

        # No relationships needed - location and services are embedded in Customer
        relations: list = []

        return GraphSchema(root_model_class=Customer, nodes=nodes, relations=relations)

    def get_all_keys(self) -> list[str]:
        """Get all Account node IDs as keys for document ingestion.

        Returns:
            List of neo4j IDs for Account nodes
        """
        account_nodes = self.get_nodes_by_label("Account")
        return [node.get("_neo4j_id", "") for node in account_nodes if node.get("_neo4j_id")]

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Map a Neo4j Account node to Customer model.

        This method finds the Account node by neo4j ID and constructs
        a Customer instance with embedded location and services.

        Args:
            key: The neo4j node ID for an Account

        Returns:
            Customer instance or None if not found
        """
        # Find the Account node
        account_nodes = self.get_nodes_by_label("Account")
        account_data = None
        for node in account_nodes:
            if node.get("_neo4j_id") == key:
                account_data = node
                break

        if account_data is None:
            logger.warning(f"Account node not found for key: {key}")
            return None

        # Map Account directly to Customer
        mapping = self._get_node_mapping("Account")
        if mapping is None:
            return None

        mapped_data = self._apply_field_mapping(account_data, mapping.field_mapping)

        # Find related Location
        location = self._find_related_location(key)

        # Find related L3 Services (through any relationship path)
        services = self._find_related_services(key)

        return Customer(
            name=mapped_data.get("name", "Unknown"),
            iris_code=mapped_data.get("iris_code"),
            segment=mapped_data.get("segment"),
            location=location,
            services=services,
        )

    def _find_related_location(self, account_id: str) -> GeoLocation | None:
        """Find the GeoLocation related to an Account via LOCATED_IN.

        Args:
            account_id: Neo4j ID of the Account node

        Returns:
            GeoLocation instance or None
        """
        mapping = self._get_node_mapping("GEO")
        if mapping is None:
            return None

        # Look for LOCATED_IN relationships from this account
        for rel_key, rels in self._rel_data.items():
            if "LOCATED_IN" in rel_key:
                for rel in rels:
                    if rel.get("_from_id") == account_id:
                        geo_id = rel.get("_to_id")
                        geo_data = self._find_node_by_id("GEO", geo_id)
                        if geo_data:
                            mapped = self._apply_field_mapping(geo_data, mapping.field_mapping)
                            return GeoLocation(
                                name=mapped.get("name", "Unknown"),
                                country=mapped.get("country", ""),
                            )
        return None

    def _find_related_services(self, account_id: str) -> list[L3Service]:
        """Find L3 Services indirectly related to an Account.

        Note: In the Stratnav schema, L3 services are typically not directly
        linked to Accounts. This method is a placeholder for future expansion.

        Args:
            account_id: Neo4j ID of the Account node

        Returns:
            List of L3Service instances
        """
        # Currently no direct relationship from Account to L3
        # This could be expanded to traverse through intermediary nodes
        return []

    def _find_node_by_id(self, label: str, node_id: str | None) -> dict[str, Any] | None:
        """Find a node by its Neo4j ID within a specific label.

        Args:
            label: Node label to search
            node_id: Neo4j node ID

        Returns:
            Node data dict or None
        """
        if not node_id:
            return None

        nodes = self.get_nodes_by_label(label)
        for node in nodes:
            if node.get("_neo4j_id") == node_id:
                return node
        return None

    def _get_node_mapping(self, neo4j_label: str) -> Neo4jNodeMapping | None:
        """Get the mapping configuration for a Neo4j label.

        Args:
            neo4j_label: The Neo4j node label

        Returns:
            Mapping configuration or None
        """
        for mapping in self._node_mappings:
            if mapping.neo4j_label == neo4j_label:
                return mapping
        return None

    def _apply_field_mapping(self, node_data: dict[str, Any], field_mapping: dict[str, str]) -> dict[str, Any]:
        """Apply field name mapping to node data.

        Args:
            node_data: Raw node data
            field_mapping: Mapping from Neo4j field names to target field names

        Returns:
            Mapped data dictionary
        """
        result: dict[str, Any] = {}

        for neo4j_field, target_field in field_mapping.items():
            if neo4j_field in node_data:
                result[target_field] = node_data[neo4j_field]

        return result

    def get_sample_queries(self) -> list[str]:
        """Get sample Cypher queries for customer data.

        Returns:
            List of sample query strings
        """
        return [
            "MATCH (n) RETURN labels(n)[0] as NodeType, count(n) as Count",
            "MATCH (c:Customer) RETURN c.name, c.segment, c.iris_code LIMIT 5",
        ]


# -----------------------------------------------------------------------------
# Standalone testing
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from genai_tk.utils.config_mngr import global_config
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    console.print(Panel.fit("[bold cyan]Testing StratnavSubgraph[/bold cyan]", border_style="cyan"))

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

    # Create the subgraph factory
    sg = StratnavSubgraph(neo4j_export_file=str(subset_file))

    # Check if initialized
    if not sg._initialized:
        console.print("[red]✗[/red] Failed to initialize subgraph")
        exit(1)

    console.print("[green]✓[/green] StratnavSubgraph initialized successfully")

    # Test 2: Check discovered schema
    console.print("\n[bold blue]Test 2:[/bold blue] Discovered schema from JSONL")

    schema_info = sg.get_schema_info()
    if schema_info:
        console.print(f"  Total nodes: {schema_info.total_nodes}")
        console.print(f"  Total relationships: {schema_info.total_relationships}")
        console.print(f"  Node labels: {list(schema_info.node_tables.keys())}")
        console.print(f"  Rel types: {list(schema_info.rel_tables.keys())}")

    # Test 3: Get all keys (Account IDs)
    console.print("\n[bold blue]Test 3:[/bold blue] Getting account keys for ingestion")
    keys = sg.get_all_keys()
    console.print(f"  Found {len(keys)} Account nodes")
    if keys:
        console.print(f"  First 5 keys: {keys[:5]}")

    # Test 4: Load structured data for a key
    if keys:
        test_key = keys[0]
        console.print(f"\n[bold blue]Test 4:[/bold blue] Loading data for key: {test_key}")

        data = sg.get_struct_data_by_key(test_key)
        if data:
            console.print("[green]✓[/green] Loaded Customer data")

            table = Table(show_header=True, header_style="bold magenta", show_lines=True)
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")

            # Data is now Customer directly (not wrapped)
            assert isinstance(data, Customer)
            table.add_row("Customer Name", data.name)
            table.add_row("IRIS Code", data.iris_code or "(none)")
            table.add_row("Segment", data.segment or "(none)")
            table.add_row("Location", data.location.name if data.location else "(none)")

            console.print(table)
        else:
            console.print(f"[red]✗[/red] No data found for key {test_key}")

    # Test 5: Build schema
    console.print("\n[bold blue]Test 5:[/bold blue] Building graph schema")
    schema = sg.build_schema()
    console.print(f"  Root model: {schema.root_model_class.__name__}")
    console.print(f"  Nodes: {[n.node_class.__name__ for n in schema.nodes]}")
    console.print(f"  Relations: {[r.name for r in schema.relations]}")

    console.print(Panel.fit("[bold green]All tests completed! ✓[/bold green]", border_style="green"))
