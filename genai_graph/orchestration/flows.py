"""Prefect flows for orchestrating knowledge graph creation.

The main ``create_kg_flow`` is structured as a DAG:

1. **Config resolution** — resolve KG profile, detect imports.
2. **Backend init** — create or delete the Kuzu database.
3. **Import phase** — for each import dependency (topologically sorted),
   ensure its parquet exists, create its schema, and load data.
4. **Schema phase** — load factories and create schemas for all bundles.
5. **Ingestion phase** — ingest documents per-bundle (each a separate Prefect task).
6. **Export phase** — schema, info, HTML, parquet, warnings (independent tasks).
"""

from __future__ import annotations

from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.task_runners import ThreadPoolTaskRunner

from genai_graph.kg.backend import get_backend_storage_path_from_config
from genai_graph.kg.ingest import DocumentStats, ParquetCollector, set_parquet_collector
from genai_graph.orchestration.dag import resolve_import_dag
from genai_graph.orchestration.models import BundleResult, ImportResult, KgRunResult, WarningsCollector
from genai_graph.orchestration.tasks import (
    create_schema_task,
    create_vector_indexes_task,
    delete_backend_task,
    export_html_task,
    export_info_task,
    export_parquet_task,
    export_schema_task,
    export_warnings_task,
    import_kg_task,
    ingest_bundle_task,
    initialize_backend_task,
    load_factories_task,
    resolve_config_task,
    summarize_warnings_task,
)

# Kuzu is an embedded database; we must avoid multi-process execution.
# A thread pool with workers > 1 allows parallel export tasks while
# ingestion tasks remain serial (submitted one at a time in the flow).
_DEFAULT_MAX_WORKERS = 4


