"""Shared helper functions for KG schema rendering.

All functions here are private to the ``genai_graph.kg.schema`` package.
They are centralised in this module so that both :mod:`doc_generator` and
:mod:`resolved` can import them without creating a cross-module dependency on
each other's internal symbols.
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache
from typing import Any, get_args, get_origin

# ---------------------------------------------------------------------------
# BAML description parsing
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _parse_baml_descriptions() -> dict[str, Any]:
    """Parse descriptions from BAML files.

    Returns dictionary with:
        - classes: dict[str, str] - Class name to description
        - fields: dict[str, dict[str, str]] - Class to field descriptions
        - enums: dict[str, dict[str, str]] - Enum name to value descriptions
    """
    from genai_graph.ekg.baml_client.inlinedbaml import _file_map

    classes: dict[str, str] = {}
    fields: dict[str, dict[str, str]] = {}
    enums: dict[str, dict[str, str]] = {}

    excluded_files = {"clients.baml", "generators.baml"}
    for filename, content in _file_map.items():
        if filename in excluded_files:
            continue
        _parse_baml_content(content, classes, fields, enums)

    return {"classes": classes, "fields": fields, "enums": enums}


def _parse_baml_content(
    content: str,
    classes: dict[str, str],
    fields: dict[str, dict[str, str]],
    enums: dict[str, dict[str, str]],
) -> None:
    """Parse a single BAML file content for descriptions."""
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        block_match = re.match(r"^(class|enum)\s+([A-Za-z_]\w*)", stripped)
        if block_match:
            block_type = block_match.group(1)
            block_name = block_match.group(2)

            inline_desc = _extract_description_from_line(line, lines, i)
            if inline_desc and "@description" in line:
                if block_type == "class":
                    classes[block_name] = inline_desc[0]
                elif block_type == "enum":
                    enums[block_name] = {}
                i = inline_desc[1]
            else:
                found = False
                for back in range(max(0, i - 3), i):
                    prev = lines[back]
                    prev_desc = _extract_description_from_line(prev, lines, back)
                    if prev_desc:
                        if block_type == "class":
                            classes[block_name] = prev_desc[0]
                        else:
                            enums[block_name] = {}
                        found = True
                        break
                if not found and block_type == "enum":
                    enums[block_name] = {}

            i += 1
            while i < len(lines):
                inner = lines[i].strip()
                if inner == "}":
                    i += 1
                    break

                if block_type == "class":
                    m = re.match(r"([A-Za-z_]\w*)\s+([^@\n]+)", inner)
                    if m:
                        fld = m.group(1)
                        desc = _extract_description_from_line(lines[i], lines, i)
                        if desc and "@description" in lines[i]:
                            if block_name not in fields:
                                fields[block_name] = {}
                            fields[block_name][fld] = desc[0]
                            i = desc[1]
                            continue
                else:
                    m = re.match(r"([A-Za-z_]\w*)", inner)
                    if m:
                        val = m.group(1)
                        desc = _extract_description_from_line(lines[i], lines, i)
                        if desc and "@description" in lines[i]:
                            if block_name in enums:
                                enums[block_name][val] = desc[0]
                                i = desc[1]
                                continue
                        else:
                            if block_name in enums and val not in enums[block_name]:
                                enums[block_name][val] = ""

                i += 1
            continue

        i += 1


def _extract_description_from_line(line: str, all_lines: list[str], start_idx: int) -> tuple[str, int] | None:
    """Extract @description content from a line or across multiple lines."""
    if "@description" not in line:
        return None

    desc_match = re.search(r"@{1,2}description\s*\(\s*", line)
    if not desc_match:
        return None

    start_pos = desc_match.end()
    current_line_idx = start_idx
    current_text = line[start_pos:]

    if current_text.startswith('#"'):
        current_text = current_text[2:]
        buffer = []
        found_end = False

        while current_line_idx < len(all_lines):
            if '"#' in current_text:
                end_pos = current_text.index('"#')
                buffer.append(current_text[:end_pos])
                found_end = True
                break
            else:
                buffer.append(current_text)
                current_line_idx += 1
                if current_line_idx < len(all_lines):
                    current_text = all_lines[current_line_idx]
                else:
                    break

        if found_end:
            result = "\n".join(buffer).strip()
            result = re.sub(r"\s+", " ", result)
            return (result, current_line_idx + 1)
        return None
    else:
        quote_match = re.search(r'(["\'])(.+?)\1', current_text)
        if quote_match:
            return (quote_match.group(2), start_idx + 1)

    return None


# ---------------------------------------------------------------------------
# Field / node description extraction
# ---------------------------------------------------------------------------


def _get_pydantic_field_description(field_info: Any) -> str:
    """Return the Pydantic v2 ``Field(description=...)`` value, or empty string."""
    if hasattr(field_info, "description") and field_info.description:
        return field_info.description
    return ""


def _get_class_description(cls: type) -> str:
    """Return the first non-empty docstring line of *cls*, or empty string."""
    if not cls.__doc__:
        return ""
    for line in cls.__doc__.strip().split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _get_field_description(node_class: type, field_name: str, field_info: Any, baml_docs: dict[str, Any]) -> str:
    """Return the field description, preferring Pydantic then BAML."""
    pydantic_desc = _get_pydantic_field_description(field_info)
    if pydantic_desc:
        return pydantic_desc
    node_name = node_class.__name__
    return baml_docs["fields"].get(node_name, {}).get(field_name, "")


def _get_node_description(node: Any, baml_docs: dict[str, Any]) -> str:
    """Return the node description, preferring GraphNode.description then docstring then BAML."""
    if node.description:
        return node.description
    class_desc = _get_class_description(node.node_class)
    if class_desc:
        return class_desc
    node_name = node.node_class.__name__
    return baml_docs["classes"].get(node_name, "")


def _get_relation_properties(node_class: Any, baml_docs: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract ``p_*_`` relationship properties from *node_class*.

    Returns list of ``(display_name, humanized_type, description)`` tuples.
    """
    properties = []
    if not hasattr(node_class, "model_fields"):
        return properties
    for field_name, field_info in node_class.model_fields.items():
        if field_name.startswith("p_") and field_name.endswith("_"):
            display_name = field_name[2:-1]
            field_type = _humanize_type_compact(field_info.annotation)
            field_desc = _get_field_description(node_class, field_name, field_info, baml_docs)
            properties.append((display_name, field_type, field_desc))
    return properties


