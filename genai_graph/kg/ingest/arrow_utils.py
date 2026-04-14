"""PyArrow utilities for graph node schema derivation.

These helpers convert Pydantic type annotations to PyArrow types and build
``pa.Schema`` objects that match the Ladybug node-table definitions produced
by :func:`genai_graph.kg.ingest.extract.create_schema`.

Extracted from ``merge.py`` so they can be reused as more of the ingestion
pipeline moves to Arrow-native operations.
"""

from __future__ import annotations

import types as _types
from enum import Enum
from typing import Any, get_args, get_origin

import pyarrow as pa

# ---------------------------------------------------------------------------
# Primitive map — defined once at module level to avoid per-call recreation
# ---------------------------------------------------------------------------
_PRIMITIVE_TO_ARROW: dict[type, pa.DataType] = {
    str: pa.string(),
    float: pa.float64(),
    int: pa.int64(),
    bool: pa.bool_(),
}


def pydantic_annotation_to_arrow(annotation: Any) -> pa.DataType:
    """Convert a Python type annotation to a PyArrow ``DataType``.

    Handles the full set of annotations that appear in Pydantic node models:

    - Primitives (``str``, ``int``, ``float``, ``bool``)
    - ``Optional[T]`` / ``T | None`` — unwrapped and recursed
    - ``list[T]`` — maps to ``pa.list_(T)``
    - ``Enum`` subclasses — ``pa.string()``
    - Pydantic sub-models — ``pa.struct([...])`` recursively
    - Unknown / unrecognised annotations — ``pa.string()`` fallback

    Args:
        annotation: Python type annotation to convert.

    Returns:
        Corresponding ``pa.DataType``.
    """
    import typing

    if annotation is None or annotation is type(None):
        return pa.string()

    origin = get_origin(annotation)

    # Unwrap Optional[X] / Union[X, None] / X | None
    if origin is typing.Union or isinstance(annotation, _types.UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        return pydantic_annotation_to_arrow(non_none[0]) if non_none else pa.string()

    # list[T]
    if origin is list:
        args = get_args(annotation)
        inner = pydantic_annotation_to_arrow(args[0]) if args else pa.string()
        return pa.list_(inner)

    # Primitives
    if annotation in _PRIMITIVE_TO_ARROW:
        return _PRIMITIVE_TO_ARROW[annotation]

    # Enum → stored as string
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return pa.string()

    # Pydantic sub-model → pa.struct (recursive)
    if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
        return pa.struct(
            [pa.field(name, pydantic_annotation_to_arrow(fi.annotation)) for name, fi in annotation.model_fields.items()]
        )

    return pa.string()  # fallback


def arrow_type_contains_struct(arrow_type: pa.DataType) -> bool:
    """Return ``True`` if ``arrow_type`` is or contains a ``pa.struct()``.

    Used as a safety-net to detect Pydantic sub-model fields that resolve to a
    struct but were not listed in ``embedded_struct_classes`` (and therefore
    should be excluded from the node table).

    Args:
        arrow_type: Arrow data type to inspect.

    Returns:
        ``True`` when the type is a struct or a list whose element type is a struct.
    """
    if pa.types.is_struct(arrow_type):
        return True
    if pa.types.is_list(arrow_type):
        return arrow_type_contains_struct(arrow_type.value_type)
    return False


def ladybug_type_to_arrow(type_str: str) -> pa.DataType:
    """Map a Ladybug/Kuzu DDL type string to a PyArrow ``DataType``.

    Handles the scalar types used in ``CREATE NODE TABLE`` statements as well
    as list variants.

    Args:
        type_str: Ladybug type string such as ``"STRING"``, ``"INT64"``,
            ``"FLOAT[]"``, ``"DOUBLE"``.

    Returns:
        Corresponding ``pa.DataType``.
    """
    type_str = type_str.strip()
    if type_str.endswith("[]"):
        return pa.list_(ladybug_type_to_arrow(type_str[:-2]))
    upper = type_str.upper()
    return {
        "STRING": pa.string(),
        "DOUBLE": pa.float64(),
        "FLOAT": pa.float64(),
        "INT64": pa.int64(),
        "INT32": pa.int64(),
        "INT16": pa.int64(),
        "BOOL": pa.bool_(),
    }.get(upper, pa.string())


def build_node_arrow_schema(
    node_class: type,
    primary_key_field: str = "id",
    excluded_fields: set[str] | None = None,
    embedded_struct_classes: list[type] | None = None,
    embedding_field_dimensions: dict[str, int] | None = None,
) -> pa.Schema:
    """Build a ``pa.Schema`` for a Pydantic node class that mirrors the Ladybug table.

    Column rules (applied in order):

    1. **Sentinel columns** — ``primary_key_field``, ``"name"``, ``"_original_name"``
       are always prepended as ``pa.string()``, deduplicated when
       ``primary_key_field == "name"``.
    2. **Excluded fields** — skipped entirely (``p_*_`` edge-property sentinels and
       relationship-target sub-model fields from
       ``GraphSchema._compute_excluded_fields``).
    3. **Embedded structs** — fields in ``embedded_struct_classes`` become
       ``pa.struct(...)`` columns with sub-fields in model-definition order.
    4. **Embedding vectors** — ``*_embedding`` fields and those listed in
       ``embedding_field_dimensions`` become ``pa.list_(pa.float64())``.
    5. **Struct-typed fields not in ``embedded_struct_classes``** — skipped
       (safety net for relationship targets not yet captured in step 2).
    6. **Everything else** — type derived via
       :func:`pydantic_annotation_to_arrow`.
    7. **Timestamp sentinels** — ``_created_at`` / ``_updated_at`` appended as
       ``pa.string()`` if not already present.

    Args:
        node_class: Pydantic class whose ``model_fields`` define the columns.
        primary_key_field: Primary key column name.
        excluded_fields: Column names to skip (p_*_ sentinels + rel targets).
        embedded_struct_classes: Sub-model classes stored inline as STRUCT columns.
        embedding_field_dimensions: Maps field name → vector dimension (used only
            to flag a field as an embedding; the stored type is always ``list<float64>``).

    Returns:
        ``pa.Schema`` ready for use in LOAD FROM MERGE operations.
    """
    from genai_graph.kg.schema.core import find_embedded_field_for_class

    excluded = excluded_fields or set()
    struct_map: dict[str, type] = {}
    for emb_cls in (embedded_struct_classes or []):
        field_name = find_embedded_field_for_class(node_class, emb_cls)
        if field_name:
            struct_map[field_name] = emb_cls

    emb_dims = embedding_field_dimensions or {}

    fields: list[pa.Field] = []
    seen: set[str] = set()

    # 1. Sentinel columns (deduplicated)
    for name in dict.fromkeys([primary_key_field, "name", "_original_name"]):
        if name not in excluded:
            fields.append(pa.field(name, pa.string()))
            seen.add(name)

    # 2-6. Model fields
    for field_name, field_info in getattr(node_class, "model_fields", {}).items():
        if field_name in seen or field_name in excluded:
            continue
        seen.add(field_name)

        if field_name in struct_map:
            emb_cls = struct_map[field_name]
            sub_fields = [
                pa.field(n, pydantic_annotation_to_arrow(fi.annotation))
                for n, fi in emb_cls.model_fields.items()
            ]
            fields.append(pa.field(field_name, pa.struct(sub_fields)))
        elif field_name in emb_dims or field_name.endswith("_embedding"):
            fields.append(pa.field(field_name, pa.list_(pa.float64())))
        else:
            arrow_type = pydantic_annotation_to_arrow(field_info.annotation)
            if arrow_type_contains_struct(arrow_type):
                continue  # safety net: un-embedded sub-model (rel target)
            fields.append(pa.field(field_name, arrow_type))

    # 7. Timestamp sentinels
    for ts in ("_created_at", "_updated_at"):
        if ts not in seen:
            fields.append(pa.field(ts, pa.string()))

    return pa.schema(fields)
