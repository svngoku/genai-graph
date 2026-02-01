"""Document ingestion helpers.

This module provides a small wrapper that the CLI can call to add one or
more documents (keys) to the graph. Instead of separate Document nodes and
SOURCE relationships, we attach provenance into the root model's
``metadata`` map field (key: ``source``).

Behavior:
- Validate that the subgraph root model exposes a ``metadata`` field whose
  annotation is either ``dict`` or ``Optional[dict]``.
- For each key, load the Pydantic model using the provided subgraph
  implementation and call ``create_graph(..., source_key=key)`` which will
  set the created root node(s) ``metadata["source"]`` when not already
  present.

For Neo4j imports:
- Use add_neo4j_data_to_graph() with a Neo4jImportFactory
- This bypasses hierarchical extraction and directly loads pre-built nodes/relationships
"""

from typing import TYPE_CHECKING, List, Type

from loguru import logger
from pydantic import BaseModel

from genai_graph.core.graph_backend import GraphBackend
from genai_graph.core.graph_schema import GraphSchema
from genai_graph.core.kg_manager import KgManager
from genai_graph.core.subgraph_factories import GraphFactory

if TYPE_CHECKING:
    from genai_graph.core.subgraph_factories import Neo4jImportFactory


class DocumentStats(BaseModel):
    """Statistics from document processing."""

    total_processed: int = 0
    total_failed: int = 0
    nodes_created: int = 0
    relationships_created: int = 0


def _has_metadata_map(root_class: Type[BaseModel], schema: GraphSchema) -> bool:
    """Return True if root_class defines a ``metadata`` field typed as ``dict``.

    Older implementations relied on an ``ExtraFields`` configuration named
    ``FileMetadata``. The simplified design only requires a real
    ``metadata`` map on the root model, which is then normalised and
    populated by :func:`apply_extra_fields`.
    """
    try:
        from typing import get_args, get_origin

        if not hasattr(root_class, "model_fields") or "metadata" not in root_class.model_fields:
            return False

        ann = root_class.model_fields["metadata"].annotation
        # Direct dict
        if ann is dict:
            return True
        origin = get_origin(ann)
        if origin is dict:
            return True
        # Optional / Union[...] handling (Python 3.9 style or 3.12+ UnionType)
        if origin is None and hasattr(ann, "__args__"):
            origin = get_origin(ann)
        if origin is None:
            return False
        # Check for Union (typing.Union) or UnionType (Python 3.12+ dict | None)
        origin_name = getattr(origin, "__name__", "")
        if origin_name in ("Union", "UnionType") or origin is tuple:
            for a in get_args(ann):
                if a is dict:
                    return True
                if get_origin(a) is dict:
                    return True
        return False
    except Exception:
        return False


def add_neo4j_data_to_graph(
    graph_impl: "Neo4jImportFactory",
    backend: GraphBackend,
    context: KgManager | None = None,
) -> DocumentStats:
    """Add Neo4j data directly to the knowledge graph.

    This is a specialized path for Neo4j imports that bypasses the hierarchical
    model extraction. The factory provides pre-built nodes and relationships
    which are loaded directly.

    Args:
        graph_impl: Neo4jImportFactory instance with build_nodes_and_relationships()
        backend: GraphBackend instance
        context: Optional KgManager for collecting warnings

    Returns:
        DocumentStats instance summarising processing results
    """
    from genai_graph.core.graph_core import import_neo4j_data

    stats = DocumentStats()

    try:
        logger.info(f"Building nodes and relationships from {graph_impl.name}")

        # Get pre-built nodes and relationships from the factory
        nodes_data, relationships = graph_impl.build_nodes_and_relationships()

        logger.info(f"Loaded {nodes_data.total_count()} nodes and {len(relationships)} relationships")

        if nodes_data.total_count() == 0:
            logger.warning("No nodes to import")
            return stats

        # Use the direct import function
        nodes_data, relationships = import_neo4j_data(
            backend=backend,
            nodes_data=nodes_data,
            relationships=relationships,
            context=context,
        )

        stats.nodes_created = nodes_data.total_count()
        stats.relationships_created = len(relationships)
        stats.total_processed = 1

    except Exception as e:
        logger.error(f"Failed to import Neo4j data: {e}")
        import traceback

        logger.debug(traceback.format_exc())
        stats.total_failed += 1

    return stats


def add_documents_to_graph(
    keys: List[str],
    graph_impl: GraphFactory,
    backend: GraphBackend,
    schema: GraphSchema,
    context: KgManager | None = None,
) -> DocumentStats:
    """Add one or more documents to the knowledge graph.

    Args:
        keys: List of keys/file paths to load via the subgraph implementation.
              For JsonFileBackedGraphFactory, these are file paths.
              For other factories, these are abstract keys.
        graph_impl: Subgraph factory providing data loading methods
        backend: GraphBackend instance
        schema: GraphSchema instance
        context: Optional KgManager for collecting warnings

    Returns:
        DocumentStats instance summarising processing results
    """
    from genai_graph.core.graph_core import create_graph

    stats = DocumentStats()

    root_class = getattr(schema, "root_model_class", None)
    if root_class is None:
        raise ValueError(
            f"Schema for subgraph '{graph_impl.name}' does not have root_model_class set. "
            "Document processing requires a root model class to validate metadata."
        )

    # Validate presence of metadata map field (allow Optional[dict])
    if not _has_metadata_map(root_class, schema):
        msg = f"Subgraph root model '{root_class.__name__}' must expose a 'metadata' map field (dict or Optional[dict])"
        if context:
            context.add_warning(msg)
        raise ValueError(msg)

    for key in keys:
        try:
            logger.debug(f"Loading key {key} for subgraph {graph_impl.name}")
            data = graph_impl.get_struct_data_by_key(key)
            logger.debug(f"Loaded? {bool(data)}")
            if not data:
                stats.total_failed += 1
                continue

            # For file-based sources, use relative path as source_key for cleaner provenance
            from genai_graph.core.subgraph_factories import JsonFileBackedGraphFactory

            if isinstance(graph_impl, JsonFileBackedGraphFactory):
                # Extract relative path from full file path for cleaner source tracking
                from genai_tk.utils.file_patterns import resolve_config_path
                from upath import UPath

                file_path = UPath(key)
                try:
                    data_root = resolve_config_path(graph_impl.data_root)
                    root_path = UPath(data_root)
                    source_key = str(file_path.relative_to(root_path))
                except (ValueError, AttributeError):
                    # Fallback to filename if relative path fails
                    source_key = file_path.name
            else:
                source_key = key

            # create_graph will attach source_key into the extracted root nodes
            nodes_data, relationships = create_graph(backend, data, schema, source_key=source_key, context=context)

            nodes_created = nodes_data.total_count() if nodes_data else 0
            rels_created = len(relationships) if relationships is not None else 0

            stats.nodes_created += nodes_created
            stats.relationships_created += rels_created
            stats.total_processed += 1

        except Exception as e:
            logger.error(f"Failed to process key {key}: {e}")
            import traceback

            logger.debug(traceback.format_exc())
            stats.total_failed += 1
            continue

    return stats
