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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_root_cause(exc: BaseException) -> str:
    """Walk the exception chain and return the deepest root cause as a string."""
    cause: BaseException = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    return f"{type(cause).__name__}: {cause}"


def _render_kg_plan(invocation: Any) -> None:
    """Print a summary table of the resolved workflow plan."""
    import json as _json

    from genai_tk.workflow.models import ResolvedWorkflowInvocation

    if not isinstance(invocation, ResolvedWorkflowInvocation):
        return

    summary = Table(title="Workflow Plan", show_header=True, header_style="bold cyan")
    summary.add_column("Property", style="cyan", no_wrap=True)
    summary.add_column("Value", style="white")
    summary.add_row("Workflow", invocation.workflow_name)
    summary.add_row("Profile", invocation.profile_name or "<none>")
    summary.add_row("Force", str(invocation.force))
    summary.add_row("Steps", str(len(invocation.workflow.steps)))
    console.print(summary)

    if invocation.values:
        console.print(Panel(_json.dumps(invocation.values, indent=2, default=str), title="Effective Values"))

    steps = Table(title="Steps", show_header=True, header_style="bold green")
    steps.add_column("Id", style="cyan", no_wrap=True)
    steps.add_column("Invoke", style="white")
    steps.add_column("Wait For", style="magenta")
    for step in invocation.workflow.steps:
        target = step.invoke.target if step.invoke else "-"
        wait_for = ", ".join(step.wait_for) if step.wait_for else "-"
        steps.add_row(step.id, target, wait_for)
    console.print(steps)


def _display_kg_results(profile_name: str, results: dict[str, Any]) -> None:
    """Display KG creation results extracted from workflow step outputs."""
    kg_results = {sid: r for sid, r in results.items() if hasattr(r, "stats")}
    if not kg_results:
        console.print(f"[green]✓ {profile_name}: workflow completed ({len(results)} step(s))[/green]")
        return

    for step_id, result in kg_results.items():
        stats = result.stats
        warnings = getattr(result, "warnings", [])
        has_failures = stats.total_failed > 0
        color = "yellow" if has_failures else "green"
        icon = "⚠" if has_failures else "✓"
        console.print(f"[{color}]{icon} {step_id}:[/{color}] {stats.total_processed} ok, {stats.total_failed} failed")
        if getattr(result, "db_path", None):
            console.print(f"  [dim]Path:[/dim] {result.db_path}")
        for w in warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")
        if getattr(result, "html_export", None):
            export_path = result.html_export.output_path
            console.print(f"  [green]📊 HTML:[/green] file://{export_path}")


