"""Factory that ingests a directory of text/markdown files as a Document+Chunk graph.

Each file becomes a :class:`~genai_graph.kg.nodes.document.Document` node.
The file content is split into :class:`~genai_graph.kg.nodes.document.Chunk` nodes
using the chonkie semantic chunker. Sequential chunks are linked with ``NEXT``
relationships; the parent document is linked with ``CONTAINS``.

Usage in a workflow YAML:

    graph:
      factory: genai_graph.kg.factories.document_factory.DocumentDirectoryFactory
      data_root: /path/to/documents
      include: ['*.md', '*.txt']
      chunk_size: 512
      overlap: 50

Extending
---------
To add LLM-based entity extraction on top of the chunked graph, create a subclass
and override ``build_schema()`` to add your domain-specific node types and
``get_struct_data_by_key()`` to return extracted Pydantic models per document.
This mirrors the pattern used by ``JsonFileBackedFactory`` for BAML-extracted data.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import ClassVar

from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.nodes.document import (
    CONTAINS_DOC,
    NEXT_CHUNK,
    Chunk,
    ChunkNode,
    Document,
    DocumentNode,
)
from genai_graph.kg.schema.core import GraphSchema

# File extensions that are treated as plain-text readable
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".rst", ".tex", ".csv", ".log", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js", ".html"}
)


def _read_text_safe(path: Path) -> str | None:
    """Read a file as UTF-8 text, falling back to latin-1 on errors."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except Exception as exc:
            logger.warning("Cannot read {}: {}", path, exc)
            return None


