"""Factory that ingests a Markdown corpus as a Folder → Document → Section graph.

Each source file becomes a :class:`~genai_graph.kg.nodes.document.Document`
(keyed by content hash) inside a :class:`~genai_graph.kg.nodes.document.Folder`.
The document's Markdown heading hierarchy is extracted into a flat,
order-preserving list of
:class:`~genai_graph.kg.nodes.document_section.MarkdownSection` nodes (see
:mod:`genai_graph.kg.document_graph.tree_parser`).

`sources` accepts a mix of directories, individual files, and ``.zip`` archives;
each source becomes a Folder (see
:class:`~genai_graph.kg.document_graph.repository.SourceFolder`).

The actual graph is built by
:func:`genai_graph.kg.document_graph.ingest.ingest_document_graph`, which bypasses
the generic Pydantic-nesting extraction (the section hierarchy is
self-referential) and merges nodes/relationships directly via the same
Arrow/Ladybug primitives (`merge_nodes_batch`, `merge_relationships_batch`) used
elsewhere in genai-graph.
"""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.document_graph.repository import SourceFolder
from genai_graph.kg.document_graph.tree_parser import _estimate_token_count, parse_markdown_tree
from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.nodes.document import (
    CONTAINS_DOC,
    Document,
    DocumentNode,
    Folder,
    FolderNode,
)
from genai_graph.kg.nodes.document_section import (
    HAS_SECTION,
    HAS_SUBSECTION,
    MarkdownSection,
    SectionNode,
)
from genai_graph.kg.schema.core import GraphSchema


class DocumentGraphBundle(BaseModel):
    """A fully parsed Markdown document: folder, document and sections."""

    folder: Folder
    document: Document
    sections: list[MarkdownSection] = Field(default_factory=list)


class DocumentGraphFactory(KgFactory):
    """Scans directories/files/zip archives and builds a Folder+Document+Section graph.

    Extracts the *heading hierarchy* of each Markdown file so an agent can
    navigate a document's table of contents and fetch only the sections it needs,
    and it stores the file structure (Folder → Document) explicitly.
    """

    TOP_CLASS: type[BaseModel] | None = None

    sources: list[str] = Field(..., description="Directories, files, or .zip archives to ingest")
    include: list[str] = Field(default_factory=lambda: ["*.md"], description="Glob patterns to include")
    exclude: list[str] = Field(default_factory=list, description="Glob patterns to exclude")
    recursive: bool = Field(default=True, description="Recurse into sub-directories")
    cache_dir: str | None = Field(default=None, description="Directory to extract .zip archives into")

    # Per-instance caches
    _files_cache: list[Path] | None = None
    _folder_by_file: dict[str, SourceFolder] | None = None

    # ------------------------------------------------------------------
    # KgFactory protocol
    # ------------------------------------------------------------------

    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=None,
            nodes=[FolderNode, DocumentNode, SectionNode],
            relations=[CONTAINS_DOC, HAS_SECTION, HAS_SUBSECTION],
        )

    def get_keys(self) -> list[str]:
        """Return all discovered Markdown file paths as factory keys."""
        return [str(p) for p in self._get_files()]

    def get_struct_data_by_key(self, key: str) -> DocumentGraphBundle | None:
        """Return the parsed document-graph bundle for a file."""
        path = Path(key)
        if not path.exists():
            logger.warning("DocumentGraphFactory: file not found: {}", key)
            return None
        folder = self._folder_for(key)
        return self._build_bundle(path, folder)

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _get_files(self) -> list[Path]:
        if self._files_cache is not None:
            return self._files_cache

        files: list[Path] = []
        folder_by_file: dict[str, SourceFolder] = {}
        seen: set[str] = set()

        from genai_tk.config_mgmt.file_patterns import resolve_config_path

        for raw_source in self.sources:
            resolved = Path(resolve_config_path(raw_source))
            if not resolved.exists():
                logger.warning("DocumentGraphFactory: source not found: {}", resolved)
                continue

            folder = SourceFolder.from_source(raw_source, cache_dir=self.cache_dir)
            single = resolved if (resolved.is_file() and resolved.suffix.lower() != ".zip") else None
            resolved_files = folder.iter_files(
                include=self.include, exclude=self.exclude, recursive=self.recursive, single_file=single
            )
            for rf in resolved_files:
                dedup_key = str(rf.abs_path.resolve())
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                files.append(rf.abs_path)
                folder_by_file[str(rf.abs_path)] = folder

        self._files_cache = files
        self._folder_by_file = folder_by_file
        logger.info("DocumentGraphFactory: discovered {} file(s) from {} source(s)", len(files), len(self.sources))
        return files

    def _folder_for(self, key: str) -> SourceFolder:
        if self._folder_by_file is None:
            self._get_files()
        assert self._folder_by_file is not None
        folder = self._folder_by_file.get(key)
        if folder is None:
            # File not discovered through a source (e.g. direct key) — synthesise a folder.
            folder = SourceFolder.from_source(str(Path(key).parent), cache_dir=self.cache_dir)
        return folder

    # ------------------------------------------------------------------
    # Bundle construction
    # ------------------------------------------------------------------

    def _build_bundle(self, path: Path, folder: SourceFolder) -> DocumentGraphBundle:
        from genai_tk.utils.hashing import buffer_digest, file_digest

        text = path.read_text(encoding="utf-8", errors="replace")

        try:
            stat = path.stat()
            file_size: int | None = stat.st_size
            modified_at: str | None = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except Exception as exc:
            logger.warning("Could not stat {}: {}", path, exc)
            file_size = None
            modified_at = None

        content_hash = file_digest(path)
        mime_type, _ = mimetypes.guess_type(str(path))

        markdown_hash = buffer_digest(text.encode("utf-8"))
        flat_sections = parse_markdown_tree(text)
        sections = [
            MarkdownSection(
                section_id=f"{markdown_hash}::{idx}",
                markdown_hash=markdown_hash,
                parent_section_id=(f"{markdown_hash}::{fs.parent_index}" if fs.parent_index is not None else None),
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

        document = Document(
            content_hash=content_hash,
            markdown_hash=markdown_hash,
            filename=path.name,
            folder_id=folder.folder_id,
            relative_path=folder.relative_path_of(path),
            path=str(path),
            file_size=file_size,
            mime_type=mime_type,
            modified_at=modified_at,
            token_count=_estimate_token_count(text),
            section_count=len(sections),
        )

        return DocumentGraphBundle(
            folder=folder.folder_node(),
            document=document,
            sections=sections,
        )
