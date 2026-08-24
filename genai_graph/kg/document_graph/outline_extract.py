"""LLM outline extraction for the Document Graph build.

Takes a Markdown document and asks a (typically cheap, large-context "flash")
model for its **table of contents** plus a one-sentence description of every
section (and a short summary of the substantial ones) — *without* re-emitting
the section content, so the output stays small regardless of document size.

The outline is a content-free JSON artifact, cached by ``markdown_hash`` (and a
policy/LLM hash) so re-runs are free. A later deterministic pass
(:func:`genai_graph.kg.document_graph.outline_merge.merge_outline`) reconciles
the outline's heading anchors against the Markdown to produce the actual
section nodes. Sending the outline separately from the text — instead of asking
the model to repeat each section's body — keeps output tokens low on
million-token documents.

Two failure modes both degrade to "no outline" (the build then falls back to
the algorithmic ``parse_markdown_tree`` for that document, with no summaries):
the document exceeding the model's context window (no LLM call is made), and
the LLM call itself failing.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from genai_tk.utils.tokens import count_tokens
from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.document_graph.summarize import _clean_text, _is_length_limit_error

_DEFAULT_LLM_TAG = "default"

# "Page 12" conversion artifacts that leak in from PDF/Office -> Markdown.
_PAGE_MARKER_RE = re.compile(r"(?im)^\s*#*\s*page\s+\d+\s*$")


class OutlineEntry(BaseModel):
    """One section in the LLM's table of contents for a document."""

    title: str = Field(
        ..., description="The heading text EXACTLY as it appears on its own line in the document (used to locate it)."
    )
    level: int = Field(..., description="Heading level, 1 (top) to 6, inferred from numbering/TOC/indentation.")
    description: str = Field(
        ..., description="ONE plain-text sentence, at most 20 words, saying what this section contains."
    )
    summary: str | None = Field(
        default=None, description="Only for substantial sections: 2-3 plain-text sentences, at most 60 words."
    )


class DocumentOutline(BaseModel):
    """Structured-output schema for one outline-extraction LLM call."""

    document_description: str = Field(..., description="ONE plain-text sentence, at most 20 words, on the whole document.")
    document_summary: str = Field(..., description="2-4 plain-text sentences, at most 60 words, abstracting the document.")
    sections: list[OutlineEntry] = Field(..., description="Every section in document order; titles must appear verbatim.")


class OutlineConfig(BaseModel):
    """Policy and LLM settings for outline extraction."""

    llm: str | None = Field(default=None, description="LLM id (name@provider) or tag; None uses kg_build.llms.default")
    context_safety_ratio: float = Field(
        default=0.9,
        description="Degrade (no LLM call) when the document's token count exceeds this fraction of the context window",
    )
    summary_min_tokens: int = Field(
        default=800, description="Heuristic passed to the prompt for what counts as a 'substantial' section"
    )
    max_description_words: int = Field(default=20, description="Target length of a section/document description")
    max_summary_words: int = Field(default=60, description="Target length of a section/document summary")
    max_description_chars: int = Field(default=180, description="Hard cap applied to a description after cleaning")
    max_summary_chars: int = Field(default=500, description="Hard cap applied to a summary after cleaning")
    llm_max_tokens: int | None = Field(
        default=None,
        description="Explicit max output tokens for the call; raise if a reasoning model exhausts its completion budget.",
    )
    retry_max_tokens: int = Field(
        default=32_000, description="max_tokens for the one automatic retry after a 'length limit reached' failure"
    )
    cache_root: str | None = Field(default=None, description="Directory for the content-addressed outline JSON cache")


class OutlineResult(BaseModel):
    """Outcome of extracting one document's outline (cached on disk)."""

    outline: DocumentOutline | None = Field(default=None, description="The extracted outline, or None when degraded")
    degraded: bool = Field(default=False, description="True when no outline was produced (over context window or failure)")
    reason: str | None = Field(default=None, description="Why degradation happened, if it did")
    llm_calls: int = 0


class OutlineStats(BaseModel):
    """Aggregate outcome of the parallel outline pre-pass over a corpus."""

    total_files: int = 0
    degraded_count: int = 0
    llm_calls: int = 0
    warnings: list[str] = Field(default_factory=list)


def _resolve_llm_id(config: OutlineConfig) -> str:
    """Resolve the LLM id from the config or the global default."""
    if config.llm:
        return config.llm
    from genai_tk.config_mgmt.config_mngr import global_config

    return global_config().get_str("kg_build.llms.default", default=_DEFAULT_LLM_TAG) or _DEFAULT_LLM_TAG


