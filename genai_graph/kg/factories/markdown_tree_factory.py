"""Factory that ingests a corpus of Markdown files as a Document + Section knowledge tree.

Each file becomes a :class:`~genai_graph.kg.nodes.document.Document` node whose
heading hierarchy is extracted into a flat, order-preserving list of
:class:`~genai_graph.kg.nodes.markdown_tree.MarkdownSection` nodes (see
:mod:`genai_graph.kg.markdown.tree_parser`).

`sources` accepts a mix of:

- directories (scanned recursively with `include`/`exclude` gitignore-style pathspecs)
- individual files
- `.zip` archives (extracted into a cache directory, then scanned like a directory)

Usage in a workflow YAML::

    graph:
      factory: genai_graph.kg.factories.markdown_tree_factory.MarkdownTreeFactory
      sources: ["/path/to/docs", "/path/to/extra.md", "/path/to/archive.zip"]
      include: ["*.md"]

The actual graph is built by :func:`genai_graph.kg.markdown.ingest.ingest_markdown_tree`,
which bypasses the generic Pydantic-nesting extraction (the section hierarchy is
self-referential) and merges nodes/relationships directly via the same
Arrow/Ladybug primitives (`merge_nodes_batch`, `merge_relationships_batch`) used
elsewhere in genai-graph.
"""

from __future__ import annotations

import hashlib
import mimetypes
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.markdown.tree_parser import parse_markdown_tree
from genai_graph.kg.nodes.document import Document, DocumentNode
from genai_graph.kg.nodes.markdown_tree import HAS_SECTION, HAS_SUBSECTION, MarkdownSection, SectionNode
from genai_graph.kg.schema.core import GraphSchema


class MarkdownDocumentTree(BaseModel):
    """A parsed Markdown document plus its flat, hierarchy-linked list of sections."""

    document: Document
    sections: list[MarkdownSection] = Field(default_factory=list)


