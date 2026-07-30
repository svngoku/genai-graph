"""Factory that ingests a Markdown corpus as a Repository → Document →
MarkdownDocument → Section → Chunk knowledge tree.

Each source file becomes a :class:`~genai_graph.kg.nodes.document.Document`
(keyed by content hash) inside a :class:`~genai_graph.kg.nodes.document.Repository`.
The Markdown rendering is a :class:`~genai_graph.kg.nodes.document.MarkdownDocument`
whose heading hierarchy is extracted into a flat, order-preserving list of
:class:`~genai_graph.kg.nodes.markdown_tree.MarkdownSection` nodes (see
:mod:`genai_graph.kg.markdown.tree_parser`). Each section is split into one or
more :class:`~genai_graph.kg.nodes.document.Chunk` nodes carrying optional
embeddings.

`sources` accepts a mix of directories, individual files, and ``.zip`` archives;
each source becomes a Repository (see
:class:`~genai_graph.kg.markdown.repository.SourceRepository`).

The actual graph is built by
:func:`genai_graph.kg.markdown.ingest.ingest_markdown_tree`, which bypasses the
generic Pydantic-nesting extraction (the section hierarchy is self-referential)
and merges nodes/relationships directly via the same Arrow/Ladybug primitives
(`merge_nodes_batch`, `merge_relationships_batch`) used elsewhere in genai-graph.
"""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.markdown.repository import SourceRepository
from genai_graph.kg.markdown.tree_parser import _estimate_token_count, parse_markdown_tree
from genai_graph.kg.nodes.document import (
    CONTAINS_DOC,
    HAS_DOCUMENT,
    MARKDOWNIZED_AS,
    NEXT_CHUNK,
    Chunk,
    ChunkNode,
    Document,
    DocumentNode,
    MarkdownDocument,
    MarkdownDocumentNode,
    Repository,
    RepositoryNode,
)
from genai_graph.kg.nodes.markdown_tree import (
    HAS_CHUNK,
    HAS_SECTION,
    HAS_SUBSECTION,
    MarkdownSection,
    SectionNode,
)
from genai_graph.kg.schema.core import GraphSchema


class MarkdownTreeBundle(BaseModel):
    """A fully parsed Markdown document: repository, document, sections and chunks.

    Chunks are created without embeddings; call
    :meth:`MarkdownTreeFactory.compute_embeddings` to populate them (done only for
    newly ingested Markdown documents so identical content is never re-embedded).
    """

    repository: Repository
    document: Document
    markdown_document: MarkdownDocument
    sections: list[MarkdownSection] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)