@flow(name="create_kg_flow", task_runner=ThreadPoolTaskRunner(max_workers=_DEFAULT_MAX_WORKERS))  # type: ignore[call-overload]
def create_kg_flow(
    config_name: str | None = None,
    delete_first: bool = False,
    export_html: bool = True,
    force_rebuild: bool = False,
) -> KgRunResult:
    """Create the knowledge graph and ingest documents using Prefect.

    The flow is organized as a proper DAG:

    1. Optional backend deletion (fresh start).
    2. Resolve KG configuration and build import dependency graph.
    3. Initialize graph backend (Kuzu).
    4. Import phase — process each import dependency as a distinct task.
       Smart cache validation checks fingerprints before rebuilding.
    5. Schema phase — load factories and create schemas (Pass 1).
    6. Ingestion phase — ingest per-bundle, each as a separate task (Pass 2).
    7. Warning aggregation (including cross-import warnings).
    8. Export phase — schema, info, HTML, parquet, warnings (parallel-ready).
    """
    pf_logger = get_run_logger()

    # Clear subgraph factory caches to ensure fresh file/data discovery
    from genai_graph.kg.factories import (
        JsonFileBackedFactory,
        Neo4jFactory,
        TableBackedFactory,
    )

    JsonFileBackedFactory.clear_cache()
    TableBackedFactory.clear_cache()
    Neo4jFactory.clear_cache()
    pf_logger.info("Cleared subgraph factory caches for fresh discovery")

    # ------------------------------------------------------------------
    # 1. Optional delete
    # ------------------------------------------------------------------
    if delete_first:
        pf_logger.info("Deleting existing backend before KG creation")
        delete_backend_task.submit("default", config_name).result()

    # ------------------------------------------------------------------
    # 2. Resolve config and build import DAG
    # ------------------------------------------------------------------
    cfg_name, kg_cfg = resolve_config_task.submit(config_name).result()

    from genai_graph.kg.manager import get_kg_manager

    manager = get_kg_manager()
    manager.activate()
    manager.log_outcome("create_kg", "started", "Starting KG creation flow")

    # Build the import dependency graph upfront (flat, topologically sorted)
    import_dag = resolve_import_dag(cfg_name, manager.ekg_config.kg_configs)
    if import_dag.execution_order:
        pf_logger.info(
            "Import DAG for '%s': %s",
            cfg_name,
            [n.config_name for n in import_dag.execution_order],
        )

    # ------------------------------------------------------------------
    # 3. Initialize backend
    # ------------------------------------------------------------------
    backend = initialize_backend_task.submit("default", cfg_name).result()
    db_path = get_backend_storage_path_from_config("default", cfg_name)

    # Set up parquet collector BEFORE imports to capture all data
    collector = ParquetCollector()
    set_parquet_collector(collector)

    # Accumulate warnings from all phases
    all_warnings = WarningsCollector(source=cfg_name)
    import_results: list[ImportResult] = []
    bundle_results: list[BundleResult] = []

    try:
        # ------------------------------------------------------------------
        # 4. Import phase — each import is a separate Prefect task
        # ------------------------------------------------------------------
        if import_dag.execution_order:
            for import_node in import_dag.execution_order:
                imp_result = import_kg_task.submit(import_node.config_name, backend, force_rebuild).result()
                import_results.append(imp_result)
                all_warnings.merge(imp_result.warnings)

            pf_logger.info(
                "Imports complete: %d nodes, %d rels from %d import(s)",
                sum(r.nodes_imported for r in import_results),
                sum(r.rels_imported for r in import_results),
                len(import_results),
            )

            # Clear caches after imports — import schema creation may have
            # triggered factory init that pollutes the cache for main subgraphs
            JsonFileBackedFactory.clear_cache()
            TableBackedFactory.clear_cache()
            Neo4jFactory.clear_cache()
            pf_logger.info("Re-cleared subgraph factory caches after import processing")

        # ------------------------------------------------------------------
        # 5. Schema phase — load factories + create schemas
        # ------------------------------------------------------------------
        bundles = load_factories_task.submit(kg_cfg).result()
        bundles = create_schema_task.submit(bundles, backend).result()

        # ------------------------------------------------------------------
        # 6. Ingestion phase — per-bundle tasks
        # ------------------------------------------------------------------
        for bundle in bundles:
            result = ingest_bundle_task.submit(bundle, backend).result()
            bundle_results.append(result)
            all_warnings.merge(result.warnings)

    finally:
        # Clear collector reference after ingestion
        set_parquet_collector(None)

    # ------------------------------------------------------------------
    # 6b. Create vector indexes for embedding fields
    # ------------------------------------------------------------------
    create_vector_indexes_task.submit(bundles, backend).result()

    # Aggregate stats from all bundles
    total_stats = DocumentStats()
    for br in bundle_results:
        total_stats.total_processed += br.stats.total_processed
        total_stats.total_failed += br.stats.total_failed
        total_stats.nodes_created += br.stats.nodes_created
        total_stats.relationships_created += br.stats.relationships_created

    # ------------------------------------------------------------------
    # 7. Warning aggregation
    # ------------------------------------------------------------------
    # Merge task-level warnings into the KgManager (for backward compat)
    for w in all_warnings.warnings:
        manager.add_warning(w)

    warnings = summarize_warnings_task.submit(cfg_name).result()

    # ------------------------------------------------------------------
    # 8. Export phase — independent tasks (parallel-ready)
    # ------------------------------------------------------------------
    _run_export_phase(cfg_name, backend, collector, warnings, export_html, manager)

    # Log completion outcome
    outcome_status = "warning" if warnings else "success"
    manager.log_outcome(
        "create_kg",
        outcome_status,
        f"KG creation completed with {total_stats.total_processed} docs processed",
        details={
            "processed": total_stats.total_processed,
            "failed": total_stats.total_failed,
            "nodes_created": total_stats.nodes_created,
            "relationships_created": total_stats.relationships_created,
            "warning_count": len(warnings),
            "imports": len(import_results),
        },
    )

    # Create a markdown artifact summarizing the run
    _create_summary_artifact(cfg_name, db_path, total_stats, warnings, import_results, bundle_results)

    return KgRunResult(
        config_name=cfg_name,
        db_path=db_path,
        stats=total_stats,
        warnings=warnings,
        import_results=import_results,
        bundle_results=bundle_results,
        html_export=None,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_export_phase(
    cfg_name: str,
    backend: object,
    collector: ParquetCollector,
    warnings: list[str],
    do_export_html: bool,
    manager: object,
) -> None:
    """Run all export tasks concurrently.

    Export tasks are read-only (they don't mutate the Kuzu DB or shared
    state), so they can safely run in parallel when ``max_workers > 1``.
    We submit them all, then collect results to ensure any exceptions
    are captured.
    """
    futures = []

    # Warnings report
    futures.append(export_warnings_task.submit(cfg_name, warnings))

    # Schema artifacts (txt + JSON + HTML)
    futures.append(export_schema_task.submit(cfg_name))

    # Info markdown
    futures.append(export_info_task.submit(cfg_name, backend))

    # Parquet from collected DataFrames
    futures.append(export_parquet_task.submit(cfg_name, collector))

    # HTML visualization
    if do_export_html:
        futures.append(export_html_task.submit(cfg_name, backend))

    # Wait for all exports to finish
    for future in futures:
        future.result()

    manager.log_outcome("export_schema", "success", "Schema exported")
    manager.log_outcome("export_info", "success", "Info exported")
    if do_export_html:
        manager.log_outcome("export_html", "success", "HTML exported")


def _create_summary_artifact(
    cfg_name: str,
    db_path: object,
    stats: DocumentStats,
    warnings: list[str],
    import_results: list[ImportResult],
    bundle_results: list[BundleResult],
) -> None:
    """Best-effort creation of a Prefect markdown artifact."""

    summary_lines: list[str] = [
        "# KG Creation Summary",
        "",
        f"**Config name:** `{cfg_name}`",
        f"**DB path:** `{db_path}`",
        "",
    ]

    # Import summary
    if import_results:
        summary_lines.append("## Imports")
        for ir in import_results:
            status = "⏭️ cached (fingerprints valid)" if ir.skipped else "🔨 rebuilt"
            summary_lines.append(f"- **{ir.config_name}**: {ir.nodes_imported} nodes, {ir.rels_imported} rels {status}")
        summary_lines.append("")

    # Bundle summary
    if bundle_results:
        summary_lines.append("## Bundles")
        for br in bundle_results:
            summary_lines.append(
                f"- **{br.factory_path}**: {br.stats.nodes_created} nodes, "
                f"{br.stats.relationships_created} rels, {br.stats.total_failed} failed"
            )
        summary_lines.append("")

    # Totals
    summary_lines.extend(
        [
            "## Document statistics",
            f"- Processed: {stats.total_processed}",
            f"- Failed: {stats.total_failed}",
            f"- Nodes created: {stats.nodes_created}",
            f"- Relationships created: {stats.relationships_created}",
            "",
        ]
    )

    if warnings:
        summary_lines.append("## Warnings")
        summary_lines.extend([f"- {w}" for w in warnings])
    else:
        summary_lines.append("## Warnings")
        summary_lines.append("- None")

    try:  # pragma: no cover - network / environment dependent
        create_markdown_artifact("\n".join(summary_lines), key="kg-create-summary")
    except Exception as exc:  # pragma: no cover - defensive
        get_run_logger().warning("Failed to create Prefect artifact: %s", exc)
