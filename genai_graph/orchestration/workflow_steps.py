"""Workflow step wrappers for use with the genai-tk workflow engine.

These functions are thin adapters that translate workflow engine parameters
into calls to the existing Prefect flows. They are referenced by dotted path
in ``config/workflows.yaml``.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def _clear_factory_caches() -> None:
    from genai_graph.kg.factories import JsonFileBackedFactory, Neo4jFactory, TableBackedFactory

    JsonFileBackedFactory.clear_cache()
    TableBackedFactory.clear_cache()
    Neo4jFactory.clear_cache()


def _result_dict(config_name: str, result: Any) -> dict:
    return {
        "config_name": config_name,
        "total_processed": result.stats.total_processed,
        "total_failed": result.stats.total_failed,
        "warnings_count": len(result.warnings),
        "db_path": str(result.db_path),
    }


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
    from genai_graph.orchestration.flows import create_kg_flow

    _clear_factory_caches()
    logger.info("Running KG creation flow for config: {}", config_name)

    result = create_kg_flow(
        config_name=config_name,
        delete_first=delete_first,
        export_html=export_html,
        force_rebuild=force_rebuild,
    )

    return _result_dict(config_name, result)


def kg_build_step(
    *,
    graphs: list[dict[str, Any]],
    kg_name: str = "inline",
    delete_first: bool = False,
    export_html: bool = True,
    force_rebuild: bool = False,
) -> dict:
    """Execute the KG creation flow with inline graph configurations.

    Instead of looking up a ``config_name`` in ``kg_configs``, this step
    receives graph factory definitions directly and registers them as a
    temporary KG profile before running the build flow.

    Args:
        graphs: List of graph factory configurations (same format as
            ``kg_configs.<name>.graphs`` entries in ``ekg.yaml``).
        kg_name: Name used for the database directory and profile identity.
        delete_first: Whether to delete existing database before building.
        export_html: Whether to export an HTML visualization.
        force_rebuild: Whether to force rebuild of import caches.
    """
    from genai_graph.kg.manager import KgProfileConfig, get_kg_manager
    from genai_graph.orchestration.flows import create_kg_flow

    _clear_factory_caches()

    # Register the inline graphs as a temporary profile in the KgManager
    manager = get_kg_manager()
    profile_cfg = KgProfileConfig(graphs=graphs)
    manager.ekg_config.kg_configs[kg_name] = profile_cfg
    manager.profile = kg_name
    manager.reset_cached_paths()

    logger.info("Running KG build flow for inline config '{}' ({} graph(s))", kg_name, len(graphs))

    result = create_kg_flow(
        config_name=kg_name,
        delete_first=delete_first,
        export_html=export_html,
        force_rebuild=force_rebuild,
    )

    return _result_dict(kg_name, result)
