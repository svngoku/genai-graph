"""Prefect flows for orchestrating knowledge graph creation."""

from __future__ import annotations

from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.task_runners import ThreadPoolTaskRunner

from genai_graph.core.graph_backend import get_backend_storage_path_from_config
from genai_graph.core.graph_merge import ParquetCollector, set_parquet_collector
from genai_graph.core.kg_exports import (
    export_html as export_html_file,
)
from genai_graph.core.kg_exports import (
    export_info,
    export_schema,
    import_from_parquet,
    load_parquet_manifest,
    save_parquet_from_collector,
)
from genai_graph.orchestration.models import KgRunResult
from genai_graph.orchestration.tasks import (
    create_schema,
    delete_backend_task,
    ingest_subgraphs_task,
    initialize_backend_task,
    load_factories_task,
    resolve_config_task,
    summarize_warnings,
)

# Kuzu is an embedded database; we must avoid multi-process execution.
# A single-worker thread pool keeps all access in one process while still
# going through Prefect's task infrastructure.
# TODO : Revisit !!


def _ensure_kg_exists(import_name: str, logger: any) -> None:
    """Ensure a KG exists by creating it if needed.

    Args:
        import_name: Name of the KG config to ensure exists
        logger: Logger instance
    """
    from genai_tk.extra.prefect.runtime import ephemeral_prefect_settings

    manifest = load_parquet_manifest(import_name)
    if manifest is not None:
        logger.info(f"Found existing parquet export for '{import_name}' (exported at {manifest.exported_at})")
        return

    logger.info(f"No parquet export found for '{import_name}', creating KG first...")

    # Recursively create the KG - use ephemeral settings to avoid nested flow issues
    with ephemeral_prefect_settings():
        create_kg_flow(
            config_name=import_name,
            delete_first=True,
            export_html=False,
        )


def _process_imports(
    imports: list[str],
    backend: any,
    logger: any,
) -> tuple[int, int]:
    """Process KG imports by loading from parquet.

    For each import, this function:
    1. Ensures the KG exists (creates it if needed)
    2. Loads and creates the schema from the imported KG
    3. Imports nodes from parquet files

    Args:
        imports: List of KG config names to import
        backend: Graph backend instance
        logger: Logger instance

    Returns:
        Tuple of (total_nodes_imported, total_rels_imported)
    """
    total_nodes = 0
    total_rels = 0

    for import_name in imports:
        logger.info(f"Processing import: {import_name}")

        # Ensure the KG exists (create if needed)
        _ensure_kg_exists(import_name, logger)

        # Load and create schema from the imported KG configuration
        logger.info(f"Creating schema from imported KG '{import_name}'")
        _create_schema_for_import(import_name, backend, logger)

        # Load from parquet
        try:
            nodes, rels = import_from_parquet(import_name, backend)
            total_nodes += nodes
            total_rels += rels
            logger.info(f"Imported {nodes} nodes, {rels} rels from '{import_name}'")
        except FileNotFoundError as exc:
            logger.error(f"Failed to import '{import_name}': {exc}")
            raise

    return total_nodes, total_rels


def _create_schema_for_import(import_name: str, backend: any, logger: any) -> None:
    """Create schema from an imported KG's configuration.

    This loads the factories from the imported KG and creates their schemas
    in the current backend so that nodes can be imported.

    Args:
        import_name: Name of the KG config to import schema from
        backend: Graph backend instance
        logger: Logger instance
    """
    from genai_graph.core.kg_manager import get_kg_manager

    manager = get_kg_manager()

    # Get the config for the imported KG
    if import_name not in manager.ekg_config.kg_configs:
        raise ValueError(f"Imported KG config '{import_name}' not found")

    import_cfg = manager.ekg_config.kg_configs[import_name].model_dump()

    # Load factories and create schemas
    bundles = load_factories_task.submit(import_cfg).result()
    bundles = create_schema(bundles, backend)

    logger.info(f"Created schema from '{import_name}' with {len(bundles)} subgraph(s)")