class MarkdownTreeFactory(KgFactory):
    """Factory that scans directories/files/zip archives and builds a Document+Section tree.

    Unlike :class:`~genai_graph.kg.factories.document_factory.DocumentDirectoryFactory`
    (which chunks file content for RAG), this factory extracts the *heading
    hierarchy* of each Markdown file so an agent can navigate a document's
    table of contents and fetch only the sections it needs — no embeddings.
    """

    TOP_CLASS: type[BaseModel] | None = None

    sources: list[str] = Field(..., description="Directories, files, or .zip archives to ingest")
    include: list[str] = Field(default_factory=lambda: ["*.md"], description="Glob patterns to include")
    exclude: list[str] = Field(default_factory=list, description="Glob patterns to exclude")
    recursive: bool = Field(default=True, description="Recurse into sub-directories")
    cache_dir: str | None = Field(
        default=None,
        description="Directory to extract .zip archives into; defaults to '<zip_parent>/.markdown_tree_cache'",
    )

    # Class-level file cache (reset between sessions by clear_cache())
    _files_cache: list[Path] | None = None
    _initialized_roots: ClassVar[set[str]] = set()

    # ------------------------------------------------------------------
    # KgFactory protocol
    # ------------------------------------------------------------------

    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=None,
            nodes=[DocumentNode, SectionNode],
            relations=[HAS_SECTION, HAS_SUBSECTION],
        )

    def get_struct_data_by_key(self, key: str) -> MarkdownDocumentTree | None:
        """Return the parsed Document + Section tree for the given file path (key)."""
        path = Path(key)
        if not path.exists():
            logger.warning("MarkdownTreeFactory: file not found: {}", key)
            return None
        return self._build_document_tree(path)

    # ------------------------------------------------------------------
    # Additional interface
    # ------------------------------------------------------------------

    def get_keys(self) -> list[str]:
        """Return all discovered Markdown file paths as factory keys."""
        return [str(p) for p in self._get_files()]

    @classmethod
    def clear_cache(cls) -> None:
        """Reset the class-level extracted-zip cache (call between workflow runs/tests)."""
        cls._initialized_roots.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_files(self) -> list[Path]:
        if self._files_cache is not None:
            return self._files_cache

        from genai_tk.config_mgmt.file_patterns import resolve_config_path

        discovered: list[Path] = []
        for raw_source in self.sources:
            resolved = resolve_config_path(raw_source)
            src = Path(resolved)

            if not src.exists():
                logger.warning("MarkdownTreeFactory: source not found: {}", src)
                continue

            if src.is_dir():
                discovered.extend(self._scan_directory(src))
            elif src.suffix.lower() == ".zip":
                discovered.extend(self._scan_directory(self._extract_zip(src)))
            else:
                discovered.append(src)

        # De-dup while preserving discovery order
        seen: set[str] = set()
        unique: list[Path] = []
        for p in discovered:
            resolved_key = str(p.resolve())
            if resolved_key not in seen:
                seen.add(resolved_key)
                unique.append(p)

        self._files_cache = unique
        logger.info("MarkdownTreeFactory: discovered {} file(s) from {} source(s)", len(unique), len(self.sources))
        return unique

    def _scan_directory(self, root: Path) -> list[Path]:
        from genai_tk.config_mgmt.file_patterns import resolve_files

        patterns = self.include or ["*.md"]
        pathspecs: list[str] = []
        for pattern in patterns:
            if self.recursive:
                pathspecs.append(f"**/{pattern}")
            pathspecs.append(pattern)
        pathspecs.extend(f"!{p}" for p in (self.exclude or []))
        return resolve_files(str(root), pathspecs=pathspecs)

    def _extract_zip(self, zip_path: Path) -> Path:
        base_cache = Path(self.cache_dir) if self.cache_dir else zip_path.parent / ".markdown_tree_cache"
        digest = hashlib.sha256(str(zip_path.resolve()).encode()).hexdigest()[:16]
        extract_dir = base_cache / f"{zip_path.stem}_{digest}"

        if str(extract_dir) in self.__class__._initialized_roots or extract_dir.exists():
            self.__class__._initialized_roots.add(str(extract_dir))
            return extract_dir

        extract_dir.mkdir(parents=True, exist_ok=True)
        logger.info("MarkdownTreeFactory: extracting {} -> {}", zip_path.name, extract_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        self.__class__._initialized_roots.add(str(extract_dir))
        return extract_dir

    def _build_document_tree(self, path: Path) -> MarkdownDocumentTree:
        text = path.read_text(encoding="utf-8", errors="replace")

        try:
            stat = path.stat()
            file_size: int | None = stat.st_size
            modified_at: str | None = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except Exception as exc:
            logger.warning("Could not stat {}: {}", path, exc)
            file_size = None
            modified_at = None

        try:
            from genai_tk.utils.hashing import file_digest

            content_hash: str | None = file_digest(path)
        except Exception as exc:
            logger.warning("Could not hash {}: {}", path, exc)
            content_hash = None

        mime_type, _ = mimetypes.guess_type(str(path))
        document_path = str(path)

        document = Document(
            path=document_path,
            filename=path.name,
            file_size=file_size,
            mime_type=mime_type,
            modified_at=modified_at,
            content_hash=content_hash,
        )

        flat_sections = parse_markdown_tree(text)
        section_ids = [f"{document_path}::{fs.line_start}" for fs in flat_sections]
        sections = [
            MarkdownSection(
                section_id=section_ids[idx],
                document_path=document_path,
                parent_section_id=section_ids[fs.parent_index] if fs.parent_index is not None else None,
                title=fs.title,
                level=fs.level,
                line_start=fs.line_start,
                line_end=fs.line_end,
                text=fs.text,
                token_count=fs.token_count,
                sequence=idx,
            )
            for idx, fs in enumerate(flat_sections)
        ]

        return MarkdownDocumentTree(document=document, sections=sections)
