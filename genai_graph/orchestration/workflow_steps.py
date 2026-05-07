"""Workflow step wrappers for use with the genai-tk workflow engine.

These functions are thin adapters that translate workflow engine parameters
into calls to the existing Prefect flows. They are referenced by dotted path
in ``config/workflows.yaml``.
"""

from __future__ import annotations

from genai_tk.extra.prefect.runtime import ephemeral_prefect_settings
from loguru import logger


def kg_create_step(
    *,
    config_name: str,
    delete_first: bool = False,
    export_html: bool = True,
    force_rebuild: bool = False,
) -> dict:
    """Execute the KG creation flow for a given config profile.

    This wrapper handles:
    - Clearing factory caches (prevents cross-contamination)
    - Setting up ephemeral Prefect context
    - Running the full create_kg_flow

    Returns a summary dict suitable for workflow engine result tracking.
    """
    from genai_graph.kg.factories import JsonFileBackedFactory, Neo4jFactory, TableBackedFactory
    from genai_graph.orchestration.flows import create_kg_flow

    # Clear factory caches before each invocation
    JsonFileBackedFactory.clear_cache()
    TableBackedFactory.clear_cache()
    Neo4jFactory.clear_cache()

    logger.info("Running KG creation flow for config: {}", config_name)

    with ephemeral_prefect_settings():
        result = create_kg_flow(
            config_name=config_name,
            delete_first=delete_first,
            export_html=export_html,
            force_rebuild=force_rebuild,
        )

    return {
        "config_name": config_name,
        "total_processed": result.stats.total_processed,
        "total_failed": result.stats.total_failed,
        "warnings_count": len(result.warnings),
        "db_path": str(result.db_path),
    }
