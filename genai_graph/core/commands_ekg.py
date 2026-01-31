"""CLI commands for interacting with the Enterprise Knowledge Graph.

This module provides the ``kg`` top-level command (as configured via
``config/overrides.yaml``). The ``create`` command runs a Prefect flow
using an in-process runner so no long-lived Prefect server is required.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from genai_tk.main.cli import CliTopCommand
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

GRAPH_DB_CONFIG = "default"

console = Console()


class EkgCommands(CliTopCommand):
    """Commands for interacting with a Knowledge Graph."""

    def get_description(self) -> tuple[str, str]:  # type: ignore[override]
        return "kg", "Knowledge Graph commands."

    def register_sub_commands(self, cli_app: typer.Typer) -> None:  # type: ignore[override]
        """Register ``kg`` subcommands on the given Typer application."""

        @cli_app.command("create")
        def create(
            kg: Annotated[
                list[str] | None,
                typer.Option(
                    "--kg",
                    help="KG configuration name(s) to create. Can be specified multiple times.",
                ),
            ] = None,
            all_graphs: Annotated[
                bool,
                typer.Option(
                    "--all-graphs",
                    help="Create all KG configurations defined in ekg.yaml",
                ),
            ] = False,
            delete_first: Annotated[
                bool,
                typer.Option(
                    "--delete-first/--no-delete-first",
                    help="Delete existing KG database before creation",
                ),
            ] = True,
            export_html: Annotated[
                bool,
                typer.Option(
                    "--export-html/--no-export-html",
                    help="Export HTML visualization after creation",
                ),
            ] = True,
        ) -> None:
            """Create the KG database and ingest documents using a Prefect flow.

            The flow is executed with an in-process runner and ephemeral client
            so that no long-lived Prefect server or agent is required.

            Examples:
                cli kg create                        # Use kg_config from config
                cli kg create --kg simple            # Create specific KG
                cli kg create --kg simple --kg test1_with_db  # Create multiple KGs
                cli kg create --all-graphs           # Create all defined KGs
            """

            # Get the configured KG config name.
            from genai_tk.extra.prefect.runtime import ephemeral_prefect_settings
            from genai_tk.utils.config_mngr import global_config

            from genai_graph.core.kg_manager import get_kg_manager
            from genai_graph.orchestration.flows import create_kg_flow

            # Determine which KG configs to process
            kg_configs_to_process: list[str] = []

            if all_graphs:
                # Get all KG configs from global_config
                try:
                    cfg = global_config()
                    all_kg_configs = cfg.get_dict("kg_configs")
                    kg_configs_to_process = list(all_kg_configs.keys())
                    console.print(
                        f"[bold]Processing all KG configurations:[/bold] "
                        f"[cyan]{', '.join(kg_configs_to_process)}[/cyan]"
                    )
                except Exception as exc:
                    console.print(f"[red]❌ Failed to retrieve kg_configs: {exc}[/red]")
                    raise typer.Exit(1) from exc
            elif kg:
                # Use specified KG config(s)
                kg_configs_to_process = kg
                console.print(
                    f"[bold]Processing specified KG configuration(s):[/bold] "
                    f"[cyan]{', '.join(kg_configs_to_process)}[/cyan]"
                )
            else:
                # Use default from kg_config
                cfg_name = get_kg_manager().profile
                kg_configs_to_process = [cfg_name]
                console.print(f"[bold]Processing default KG configuration:[/bold] [cyan]{cfg_name}[/cyan]")

            # Track results for all KG configs
            all_results: list[tuple[str, Any]] = []
            failed_configs: list[tuple[str, str]] = []

            # Process each KG config
            for cfg_name in kg_configs_to_process:
                console.print("")
                console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")
                console.print(f"[bold]Creating KG:[/bold] [cyan]{cfg_name}[/cyan]")
                console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")

                # Run the Prefect flow with an ephemeral, in-process server.
                try:
                    with ephemeral_prefect_settings():
                        result = create_kg_flow(
                            config_name=cfg_name,
                            delete_first=delete_first,
                            export_html=export_html,
                        )

                    all_results.append((cfg_name, result))

                    stats = result.stats
                    warnings = result.warnings

                    console.print("")
                    console.print(
                        f"[green]✓ KG creation completed for [bold]{cfg_name}[/bold].[/green] Processed: "
                        f"{stats.total_processed} ok, {stats.total_failed} failed. "
                        f"Path: {result.db_path}",
                    )

                    if warnings:
                        console.print(Panel.fit("[bold yellow]⚠️  Warnings[/bold yellow]", border_style="yellow"))
                        for idx, warning in enumerate(warnings, 1):
                            console.print(f"  [yellow]{idx}.[/yellow] {warning}")
                        console.print("")
                    else:
                        console.print("[green]✓ No warnings[/green]")

                    if result.html_export and export_html:
                        file_url = f"file://{result.html_export.output_path}"
                        console.print(
                            f"[green]📊 HTML export:[/green] [link={file_url}]{result.html_export.output_path}[/link]"
                        )

                except Exception as exc:  # pragma: no cover - defensive
                    import traceback as tb

                    logger.error(f"KG creation failed for {cfg_name}: {exc}")
                    logger.error(tb.format_exc())
                    console.print(f"[red]❌ KG creation failed for {cfg_name}: {exc}[/red]")
                    failed_configs.append((cfg_name, str(exc)))
                    # Continue with next config instead of exiting
                    continue

            # Summary for multiple KG configs
            if len(kg_configs_to_process) > 1:
                console.print("")
                console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")
                console.print(f"[bold]Summary: Processed {len(kg_configs_to_process)} KG configuration(s)[/bold]")
                console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")

                if all_results:
                    console.print(f"[green]✓ Successfully created: {len(all_results)}[/green]")
                    for cfg_name, result in all_results:
                        console.print(f"  • [cyan]{cfg_name}[/cyan]: {result.stats.total_processed} docs processed")

                if failed_configs:
                    console.print(f"[red]✗ Failed: {len(failed_configs)}[/red]")
                    for cfg_name, error in failed_configs:
                        console.print(f"  • [red]{cfg_name}[/red]: {error}")
                    raise typer.Exit(1)

        @cli_app.command("info")
        def info() -> None:
            """Display EKG database information, schema overview, and mappings.

            Reads and displays the info that was automatically generated during
            graph creation. The info includes database details, schema statistics,
            node/relationship mappings, and subgraph configurations.
            """
            from genai_graph.core.kg_manager import get_kg_manager

            # Get the current KG manager (auto-activates)
            manager = get_kg_manager()

            # Check if info file exists
            if not manager.info_path.exists():
                console.print(
                    "[red]❌ No info file found.[/red]\n"
                    "[yellow]💡 Run [bold]cli kg create[/bold] to generate the info[/yellow]"
                )
                raise typer.Exit(1)

            # Read and display the info
            try:
                from rich.markdown import Markdown

                info_content = manager.info_path.read_text(encoding="utf-8")
                console.print(
                    Panel(
                        "[bold cyan]Knowledge Graph Information[/bold cyan]",
                    )
                )
                console.print(Markdown(info_content))
            except Exception as exc:
                import traceback as tb

                console.print(f"[red]❌ Failed to read info: {exc}[/red]")
                console.print("[red]" + tb.format_exc() + "[/red]")
                raise typer.Exit(1) from exc

        @cli_app.command("schema")
        def schema() -> None:
            """Display knowledge graph schema.

            Reads and displays the schema that was automatically generated during
            graph creation. The schema includes node types, relationships, properties,
            and indexed fields.
            """

            from genai_graph.core.kg_manager import get_kg_manager

            # Get the current KG manager (auto-activates)
            manager = get_kg_manager()

            # Check if schema file exists
            if not manager.schema_path.exists():
                console.print(
                    "[red]❌ No schema file found.[/red]\n"
                    "[yellow]💡 Run [bold]cli kg create[/bold] to generate the schema[/yellow]"
                )
                raise typer.Exit(1)

            # Read and display the schema
            try:
                schema_content = manager.schema_path.read_text(encoding="utf-8")
                console.print(
                    Panel(
                        "[bold cyan]Knowledge Graph Schema[/bold cyan]",
                    )
                )
                console.print(schema_content)
            except Exception as exc:
                import traceback as tb

                console.print(f"[red]❌ Failed to read schema: {exc}[/red]")
                console.print("[red]" + tb.format_exc() + "[/red]")
                raise typer.Exit(1) from exc

        # TODO: other commands (delete, export-html, query) can be
        # migrated to Prefect-based flows in a similar fashion if needed.
        @cli_app.command("agent")
        def agent(
            input: Annotated[
                str | None,
                typer.Option(
                    "--input",
                    "-i",
                    help="Input query or '-' to read from stdin",
                ),
            ] = None,
            chat: Annotated[
                bool,
                typer.Option(
                    "--chat",
                    "-s",
                    help="Start an interactive chat session with the EKG agent",
                ),
            ] = False,
            llm: Annotated[
                str | None,
                typer.Option(
                    "--llm",
                    "-m",
                    help="LLM identifier (ID or tag) to use; default comes from configuration",
                ),
            ] = None,
            mcp: Annotated[
                list[str],
                typer.Option(
                    "--mcp",
                    help="MCP server names to connect to (e.g. playwright, filesystem, ..)",
                ),
            ] = [],
            debug: Annotated[
                bool,
                typer.Option(
                    "--debug",
                    "-d",
                    help="Display generated Cypher queries before execution",
                ),
            ] = False,
            lc_verbose: Annotated[
                bool,
                typer.Option(
                    "--verbose",
                    "-v",
                    help="Enable LangChain verbose mode",
                ),
            ] = False,
            lc_debug: Annotated[
                bool,
                typer.Option(
                    "--debug-lc",
                    help="Enable LangChain debug mode",
                ),
            ] = False,
            first_tool: Annotated[
                bool,
                typer.Option(
                    "--first-tool",
                    help="Stop after the first tool call and return raw result (non-chat mode only)",
                ),
            ] = False,
        ) -> None:
            """Run an EKG-aware LangChain ReAct agent over the knowledge graph.

            The agent answers questions about enterprise data and can call a
            Cypher execution tool to query the graph when needed.

            Examples:
                uv run cli kg agent -i "List the names of all competitors"
                uv run cli kg agent --chat
                uv run cli kg agent --first-tool -i "List all competitors"
                uv run cli kg agent --mcp filesystem -i "List recent EKG exports on disk"
            """
            import asyncio
            import sys

            from genai_tk.cli.langchain_agent import (
                run_langchain_agent_direct,
                run_langchain_agent_shell,
            )
            from genai_tk.extra.agents.langchain_setup import setup_langchain

            from genai_graph.core.ekg_agent import (
                build_ekg_agent_system_prompt,
                create_ekg_cypher_tool,
            )
            from genai_graph.core.kg_manager import get_kg_manager

            # Get the current KG manager (auto-activates)
            manager = get_kg_manager()
            kg_config_name = manager.profile

            setup_langchain(llm, lc_debug, lc_verbose)

            system_prompt = build_ekg_agent_system_prompt(single_tool_mode=first_tool)
            ekg_tool = create_ekg_cypher_tool(
                backend_config=GRAPH_DB_CONFIG,
                kg_config_name=kg_config_name,
                console=console,
                debug=debug,
            )

            if chat:
                # Interactive chat mode using the shared LangChain shell
                asyncio.run(
                    run_langchain_agent_shell(
                        llm,
                        tools=[ekg_tool],
                        mcp_server_names=mcp,
                        system_prompt=system_prompt,
                    )
                )
            else:
                # Handle input from --input parameter or stdin
                if not input and not sys.stdin.isatty():
                    input = sys.stdin.read()
                if not input or len(input.strip()) < 3:
                    console.print("[red]❌ Input parameter or something in stdin is required[/red]")
                    raise typer.Exit(1)

                # Reuse the common ReAct helper from genai-tk
                # If --first-tool is specified, the agent stops after one tool call
                asyncio.run(
                    run_langchain_agent_direct(
                        input.strip(),
                        llm_id=llm,
                        mcp_server_names=mcp,
                        additional_tools=[ekg_tool],
                        pre_prompt=system_prompt,
                        single_tool_mode=first_tool,
                    )
                )

        @cli_app.command("cypher")
        def cypher(
            query: str = typer.Argument(help="Cypher query to execute"),
        ) -> None:
            """Execute Cypher queries on the EKG database."""

            from rich.panel import Panel

            from genai_graph.core.graph_backend import (
                create_backend_from_config,
            )
            from genai_graph.core.kg_manager import get_kg_manager

            # Get the current KG manager (auto-activates)
            manager = get_kg_manager()

            console.print(Panel(f"[bold cyan]Querying EKG Database[/bold cyan]\n[dim]Config: {manager.profile}[/dim]"))

            # Get database connection
            backend = create_backend_from_config(GRAPH_DB_CONFIG, manager.profile)
            if not backend:
                console.print("[red]❌ No EKG database found[/red]")
                console.print("[yellow]💡 Run [bold]cli kg create[/bold] to create the database[/yellow]")
                raise typer.Exit(1)

            def execute_query(cypher_query: str) -> None:
                """Execute a single Cypher query and display results."""
                if not cypher_query.strip():
                    return

                try:
                    console.print(f"[dim]Executing: {cypher_query}[/dim]")
                    df = backend.execute_get_as_df(cypher_query, union=True)

                    if df.empty:
                        console.print("[yellow]Query returned no results[/yellow]")
                        return

                    # Create a Rich table for results
                    table = Table(title=f"Query Results ({len(df)} rows)")

                    # Add columns
                    for col in df.columns:
                        table.add_column(str(col), style="cyan")

                    # Add rows (limit to first 20 for readability)
                    max_rows = 20
                    for i, (_, row) in enumerate(df.iterrows()):
                        if i >= max_rows:
                            table.add_row(*["..." for _ in df.columns])
                            break
                        table.add_row(*[str(val) for val in row])

                    console.print(table)

                    if len(df) > max_rows:
                        console.print(f"[dim]Showing first {max_rows} of {len(df)} results[/dim]")

                except Exception as e:
                    import traceback as tb

                    console.print(f"[red]❌ Query error: {e}[/red]")
                    console.print("[red]" + tb.format_exc() + "[/red]")

            # Execute single query if provided
            if query:
                execute_query(query)
                return

        @cli_app.command("query")
        def query_ekg(
            query: str = typer.Argument(help="query to execute"),
            llm: Annotated[
                str | None,
                typer.Option(help="Name or tag of the LLM to use by BAML"),
            ] = None,
        ) -> None:
            """Execute queries in natural language (Text-2-Cypher) on the EKG database.

            ex:  List the names of all competitors for opportunities created after January 1, 2012."""

            from genai_graph.core.text2cypher import query_kg

            try:
                from rich.table import Table

                df = query_kg(query, llm_id=llm)

                if df.empty:
                    console.print("[yellow]Query returned no results[/yellow]")
                    return

                # Create a Rich table for results
                table = Table(title="Query Results")
                for col in df.columns:
                    table.add_column(str(col), style="cyan")
                MAX_ROWS = 20
                for i, (_, row) in enumerate(df.iterrows()):
                    if i >= MAX_ROWS:
                        table.add_row(*["..." for _ in df.columns])
                        break
                    table.add_row(*[str(val) for val in row])
                console.print(table)

                if len(df) > MAX_ROWS:
                    console.print(f"[dim]Showing first {MAX_ROWS} of {len(df)} results[/dim]")

            except Exception as e:
                import traceback as tb

                logger.error(f"Failed to process query: {e}")
                console.print(f"[red]❌ Query error: {e}[/red]")
                console.print("[red]" + tb.format_exc() + "[/red]")
                return

        @cli_app.command("view")
        def view_html() -> None:
            """Open the HTML visualization of the current KG configuration in a browser.

            Opens the most recently generated HTML export file for the active KG
            configuration in the default web browser.
            """
            import webbrowser

            from genai_graph.core.kg_manager import get_kg_manager

            # Get the current KG manager (auto-activates)
            manager = get_kg_manager()

            # Check if HTML file exists
            if not manager.html_path.exists():
                console.print(
                    "[red]❌ No HTML export found.[/red]\n"
                    "[yellow]💡 Run [bold]cli kg create[/bold] to generate a visualization[/yellow]"
                )
                raise typer.Exit(1)

            file_url = manager.html_path.as_uri()

            console.print(f"[bold cyan]🌐 Opening HTML visualization:[/bold cyan] {manager.html_path.name}")

            # Open in browser
            webbrowser.open(file_url)

            console.print("[green]✓ Opened in your default browser[/green]")

        @cli_app.command("fake-rainbow-from-crm")
        def fake_rainbow_from_crm(
            num_files: Annotated[
                int,
                typer.Option(
                    "--num",
                    "-n",
                    help="Number of fake Rainbow JSON files to generate",
                ),
            ] = 5,
            config_name: Annotated[
                str,
                typer.Option(
                    "--config",
                    help="Name of the structured config to use from YAML config",
                ),
            ] = "default",
            llm: Annotated[
                str | None,
                typer.Option(help="Name or tag of the LLM to use by BAML"),
            ] = None,
            output_dir: Annotated[
                str | None,
                typer.Option(
                    "--out-dir",
                    help=(
                        "Output directory for result (supports config variables like ${paths.rainbow_json}). "
                        "If not specified, uses ${paths.rainbow_json}."
                    ),
                ),
            ] = None,
            force: bool = typer.Option(False, "--force", help="Overwrite existing output files if they exist"),
        ) -> None:
            """Generate fake Rainbow JSON files from CRM export data.

            Reads the CRM export Excel file and generates fake Rainbow JSON files
            using the BAML FakeRainbowJson function. Each file is generated based on
            opportunity information extracted from the CRM data.

            The CRM file is read from ${paths.ekg_data}/crm_export/report1750429630460_500lines.xlsx.
            Output files are written to the fake/ subdirectory of the output directory.

            Examples:
                ```bash
                # Generate 5 fake files (default)
                uv run cli kg fake-rainbow-from-crm

                # Generate 2 fake files for testing
                uv run cli kg fake-rainbow-from-crm --num 2

                # Generate with specific LLM and force overwrite
                uv run cli kg fake-rainbow-from-crm --num 3 --llm openai/gpt-4 --force

                # Custom output directory
                uv run cli kg fake-rainbow-from-crm --out-dir ./my_output --num 10
                ```
            """
            from genai_tk.extra.prefect.runtime import run_flow_ephemeral

            from genai_graph.orchestration.crm_fake_rainbow_flow import crm_fake_rainbow_flow

            # Set default output directory if not provided
            if output_dir is None:
                output_dir = "${paths.rainbow_json}"

            # CRM file path is hardcoded as per requirements
            crm_file_path = "${paths.ekg_data}/crm_export/report1750429630460_500lines.xlsx"

            console.print(f"[bold]Generating {num_files} fake Rainbow JSON files from CRM data[/bold]")
            if llm:
                console.print(f"[cyan]Using LLM:[/cyan] {llm}")

            try:
                # Run the Prefect flow with ephemeral settings
                result = run_flow_ephemeral(
                    crm_fake_rainbow_flow,
                    crm_file_path=crm_file_path,
                    output_dir=output_dir,
                    num_files=num_files,
                    config_name=config_name,
                    llm=llm,
                    force=force,
                )

                # Display results
                console.print("")
                console.print(f"[green]✓ Generation completed.[/green] Generated: {result.total_generated} files")
                console.print(f"[cyan]Output directory:[/cyan] {result.output_dir}/fake/")

            except FileNotFoundError as exc:
                logger.error(f"CRM file not found: {exc}")
                console.print(f"[red]❌ CRM file not found: {exc}[/red]")
                raise typer.Exit(1) from exc
            except Exception as exc:
                logger.error(f"Fake Rainbow generation failed: {exc}")
                console.print(f"[red]❌ Generation failed: {exc}[/red]")
                raise typer.Exit(1) from exc