def _mtime_iso(mtime: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Sentinel model — DocumentDirectoryFactory has no single "root model",
# it directly produces Document + Chunk records via get_keys / get_struct_data_by_key.
# We use Document as the TOP_CLASS so that the manager can name this factory.
# ---------------------------------------------------------------------------


class DocumentDirectoryFactory(KgFactory):
    """Factory that scans a directory and builds a Document+Chunk knowledge graph.

    Each file matching the include/exclude patterns becomes a Document node.
    File content is semantically chunked into Chunk nodes linked to their parent
    Document via CONTAINS relationships.  Sequential chunks are linked via NEXT.

    The factory is intentionally minimal — it does NOT perform any LLM-based
    entity extraction.  Subclass it and override ``build_schema()`` /
    ``get_struct_data_by_key()`` to layer structured extraction on top.
    """

    TOP_CLASS: type[BaseModel] | None = Document

    data_root: str = Field(..., description="Root directory to scan for documents")
    include: list[str] = Field(default_factory=lambda: ["*.md", "*.txt"], description="Glob patterns to include")
    exclude: list[str] = Field(default_factory=list, description="Glob patterns to exclude")
    recursive: bool = Field(default=True, description="Recurse into sub-directories")
    chunk_size: int = Field(default=512, description="Target chunk size in tokens")
    overlap: int = Field(default=50, description="Token overlap between consecutive chunks")
    embed_chunks: bool = Field(
        default=False,
        description="Compute embeddings for each chunk. Requires embeddings to be configured.",
    )
    embeddings_model: str | None = Field(
        default=None,
        description="Embeddings model ID (e.g. 'ada_002@openai'). Uses config default when None.",
    )

    # Class-level file cache (reset between sessions by clear_cache())
    _files_cache: list[Path] | None = None
    _initialized_roots: ClassVar[set[str]] = set()

    # ------------------------------------------------------------------
    # KgFactory protocol
    # ------------------------------------------------------------------

    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=Document,
            nodes=[DocumentNode, ChunkNode],
            relations=[CONTAINS_DOC, NEXT_CHUNK],
        )

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Return the Document model for the given file path (key).

        The Document is used as the root record for extraction.  Chunk nodes
        are created separately via ``build_document_chunks()``.
        """
        path = Path(key)
        if not path.exists():
            logger.warning("File not found: {}", key)
            return None

        return self._build_document(path)

    # ------------------------------------------------------------------
    # Additional interface for chunk creation
    # ------------------------------------------------------------------

    def get_keys(self) -> list[str]:
        """Return all discovered file paths as factory keys."""
        return [str(p) for p in self._get_files()]

    def build_document_chunks(self, document_path: str) -> list[Chunk]:
        """Chunk the content of *document_path* and return Chunk instances.

        Args:
            document_path: Absolute path to the source file.

        Returns:
            Ordered list of Chunk models (may be empty if the file is unreadable).
        """
        path = Path(document_path)
        text = _read_text_safe(path)
        if not text or not text.strip():
            logger.debug("Skipping empty file: {}", document_path)
            return []

        raw_chunks = self._chunk_text(text)
        chunks: list[Chunk] = []
        for idx, (chunk_text, start, end, token_count) in enumerate(raw_chunks):
            chunk_id = f"{document_path}::{idx}"
            embedding: list[float] | None = None
            if self.embed_chunks:
                embedding = self._embed(chunk_text)
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_path=document_path,
                    text=chunk_text,
                    chunk_index=idx,
                    start_offset=start,
                    end_offset=end,
                    token_count=token_count,
                    embedding=embedding,
                )
            )
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_files(self) -> list[Path]:
        """Discover files under data_root matching include/exclude patterns."""
        if self._files_cache is not None:
            return self._files_cache

        from genai_tk.config_mgmt.file_patterns import resolve_config_path, resolve_files

        resolved_root = resolve_config_path(self.data_root)
        root_path = Path(resolved_root)

        if not root_path.exists():
            logger.warning("DocumentDirectoryFactory: data_root not found: {}", root_path)
            self._files_cache = []
            return []

        user_patterns = self.include or ["*.md", "*.txt"]
        pathspecs: list[str] = []
        for pattern in user_patterns:
            if self.recursive:
                pathspecs.append(f"**/{pattern}")
                pathspecs.append(pattern)
            else:
                pathspecs.append(pattern)

        exclude_specs: list[str] = [f"!{p}" for p in (self.exclude or [])]

        all_specs = pathspecs + exclude_specs
        discovered = resolve_files(str(root_path), pathspecs=all_specs)
        self._files_cache = [Path(p) for p in discovered]
        logger.info("DocumentDirectoryFactory: discovered {} files under {}", len(self._files_cache), root_path)
        return self._files_cache

    def _build_document(self, path: Path) -> Document:
        """Build a Document model from file metadata."""
        try:
            stat = path.stat()
            file_size: int | None = stat.st_size
            modified_at: str | None = _mtime_iso(stat.st_mtime)
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

        return Document(
            path=str(path),
            filename=path.name,
            file_size=file_size,
            mime_type=mime_type,
            modified_at=modified_at,
            content_hash=content_hash,
        )

    def _chunk_text(self, text: str) -> list[tuple[str, int | None, int | None, int | None]]:
        """Split text into chunks using chonkie.

        Returns:
            List of (text, start_offset, end_offset, token_count) tuples.
        """
        try:
            from chonkie import TokenChunker

            chunker = TokenChunker(chunk_size=self.chunk_size, chunk_overlap=self.overlap)
            raw = chunker(text)
            result: list[tuple[str, int | None, int | None, int | None]] = []
            for chunk in raw:
                start = getattr(chunk, "start_index", None)
                end = getattr(chunk, "end_index", None)
                token_count = getattr(chunk, "token_count", None)
                result.append((chunk.text, start, end, token_count))
            return result
        except Exception as exc:
            logger.warning("Chunking failed ({}), falling back to paragraph split", exc)
            return self._fallback_chunk(text)

    def _fallback_chunk(self, text: str) -> list[tuple[str, int | None, int | None, int | None]]:
        """Simple paragraph-based fallback chunker."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        result: list[tuple[str, int | None, int | None, int | None]] = []
        for paragraph in paragraphs:
            result.append((paragraph, None, None, None))
        return result

    def _embed(self, text: str) -> list[float] | None:
        """Compute embedding for *text* using the configured embeddings model."""
        try:
            from genai_tk.core.factories import get_embeddings

            model = get_embeddings(self.embeddings_model)
            return model.embed_query(text)
        except Exception as exc:
            logger.warning("Embedding failed: {}", exc)
            return None

    @classmethod
    def clear_cache(cls) -> None:
        """Clear class-level file caches (call between test runs or workflow steps)."""
        cls._initialized_roots.clear()

    def get_sample_queries(self) -> list[str]:
        return [
            "MATCH (d:Document) RETURN d.filename, d.file_size ORDER BY d.filename LIMIT 20",
            "MATCH (d:Document)-[:CONTAINS]->(c:Chunk) RETURN d.filename, count(c) AS chunks ORDER BY chunks DESC",
            "MATCH (c1:Chunk)-[:NEXT]->(c2:Chunk) RETURN c1.chunk_id, c2.chunk_id LIMIT 10",
            "MATCH (c:Chunk) WHERE c.text CONTAINS 'important' RETURN c.chunk_id, c.text LIMIT 5",
        ]
