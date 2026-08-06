"""CLI commands for the Document Graph (``cli docgraph ...``).

Provides:
- ``run``    : execute a document-graph workflow profile (markdownize sources,
               then run the configured sub-graph factories) via the workflow
               engine. Sources can be overridden ad-hoc with ``-s``.
- ``build``  : quick document-graph-only build (markdownize sources, then ingest
               the Folder → Document → Section structure) directly on a Ladybug DB.
- ``list`` / ``toc`` / ``cat`` / ``search`` / ``tui`` : navigate an ingested graph.

``run`` and ``kg create`` share the same workflow engine; ``run`` targets ad-hoc
sources while ``kg create`` targets a predefined, named set of documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from genai_tk.config_mgmt.config_mngr import global_config
from genai_tk.main.cli import CliTopCommand
from genai_tk.workflow.force import ForceStage
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _resolve_db_path(db_path: str | None = None) -> str:
    """Resolve database path from parameter or config default.

    Args:
        db_path: Explicit database path. If provided, use it.

    Returns:
        Resolved database path.

    Raises:
        typer.Exit: If no path provided and no config default found.
    """
    if db_path:
        return db_path

    # Try to get default from config
    default_db = global_config().get("graph_db.default", None)
    if default_db:
        return str(default_db)

    console.print(
        "[red]Error: No database path provided and no graph_db.default configured.[/red]\n"
        "  Use --db <path> or add graph_db.default to your config file."
    )
    raise typer.Exit(1)


def _validate_force(force: str | None) -> None:
    if force is None:
        return
    try:
        ForceStage(force)
    except ValueError as exc:
        stages = ", ".join(s.value for s in ForceStage)
        console.print(f"[red]Invalid --force stage '{force}'. Choose one of: {stages}[/red]")
        raise typer.Exit(1) from exc


class DocGraphCommands(CliTopCommand):
    """Commands for building and navigating a Document Graph."""

    def get_description(self) -> tuple[str, str]:  # type: ignore[override]
        return "docgraph", "Document Graph commands."

    def register_sub_commands(self, cli_app: typer.Typer) -> None:  # type: ignore[override]
        """Register ``docgraph`` subcommands on the given Typer application."""

        @cli_app.command("run")
        def run(
            workflow: Annotated[
                str,
                typer.Option("--workflow", "-w", help="Document-graph workflow profile (e.g. 'rainbow_extract')."),
            ],
            source: Annotated[
                list[str] | None,
                typer.Option("--source", "-s", help="Ad-hoc source file(s)/dir(s)/zip(s); overrides profile sources."),
            ] = None,
            dry_run: Annotated[
                bool,
                typer.Option("--dry-run", help="Resolve the workflow plan without executing."),
            ] = False,
            force: Annotated[
                str | None,
                typer.Option("--force", help="Force-invalidate caches from this stage onward."),
            ] = None,
            delete_first: Annotated[
                bool,
                typer.Option("--delete-first/--no-delete-first", help="Delete existing graph before creation."),
            ] = False,
            export_html: Annotated[
                bool,
                typer.Option("--export-html/--no-export-html", help="Export HTML visualization after creation."),
            ] = True,
            set_values: Annotated[
                list[str] | None,
                typer.Option("--set", help="Override profile values as KEY=VALUE.", metavar="KEY=VALUE"),
            ] = None,
        ) -> None:
            """Run a document-graph workflow over ad-hoc or configured sources.

            Examples:
                cli docgraph run --workflow rainbow_extract -s "04...VENUS...pptx"
                cli docgraph run -w rainbow_extract -s ./ppt --force md
            """
            _validate_force(force)

            from genai_tk.workflow.executor import execute_workflow
            from genai_tk.workflow.resolver import (
                WorkflowResolutionError,
                parse_cli_overrides,
                resolve_workflow_invocation,
            )

            cli_overrides: dict[str, Any] = parse_cli_overrides(set_values) if set_values else {}
            cli_overrides.setdefault("force_stage", force)
            cli_overrides.setdefault("delete_first", delete_first)
            cli_overrides.setdefault("export_html", export_html)
            if source:
                cli_overrides["sources"] = list(source)

            try:
                invocation = resolve_workflow_invocation(workflow, cli_overrides=cli_overrides)
            except WorkflowResolutionError as exc:
                console.print(Panel(str(exc), title=f"Resolution Error: {workflow}", border_style="red"))
                raise typer.Exit(1) from exc

            _render_plan(invocation)
            if dry_run:
                console.print(Panel("Dry run complete — no execution performed.", border_style="green"))
                return

            try:
                results = execute_workflow(invocation)
            except Exception as exc:
                logger.debug("docgraph run error for {}: {}", workflow, exc, exc_info=True)
                console.print(Panel(str(exc), title=f"docgraph run failed: {workflow}", border_style="red"))
                raise typer.Exit(1) from exc
            console.print(f"[green]✓ {workflow}: workflow completed ({len(results)} step(s))[/green]")

        @cli_app.command("build")
        def build(
            source: Annotated[
                list[str],
                typer.Argument(help="Directories, files, or .zip archives to ingest (raw docs or Markdown)."),
            ],
            db_path: Annotated[
                str | None,
                typer.Option(
                    "--db", help="Path to the Ladybug database file. Uses graph_db.default from config if omitted."
                ),
            ] = None,
            md_output_dir: Annotated[
                str | None,
                typer.Option(
                    "--md-output-dir",
                    help="Where converted Markdown is written. Defaults to '<db_path stem>_markdown'.",
                ),
            ] = None,
            cache_dir: Annotated[
                str | None,
                typer.Option("--cache-dir", help="Intermediates directory (unzipped/pdf/manifest)."),
            ] = None,
            profile: Annotated[
                str,
                typer.Option("--profile", help="markdownize profile: fast, medium, best, or default."),
            ] = "default",
            include: Annotated[
                list[str] | None,
                typer.Option("--include", help="Glob pattern(s) to include (default '*.md')."),
            ] = None,
            exclude: Annotated[
                list[str] | None,
                typer.Option("--exclude", help="Glob pattern(s) to exclude."),
            ] = None,
            force: Annotated[
                str | None,
                typer.Option("--force", help="Force-invalidate caches from this stage onward."),
            ] = None,
            delete_first: Annotated[
                bool,
                typer.Option("--delete-first", help="Drop existing Section tables before ingesting."),
            ] = False,
        ) -> None:
            """Markdownize sources, then build (or update) a Document Graph.

            Examples:
                cli docgraph build ./docs --db ./data/kg/tree.db
                cli docgraph build ./Alko.zip --db ./data/kg/tree.db --force md
                cli docgraph build ./docs (uses graph_db.default from config)
            """
            _validate_force(force)
            db_path = _resolve_db_path(db_path)

            from genai_tk.workflow.markdownize import markdownize_flow

            from genai_graph.orchestration.document_graph_flow import document_graph_flow

            resolved_md_output_dir = md_output_dir or str(Path(db_path).with_suffix("")) + "_markdown"

            console.print(f"[dim]Markdownizing {len(source)} source(s) -> {resolved_md_output_dir}[/dim]")
            markdownize_flow(
                sources=source,
                md_output_dir=resolved_md_output_dir,
                cache_dir=cache_dir,
                profile=profile,
                force_stage=force,
            )

            result_dict = document_graph_flow(
                sources=[resolved_md_output_dir],
                db_path=db_path,
                include=include or ["*.md"],
                exclude=exclude or [],
                force_stage=force,
                delete_first=delete_first,
            )

            table = Table(title="Document Graph — Build Result")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            table.add_row("Processed", str(result_dict["documents_processed"]))
            table.add_row("Skipped (unchanged)", str(result_dict["documents_skipped"]))
            table.add_row("Failed", str(result_dict["documents_failed"]))
            table.add_row("Sections created", str(result_dict["sections_created"]))
            table.add_row("Relationships created", str(result_dict["relationships_created"]))
            console.print(table)
            for w in result_dict["warnings"]:
                console.print(f"[yellow]⚠ {w}[/yellow]")

        @cli_app.command("list")
        def list_docs(
            db_path: Annotated[
                str | None,
                typer.Option(
                    "--db", help="Path to the Ladybug database file. Uses graph_db.default from config if omitted."
                ),
            ] = None,
        ) -> None:
            """List every ingested document."""
            db_path = _resolve_db_path(db_path)
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.document_graph_tools import list_documents

            backend = KuzuBackend()
            backend.connect(db_path)
            rows = list_documents(backend)
            if not rows:
                console.print("[yellow]No documents ingested yet.[/yellow]")
                return

            table = Table(title="Documents")
            table.add_column("Filename", style="cyan")
            table.add_column("Sections", style="white")
            table.add_column("Markdown Hash", style="dim")
            table.add_column("Path", style="dim")
            for r in rows:
                table.add_row(str(r["filename"]), str(r["section_count"]), str(r["markdown_hash"]), str(r["path"]))
            console.print(table)

        @cli_app.command("toc")
        def toc(
            document: Annotated[str, typer.Argument(help="Document hash (or prefix) or filename.")],
            db_path: Annotated[
                str | None,
                typer.Option(
                    "--db", help="Path to the Ladybug database file. Uses graph_db.default from config if omitted."
                ),
            ] = None,
        ) -> None:
            """Show the table of contents (heading tree) for one document."""
            db_path = _resolve_db_path(db_path)
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.document_graph_tools import get_document_toc

            backend = KuzuBackend()
            backend.connect(db_path)
            rows = get_document_toc(backend, document)
            if not rows:
                console.print(f"[yellow]No sections found for document: {document}[/yellow]")
                return
            for r in rows:  # type: ignore[union-attr]
                indent = "  " * max(int(r["level"]) - 1, 0)
                console.print(f"{indent}- [{r['section_id']}] {r['title']} (line {r['line_start']})")

        @cli_app.command("cat")
        def cat(
            document: Annotated[
                str,
                typer.Argument(
                    help="Document hash (or prefix), filename, or a section id "
                    "(e.g. 'd9387cdaf256734a::1') to show just that section and its subsections."
                ),
            ],
            db_path: Annotated[
                str | None,
                typer.Option(
                    "--db", help="Path to the Ladybug database file. Uses graph_db.default from config if omitted."
                ),
            ] = None,
            cypher: Annotated[
                bool,
                typer.Option("--cypher", help="Print the Cypher query used to fetch the content."),
            ] = False,
            raw: Annotated[
                bool,
                typer.Option("--raw", help="Print raw Markdown text instead of rendering it."),
            ] = False,
        ) -> None:
            """Reconstruct and print a document's (or one section's) Markdown text from its sections."""
            db_path = _resolve_db_path(db_path)
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.document_graph_tools import reconstruct_document, reconstruct_section

            backend = KuzuBackend()
            backend.connect(db_path)

            if "::" in document:
                text, query = reconstruct_section(backend, document, return_query=True)
            else:
                text, query = reconstruct_document(backend, document, return_query=True)

            if cypher:
                console.print(Panel(query, title="Cypher", border_style="cyan"))

            if text is None:
                console.print(f"[red]No document or section found matching: {document}[/red]")
                raise typer.Exit(1)

            if raw:
                console.print(text)
            else:
                from rich.markdown import Markdown as RichMarkdown

                console.print(RichMarkdown(text))

        @cli_app.command("search")
        def search(
            keyword: Annotated[str, typer.Argument(help="Keyword to search for in section titles/text.")],
            db_path: Annotated[
                str | None,
                typer.Option(
                    "--db", help="Path to the Ladybug database file. Uses graph_db.default from config if omitted."
                ),
            ] = None,
            limit: Annotated[int, typer.Option("--limit", help="Max number of matches.")] = 20,
        ) -> None:
            """Search section titles and text across all ingested documents."""
            db_path = _resolve_db_path(db_path)
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.document_graph_tools import search_sections

            backend = KuzuBackend()
            backend.connect(db_path)
            rows = search_sections(backend, keyword, limit)
            if not rows:
                console.print(f"[yellow]No sections matched keyword: {keyword!r}[/yellow]")
                return
            for r in rows:  # type: ignore[union-attr]
                console.print(f"- [{r['section_id']}] {r['title']} (line {r['line_start']}) — {r['markdown_hash']}")

        @cli_app.command("tui")
        def tui(
            db_path: Annotated[
                str | None,
                typer.Option(
                    "--db", help="Path to the Ladybug database file. Uses graph_db.default from config if omitted."
                ),
            ] = None,
        ) -> None:
            """Launch an interactive Textual TUI to browse the Document Graph."""
            db_path = _resolve_db_path(db_path)
            from genai_graph.kg.query.document_graph_tui import run_document_graph_tui

            run_document_graph_tui(db_path)

        logger.debug("Registered 'docgraph' CLI commands")


def _render_plan(invocation: Any) -> None:
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
    summary.add_row("Force stage", invocation.force_stage or "<none>")
    summary.add_row("Steps", str(len(invocation.workflow.steps)))
    console.print(summary)

    if invocation.values:
        console.print(Panel(_json.dumps(invocation.values, indent=2, default=str), title="Effective Values"))