# ---------------------------------------------------------------------------
# Enum collection
# ---------------------------------------------------------------------------


def _collect_used_enums(schema: Any) -> set[type]:
    """Collect all Enum types referenced by node classes in *schema*."""
    import types
    import typing

    used_enums: set[type] = set()

    def extract_enums_from_annotation(annotation: Any) -> None:
        if annotation is None or annotation is type(None):
            return
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            used_enums.add(annotation)
            return
        origin = get_origin(annotation)
        if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
            for arg in get_args(annotation):
                extract_enums_from_annotation(arg)
            return
        if origin in (list, set, tuple):
            for arg in get_args(annotation):
                extract_enums_from_annotation(arg)
            return
        if origin is dict:
            for arg in get_args(annotation):
                extract_enums_from_annotation(arg)

    def extract_enums_from_class(cls: type) -> None:
        if not hasattr(cls, "model_fields"):
            return
        for field_info in cls.model_fields.values():
            extract_enums_from_annotation(field_info.annotation)

    for node in schema.nodes:
        extract_enums_from_class(node.node_class)
        for embedded_cls in getattr(node, "embedded_struct_classes", []) or []:
            extract_enums_from_class(embedded_cls)

    return used_enums


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------


def _get_kuzu_type_for_field(annotation: Any) -> str:
    """Map a Python type annotation to a Kuzu/Ladybug type string."""
    import types
    import typing

    if annotation is None or annotation is type(None):
        return "STRING"

    origin = get_origin(annotation)
    actual_type = annotation

    if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if non_none_args:
            actual_type = non_none_args[0]
            origin = get_origin(actual_type)

    if origin is list:
        inner_args = get_args(actual_type)
        if inner_args:
            if inner_args[0] is float:
                return "FLOAT[]"
            inner_type = _get_kuzu_type_for_field(inner_args[0])
            inner_base = inner_type.rstrip("[]")
            return f"{inner_base}[]"
        return "STRING[]"
    elif actual_type is int:
        return "INT64"
    elif actual_type is float:
        return "DOUBLE"
    elif actual_type is bool:
        return "BOOL"
    elif actual_type is str:
        return "STRING"
    elif isinstance(actual_type, type) and issubclass(actual_type, Enum):
        return "STRING"
    else:
        if hasattr(actual_type, "__name__"):
            return f"STRUCT:{actual_type.__name__}"
        return "STRING"


def _humanize_type_compact(annotation: Any, is_optional: bool = False) -> str:
    """Convert a Python type annotation to a compact LLM-friendly string."""
    if annotation is type(None):
        return "null"

    base_type, is_opt = _unwrap_optional(annotation)
    is_optional = is_optional or is_opt

    origin = get_origin(base_type)
    args = get_args(base_type)

    if origin is list:
        inner = _humanize_type_compact(args[0]) if args else "any"
        result = f"{inner.rstrip('?')}[]"
    elif origin is set:
        inner = _humanize_type_compact(args[0]) if args else "any"
        result = f"{inner.rstrip('?')}[]"
    elif origin is tuple:
        inner = _humanize_type_compact(args[0]) if args else "any"
        result = f"{inner.rstrip('?')}[]"
    elif origin is dict:
        result = "object"
    elif base_type is str:
        result = "string"
    elif base_type is int:
        result = "int"
    elif base_type is float:
        result = "float"
    elif base_type is bool:
        result = "boolean"
    elif isinstance(base_type, type) and issubclass(base_type, Enum):
        result = f"enum({base_type.__name__})"
    elif hasattr(base_type, "__name__"):
        result = base_type.__name__
    else:
        result = str(base_type)

    if is_optional:
        result = f"{result}?"

    return result


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Unwrap ``Optional[T]`` / ``T | None`` to ``(T, True)``, else ``(annotation, False)``."""
    import types
    from typing import Union

    origin = get_origin(annotation)

    if origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0], True
        return non_none_args[0] if non_none_args else annotation, True

    return annotation, False


# ---------------------------------------------------------------------------
# Vector index section (used by both doc_generator and resolved)
# ---------------------------------------------------------------------------


def _format_vector_index_section(schema: Any, baml_docs: Any) -> str:
    """Generate the ``### Vector-Indexed Fields`` Markdown section.

    Only includes fields whose ``{field_name}_embedding`` column is explicitly
    declared in the node's Pydantic model, ensuring the column actually exists
    in the database.

    Returns:
        Markdown section string, or empty string if no vector-indexed fields.
    """
    entries: list[str] = []
    for node in schema.nodes:
        if not node.compute_embeddings:
            continue
        table_name = node.node_class.__name__
        model_fields = node.node_class.model_fields
        for field_name, _model_override in node.index_field_specs:
            embedding_col = f"{field_name}_embedding"
            if embedding_col not in model_fields:
                continue
            entries.append(f"- {table_name}.{embedding_col} // embeddings of {table_name}.{field_name}")

    if not entries:
        return ""

    lines = ["### Vector-Indexed Fields (for semantic similarity search)", ""]
    lines.extend(entries)
    return "\n".join(lines)
