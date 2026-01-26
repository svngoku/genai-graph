"""Streamlit page for Knowledge Graph database table exploration.

This page lets users:
- Select a KG configuration/profile.
- View tables loaded from Excel/CSV files via TableBackedSubgraphFactory.
- Inspect table contents with filtering and search capabilities.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from genai_tk.utils.config_mngr import global_config, import_from_qualified
from genai_tk.utils.file_patterns import resolve_config_path
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from streamlit import session_state as sss
from upath import UPath

from genai_graph.core.kg_manager import get_kg_manager
from genai_graph.core.subgraph_factories import TableBackedSubgraphFactory


class TableInfo(BaseModel):
    """Information about a database table from a subgraph."""

    subgraph_name: str
    table_name: str
    db_dsn: str
    source_files: list[str]
    row_count: int = 0

    model_config = {"arbitrary_types_allowed": True}


def _get_data_roots() -> list[Path]:
    """Get resolved data root paths for relative path display."""
    roots = []
    try:
        cfg = global_config()
        ekg_data = cfg.get_dir_path("paths.ekg_data")
        if ekg_data:
            roots.append(Path(ekg_data))
    except Exception as exc:
        logger.warning("Could not extract data_roots: %s", exc)
    return roots


def _make_relative_path(full_path: Path | str, data_roots: list[Path]) -> str:
    """Convert a full path to a relative path based on data_root."""
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
    except Exception as exc:
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

    if "tables_selected_table" not in sss:
        sss.tables_selected_table = None


def _select_configuration() -> None:
    """Render KG configuration selector in the sidebar and apply changes."""
    available_configs = _get_available_kg_configs()

    with st.sidebar:
        selected_config = st.selectbox(
            "⚙️ KG Configuration",
            options=available_configs,
            index=available_configs.index(sss.kg_config_selected) if sss.kg_config_selected in available_configs else 0,
            help="Select the Knowledge Graph configuration whose tables you want to inspect.",
        )

        if selected_config != sss.kg_config_selected:
            sss.kg_config_selected = selected_config

            cfg = global_config()
            cfg.set("kg_config", selected_config)

            get_kg_manager.invalidate()  # pyright: ignore[reportFunctionMemberAccess]

            st.rerun()


def _discover_table_subgraphs() -> list[TableInfo]:
    """Discover all TableBackedSubgraphFactory instances in the current configuration."""
    tables: list[TableInfo] = []

    try:
        manager = get_kg_manager()
        profile_cfg = manager.get_profile_dict()
        subgraphs_cfg = profile_cfg.get("subgraphs", []) or []

        for subgraph_cfg in subgraphs_cfg:
            if not isinstance(subgraph_cfg, dict):
                continue

            factory_path = subgraph_cfg.get("factory")
            db_dsn = subgraph_cfg.get("db_dsn")
            files = subgraph_cfg.get("files", [])

            if not factory_path or not db_dsn:
                continue

            # Try to import and check if it's a TableBackedSubgraphFactory
            try:
                imported = import_from_qualified(factory_path)
                if not isinstance(imported, type) or not issubclass(imported, TableBackedSubgraphFactory):
                    continue

                # Resolve file paths
                resolved_files = []
                for f in files:
                    resolved = resolve_config_path(str(f))
                    resolved_files.append(resolved)

                # Get table name from the factory
                # Instantiate temporarily to get the table name
                constructor_kwargs = {
                    k: v for k, v in subgraph_cfg.items() if k not in {"factory", "initial_load", "trigger", "pull"}
                }
                # Resolve the db_dsn path
                if "db_dsn" in constructor_kwargs:
                    dsn = constructor_kwargs["db_dsn"]
                    if dsn.startswith("sqlite:///"):
                        db_path = dsn.replace("sqlite:///", "")
                        resolved_db = resolve_config_path(db_path)
                        constructor_kwargs["db_dsn"] = f"sqlite:///{resolved_db}"

                # Convert file paths to UPath
                if "files" in constructor_kwargs:
                    constructor_kwargs["files"] = [
                        UPath(resolve_config_path(str(f))) for f in constructor_kwargs["files"]
                    ]

                subgraph_instance = imported(**constructor_kwargs)
                table_name = subgraph_instance.table_name

                # Get row count from the database
                row_count = 0
                try:
                    engine = create_engine(constructor_kwargs["db_dsn"])
                    with engine.connect() as conn:
                        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                        row_count = result.scalar() or 0
                except Exception:
                    pass

                tables.append(
                    TableInfo(
                        subgraph_name=factory_path.split(":")[-1],
                        table_name=table_name,
                        db_dsn=constructor_kwargs["db_dsn"],
                        source_files=resolved_files,
                        row_count=row_count,
                    )
                )

            except Exception as exc:
                logger.warning("Could not process subgraph %s: %s", factory_path, exc)
                continue

    except Exception as exc:
        logger.error("Failed to discover table subgraphs: %s", exc)

    return tables


def _load_table_data(table_info: TableInfo) -> pd.DataFrame:
    """Load all data from a database table."""
    try:
        engine = create_engine(table_info.db_dsn)
        with engine.connect() as conn:
            # Use pd.read_sql with explicit query instead of pd.read_sql_table
            # to avoid type inference issues with mixed-type columns
            df = pd.read_sql(text(f"SELECT * FROM {table_info.table_name}"), conn)
        return df
    except Exception as exc:
        logger.error("Failed to load table %s: %s", table_info.table_name, exc)
        return pd.DataFrame()


def _select_table(tables: list[TableInfo], data_roots: list[Path]) -> TableInfo | None:
    """Render table selector in the sidebar and return the chosen table."""
    if not tables:
        return None

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📊 Database Tables")

        # Create labels with relative file paths
        labels = []
        for t in tables:
            if t.source_files:
                relative_files = [_make_relative_path(f, data_roots) for f in t.source_files]
                file_names = [Path(f).name for f in relative_files]
                labels.append(f"{', '.join(file_names)} ({t.row_count} rows)")
            else:
                labels.append(f"{t.table_name} ({t.row_count} rows)")

        # Find default index
        default_index = 0
        if sss.tables_selected_table:
            for i, t in enumerate(tables):
                if t.table_name == sss.tables_selected_table:
                    default_index = i
                    break

        selected_label = st.selectbox(
            "Source File",
            options=labels,
            index=default_index,
        )

        selected_index = labels.index(selected_label)
        sss.tables_selected_table = tables[selected_index].table_name

        return tables[selected_index]


def _render_table_info(table_info: TableInfo, data_roots: list[Path]) -> None:
    """Render table metadata information."""
    st.subheader("📋 Table Information")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Table Name", table_info.table_name)
    with col2:
        st.metric("Row Count", table_info.row_count)

    if table_info.source_files:
        st.markdown("**Source Files:**")
        for f in table_info.source_files:
            relative = _make_relative_path(f, data_roots)
            st.caption(f"📄 {relative}")


def _render_table_data(table_info: TableInfo) -> None:
    """Render the table data with search and filtering."""
    st.subheader("📊 Table Data")

    df = _load_table_data(table_info)

    if df.empty:
        st.warning("No data found in this table.")
        return

    # Search filter
    search_term = st.text_input("🔍 Search", placeholder="Filter rows containing...")

    if search_term:
        # Filter rows that contain the search term in any column
        mask = df.astype(str).apply(lambda row: row.str.contains(search_term, case=False, na=False).any(), axis=1)
        df_filtered = df[mask]
        st.caption(f"Showing {len(df_filtered)} of {len(df)} rows matching '{search_term}'")
    else:
        df_filtered = df
        st.caption(f"Showing all {len(df)} rows")

    # Column selector
    with st.expander("🔧 Column Selection", expanded=False):
        all_columns = df.columns.tolist()
        selected_columns = st.multiselect(
            "Select columns to display",
            options=all_columns,
            default=all_columns,
        )
        if selected_columns:
            df_filtered = df_filtered[selected_columns]

    # Display the dataframe
    st.dataframe(
        df_filtered,
        width="stretch",
        hide_index=True,
    )


def _render_import_history(table_info: TableInfo, data_roots: list[Path]) -> None:
    """Render the import history for this table's files."""
    st.subheader("📜 Import History")

    try:
        engine = create_engine(table_info.db_dsn)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM imported_files ORDER BY import_date DESC"))
            rows = result.fetchall()

            if not rows:
                st.info("No import history found.")
                return

            # Convert to dataframe for display
            history_data = []
            for row in rows:
                relative_path = _make_relative_path(row[0], data_roots)
                history_data.append(
                    {
                        "File": relative_path,
                        "Checksum": row[1][:12] + "..." if len(row[1]) > 12 else row[1],
                        "Import Date": row[2],
                        "Row Count": row[3],
                    }
                )

            df = pd.DataFrame(history_data)
            st.dataframe(df, width="stretch", hide_index=True)

    except Exception as exc:
        st.warning(f"Could not load import history: {exc}")


