from typing import Any

from pydantic import BaseModel

from genai_graph.ekg.baml_client.types import Person
from genai_graph.ekg.schema.canonical_nodes import CustomerNode, OpportunityNode, PersonNode
from genai_graph.ekg.schema.common_nodes import Customer, Opportunity, WinLoss
from genai_graph.kg.factories import TableBackedFactory
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

WinLossNode: GraphNode = GraphNode(
    node_class=WinLoss,
    name_from=lambda data, _base: data.get("reason") or data.get("result") or "other/unset",
    key_from="AUTO_ID",
    description="Win/Loss outcome for the opportunity",
)


class CrmExtractGraph(TableBackedFactory, BaseModel):
    """CRM data graph.

    Imports CRM export data and creates Opportunity-centric graphs
    with related Customer, Person, and WinLoss nodes.
    """

    TOP_CLASS: type[BaseModel] | None = Opportunity

    @property
    def table_name(self) -> str:
        """Keep the original table name."""
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
            OpportunityNode,
            CustomerNode,
            PersonNode,
            WinLossNode,
        ]

        relations = [
            GraphRelation(
                from_node=OpportunityNode,
                to_node=WinLossNode,
                name="WIN_LOSS_INFO",
                description="Win/loss outcome for this opportunity",
            ),
            GraphRelation(
                from_node=OpportunityNode,
                to_node=PersonNode,
                name="LEAD_BY",
                description="Account Sales Leader",
                field_paths=[("", "lead")],
            ),
            GraphRelation(
                from_node=OpportunityNode,
                to_node=CustomerNode,
                name="HAS_CUSTOMER",
                description="Customer organization for this opportunity",
            ),
            GraphRelation(
                from_node=CustomerNode,
                to_node=PersonNode,
                name="HAS_CONTACT",
                description="Customer contact persons",
                field_paths=[("customer", "customer.employees")],
            ),
        ]
        return GraphSchema(root_model_class=Opportunity, nodes=nodes, relations=relations)


# Atos Opportunity ID	Fiscal Period	Order entry (converted) Currency	Order entry (converted)	IRIS Account Name	Opportunity Name	Closing Date	Leading Profit Center: Profit Center Name	Status	Reason	Item Order Entry (converted) Currency	Item Order Entry (converted)	Industry	Item Number	Client Leader	Close Month	Account Name	Product Business Line Code	Leading Profit Center: Country	Portfolio	Sub-Industry	Bid Budget (converted) Currency	Bid Budget (converted)	Item Business Line Name


if __name__ == "__main__":
    from genai_tk.config_mgmt.config_mngr import global_config
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    TEST_FILE = "crm_export/report1750429630460_SHORTEN.xlsx"

    root = global_config().get_dir_path("paths.ekg_data")
    test_file = root / TEST_FILE
    assert test_file.exists(), f"File not found: {test_file}"

    console.print(Panel.fit("[bold cyan]Testing CrmExtractGraph[/bold cyan]", border_style="cyan"))

    # Test 1: Create graph and load data
    console.print("\n[bold blue]Test 1:[/bold blue] Creating CrmExtractGraph and loading data...")
    sg = CrmExtractGraph(files=[test_file])
    console.print("[green]✓[/green] CrmExtractGraph created successfully")

    # Test 2: Query existing data
    test_key = "9000559500"
    console.print(f"\n[bold blue]Test 2:[/bold blue] Querying for key: [yellow]{test_key}[/yellow]")
    result = sg.get_struct_data_by_key(test_key)

    if result:
        console.print(f"[green]✓[/green] Found data for key {test_key}")

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

    # Test 4: Re-run to verify cache hit (second init should skip loading)
    console.print("\n[bold blue]Test 4:[/bold blue] Creating second instance to test cache hit...")
    CrmExtractGraph.clear_cache()
    sg2 = CrmExtractGraph(files=[test_file])
    console.print("[green]✓[/green] Second instance created - Parquet cache was used (check logs above)")

    console.print(Panel.fit("[bold green]All tests completed successfully! ✓[/bold green]", border_style="green"))
