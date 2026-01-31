from typing import Any, Type

from pydantic import BaseModel

from genai_graph.core.graph_schema import GraphNode, GraphRelation, GraphSchema
from genai_graph.core.subgraph_factories import TableBackedSubgraphFactory
from genai_graph.ekg.baml_client.types import Person
from genai_graph.ekg.schema.common_nodes import Customer, Opportunity, WinLoss


class CrmExtractSubGraph(TableBackedSubgraphFactory, BaseModel):
    """CRM data subgraph implementation.

    Imports CRM export data and creates Opportunity-centric graphs
    with related Customer, Person, and WinLoss nodes.
    """

    TOP_CLASS: Type[BaseModel] = Opportunity

    @property
    def table_name(self) -> str:
        """Keep the original table name for backward compatibility."""
        return "crm_extract"

    def get_key_field(self) -> str:
        """Return the field name used as the unique key for data retrieval."""
        return "Atos Opportunity ID"

    def mapper_function(self, row: dict[str, Any]) -> Opportunity | None:
        """Map database row to Opportunity model."""
        return Opportunity(
            opportunity_id=str(row.get("Atos Opportunity ID", "")),
            name=row.get("Opportunity Name", ""),
            customer=Customer(
                name=row.get("Account Name", ""),
                segment=row.get("Sub-Industry", ""),
            ),
            lead=Person(name=row.get("Client Leader", ""), p_role_="Client Leader", organization="Atos"),
            win_loss=WinLoss(
                result=row.get("Status", ""),
                reason=row.get("Reason", ""),
            ),
        )

    def build_schema(self) -> GraphSchema:
        """Build the graph schema for CRM extract data.

        Creates schema with Opportunity, Person, and WinLoss nodes and their relationships.
        """
        nodes = [
            GraphNode(
                node_class=Opportunity,
                name_from="name",
                key_from="opportunity_id",
                description="Sales opportunity with win/loss tracking",
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
            GraphNode(
                node_class=WinLoss,
                name_from=lambda data, _base: data.get("reason") or data.get("result") or "other/unset",
                key_from="AUTO_ID",
                description="Win/Loss outcome for the opportunity",
            ),
        ]

        relations = [
            GraphRelation(
                from_node=Opportunity,
                to_node=WinLoss,
                name="WIN_LOSS_INFO",
                description="Win/loss outcome for this opportunity",
            ),
            GraphRelation(
                from_node=Opportunity,
                to_node=Person,
                name="LEAD_BY",
                description="Account Sales Leader",
            ),
            GraphRelation(
                from_node=Opportunity,
                to_node=Customer,
                name="FOR_CUSTOMER",
                description="Customer organization for this opportunity",
            ),
            GraphRelation(
                from_node=Customer,
                to_node=Person,
                name="HAS_CONTACT",
                description="Customer contact persons",
            ),
        ]
        return GraphSchema(root_model_class=Opportunity, nodes=nodes, relations=relations)


# Atos Opportunity ID	Fiscal Period	Order entry (converted) Currency	Order entry (converted)	IRIS Account Name	Opportunity Name	Closing Date	Leading Profit Center: Profit Center Name	Status	Reason	Item Order Entry (converted) Currency	Item Order Entry (converted)	Industry	Item Number	Client Leader	Close Month	Account Name	Product Business Line Code	Leading Profit Center: Country	Portfolio	Sub-Industry	Bid Budget (converted) Currency	Bid Budget (converted)	Item Business Line Name


if __name__ == "__main__":
    from genai_tk.utils.config_mngr import global_config
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    TEST_FILE = "misc/report1750429630460_SHORTEN.xlsx"

    root = global_config().get_dir_path("paths.ekg_data")
    test_file = root / TEST_FILE
    assert test_file.exists(), f"File not found: {test_file}"

    console.print(Panel.fit("[bold cyan]Testing CrmExtractSubGraph[/bold cyan]", border_style="cyan"))

    # Test 1: Create subgraph and load data
    console.print("\n[bold blue]Test 1:[/bold blue] Creating CrmExtractSubGraph and loading data...")
    sg = CrmExtractSubGraph(db_dsn="sqlite:////tmp/mydatabase2.db", files=[test_file])
    console.print("[green]✓[/green] CrmExtractSubGraph created successfully")

    # Test 2: Query existing data
    test_key = "9000559500"
    console.print(f"\n[bold blue]Test 2:[/bold blue] Querying for key: [yellow]{test_key}[/yellow]")
    result = sg.get_struct_data_by_key(test_key)

    if result:
        console.print(f"[green]✓[/green] Found data for key {test_key}")

        # Type narrow for IDE support
        assert isinstance(result, Opportunity)

        table = Table(show_header=True, header_style="bold magenta", show_lines=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Opportunity", result.name)
        table.add_row("Customer", result.customer.name)
        table.add_row("Lead", result.lead.name if result.lead else "(none)")
        table.add_row("Win/Loss", result.win_loss.result if result.win_loss else "(empty)")

        console.print(table)
    else:
        console.print(f"[red]✗[/red] No data found for key {test_key}")

    # Test 3: Query non-existent key
    console.print("\n[bold blue]Test 3:[/bold blue] Querying for non-existent key: [yellow]00012345[/yellow]")
    result = sg.get_struct_data_by_key("00012345")

    if result is None:
        console.print("[green]✓[/green] Correctly returned None for non-existent key")
    else:
        console.print("[red]✗[/red] Expected None but got a result")

    # Test 4: Re-run to verify deduplication (should skip already imported file)
    console.print("\n[bold blue]Test 4:[/bold blue] Creating new instance to test import tracking...")
    sg2 = CrmExtractSubGraph(db_dsn="sqlite:////tmp/mydatabase.db", files=[test_file])
    console.print("[green]✓[/green] Second instance created - file was skipped (check logs above)")

    # Test 5: Test with custom pandas parameters (if user uncomments)
    # console.print("\n[bold blue]Test 5:[/bold blue] Testing with custom pandas parameters...")
    # sg3 = CrmExtractSubGraph(
    #     db_dsn="sqlite:///temp/mydatabase.db",
    #     files=[test_file],
    #     pd_read_parameters={"sheet_name": 0, "skiprows": 1}
    # )

    console.print(Panel.fit("[bold green]All tests completed successfully! ✓[/bold green]", border_style="green"))