@flow(name="create_kg_flow", task_runner=ThreadPoolTaskRunner(max_workers=1))  # type: ignore[call-overload]
def create_kg_flow(
    config_name: str | None = None,
    delete_first: bool = False,
    export_html: bool = True,
) -> KgRunResult:
    """Create the knowledge graph and ingest documents using Prefect.

    The high-level steps are:
    1. Optional backend deletion (fresh start).
    2. Resolve KG configuration name and load configuration.
    3. Initialize graph backend (Kuzu or other).
    4. Process imports (load from parquet, creating KG if needed).
    5. Pass 1: load subgraph factories and create schemas.
    6. Pass 2: ingest documents into the graph.
    7. Collect warnings and create Prefect artifacts.
    8. Export parquet for future imports.
    9. Optionally export an HTML visualization of the KG.
    """

    logger = get_run_logger()

    # Clear subgraph factory caches to ensure fresh file/data discovery
    from genai_graph.core.subgraph_factories import (
        JsonFileBackedSubgraphFactory,
        Neo4jSubgraphFactory,
        TableBackedSubgraphFactory,
    )

    JsonFileBackedSubgraphFactory.clear_cache()
    TableBackedSubgraphFactory.clear_cache()
    Neo4jSubgraphFactory.clear_cache()
    logger.info("Cleared subgraph factory caches for fresh discovery")

    if delete_first:
        logger.info("Deleting existing backend before KG creation")
        delete_backend_task.submit("default", config_name).result()

    cfg_name, kg_cfg = resolve_config_task.submit(config_name).result()

    # Initialize KG manager and log start
    from genai_graph.core.kg_manager import get_kg_manager

    manager = get_kg_manager()
    manager.activate()
    manager.log_outcome("create_kg", "started", "Starting KG creation flow")

    backend = initialize_backend_task.submit("default", cfg_name).result()
    db_path = get_backend_storage_path_from_config("default", cfg_name)

    # Set up parquet collector BEFORE imports to capture all data
    collector = ParquetCollector()
    set_parquet_collector(collector)

    try:
        # Process imports before loading own subgraphs
        imports = kg_cfg.get("imports", []) or kg_cfg.get("import", []) or []
        imported_nodes = 0
        imported_rels = 0

        if imports:
            logger.info(f"Processing {len(imports)} import(s): {imports}")
            imported_nodes, imported_rels = _process_imports(imports, backend, logger)
            logger.info(f"Imports complete: {imported_nodes} nodes, {imported_rels} rels total")

            # Clear caches again after imports - the import schema creation may have
            # triggered factory initialization that pollutes the cache for main subgraphs
            JsonFileBackedSubgraphFactory.clear_cache()
            TableBackedSubgraphFactory.clear_cache()
            Neo4jSubgraphFactory.clear_cache()
            logger.info("Re-cleared subgraph factory caches after import processing")

        bundles = load_factories_task.submit(kg_cfg).result()
        bundles = create_schema(bundles, backend)

        stats = ingest_subgraphs_task.submit(bundles, backend).result()
    finally:
        # Clear collector reference after ingestion
        set_parquet_collector(None)

    warnings = summarize_warnings(cfg_name)

    # Log completion outcome
    outcome_status = "warning" if warnings else "success"
    manager.log_outcome(
        "create_kg",
        outcome_status,
        f"KG creation completed with {stats.total_processed} docs processed",
        details={
            "processed": stats.total_processed,
            "failed": stats.total_failed,
            "nodes_created": stats.nodes_created,
            "relationships_created": stats.relationships_created,
            "warning_count": len(warnings),
        },
    )

    # Create a markdown artifact summarizing the run
    summary_lines: list[str] = [
        "# KG Creation Summary",
        "",
        f"**Config name:** `{cfg_name}`",
        f"**DB path:** `{db_path}`",
        "",
        "## Document statistics",
        f"- Processed: {stats.total_processed}",
        f"- Failed: {stats.total_failed}",
        f"- Nodes created: {stats.nodes_created}",
        f"- Relationships created: {stats.relationships_created}",
        "",
    ]

    if warnings:
        summary_lines.append("## Warnings")
        summary_lines.extend([f"- {w}" for w in warnings])
    else:
        summary_lines.append("## Warnings")
        summary_lines.append("- None")

    # Creating artifacts requires a running Prefect server; treat this as
    # best-effort so local CLI invocations and tests do not fail if no
    # server is available.
    try:  # pragma: no cover - network / environment dependent
        create_markdown_artifact("\n".join(summary_lines), key="kg-create-summary")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to create Prefect artifact for KG summary: %s",
            exc,
        )

    # Export schema to text file
    export_schema(cfg_name)
    manager.log_outcome(
        "export_schema",
        "success",
        f"Schema exported to {manager.schema_path}",
    )

    # Export info to markdown file
    export_info(cfg_name, backend)
    manager.log_outcome(
        "export_info",
        "success",
        f"Info exported to {manager.info_path}",
    )

    # Save parquet from collected DataFrames (avoids Kuzu query bugs)
    try:
        parquet_result = save_parquet_from_collector(cfg_name, collector)
        manager.log_outcome(
            "export_parquet",
            "success",
            f"Parquet saved: {parquet_result.node_count} nodes, {parquet_result.rel_count} rels",
        )
        logger.info(f"Saved parquet from collector: {parquet_result.node_count} nodes, {parquet_result.rel_count} rels")
    except Exception as exc:
        logger.warning(f"Failed to save parquet: {exc}")
        manager.log_outcome(
            "export_parquet",
            "warning",
            f"Failed to save parquet: {exc}",
        )

    html_result = None
    if export_html:
        html_result = export_html_file(cfg_name, backend)
        manager.log_outcome(
            "export_html",
            "success",
            f"HTML exported to {html_result.output_path}",
        )

    return KgRunResult(
        config_name=cfg_name,
        db_path=db_path,
        stats=stats,
        warnings=warnings,
        html_export=html_result,
    )