def main() -> None:
    """Main Streamlit app for KG database table exploration."""
    st.set_page_config(
        page_title="Knowledge Graph Tables",
        page_icon="📊",
        layout="wide",
    )

    _initialize_session_state()

    st.title("📊 Knowledge Graph Database Tables")
    st.markdown(
        """Explore data loaded from Excel and CSV files into the Knowledge Graph database.""",
    )

    _select_configuration()

    # Get data roots for relative path display
    data_roots = _get_data_roots()

    # Discover table-backed subgraphs
    tables = _discover_table_subgraphs()

    if not tables:
        st.info(
            "No database tables found for this configuration. "
            "Ensure you have TableBackedSubgraphFactory subgraphs configured "
            "with `db_dsn` and `files` parameters."
        )
        return

    # Select table from sidebar
    selected_table = _select_table(tables, data_roots)

    if not selected_table:
        st.info("Select a table from the sidebar to view its contents.")
        return

    # Tabs for different views
    tab_data, tab_info, tab_history = st.tabs(
        [
            "📊 Table Data",
            "📋 Table Info",
            "📜 Import History",
        ]
    )

    with tab_data:
        _render_table_data(selected_table)

    with tab_info:
        _render_table_info(selected_table, data_roots)

    with tab_history:
        _render_import_history(selected_table, data_roots)


if __name__ == "__main__":  # pragma: no cover - manual execution
    main()
