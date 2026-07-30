"""CLI commands for the Markdown Knowledge Tree (``cli tree ...``).

Provides ``build``, ``list``, ``toc``, ``cat``, and ``search`` sub-commands
that operate directly on a Ladybug database via
`genai_graph.kg.markdown.ingest` and `genai_graph.kg.query.markdown_tree_tools`.
"""

from __future__ import annotations

from typing import Annotated

import typer
from genai_tk.main.cli import CliTopCommand
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
                typer.Argument(help="Directories, files, or .zip archives to ingest."),
            ],
            db_path: Annotated[
                str,
                typer.Option("--db", help="Path to the Ladybug database file."),
            ],
            include: Annotated[
                list[str] | None,
                typer.Option("--include", help="Glob pattern(s) to include (default '*.md')."),
            ] = None,
            exclude: Annotated[
                list[str] | None,
                typer.Option("--exclude", help="Glob pattern(s) to exclude."),
            ] = None,
            force: Annotated[
                bool,
                typer.Option("--force", help="Rebuild sections/chunks for documents already in the graph."),
            ] = False,
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
            """Build (or update) a Markdown Knowledge Tree graph from a corpus."""
            from genai_graph.kg.backend import KuzuBackend
            from genai_graph.kg.factories.markdown_tree_factory import MarkdownTreeFactory
            from genai_graph.kg.markdown.ingest import drop_markdown_tree, ingest_markdown_tree

            backend = KuzuBackend()
            backend.connect(db_path)

            if delete_first:
                console.print("[yellow]Dropping existing Markdown Knowledge Tree tables...[/yellow]")
                drop_markdown_tree(backend)

            factory = MarkdownTreeFactory(
                sources=source,
                include=include or ["*.md"],
                exclude=exclude or [],
                embed_chunks=embed,
                embeddings_model=embeddings_model,
            )

            result = ingest_markdown_tree(backend, factory, force=force)

            table = Table(title="Markdown Knowledge Tree — Build Result")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            table.add_row("Processed", str(result.documents_processed))
            table.add_row("Skipped (unchanged)", str(result.documents_skipped))
            table.add_row("Failed", str(result.documents_failed))
            table.add_row("Sections created", str(result.sections_created))
            table.add_row("Chunks created", str(result.chunks_created))
            table.add_row("Relationships created", str(result.relationships_created))
            console.print(table)
            for w in result.warnings:
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

        logger.debug("Registered 'tree' CLI commands")
