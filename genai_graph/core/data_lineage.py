"""Data lineage utilities for Knowledge Graph artifacts.

This module discovers BAML-generated JSON files used to build the
knowledge graph and links them back to their originating Markdown and
source documents.

The logic here is intentionally independent from the Streamlit UI so it
can be reused by CLI tools and tests.
"""

from __future__ import annotations

import json
from typing import Any

from genai_tk.utils.config_mngr import import_from_qualified
from loguru import logger
from pydantic import BaseModel, Field
from upath import UPath

from genai_graph.core.kg_manager import KgManager
from genai_graph.core.subgraph_factories import JsonFileBackedGraphFactory


class JsonArtifact(BaseModel):
    """Represents a single BAML JSON file used to build the KG."""

    path: UPath
    subgraph: str

    model_config = {
        "arbitrary_types_allowed": True,
    }


class MarkdownLineage(BaseModel):
    """Lineage information for a markdown document and its derived artifacts."""

    profile: str
    markdown_path: UPath
    source_path: UPath | None = None
    json_files: list[JsonArtifact] = Field(default_factory=list)

    model_config = {
        "arbitrary_types_allowed": True,
    }


def build_lineage_for_manager(manager: KgManager) -> list[MarkdownLineage]:
    """Build data lineage for all JSON-backed subgraphs of a KG manager.

    The function inspects the active profile configuration, instantiates
    JSON-file-backed subgraphs, and for each discovered JSON file tries to
    resolve the originating Markdown and source documents via nearby
    ``manifest.json`` files.

    Returns:
        List of MarkdownLineage entries, one per Markdown file.
    """

    # Ensure JSON file discovery is fresh even if JsonFileBackedGraphFactory
    # instances were created earlier in the process (for example by
    # GraphRegistry or other components).
    JsonFileBackedGraphFactory.clear_cache()

    profile_cfg = manager.get_profile_dict()
    graphs_cfg = profile_cfg.get("graphs", []) or []

    # Aggregate lineage by markdown file so that multiple JSON files that
    # originate from the same document are grouped together.
    by_markdown: dict[UPath, MarkdownLineage] = {}

    for graph_cfg in graphs_cfg:
        factory_path = graph_cfg.get("factory") if isinstance(graph_cfg, dict) else None
        if not factory_path:
            continue

        try:
            imported = import_from_qualified(factory_path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Cannot import subgraph factory %s: %s", factory_path, exc)
            continue

        if not isinstance(imported, type) or not issubclass(imported, JsonFileBackedGraphFactory):
            # Not a JSON-file-backed subgraph; nothing to do for lineage.
            continue

        constructor_kwargs = {k: v for k, v in graph_cfg.items() if k not in {"factory", "initial_load", "trigger"}}

        try:
            subgraph = imported(**constructor_kwargs)  # type: ignore[call-arg]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to instantiate subgraph %s: %s", factory_path, exc)
            continue

        for json_path in subgraph.get_all_file_paths():
            lineage = _build_lineage_for_json(
                manager.profile,
                factory_path,
                json_path,
                data_root=getattr(subgraph, "data_root", None),
            )
            if lineage is None:
                # If we cannot resolve markdown/source for a particular JSON
                # file we simply skip it; the KG can still be queried but we
                # have no lineage information for that file.
                continue

            existing = by_markdown.get(lineage.markdown_path)
            if existing is None:
                by_markdown[lineage.markdown_path] = lineage
            else:
                existing.json_files.extend(lineage.json_files)
                # Prefer the first discovered source path, but allow later
                # ones to fill in missing information.
                if lineage.source_path and not existing.source_path:
                    existing.source_path = lineage.source_path

    return sorted(by_markdown.values(), key=lambda lineage: str(lineage.markdown_path))


def _build_lineage_for_json(
    profile: str,
    subgraph_name: str,
    json_path: UPath,
    data_root: str | None = None,
) -> MarkdownLineage | None:
    """Build lineage entry for a single JSON file.

    Resolution strategy:
    1. Look for ``manifest.json`` in the JSON file's directory.
    2. From that manifest, heuristically resolve the originating Markdown
       path based on file extensions and name similarity.
    3. In the Markdown directory, look for another ``manifest.json`` and
       from it heuristically resolve the original source file (PDF or
       other binary document).

    If the Markdown file cannot be resolved, the JSON file is skipped.

    Returns:
        MarkdownLineage for the resolved Markdown file, or None if no
        suitable lineage information could be determined.
    """

    json_manifest = json_path.parent / "manifest.json"
    markdown_path = _resolve_related_path(
        manifest_path=json_manifest,
        target_path=json_path,
        exts=(".md", ".markdown"),
    )

    if markdown_path is None:
        logger.debug("No markdown lineage found in manifest for JSON file %s", json_path)
        markdown_path, source_path = _guess_lineage_from_paths(json_path, data_root=data_root)
        if markdown_path is None and source_path is None:
            return None
    else:
        md_manifest = markdown_path.parent / "manifest.json"
        source_path = _resolve_related_path(
            manifest_path=md_manifest,
            target_path=markdown_path,
            exts=(
                ".pdf",
                ".docx",
                ".doc",
                ".pptx",
                ".ppt",
            ),
        )

    # Ensure markdown_path is not None before creating MarkdownLineage
    if markdown_path is None:
        return None

    return MarkdownLineage(
        profile=profile,
        markdown_path=markdown_path,
        source_path=source_path,
        json_files=[JsonArtifact(path=json_path, subgraph=subgraph_name)],
    )


def _resolve_related_path(
    manifest_path: UPath,
    target_path: UPath,
    exts: tuple[str, ...],
) -> UPath | None:
    """Resolve a related path from a manifest file.

    The function is intentionally forgiving about manifest structure – it
    recursively scans all string values, looking for file paths matching
    the desired extensions. Preference is given to candidates whose base
    name contains the base name of ``target_path``.

    Args:
        manifest_path: Path to the ``manifest.json`` file.
        target_path: File path we are resolving lineage *from*.
        exts: Tuple of allowed file extensions (including leading dot).

    Returns:
        Resolved file path or None if nothing plausible was found.
    """

    if not manifest_path.exists():
        return None

    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to read or parse manifest %s: %s", manifest_path, exc)
        return None

    target_stem = target_path.stem.lower()

    def iter_strings(obj: Any) -> list[str]:
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            result: list[str] = []
            for value in obj.values():
                result.extend(iter_strings(value))
            return result
        if isinstance(obj, list):
            result_list: list[str] = []
            for item in obj:
                result_list.extend(iter_strings(item))
            return result_list
        return []

    all_strings = iter_strings(data)

    def to_candidate_paths(require_target_match: bool) -> list[UPath]:
        candidates: list[UPath] = []
        for text in all_strings:
            lower = text.lower().strip()
            if not lower.endswith(exts):
                continue
            if "://" in lower:
                # Skip URLs – we only care about local filesystem paths.
                continue

            path = UPath(text)
            if not path.is_absolute():
                path = manifest_path.parent / path

            stem = path.stem.lower()
            if require_target_match and target_stem not in stem:
                continue

            candidates.append(path)
        return candidates

    # First, try to find candidates that reference the target's stem.
    candidates = to_candidate_paths(require_target_match=True)

    # Fallback: accept any path with the right extension.
    if not candidates:
        candidates = to_candidate_paths(require_target_match=False)

    if not candidates:
        return None

    # Choose a deterministic, stable candidate: shortest textual
    # representation first, then lexicographical order.
    candidates.sort(key=lambda p: (len(str(p)), str(p)))
    return candidates[0]


def _guess_lineage_from_paths(
    json_path: UPath,
    data_root: str | None = None,
) -> tuple[UPath | None, UPath | None]:
    """Best-effort lineage guessing using configured paths.

    This is used as a fallback when manifests do not explicitly record
    markdown or source document locations. It looks for a matching
    ``*_json`` entry in the global ``paths`` configuration and then
    derives potential ``*_md`` and ``*_pdf`` roots from it.

    The matching strategy uses filename stems with and without
    underscores to connect JSON files with their markdown/PDF siblings.
    """

    # Import lazily to avoid circular import issues at module import time.
    from genai_tk.utils.config_mngr import global_config

    try:
        cfg = global_config()
        paths_cfg = cfg.get_dict("paths")
    except Exception:  # pragma: no cover - defensive
        return None, None

    base_key = None

    json_path_str = str(json_path)

    for key, value in paths_cfg.items():
        if not key.endswith("_json"):
            continue

        root_str = str(value).rstrip("/")

        if data_root and str(data_root).rstrip("/") == root_str:
            base_key = key[: -len("_json")]
            break

        if json_path_str.startswith(root_str + "/"):
            base_key = key[: -len("_json")]
            break

    if not base_key:
        return None, None

    md_root = paths_cfg.get(base_key + "_md")
    pdf_root = paths_cfg.get(base_key + "_pdf")

    markdown_path: UPath | None = None
    source_path: UPath | None = None

    # Normalise stems to connect files that differ in underscores/spaces or
    # other punctuation.
    def _norm(stem: str) -> str:
        return "".join(ch for ch in stem.lower() if ch.isalnum())

    json_stem_norm = _norm(json_path.stem)

    if md_root:
        md_root_path = UPath(str(md_root))
        if md_root_path.exists():
            for candidate in md_root_path.rglob("*.md"):
                if _norm(candidate.stem) == json_stem_norm:
                    markdown_path = candidate
                    break

    if pdf_root:
        pdf_root_path = UPath(str(pdf_root))
        if pdf_root_path.exists():
            for candidate in pdf_root_path.rglob("*.pdf"):
                if _norm(candidate.stem) == json_stem_norm:
                    source_path = candidate
                    break

    return markdown_path, source_path