class MarkdownTreeFactory(KgFactory):
    """Scans directories/files/zip archives and builds a Document+Section+Chunk tree.

    Unlike :class:`~genai_graph.kg.factories.document_factory.DocumentDirectoryFactory`
    (which chunks whole files for RAG), this factory extracts the *heading
    hierarchy* of each Markdown file so an agent can navigate a document's table
    of contents and fetch only the sections it needs, and it stores the file
    structure (Repository → Document → MarkdownDocument) explicitly.
    """

    TOP_CLASS: type[BaseModel] | None = None

    sources: list[str] = Field(..., description="Directories, files, or .zip archives to ingest")
    include: list[str] = Field(default_factory=lambda: ["*.md"], description="Glob patterns to include")
    exclude: list[str] = Field(default_factory=list, description="Glob patterns to exclude")
    recursive: bool = Field(default=True, description="Recurse into sub-directories")
    cache_dir: str | None = Field(default=None, description="Directory to extract .zip archives into")
    embed_chunks: bool = Field(default=False, description="Compute embeddings for each chunk")
    embeddings_model: str | None = Field(default=None, description="Embeddings model ID (config default when None)")
    chunk_max_tokens: int = Field(default=300, description="Split a section once it exceeds this many tokens")
    chunk_min_tokens: int = Field(default=50, description="Minimum tokens before merging small chunks")

    # Per-instance caches
    _files_cache: list[Path] | None = None
    _repo_by_file: dict[str, SourceRepository] | None = None
    _embeddings_handler: object | None = None

    # ------------------------------------------------------------------
    # KgFactory protocol
    # ------------------------------------------------------------------

    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=None,
            nodes=[RepositoryNode, DocumentNode, MarkdownDocumentNode, SectionNode, ChunkNode],
            relations=[HAS_DOCUMENT, MARKDOWNIZED_AS, HAS_SECTION, HAS_SUBSECTION, HAS_CHUNK, CONTAINS_DOC, NEXT_CHUNK],
        )

    def get_keys(self) -> list[str]:
        """Return all discovered Markdown file paths as factory keys."""
        return [str(p) for p in self._get_files()]

    def get_struct_data_by_key(self, key: str) -> MarkdownTreeBundle | None:
        """Return the parsed tree bundle for a file (chunks have no embeddings yet)."""
        path = Path(key)
        if not path.exists():
            logger.warning("MarkdownTreeFactory: file not found: {}", key)
            return None
        repo = self._repository_for(key)
        return self._build_bundle(path, repo)

    # ------------------------------------------------------------------
    # Embeddings (deferred; only for newly ingested Markdown documents)
    # ------------------------------------------------------------------

    def compute_embeddings(self, chunks: list[Chunk]) -> None:
        """Populate ``chunk.embedding`` in place for every chunk."""
        if not self.embed_chunks or not chunks:
            return
        handler = self._get_embeddings_handler()
        for chunk in chunks:
            if chunk.text.strip():
                chunk.embedding = handler.compute_embeddings(chunk.text)

    def _get_embeddings_handler(self):  # type: ignore[no-untyped-def]
        if self._embeddings_handler is None:
            from genai_graph.kg.embeddings_handler import EmbeddingsHandler

            self._embeddings_handler = EmbeddingsHandler(embeddings_id=self.embeddings_model or "default")
        return self._embeddings_handler

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _get_files(self) -> list[Path]:
        if self._files_cache is not None:
            return self._files_cache

        files: list[Path] = []
        repo_by_file: dict[str, SourceRepository] = {}
        seen: set[str] = set()

        from genai_tk.config_mgmt.file_patterns import resolve_config_path

        for raw_source in self.sources:
            resolved = Path(resolve_config_path(raw_source))
            if not resolved.exists():
                logger.warning("MarkdownTreeFactory: source not found: {}", resolved)
                continue

            repo = SourceRepository.from_source(raw_source, cache_dir=self.cache_dir)
            single = resolved if (resolved.is_file() and resolved.suffix.lower() != ".zip") else None
            resolved_files = repo.iter_files(
                include=self.include, exclude=self.exclude, recursive=self.recursive, single_file=single
            )
            for rf in resolved_files:
                dedup_key = str(rf.abs_path.resolve())
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                files.append(rf.abs_path)
                repo_by_file[str(rf.abs_path)] = repo

        self._files_cache = files
        self._repo_by_file = repo_by_file
        logger.info("MarkdownTreeFactory: discovered {} file(s) from {} source(s)", len(files), len(self.sources))
        return files

    def _repository_for(self, key: str) -> SourceRepository:
        if self._repo_by_file is None:
            self._get_files()
        assert self._repo_by_file is not None
        repo = self._repo_by_file.get(key)
        if repo is None:
            # File not discovered through a source (e.g. direct key) — synthesise a repo.
            repo = SourceRepository.from_source(str(Path(key).parent), cache_dir=self.cache_dir)
        return repo

    # ------------------------------------------------------------------
    # Bundle construction
    # ------------------------------------------------------------------

    def _build_bundle(self, path: Path, repo: SourceRepository) -> MarkdownTreeBundle:
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

        document = Document(
            content_hash=content_hash,
            filename=path.name,
            repository_id=repo.repo_id,
            relative_path=repo.relative_path_of(path),
            path=str(path),
            file_size=file_size,
            mime_type=mime_type,
            modified_at=modified_at,
        )

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

        markdown_document = MarkdownDocument(
            content_hash=markdown_hash,
            source_hash=content_hash,
            filename=path.name,
            token_count=_estimate_token_count(text),
            section_count=len(sections),
        )

        chunks = self._build_chunks(markdown_hash, sections)
        return MarkdownTreeBundle(
            repository=repo.repository_node(),
            document=document,
            markdown_document=markdown_document,
            sections=sections,
            chunks=chunks,
        )

    def _build_chunks(self, markdown_hash: str, sections: list[MarkdownSection]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in sections:
            for local_idx, piece in enumerate(self._split_section_text(section.text)):
                chunks.append(
                    Chunk(
                        chunk_id=f"{section.section_id}::{local_idx}",
                        text=piece,
                        chunk_index=local_idx,
                        section_id=section.section_id,
                        markdown_hash=markdown_hash,
                        token_count=_estimate_token_count(piece),
                    )
                )
        return chunks

    def _split_section_text(self, text: str) -> list[str]:
        if _estimate_token_count(text) <= self.chunk_max_tokens:
            return [text]
        try:
            from genai_tk.workflow.rag.markdown_chunking import create_markdown_splitter
            from langchain_core.documents import Document as LCDocument

            splitter = create_markdown_splitter(max_tokens=self.chunk_max_tokens, min_tokens=self.chunk_min_tokens)
            pieces = [d.page_content for d in splitter.split_documents([LCDocument(page_content=text)])]
            return pieces or [text]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Markdown splitter failed, keeping section as one chunk: {}", exc)
            return [text]
