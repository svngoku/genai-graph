"""Streamlit page for Knowledge Graph data source lineage.

This page lets users:
- Select a KG configuration/profile.
- Discover BAML-generated JSON files that contributed to the graph.
- Trace each JSON file back to the originating Markdown and source
  document (PDF or similar) using nearby manifest.json files.
- View Markdown, source PDF, and JSON content side by side.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st
from genai_tk.utils.config_mngr import global_config
from genai_tk.utils.file_patterns import resolve_config_path
from loguru import logger
from streamlit import session_state as sss

from genai_graph.core.kg_manager import get_kg_manager

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from genai_graph.core.data_lineage import JsonArtifact, MarkdownLineage


def _get_data_roots() -> list[Path]:
    """Get resolved data root paths for relative path display.

    Includes paths.ekg_data (common parent for all data) plus any
    explicit data_root values from subgraph configurations.
    """
    roots = []
    try:
        cfg = global_config()
        # Primary root: paths.ekg_data is the common parent for md/, json/, pdf/
        ekg_data = cfg.get_dir_path("paths.ekg_data")
        if ekg_data:
            roots.append(Path(ekg_data))

        # Also include explicit data_root values from subgraphs
        manager = get_kg_manager()
        profile_cfg = manager.get_profile_dict()
        subgraphs_cfg = profile_cfg.get("subgraphs", []) or []

        for subgraph_cfg in subgraphs_cfg:
            if isinstance(subgraph_cfg, dict) and "data_root" in subgraph_cfg:
                resolved = resolve_config_path(subgraph_cfg["data_root"])
                roots.append(Path(resolved))
    except Exception as exc:
        logger.warning("Could not extract data_roots: %s", exc)

    return roots


def _make_relative_path(full_path: Path | str, data_roots: list[Path]) -> str:
    """Convert a full path to a relative path based on data_root.

    Tries each data_root and returns the relative path for the first match.
    Falls back to the full path if no data_root matches.
    """
    full_path = Path(full_path)
    for root in data_roots:
        try:
            if full_path.is_relative_to(root):
                return str(full_path.relative_to(root))
        except (ValueError, TypeError):
            continue
    return str(full_path)


def _get_available_kg_configs() -> list[str]:
    """Return list of available KG configurations from ekg.yaml."""

    try:
        manager = get_kg_manager()
        return sorted(manager.ekg_config.kg_configs.keys())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not load KG configurations: %s", exc)
        return ["default"]


def _initialize_session_state() -> None:
    """Initialize session state variables for this page."""

    if "kg_config_selected" not in sss:
        try:
            manager = get_kg_manager()
            sss.kg_config_selected = manager.ekg_config.kg_config
        except Exception:
            sss.kg_config_selected = "default"

    if "lineage_selected_dir" not in sss:
        sss.lineage_selected_dir = None
    if "lineage_selected_markdown" not in sss:
        sss.lineage_selected_markdown = None
    if "lineage_selected_json_index" not in sss:
        sss.lineage_selected_json_index = 0


def _select_configuration() -> None:
    """Render KG configuration selector in the sidebar and apply changes."""

    available_configs = _get_available_kg_configs()

    with st.sidebar:
        selected_config = st.selectbox(
            "⚙️ KG Configuration",
            options=available_configs,
            index=available_configs.index(sss.kg_config_selected) if sss.kg_config_selected in available_configs else 0,
            help="Select the Knowledge Graph configuration whose lineage you want to inspect.",
        )

        if selected_config != sss.kg_config_selected:
            sss.kg_config_selected = selected_config

            # Update global config so KgManager and subgraphs use this profile
            cfg = global_config()
            cfg.set("kg_config", selected_config)

            # Invalidate KgManager singleton to pick up new configuration
            get_kg_manager.invalidate()  # pyright: ignore[reportFunctionMemberAccess]

            st.rerun()


def _group_lineage_by_directory(
    lineage: list["MarkdownLineage"],
    data_roots: list[Path],
) -> dict[str, list["MarkdownLineage"]]:
    """Group lineage entries by their markdown parent directory (relative to data_root)."""

    grouped: dict[str, list["MarkdownLineage"]] = defaultdict(list)
    for entry in lineage:
        relative_dir = _make_relative_path(entry.markdown_path.parent, data_roots)
        grouped[relative_dir].append(entry)
    for entries in grouped.values():
        entries.sort(key=lambda e: e.markdown_path.name.lower())
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _select_markdown_entry(
    grouped: dict[str, list["MarkdownLineage"]],
) -> "MarkdownLineage" | None:
    """Render directory + markdown selectors and return the chosen entry."""

    if not grouped:
        return None

    directories = list(grouped.keys())

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📁 Markdown Files")

        # Directory selector to approximate a tree view
        selected_dir = st.selectbox(
            "Directory",
            options=directories,
            index=directories.index(sss.lineage_selected_dir) if sss.lineage_selected_dir in directories else 0,
        )
        sss.lineage_selected_dir = selected_dir

        entries = grouped[selected_dir]
        labels = [entry.markdown_path.name for entry in entries]

        # Keep previous selection when possible
        default_index = 0
        if sss.lineage_selected_markdown in labels:
            default_index = labels.index(sss.lineage_selected_markdown)

        selected_label = st.selectbox(
            "Markdown file",
            options=labels,
            index=default_index,
        )
        sss.lineage_selected_markdown = selected_label

    for entry in entries:
        if entry.markdown_path.name == selected_label:
            return entry

    return entries[0]


def _render_markdown_tab(entry: "MarkdownLineage", data_roots: list[Path]) -> None:
    """Render the Markdown content tab."""

    st.subheader("Markdown Document")
    relative_path = _make_relative_path(entry.markdown_path, data_roots)
    st.caption(relative_path)

    try:
        content = entry.markdown_path.read_text(encoding="utf-8")
        st.markdown(content)
    except FileNotFoundError:
        st.error(f"Markdown file not found: {relative_path}")
    except Exception as exc:  # pragma: no cover - defensive
        st.error(f"Failed to read markdown file: {exc}")


def _render_source_tab(entry: "MarkdownLineage", data_roots: list[Path]) -> None:
    """Render the source (PDF or other) tab."""

    st.subheader("Source Document (PDF or original)")

    if not entry.source_path:
        st.info("No source document could be resolved from manifest files for this markdown.")
        return

    relative_path = _make_relative_path(entry.source_path, data_roots)
    st.caption(relative_path)

    if entry.source_path.suffix.lower() == ".pdf":
        # Use the new Streamlit PDF widget when available
        st.pdf(str(entry.source_path))
    else:
        st.warning(
            "Source document is not a PDF. It cannot be embedded directly, "
            "but you can access it on disk using the path above.",
        )


def _render_json_tab(entry: "MarkdownLineage", data_roots: list[Path]) -> None:
    """Render the JSON content tab with optional per-file selector."""

    st.subheader("BAML Generated Files")

    if not entry.json_files:
        st.info("No JSON files recorded for this markdown.")
        return

    json_files: list[JsonArtifact] = entry.json_files

    if len(json_files) == 1:
        selected_index = 0
    else:
        labels = [f"{art.path.name} ({art.subgraph})" for art in json_files]
        selected_index = st.selectbox(
            "JSON file",
            options=list(range(len(json_files))),
            format_func=lambda i: labels[i],
            index=min(sss.lineage_selected_json_index, len(json_files) - 1),
        )
        sss.lineage_selected_json_index = selected_index

    artifact = json_files[selected_index]

    relative_path = _make_relative_path(artifact.path, data_roots)
    st.caption(f"Path: {relative_path}\nSubgraph: {artifact.subgraph}")

    try:
        raw = artifact.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        st.error(f"JSON file not found: {relative_path}")
        return
    except Exception as exc:  # pragma: no cover - defensive
        st.error(f"Failed to read JSON file: {exc}")
        return

    try:
        data = st.session_state.get("_kg_lineage_last_json_data")
        # Always parse fresh to avoid showing stale content if files change
        data = __import__("json").loads(raw)
        st.session_state["_kg_lineage_last_json_data"] = data
        st.json(data)
    except Exception:
        # Fall back to raw text if JSON is not well-formed
        st.code(raw, language="json")


def main() -> None:
    """Main Streamlit app for KG data source lineage exploration."""

    st.set_page_config(
        page_title="Knowledge Graph Data Lineage",
        page_icon="🧬",
        layout="wide",
    )

    _initialize_session_state()

    st.title("🧬 Knowledge Graph Lineage")
    st.markdown(
        """Inspect how your Knowledge Graph was built from BAML JSON
        files, intermediate Markdown, and original source documents.""",
    )

    _select_configuration()

    # Get data_roots for relative path display
    data_roots = _get_data_roots()

    # Load lineage information for the active configuration
    try:
        manager = get_kg_manager()
        lineage_entries = manager.get_data_lineage()
    except Exception as exc:  # pragma: no cover - defensive
        st.error(f"Failed to collect data lineage: {exc}")
        return

    if not lineage_entries:
        st.info(
            "No BAML-based JSON lineage found for this configuration. "
            "Ensure you have run KG creation and that JSON-backed "
            "subgraphs are configured.",
        )
        return

    grouped = _group_lineage_by_directory(lineage_entries, data_roots)
    selected_entry = _select_markdown_entry(grouped)

    if not selected_entry:
        st.info("No markdown files discovered for lineage.")
        return

    # Tabs for different artifact types
    default = "📄 Markdown ➥"
    tab_src, tab_md, tab_json = st.tabs(
        [
            "📎 Source Document ➥",
            default,
            "🧱 Generated JSON",
        ],
        default=default,
    )

    with tab_md:
        _render_markdown_tab(selected_entry, data_roots)

    with tab_src:
        _render_source_tab(selected_entry, data_roots)

    with tab_json:
        _render_json_tab(selected_entry, data_roots)


if __name__ == "__main__":  # pragma: no cover - manual execution
    main()