def _context_window_for(llm_id: str) -> int | None:
    """Resolve a model's effective context window, or None if it cannot be determined."""
    from genai_tk.core.factories.llm_factory import get_llm_info

    try:
        return get_llm_info(llm_id).effective_context_window
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not resolve context window for {}: {}", llm_id, exc)
        return None


def _policy_hash(config: OutlineConfig, llm_id: str) -> str:
    """Stable short hash of the LLM + policy fields that affect the outline."""
    payload = f"{llm_id}|{config.summary_min_tokens}|{config.max_description_words}|{config.max_summary_words}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _llm_tag_for_path(llm_id: str) -> str:
    """Filesystem-safe tag derived from the LLM id (e.g. gpt_4o_mini@edenai -> gpt_4o_mini_edenai)."""
    return llm_id.replace("@", "_").replace("/", "_")


def _cache_path(config: OutlineConfig, llm_id: str, markdown_hash: str) -> Path | None:
    """Return the content-addressed cache path for one document's outline, or None if caching is disabled."""
    if not config.cache_root:
        return None
    root = Path(config.cache_root) / f"{_llm_tag_for_path(llm_id)}__{_policy_hash(config, llm_id)}"
    return root / f"{markdown_hash}.json"


def _clean_markdown_for_prompt(raw: str) -> str:
    """Drop ``Page N`` conversion artifacts so they do not clutter the LLM input."""
    return _PAGE_MARKER_RE.sub("", raw)


def _clean_outline(outline: DocumentOutline, config: OutlineConfig) -> DocumentOutline:
    """Strip Markdown noise and hard-truncate every description/summary the model returned."""
    cleaned_sections: list[OutlineEntry] = []
    for entry in outline.sections:
        cleaned_sections.append(
            entry.model_copy(
                update={
                    "description": _clean_text(entry.description, config.max_description_chars),
                    "summary": _clean_text(entry.summary, config.max_summary_chars) if entry.summary else None,
                }
            )
        )
    return outline.model_copy(
        update={
            "document_description": _clean_text(outline.document_description, config.max_description_chars),
            "document_summary": _clean_text(outline.document_summary, config.max_summary_chars),
            "sections": cleaned_sections,
        }
    )


def _build_prompt(*, filename: str, raw: str, config: OutlineConfig) -> tuple[str, str]:
    """Build the (system, user) prompt asking for the outline + summaries, never the content."""
    system = f"""
        You build the table of contents for a document library that an AI agent reads to
        decide which section to open. The document may have NO heading markup and
        inconsistent formatting (it came from a PDF/Office -> Markdown conversion). Look
        for a table of contents near the start, and for outline numbers or repeated style
        changes in the body, to recover the REAL section structure — do not just split on
        Markdown '#'.

        For every section, return (in document order):
        - `title`: the heading text EXACTLY as it appears on its own line in the document.
          We locate the section by matching this string back to the source, so it must be
          verbatim. If a heading starts with a number or '#', include that prefix.
        - `level`: 1 (top) to 6, from the numbering/TOC indentation (1 -> 1, 1.1 -> 2, ...).
        - `description`: ONE plain-text sentence, at most {config.max_description_words} words,
          saying what the section CONTAINS. No Markdown, no headings, no bullets, no line
          breaks. Do not restate the title. Name the concrete subject matter.
        - `summary`: ONLY for substantial sections (more than roughly
          {config.summary_min_tokens} tokens, or {config.summary_min_tokens * 4} words):
          2-3 plain-text sentences, at most {config.max_summary_words} words. Leave null
          otherwise.

        HARD RULE: never include the section's body text in your answer. Output only the
        title, level, description and (when warranted) summary for each section, plus the
        two document-level fields. Returning section content defeats the point (token cost)
        and is forbidden.
    """
    user = f"""
        Document: {filename}

        --- full document ---
        {raw}
        --- end document ---

        Return `document_description`, `document_summary`, and `sections` (one per heading,
        in document order, each title verbatim from the document).
    """
    return system, user


def _call_llm(
    *, llm_id: str, filename: str, raw: str, config: OutlineConfig, max_tokens: int | None
) -> DocumentOutline:
    """The LLM call boundary — isolated so tests can substitute a fake implementation."""
    from genai_tk.core.factories.llm_factory import get_llm
    from genai_tk.core.prompts import def_prompt

    system, user = _build_prompt(filename=filename, raw=raw, config=config)
    prompt = def_prompt(system=system, user=user)
    llm_kwargs = {"max_tokens": max_tokens} if max_tokens is not None else {}
    structured_llm = get_llm(llm_id, **llm_kwargs).with_structured_output(DocumentOutline)
    result = (prompt | structured_llm).invoke({})
    assert isinstance(result, DocumentOutline)
    return result


