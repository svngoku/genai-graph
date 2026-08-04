"""CLI commands for the Markdown Knowledge Tree (``cli doctree ...``).

Provides ``build``, ``list``, ``toc``, ``cat``, and ``search`` sub-commands
that operate directly on a Ladybug database. ``build`` always markdownizes its
sources first (directories, files, or ``.zip`` archives — raw Office/PDF/image
documents or pre-existing Markdown, freely mixed) via
`genai_tk.workflow.markdownize.markdownize_flow`, then
ingests the result via `genai_graph.orchestration.markdown_tree_flow.markdown_tree_flow`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from genai_tk.main.cli import CliTopCommand
from genai_tk.workflow.force import ForceStage
from loguru import logger
from rich.console import Console
from rich.table import Table

console = Console()


class TreeCommands(CliTopCommand):
    """Commands for building and navigating a Markdown Knowledge Tree."""

    def get_description(self) -> tuple[str, str]:  # type: ignore[override]
        return "doctree", "Markdown Knowledge Tree commands."

    def register_sub_commands(self, cli_app: typer.Typer) -> None:  # type: ignore[override]
        """Register ``doctree`` subcommands on the given Typer application."""

        @cli_app.command("build")
        def build(
            source: Annotated[
                list[str],
                typer.Argument(help="Directories, files, or .zip archives to ingest (raw docs or Markdown)."),
            ],
            db_path: Annotated[
                str,
                typer.Option("--db", help="Path to the Ladybug database file."),
            ],
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
                typer.Option(
                    "--force",
                    help="Force-invalidate caches from this stage onward: unzip, pdf, md, graph, embed, all.",
                ),
            ] = None,
            delete_first: Annotated[
                bool,
                typer.Option("--delete-first", help="Drop existing Section/Chunk tables before ingesting."),
            ] = False,
            embed: Annotated[
                bool,
                typer.Option("--embed", help="Compute embeddings for newly-ingested chunks."),
            ] = False,
            embeddings_model: Annotated[
                str | None,
                typer.Option("--embeddings-model", help="Embeddings model id (uses config default when omitted)."),
            ] = None,
        ) -> None:
            """Markdownize sources, then build (or update) a Markdown Knowledge Tree graph.

            Examples:
                cli doctree build ./docs --db ./data/kg/tree.db
                cli doctree build ./Alko.zip --db ./data/kg/tree.db --force md
                cli doctree build ./docs --db ./data/kg/tree.db --force all
            """
            if force is not None:
                try:
                    ForceStage(force)
                except ValueError as exc:
                    stages = ", ".join(s.value for s in ForceStage)
                    console.print(f"[red]Invalid --force stage '{force}'. Choose one of: {stages}[/red]")
                    raise typer.Exit(1) from exc

            from genai_tk.workflow.markdownize import markdownize_flow

            from genai_graph.orchestration.markdown_tree_flow import markdown_tree_flow

            resolved_md_output_dir = md_output_dir or str(Path(db_path).with_suffix("")) + "_markdown"

            console.print(f"[dim]Markdownizing {len(source)} source(s) -> {resolved_md_output_dir}[/dim]")
            markdownize_flow(
                sources=source,
                md_output_dir=resolved_md_output_dir,
                cache_dir=cache_dir,
                profile=profile,
                force_stage=force,
            )

            result_dict = markdown_tree_flow(
                sources=[resolved_md_output_dir],
                db_path=db_path,
                include=include or ["*.md"],
                exclude=exclude or [],
                force_stage=force,
                delete_first=delete_first,
                embed_chunks=embed,
                embeddings_model=embeddings_model,
            )

            table = Table(title="Markdown Knowledge Tree — Build Result")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            table.add_row("Processed", str(result_dict["documents_processed"]))
            table.add_row("Skipped (unchanged)", str(result_dict["documents_skipped"]))
            table.add_row("Failed", str(result_dict["documents_failed"]))
            table.add_row("Sections created", str(result_dict["sections_created"]))
            table.add_row("Chunks created", str(result_dict["chunks_created"]))
            table.add_row("Relationships created", str(result_dict["relationships_created"]))
            console.print(table)
            for w in result_dict["warnings"]:
                console.print(f"[yellow]⚠ {w}[/yellow]")

        @cli_app.command("list")
        def list_docs(
            db_path: Annotated[str, typer.Option("--db", help="Path to the Ladybug database file.")],
        ) -> None:
            """List every ingested Markdown document."""
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.markdown_tree_tools import list_documents

            backend = KuzuBackend()
            backend.connect(db_path)
            rows = list_documents(backend)
            if not rows:
                console.print("[yellow]No documents ingested yet.[/yellow]")
                return

            table = Table(title="Markdown Documents")
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
            db_path: Annotated[str, typer.Option("--db", help="Path to the Ladybug database file.")],
        ) -> None:
            """Show the table of contents (heading tree) for one document."""
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.markdown_tree_tools import get_document_toc

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
            document: Annotated[str, typer.Argument(help="Document hash (or prefix) or filename.")],
            db_path: Annotated[str, typer.Option("--db", help="Path to the Ladybug database file.")],
        ) -> None:
            """Reconstruct and print a document's full Markdown text from its sections."""
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.markdown_tree_tools import reconstruct_document

            backend = KuzuBackend()
            backend.connect(db_path)
            text = reconstruct_document(backend, document)
            if text is None:
                console.print(f"[red]No document found matching: {document}[/red]")
                raise typer.Exit(1)
            console.print(text)

        @cli_app.command("search")
        def search(
            keyword: Annotated[str, typer.Argument(help="Keyword to search for in section titles/text.")],
            db_path: Annotated[str, typer.Option("--db", help="Path to the Ladybug database file.")],
            limit: Annotated[int, typer.Option("--limit", help="Max number of matches.")] = 20,
        ) -> None:
            """Search section titles and text across all ingested documents."""
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.query.markdown_tree_tools import search_sections

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
            db_path: Annotated[str, typer.Option("--db", help="Path to the Ladybug database file.")],
        ) -> None:
            """Launch an interactive Textual TUI to browse the Markdown Knowledge Tree."""
            from genai_graph.kg.query.markdown_tree_tui import run_markdown_tree_tui

            run_markdown_tree_tui(db_path)

        logger.debug("Registered 'doctree' CLI commands")
