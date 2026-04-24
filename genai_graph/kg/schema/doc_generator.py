"""Generate schema documentation for knowledge graphs.

This module provides functionality to generate comprehensive, LLM-friendly
documentation of graph schemas, including node types, relationships,
properties, descriptions from BAML files, and indexed fields.

Helper functions shared with :mod:`resolved` live in :mod:`_helpers`.
"""

from __future__ import annotations

from typing import Any

from genai_graph.kg.schema._helpers import (
    _parse_baml_descriptions,
)
from genai_graph.kg.schema.core import GraphSchema
from genai_graph.kg.schema.registry import GraphRegistry, get_graph


def generate_schema_description(graphs: str | list[str], print_enums: bool = True) -> str:
    """Generate a compact, token-efficient LLM description of the graph schema.

    This unified function accepts either a single graph name (string)
    or a list of graph names. Passing an empty list means "all registered"
    graphs (delegated to `GraphRegistry.build_combined_schema`).

    Args:
        graphs: Single graph name or list of names. Empty list means all.
        print_enums: Whether to include enumeration types in the output (default: True).

    Examples:
        ```python
        # Single graph
        description = generate_schema_description("ReviewedOpportunity")

        # Combined (multiple or empty list = all)
        description = generate_schema_description(["ReviewedOpportunity", "ArchitectureDocument"])

        # Without enums
        description = generate_schema_description("ReviewedOpportunity", print_enums=False)
        ```
    """
    import warnings

    baml_docs = _parse_baml_descriptions()

    # Single graph name provided
    if isinstance(graphs, str):
        graph_impl = get_graph(graphs)
        graph_impl.build_schema()
        schema = _load_schema(graphs)
        return format_schema_description(schema=schema, baml_docs=baml_docs, print_enums=print_enums)

    # Otherwise, treat as list of graph names (possibly empty => all)
    # Suppress validation warnings for combined schemas (type mismatches between
    # extended and base types are expected when merging different graphs)
    registry = GraphRegistry.get_instance()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation.")
        schema = registry.build_combined_schema(graphs)
    return format_schema_description(schema=schema, baml_docs=baml_docs, print_enums=print_enums)


# NOTE: Combined-generator removed — use `generate_schema_description(list_or_name)`


def _load_schema(graph_name: str) -> GraphSchema:
    """Load and validate the graph schema.

    Ensures graphs are registered before lookup.
    """

    try:
        graph_impl = get_graph(graph_name)
        return graph_impl.build_schema()
    except ValueError as e:
        raise ValueError(f"Unknown graph '{graph_name}': {e}") from e


def format_schema_description(schema: GraphSchema, baml_docs: dict[str, Any], print_enums: bool = True) -> str:
    """Format schema as a compact, token-efficient description.

    Delegates to :class:`~genai_graph.kg.schema.resolved.ResolvedSchema` which
    is the single source of truth for schema rendering.

    Args:
        schema: The graph schema to format.
        baml_docs: Parsed BAML documentation containing descriptions.
        print_enums: Whether to include enumeration types (default: True).

    Returns:
        Markdown-formatted schema description.
    """
    from genai_graph.kg.schema.resolved import ResolvedSchema

    return ResolvedSchema.from_graph_schema(schema, print_enums=print_enums).to_markdown()


def generate_vector_index_description(graphs: str | list[str]) -> str:
    """Generate a standalone description of vector-indexed fields.

    Used by text-to-Cypher prompts to inform the LLM about available
    vector indexes for hybrid RAG queries.

    Args:
        graphs: Single graph name or list of names. Empty list means all.

    Returns:
        Markdown description of vector indexes, or empty string if none.
    """
    import warnings

    from genai_graph.kg.schema.resolved import ResolvedSchema

    if isinstance(graphs, str):
        schema = _load_schema(graphs)
        return ResolvedSchema.from_graph_schema(schema).to_vector_section_markdown()

    registry = GraphRegistry.get_instance()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation.")
        schema = registry.build_combined_schema(graphs)
    return ResolvedSchema.from_graph_schema(schema).to_vector_section_markdown()