def _call_llm_with_retry(
    *, llm_id: str, filename: str, raw: str, config: OutlineConfig, warnings: list[str]
) -> DocumentOutline | None:
    """Call the LLM, retrying once with a larger completion budget on a length-limit failure."""
    max_tokens = config.llm_max_tokens
    context = f"{filename} outline"
    for attempt in range(2):
        started = time.monotonic()
        try:
            outline = _call_llm(llm_id=llm_id, filename=filename, raw=raw, config=config, max_tokens=max_tokens)
            if attempt > 0:
                logger.info("{}: outline retry succeeded ({:.1f}s)", context, time.monotonic() - started)
            return _clean_outline(outline, config)
        except Exception as exc:  # noqa: BLE001
            if attempt == 0 and _is_length_limit_error(exc):
                max_tokens = max(max_tokens or 0, config.retry_max_tokens)
                msg = (
                    f"{context}: hit the completion token limit (likely a reasoning model spending its budget on "
                    f"hidden reasoning tokens, not the input context window); retrying with max_tokens={max_tokens}."
                )
                warnings.append(msg)
                logger.warning(msg)
                continue
            msg = f"LLM call failed for {context}: {exc}"
            warnings.append(msg)
            logger.error(msg)
            return None
    return None


def _load_cached(cache_path: Path) -> OutlineResult | None:
    """Load a cached outline result, or None if absent/stale-unreadable."""
    if not cache_path.exists():
        return None
    try:
        return OutlineResult.model_validate_json(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stale/invalid outline cache {} ({}); re-extracting", cache_path, exc)
        return None


def _write_cached(cache_path: Path, result: OutlineResult) -> None:
    """Persist an outline result so later merges never re-call the LLM."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        logger.warning("Could not write outline cache {}: {}", cache_path, exc)


def extract_outline(
    md_text: str,
    markdown_hash: str,
    filename: str,
    config: OutlineConfig,
    *,
    warnings: list[str],
) -> OutlineResult:
    """Extract a document's outline (TOC + summaries), cache-addressed by *markdown_hash*.

    Idempotent: a fresh cache hit returns the stored result without an LLM call.
    When the document exceeds the model's context window (scaled by
    ``context_safety_ratio``), no LLM call is made and a degraded result is
    returned (and cached) so the build falls back to the algorithmic parser. An
    LLM call failure is likewise cached as degraded, so a re-run does not retry.

    Args:
        md_text: Full Markdown document text.
        markdown_hash: Content hash of the Markdown rendering (cache key + identity).
        filename: Document filename, for prompt context and log messages.
        config: Outline policy and LLM settings.
        warnings: List to append human-readable warnings to.

    Returns:
        `OutlineResult`; ``.outline`` is None when degraded (caller falls back
        to the algorithmic parser with no summaries).
    """
    llm_id = _resolve_llm_id(config)
    cache_path = _cache_path(config, llm_id, markdown_hash)
    if cache_path is not None:
        cached = _load_cached(cache_path)
        if cached is not None:
            # No LLM call was made this invocation; the stored llm_calls reflects the
            # original extraction and is reset so callers' totals count fresh calls only.
            return cached.model_copy(update={"llm_calls": 0})

    cleaned = _clean_markdown_for_prompt(md_text)
    doc_tokens = count_tokens(cleaned)
    context_window = _context_window_for(llm_id)
    if context_window and doc_tokens > context_window * config.context_safety_ratio:
        msg = (
            f"{filename}: ~{doc_tokens} tokens over {config.context_safety_ratio:.0%} of "
            f"{llm_id}'s {context_window}-token context window; degrading to algorithmic parsing (no summaries)."
        )
        warnings.append(msg)
        logger.warning(msg)
        result = OutlineResult(degraded=True, reason="context_window_overflow")
        if cache_path is not None:
            _write_cached(cache_path, result)
        return result

    outline = _call_llm_with_retry(llm_id=llm_id, filename=filename, raw=cleaned, config=config, warnings=warnings)
    if outline is None:
        result = OutlineResult(degraded=True, reason="llm_call_failed")
    else:
        result = OutlineResult(outline=outline, llm_calls=1)
    if cache_path is not None:
        _write_cached(cache_path, result)
    return result