class EkgCommands(CliTopCommand):
    """Commands for interacting with a Knowledge Graph."""

    def get_description(self) -> tuple[str, str]:  # type: ignore[override]
        return "kg", "Knowledge Graph commands."

    def register_sub_commands(self, cli_app: typer.Typer) -> None:  # type: ignore[override]
        """Register ``kg`` subcommands on the given Typer application."""

        @cli_app.command("create")
        def create(
            name: Annotated[
                str | None,
                typer.Argument(help="KG name (e.g. 'one_rainbow' → profile 'kg_one_rainbow') or full profile name."),
            ] = None,
            all_graphs: Annotated[
                bool,
                typer.Option("--all", "--all-graphs", help="Run all kg_* workflow profiles."),
            ] = False,
            dry_run: Annotated[
                bool,
                typer.Option("--dry-run", help="Resolve the workflow plan without executing."),
            ] = False,
            force: Annotated[
                bool,
                typer.Option("--force", help="Force rebuild of imported KG dependencies."),
            ] = False,
            delete_first: Annotated[
                bool,
                typer.Option("--delete-first/--no-delete-first", help="Delete existing KG before creation."),
            ] = True,
            export_html: Annotated[
                bool,
                typer.Option("--export-html/--no-export-html", help="Export HTML visualization after creation."),
            ] = True,
            clear_all_caches: Annotated[
                bool,
                typer.Option("--clear-all-caches", help="Clear parquet caches before creation."),
            ] = False,
            set_values: Annotated[
                list[str] | None,
                typer.Option("--set", help="Override profile values as KEY=VALUE.", metavar="KEY=VALUE"),
            ] = None,
        ) -> None:
            """Create KG databases via the workflow engine.

            NAME maps to workflow profile 'kg_NAME' (e.g., 'one_rainbow' → 'kg_one_rainbow').
            Use 'cli workflow list profiles' to see all available profiles.

            Examples:
                cli kg create one_rainbow
                cli kg create one_rainbow --force --no-delete-first
                cli kg create --all
                cli kg create one_rainbow --dry-run
                cli kg create one_rainbow --set export_html=false
            """
            from genai_tk.workflow.executor import execute_workflow
            from genai_tk.workflow.resolver import (
                WorkflowResolutionError,
                list_workflow_profile_names,
                parse_cli_overrides,
                resolve_workflow_invocation,
            )

            # Clear parquet caches if requested
            if clear_all_caches:
                from genai_graph.kg.export.artifacts import clear_all_parquet_caches

                cleared = clear_all_parquet_caches()
                console.print(f"[bold green]✓[/bold green] Cleared {cleared} parquet cache(s)")

            # Build CLI overrides from convenience flags + raw --set values
            cli_overrides: dict[str, Any] = parse_cli_overrides(set_values) if set_values else {}
            cli_overrides.setdefault("force_rebuild", force)
            cli_overrides.setdefault("delete_first", delete_first)
            cli_overrides.setdefault("export_html", export_html)

            # Determine which profiles to run
            all_profile_names = list_workflow_profile_names()
            if all_graphs:
                profile_names = [p for p in all_profile_names if p.startswith("kg_")]
                if not profile_names:
                    console.print("[yellow]No kg_* workflow profiles found.[/yellow]")
                    raise typer.Exit(0)
                console.print(f"[bold]Running all KG profiles:[/bold] {', '.join(profile_names)}")
            elif name:
                # Resolve: try kg_{name} first, then fall back to exact name
                candidate = f"kg_{name}"
                profile_name = candidate if candidate in all_profile_names else name
                profile_names = [profile_name]
            else:
                # No name given: use the default KG manager profile
                from genai_graph.kg.manager import get_kg_manager

                default = f"kg_{get_kg_manager().profile}"
                profile_names = [default]
                console.print(f"[dim]Using default profile: {default}[/dim]")

            # Run each profile
            failed: list[tuple[str, str]] = []
            for profile_name in profile_names:
                if len(profile_names) > 1:
                    console.rule(f"[cyan]{profile_name}[/cyan]")

                try:
                    invocation = resolve_workflow_invocation(profile_name, cli_overrides=cli_overrides)
                except WorkflowResolutionError as exc:
                    console.print(Panel(str(exc), title=f"Resolution Error: {profile_name}", border_style="red"))
                    if len(profile_names) == 1:
                        raise typer.Exit(1) from exc
                    failed.append((profile_name, str(exc)))
                    continue

                _render_kg_plan(invocation)

                if dry_run:
                    continue

                try:
                    results = execute_workflow(invocation)
                    _display_kg_results(profile_name, results)
                except Exception as exc:
                    root_cause = _extract_root_cause(exc)
                    logger.debug("KG creation error for {}: {}", profile_name, exc, exc_info=True)
                    console.print(Panel(root_cause, title=f"KG creation failed: {profile_name}", border_style="red"))
                    if len(profile_names) == 1:
                        raise typer.Exit(1) from exc
                    failed.append((profile_name, root_cause))

            if dry_run:
                console.print(Panel("Dry run complete — no execution performed.", border_style="green"))
                return

            if len(profile_names) > 1:
                if failed:
                    console.print(
                        Panel(
                            f"{len(failed)}/{len(profile_names)} failed: " + ", ".join(p for p, _ in failed),
                            title="Summary",
                            border_style="red",
                        )
                    )
                    raise typer.Exit(1)
                else:
                    console.print(Panel(f"All {len(profile_names)} KG profile(s) completed.", border_style="green"))

        @cli_app.command("info")
        def info() -> None:
            """Display EKG database information, schema overview, and mappings.

            Reads and displays the info that was automatically generated during
            graph creation. The info includes database details, schema statistics,
            node/relationship mappings, and subgraph configurations.
            """
            from genai_graph.kg.manager import get_kg_manager

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
        def schema(
            regen: bool = typer.Option(False, "--regen", help="Regenerate schema file from current graph definitions"),
            kg: Annotated[
                str | None,
                typer.Option("--kg", help="KG config name (e.g. stratnav_subset_rainbow_crm)"),
            ] = None,
        ) -> None:
            """Display knowledge graph schema.

            Reads and displays the schema that was automatically generated during
            graph creation. The schema includes node types, relationships, properties,
            and indexed fields.

            Use --regen to regenerate the schema file from the current Python graph
            definitions without rebuilding the full graph.
            """

            from genai_graph.kg.export.artifacts import export_schema
            from genai_graph.kg.manager import get_kg_manager

            # Get the current KG manager (auto-activates)
            manager = get_kg_manager()
            active_kg = kg if kg is not None else manager.profile

            if regen:
                try:
                    from genai_graph.kg.export.artifacts import export_schema_json

                    dest_txt = export_schema(active_kg)
                    dest_json = export_schema_json(active_kg)
                    console.print(f"[green]✅ Schema regenerated → {dest_txt}[/green]")
                    console.print(f"[green]✅ Schema JSON regenerated → {dest_json}[/green]")
                except Exception as exc:
                    import traceback as tb

                    console.print(f"[red]❌ Failed to regenerate schema: {exc}[/red]")
                    console.print("[red]" + tb.format_exc() + "[/red]")
                    raise typer.Exit(1) from exc

            # Check if schema file exists
            schema_path = manager.get_schema_path_for(active_kg)
            if not schema_path.exists():
                console.print(
                    "[red]❌ No schema file found.[/red]\n"
                    "[yellow]💡 Run [bold]cli kg create[/bold] or [bold]cli kg schema --regen[/bold] to generate the schema[/yellow]"
                )
                raise typer.Exit(1)

            # Read and display the schema
            try:
                schema_content = schema_path.read_text(encoding="utf-8")
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

            from genai_tk.agents.langchain.agent_cli import (
                run_langchain_agent_direct,
                run_langchain_agent_shell,
            )
            from genai_tk.agents.langchain.langchain_agent import LangchainAgent
            from genai_tk.agents.langchain_setup import setup_langchain

            from genai_graph.kg.manager import get_kg_manager
            from genai_graph.kg.query import (
                build_ekg_agent_system_prompt,
                create_ekg_cypher_tool,
            )

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
                agent = LangchainAgent(
                    profile_name="EKG",
                    llm=llm,
                    tools=[ekg_tool],
                    mcp_servers=list(mcp),
                    system_prompt=system_prompt,
                    checkpointer=True,
                )
                asyncio.run(run_langchain_agent_shell(agent))
            else:
                # Handle input from --input parameter or stdin
                if not input and not sys.stdin.isatty():
                    input = sys.stdin.read()
                if not input or len(input.strip()) < 3:
                    console.print("[red]❌ Input parameter or something in stdin is required[/red]")
                    raise typer.Exit(1)

                agent = LangchainAgent(
                    profile_name="EKG",
                    llm=llm,
                    tools=[ekg_tool],
                    mcp_servers=list(mcp),
                    system_prompt=system_prompt,
                )
                # Reuse the common ReAct helper from genai-tk
                asyncio.run(run_langchain_agent_direct(input.strip(), agent))

        @cli_app.command("cypher")
        def cypher(
            query: str = typer.Argument(help="Cypher query to execute"),
        ) -> None:
            """Execute Cypher queries on the EKG database."""

            from rich.panel import Panel

            from genai_graph.kg.backend import (
                create_backend_from_config,
            )
            from genai_graph.kg.manager import get_kg_manager

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
            kg: Annotated[
                str | None,
                typer.Option("--kg", help="KG config name to query (e.g. learned_stratnav_subset_rainbow_crm)"),
            ] = None,
        ) -> None:
            """Execute queries in natural language (Text-2-Cypher) on the EKG database.

            ex:  List the names of all competitors for opportunities created after January 1, 2012."""

            from rich.panel import Panel

            from genai_graph.kg.manager import get_kg_manager
            from genai_graph.kg.query import query_kg

            manager = get_kg_manager()
            active_kg = kg if kg is not None else manager.profile
            console.print(Panel(f"[bold cyan]Querying EKG Database[/bold cyan]\n[dim]Config: {active_kg}[/dim]"))

            try:
                from rich.table import Table

                df = query_kg(query, llm=llm, kg_config_name=kg)

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

                logger.error("Failed to process query: {}", e)
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

            from genai_graph.kg.manager import get_kg_manager

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
            from genai_tk.workflow.prefect.run import run_flow_ephemeral

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
                logger.error("CRM file not found: {}", exc)
                console.print(f"[red]❌ CRM file not found: {exc}[/red]")
                raise typer.Exit(1) from exc
            except Exception as exc:
                logger.error("Fake Rainbow generation failed: {}", exc)
                console.print(f"[red]❌ Generation failed: {exc}[/red]")
                raise typer.Exit(1) from exc
