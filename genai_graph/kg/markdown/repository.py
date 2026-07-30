"""Repository abstraction for the Markdown Knowledge Tree.

A :class:`~genai_graph.kg.nodes.document.Repository` is a base location that
source documents are read from. This module resolves a source *spec* (a local
directory or a ``.zip`` archive) into a Repository node plus the list of files it
contains, each with a ``relative_path`` relative to the repository base. This
keeps full absolute paths out of the graph — a Document stores only its
``repository_id`` and ``relative_path``.

SharePoint (or other remote) repositories are intentionally left as a future
``kind`` — the interface (``repository_node`` + ``iter_files``) is designed so a
remote backend can be plugged in without touching callers.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from genai_graph.kg.nodes.document import Repository


class ResolvedFile(BaseModel):
    """A file discovered inside a repository."""

    abs_path: Path
    relative_path: str

    model_config = {"arbitrary_types_allowed": True}


class SourceRepository:
    """Resolves a directory or ``.zip`` source into a Repository node + its files."""

    def __init__(self, *, uri: str, kind: str, name: str, base_path: Path) -> None:
        self.uri = uri
        self.kind = kind
        self.name = name
        self.base_path = base_path
        self.repo_id = self._compute_repo_id(uri)

    @staticmethod
    def _compute_repo_id(uri: str) -> str:
        from genai_tk.utils.hashing import buffer_digest

        return f"repo_{buffer_digest(uri.encode('utf-8'))}"

    @classmethod
    def from_source(cls, source: str, *, cache_dir: str | None = None) -> "SourceRepository":
        """Build a repository from a directory path or a ``.zip`` archive path.

        Args:
            source: Path to a directory or a ``.zip`` file (config placeholders
                such as ``${paths...}`` are resolved).
            cache_dir: Where to extract ``.zip`` archives (defaults to a sibling
                ``.markdown_tree_cache`` directory).
        """
        from genai_tk.config_mgmt.file_patterns import resolve_config_path

        resolved = Path(resolve_config_path(source))
        uri = str(resolved)

        if resolved.is_dir():
            return cls(uri=uri, kind="directory", name=resolved.name, base_path=resolved)

        if resolved.suffix.lower() == ".zip":
            base_path = cls._extract_zip(resolved, cache_dir)
            return cls(uri=uri, kind="zip", name=resolved.stem, base_path=base_path)

        # A single file: treat its parent directory as the repository base.
        return cls(uri=str(resolved.parent), kind="directory", name=resolved.parent.name, base_path=resolved.parent)

    @staticmethod
    def _extract_zip(zip_path: Path, cache_dir: str | None) -> Path:
        from genai_tk.utils.hashing import buffer_digest

        base_cache = Path(cache_dir) if cache_dir else zip_path.parent / ".markdown_tree_cache"
        digest = buffer_digest(str(zip_path.resolve()).encode("utf-8"))
        extract_dir = base_cache / f"{zip_path.stem}_{digest}"

        if extract_dir.exists():
            return extract_dir

        extract_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Extracting {} -> {}", zip_path.name, extract_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        return extract_dir

    def repository_node(self) -> Repository:
        """Return the Repository model for this source."""
        return Repository(repo_id=self.repo_id, uri=self.uri, kind=self.kind, name=self.name)  # type: ignore[arg-type]

    def relative_path_of(self, abs_path: Path) -> str:
        """Return *abs_path* relative to the repository base (falls back to the name)."""
        try:
            return str(abs_path.resolve().relative_to(self.base_path.resolve()))
        except ValueError:
            return abs_path.name

    def iter_files(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        recursive: bool = True,
        single_file: Path | None = None,
    ) -> list[ResolvedFile]:
        """Discover files under the repository base matching *include*/*exclude*.

        Args:
            include: gitignore-style glob patterns to include (default ``["*.md"]``).
            exclude: glob patterns to exclude.
            recursive: recurse into sub-directories.
            single_file: when the source was a single file, restrict discovery to it.
        """
        if single_file is not None:
            return [ResolvedFile(abs_path=single_file, relative_path=self.relative_path_of(single_file))]

        from genai_tk.config_mgmt.file_patterns import resolve_files

        patterns = include or ["*.md"]
        pathspecs: list[str] = []
        for pattern in patterns:
            if recursive:
                pathspecs.append(f"**/{pattern}")
            pathspecs.append(pattern)
        pathspecs.extend(f"!{p}" for p in (exclude or []))

        discovered = resolve_files(str(self.base_path), pathspecs=pathspecs)
        return [ResolvedFile(abs_path=Path(p), relative_path=self.relative_path_of(Path(p))) for p in discovered]
