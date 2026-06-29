"""Schema compilation utilities.

This module contains the standalone functions that perform schema compilation.

Keeping compilation as free functions means they can be:

- Called directly in tests without constructing a full ``GraphSchema``
- Composed in alternative ways (e.g. incremental compilation, dry-run)
- Imported independently for inspection and documentation tooling

``GraphSchema`` still calls these functions from its ``@model_validator``
so the existing public API is unchanged.

Typical usage::

    from genai_graph.kg.schema.compiler import (
        build_model_field_map,
        deduce_node_field_paths,
        deduce_relation_field_paths,
        compute_excluded_fields,
        validate_schema_coherence,
        compile_schema,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genai_graph.kg.schema.core import GraphSchema


def build_model_field_map(schema: GraphSchema) -> None:
    """Traverse the root model graph and populate ``schema._model_field_map``.

    Walks all Pydantic classes reachable from ``schema.root_model_class`` (and
    any ``schema.merged_root_classes``), resolving ``ForwardRef`` annotations
    along the way.  The result is stored in the private attribute
    ``schema._model_field_map`` which maps each discovered class to a dict of
    ``{field_name: {path, type, is_list, annotation}}``.

    Args:
        schema: The ``GraphSchema`` instance to operate on.  Its
            ``_model_field_map`` private attribute is populated in-place.
    """
    # Delegate to the private method on the schema object — the logic lives
    # there today.  As future work, the body can be moved here directly.
    schema._build_model_field_map()


def deduce_node_field_paths(schema: GraphSchema) -> None:
    """Populate ``_field_paths`` and ``_is_list_at_paths`` on every ``GraphNode``.

    Uses the field map built by :func:`build_model_field_map` to locate each
    node class within the root model hierarchy.  Must be called after
    :func:`build_model_field_map`.

    Args:
        schema: The ``GraphSchema`` instance to operate on.
    """
    schema._deduce_node_field_paths()


def deduce_relation_field_paths(schema: GraphSchema) -> None:
    """Populate ``field_paths`` on every ``GraphRelation``.

    Finds the simplest valid path from each relation's ``from_node`` to its
    ``to_node`` within the model hierarchy.  Emits warnings when multiple
    equally-valid candidates exist and ``field_paths`` has not been set
    explicitly.

    Must be called after :func:`deduce_node_field_paths`.

    Args:
        schema: The ``GraphSchema`` instance to operate on.
    """
    schema._deduce_relation_field_paths()


def compute_excluded_fields(schema: GraphSchema) -> None:
    """Set ``_excluded_fields`` on every ``GraphNode``.

    Derives which fields on each node class are relationship targets (and
    therefore should not be materialised as node properties) by inspecting
    the declared relations and their resolved field paths.

    Must be called after :func:`deduce_relation_field_paths`.

    Args:
        schema: The ``GraphSchema`` instance to operate on.
    """
    schema._compute_excluded_fields()


def validate_schema_coherence(schema: GraphSchema, context: Any = None) -> None:
    """Run coherence checks and populate ``schema._warnings``.

    Checks for:

    - relations referencing undeclared node labels
    - duplicate relations between the same node pair
    - duplicate node labels (same ``label`` on two different classes)
    - orphaned nodes (not reachable from the root model)
    - invalid ``index_fields`` / ``extra_classes``

    Warnings are appended to ``schema._warnings`` and can be retrieved via
    ``schema.get_warnings()``.

    Args:
        schema: The ``GraphSchema`` instance to validate.
        context: Optional ``KgManager`` for additional context (e.g. registering
            warnings as outcomes).
    """
    schema._validate_coherence(context=context)


def compile_schema(schema: GraphSchema) -> GraphSchema:
    """Run the full compilation pipeline on *schema* and return it.

    This is the high-level entry point that calls all four compilation steps
    in the correct order:

    1. :func:`build_model_field_map`
    2. :func:`deduce_node_field_paths`
    3. :func:`deduce_relation_field_paths`
    4. :func:`compute_excluded_fields`
    5. :func:`validate_schema_coherence`

    The schema's ``@model_validator`` already calls this automatically on
    construction.  Use this function directly in tests or tooling that needs
    to trigger re-compilation (e.g. after modifying private attributes).

    Args:
        schema: The ``GraphSchema`` instance to compile.

    Returns:
        The same ``schema`` object, mutated in-place.
    """
    build_model_field_map(schema)
    deduce_node_field_paths(schema)
    deduce_relation_field_paths(schema)
    compute_excluded_fields(schema)
    validate_schema_coherence(schema)
    return schema
