"""Generate schema documentation for knowledge graphs.

This module provides functionality to generate comprehensive, LLM-friendly
documentation of graph schemas, including node types, relationships,
properties, descriptions from BAML files, and indexed fields.
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache
from typing import Any, get_args, get_origin

from genai_graph.kg.schema.core import GraphSchema, find_embedded_field_for_class
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

    # Exclude client and generator files
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
    """Parse a single BAML file content for descriptions.

    Handles single-line and multi-line @description annotations:
    - Single-line: @description("text")
    - Multi-line: @description(#"text\nmore text"#)
    """
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Look for class or enum block start
        block_match = re.match(r"^(class|enum)\s+([A-Za-z_]\w*)", stripped)
        if block_match:
            block_type = block_match.group(1)
            block_name = block_match.group(2)

            # Try to extract an inline or preceding description
            inline_desc = _extract_description_from_line(line, lines, i)
            if inline_desc and "@description" in line:
                if block_type == "class":
                    classes[block_name] = inline_desc[0]
                elif block_type == "enum":
                    enums[block_name] = {}
                i = inline_desc[1]
            else:
                # Look backwards up to 3 lines for a preceding @description
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

            # Enter block and parse until closing brace
            i += 1
            while i < len(lines):
                inner = lines[i].strip()
                if inner == "}":
                    i += 1
                    break

                if block_type == "class":
                    # Parse field lines like: name type ... [@description(...)]
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
                    # enum value
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
    """Extract @description or @@description content from a line or across multiple lines.

    Supports:
    - Single-line: @description("text")
    - Multi-line: @description(#"text\nmore text"#)

    Returns: (description_text, next_line_index) or None
    """
    if "@description" not in line:
        return None

    # Try to find the start of @description or @@description
    desc_match = re.search(r"@{1,2}description\s*\(\s*", line)
    if not desc_match:
        return None

    # Start position after the opening parenthesis
    start_pos = desc_match.end()
    current_line_idx = start_idx
    current_text = line[start_pos:]

    # Check if it's a multi-line description (#"..."#)
    if current_text.startswith('#"'):
        # Multi-line description
        current_text = current_text[2:]  # Remove #"
        buffer = []
        found_end = False

        while current_line_idx < len(all_lines):
            if '"#' in current_text:
                # Found the end
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
            # Clean up multi-line description: remove extra whitespace/newlines
            result = "\n".join(buffer).strip()
            # Normalize whitespace: collapse multiple spaces and newlines
            result = re.sub(r"\s+", " ", result)
            return (result, current_line_idx + 1)
        else:
            return None

    # Single-line description ("..." or '...')
    else:
        # Find the closing quote
        quote_match = re.search(r'(["\'])(.+?)\1', current_text)
        if quote_match:
            return (quote_match.group(2), start_idx + 1)

    return None


def _get_pydantic_field_description(field_info: Any) -> str:
    """Extract description from a Pydantic FieldInfo object.

    Pydantic v2 stores the description in field_info.description.

    Args:
        field_info: Pydantic FieldInfo object from model_fields

    Returns:
        Description string, or empty string if not set
    """
    if hasattr(field_info, "description") and field_info.description:
        return field_info.description
    return ""


def _get_class_description(cls: type) -> str:
    """Extract description from a class docstring.

    Gets the first non-empty line of the docstring as a brief description.

    Args:
        cls: Python class to extract docstring from

    Returns:
        First line of docstring, or empty string if not available
    """
    if not cls.__doc__:
        return ""
    # Get first non-empty line of docstring
    lines = cls.__doc__.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _get_field_description(node_class: type, field_name: str, field_info: Any, baml_docs: dict[str, Any]) -> str:
    """Get field description with proper fallback order.

    Priority:
    1. Pydantic Field(description=...) from the field_info
    2. BAML @description annotation

    Args:
        node_class: The Pydantic model class
        field_name: Name of the field
        field_info: Pydantic FieldInfo object
        baml_docs: Parsed BAML documentation

    Returns:
        Field description string
    """
    # First try Pydantic field description
    pydantic_desc = _get_pydantic_field_description(field_info)
    if pydantic_desc:
        return pydantic_desc

    # Fallback to BAML description
    node_name = node_class.__name__
    return baml_docs["fields"].get(node_name, {}).get(field_name, "")


def _get_node_description(node: Any, baml_docs: dict[str, Any]) -> str:
    """Get node description with proper fallback order.

    Priority:
    1. GraphNode.description (explicit schema definition)
    2. Pydantic model class docstring
    3. BAML class @description annotation

    Args:
        node: GraphNode configuration object
        baml_docs: Parsed BAML documentation

    Returns:
        Node description string
    """
    # First try explicit GraphNode description
    if node.description:
        return node.description

    # Then try class docstring
    class_desc = _get_class_description(node.node_class)
    if class_desc:
        return class_desc

    # Fallback to BAML description
    node_name = node.node_class.__name__
    return baml_docs["classes"].get(node_name, "")


def _get_relation_properties(node_class: Any, baml_docs: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract relationship properties from a node class.

    Relationship properties are fields that match the p_*_ pattern (prefix p_, suffix _).
    Returns list of (name, type, description) tuples.
    """
    properties = []

    if not hasattr(node_class, "model_fields"):
        return properties

    for field_name, field_info in node_class.model_fields.items():
        # Check if this is a relationship property (p_*_ pattern)
        if field_name.startswith("p_") and field_name.endswith("_"):
            # Remove the p_ prefix and _ suffix to get the display name
            display_name = field_name[2:-1]
            field_type = _humanize_type_compact(field_info.annotation)
            # Use the new helper for field description
            field_desc = _get_field_description(node_class, field_name, field_info, baml_docs)
            properties.append((display_name, field_type, field_desc))

    return properties


def _collect_used_enums(schema: Any) -> set[type]:
    """Collect all Enum types used in the schema's node classes.

    Inspects all node classes and their fields (including embedded structs)
    to find which Enum types are actually referenced.

    Args:
        schema: GraphSchema object

    Returns:
        Set of Enum types used in the schema
    """
    import types
    import typing
    from typing import get_args, get_origin

    used_enums: set[type] = set()

    def extract_enums_from_annotation(annotation: Any) -> None:
        """Recursively extract enum types from a type annotation."""
        if annotation is None or annotation is type(None):
            return

        # Check if it's an enum
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            used_enums.add(annotation)
            return

        origin = get_origin(annotation)

        # Handle Union/Optional types
        if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
            for arg in get_args(annotation):
                extract_enums_from_annotation(arg)
            return

        # Handle list, set, tuple
        if origin in (list, set, tuple):
            for arg in get_args(annotation):
                extract_enums_from_annotation(arg)
            return

        # Handle dict
        if origin is dict:
            for arg in get_args(annotation):
                extract_enums_from_annotation(arg)
            return

    def extract_enums_from_class(cls: type) -> None:
        """Extract enums from a Pydantic model class."""
        if not hasattr(cls, "model_fields"):
            return

        for field_info in cls.model_fields.values():
            extract_enums_from_annotation(field_info.annotation)

    # Process all node classes
    for node in schema.nodes:
        extract_enums_from_class(node.node_class)

        # Also process embedded struct classes
        for embedded_cls in getattr(node, "embedded_struct_classes", []) or []:
            extract_enums_from_class(embedded_cls)

    return used_enums


def _get_kuzu_type_for_field(annotation: Any) -> str:
    """Map Python type annotation to Kuzu type string for JSON schema export.

    This is similar to graph_core._get_kuzu_type but returns a consistent
    type string for JSON schema export.

    Args:
        annotation: Python type annotation from Pydantic model

    Returns:
        Kuzu-compatible type string (e.g., "STRING", "DOUBLE", "INT64", "STRING[]")
    """
    import types
    import typing
    from typing import get_args, get_origin

    if annotation is None or annotation is type(None):
        return "STRING"

    origin = get_origin(annotation)
    actual_type = annotation

    # Handle Optional[...] types by unwrapping to get the inner type
    if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = get_args(annotation)
        # Optional[X] is Union[X, None], so extract X
        non_none_args = [arg for arg in args if arg is not type(None)]
        if non_none_args:
            actual_type = non_none_args[0]
            origin = get_origin(actual_type)

    # Check if it's a list type (after unwrapping Optional)
    if origin is list:
        inner_args = get_args(actual_type)
        if inner_args:
            if inner_args[0] is float:
                return "FLOAT[]"
            inner_type = _get_kuzu_type_for_field(inner_args[0])
            # Remove trailing [] if present to avoid double array notation
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
        return "STRING"  # Enums are stored as strings
    else:
        # For complex types (Pydantic models), return the class name as STRUCT indicator
        if hasattr(actual_type, "__name__"):
            return f"STRUCT:{actual_type.__name__}"
        return "STRING"


def format_schema_description(schema: GraphSchema, baml_docs: dict[str, Any], print_enums: bool = True) -> str:
    """Format schema as a compact, token-efficient description.

    Output format:
    - Nodes grouped by type with fields in format: name: type? // description
    - Relationships as: Source → [RELATION] → Dest with properties and description
    - Enumeration types with their values
    - Excludes embeddings and subgraph names

    Args:
        schema: The graph schema to format.
        baml_docs: Parsed BAML documentation containing descriptions.
        print_enums: Whether to include enumeration types (default: True).

    Returns:
        Markdown-formatted schema description.
    """
    lines = ["## Graph Schema Description", ""]

    # Track embedded classes to exclude from main type listing
    embedded_classes = set()
    for node in schema.nodes:
        for embedded_class in getattr(node, "embedded_struct_classes", []) or []:
            embedded_classes.add(embedded_class.__name__)

    # Group nodes by type
    lines.append("### Node Types and their fields (labels)")
    lines.append("")

    for node in schema.nodes:
        node_name = node.node_class.__name__

        # Skip embedded classes from main listing
        if node_name in embedded_classes:
            continue

        # Get description using proper fallback order:
        # 1. GraphNode.description, 2. class docstring, 3. BAML
        description = _get_node_description(node, baml_docs)

        # Start with node type header
        if description:
            lines.append(f"{node_name} // {description}")
        else:
            lines.append(f"{node_name}")

        # Build field list with compact format
        for field_name, field_info in node.node_class.model_fields.items():
            # Do not print the raw `metadata` map field – we surface
            # provenance via `metadata.source` for the root model below.
            if field_name == "metadata":
                continue
            if field_name not in node.excluded_fields:
                field_type_str = _humanize_type_compact(field_info.annotation)

                # Skip ForwardRef fields (they're covered by relationships)
                if "ForwardRef" in field_type_str:
                    continue

                # Get field description with proper fallback:
                # 1. Pydantic Field(description=...), 2. BAML @description
                field_desc = _get_field_description(node.node_class, field_name, field_info, baml_docs)

                # Check if this field is an embedded class and flatten it
                embedded_class = None
                for emb_class in getattr(node, "embedded_struct_classes", []) or []:
                    emb_field_name = find_embedded_field_for_class(node.node_class, emb_class)
                    if emb_field_name == field_name:
                        embedded_class = emb_class
                        break

                if embedded_class:
                    # Flatten embedded fields with dot notation
                    if hasattr(embedded_class, "model_fields"):
                        for sub_field_name, sub_field_info in embedded_class.model_fields.items():
                            sub_field_type = _humanize_type_compact(sub_field_info.annotation)
                            # Use proper fallback for embedded field descriptions
                            sub_field_desc = _get_field_description(
                                embedded_class, sub_field_name, sub_field_info, baml_docs
                            )

                            # Format: parent.child: type // description
                            line = f"  {field_name}.{sub_field_name}: {sub_field_type}"
                            if sub_field_desc:
                                line += f" // {sub_field_desc}"
                            lines.append(line)
                else:
                    # Regular field
                    line = f"  {field_name}: {field_type_str}"
                    if field_desc:
                        line += f" // {field_desc}"
                    lines.append(line)

        # If this node exposes a provenance field, document ``metadata.source``.
        try:
            if hasattr(node.node_class, "model_fields") and "metadata" in node.node_class.model_fields:
                lines.append("  metadata.source: string // source of the document")
        except Exception:
            pass

        lines.append("")

    # (Provenance `metadata.source` is documented inline under the root node above.)

    # Group relationships
    lines.extend(["### Relationships and their properties", ""])

    # Group by source node for clarity
    rels_by_source = {}
    for relation in schema.relations:
        source = relation.from_node.label
        if source not in rels_by_source:
            rels_by_source[source] = []
        rels_by_source[source].append(relation)

    for source in sorted(rels_by_source.keys()):
        for relation in rels_by_source[source]:
            dest = relation.to_node.label
            rel_name = relation.name
            description = relation.description

            # Format: Source → RELATION → Dest  # description
            line = f"{source} → {rel_name} → {dest}"
            if description:
                line += f" // {description}"
            lines.append(line)

            # Add relationship properties from destination node (fields with p_*_ pattern)
            rel_properties = _get_relation_properties(relation.to_node.node_class, baml_docs)
            for prop_name, prop_type, prop_desc in rel_properties:
                prop_line = f"  {prop_name}: {prop_type}"
                if prop_desc:
                    prop_line += f" // {prop_desc}"
                lines.append(prop_line)

        lines.append("")

    # High-level linkage between the logical root entity and its relationships.
    root_name = schema.root_model_class.__name__ if schema.root_model_class else "(no root)"
    lines.append(f"{root_name} → [relation] → [Target] // Relationships originating from the root entity")

    # Add enumerations section - only include enums actually used in the schema
    if print_enums:
        used_enums = _collect_used_enums(schema)
        used_enum_names = {e.__name__ for e in used_enums}

        # Filter to only enums used in this schema
        relevant_enums = {name: values for name, values in baml_docs["enums"].items() if name in used_enum_names}

        # Also add enums that are used but not in BAML docs (defined in Python)
        for enum_cls in used_enums:
            enum_name = enum_cls.__name__
            if enum_name not in relevant_enums:
                # Build enum values from the Python enum class
                relevant_enums[enum_name] = {
                    member.name: (member.value if isinstance(member.value, str) else "") for member in enum_cls
                }

        if relevant_enums:
            lines.extend(["### Enumerations", ""])

            for enum_name in sorted(relevant_enums.keys()):
                enum_values = relevant_enums[enum_name]
                # Try to get description from BAML or class docstring
                enum_desc = baml_docs["classes"].get(enum_name, "")
                if not enum_desc:
                    # Try to find the enum class and get its docstring
                    for enum_cls in used_enums:
                        if enum_cls.__name__ == enum_name:
                            enum_desc = _get_class_description(enum_cls)
                            break

                if enum_desc:
                    lines.append(f"{enum_name} // {enum_desc}")
                else:
                    lines.append(f"{enum_name}")

                # List enum values
                for value_name in sorted(enum_values.keys()):
                    value_desc = enum_values[value_name]
                    if value_desc:
                        lines.append(f"  {value_name} // {value_desc}")
                    else:
                        lines.append(f"  {value_name}")

                lines.append("")

    return "\n".join(lines)


def _humanize_type_compact(annotation: Any, is_optional: bool = False) -> str:
    """Convert Python type annotation to compact LLM-friendly format.

    Examples:
        - string, int, float, boolean
        - string[], int[] (for lists)
        - string? (for optional)
        - string[]? (for optional list)
    """
    # Handle None/NoneType
    if annotation is type(None):
        return "null"

    # Unwrap Optional
    base_type, is_opt = _unwrap_optional(annotation)
    is_optional = is_optional or is_opt

    # Get the actual type to process
    origin = get_origin(base_type)
    args = get_args(base_type)

    # Handle generic types
    if origin is list:
        inner = _humanize_type_compact(args[0]) if args else "any"
        # Remove optional marker from inner type for list display
        inner_clean = inner.rstrip("?")
        result = f"{inner_clean}[]"
    elif origin is set:
        inner = _humanize_type_compact(args[0]) if args else "any"
        inner_clean = inner.rstrip("?")
        result = f"{inner_clean}[]"
    elif origin is tuple:
        inner = _humanize_type_compact(args[0]) if args else "any"
        inner_clean = inner.rstrip("?")
        result = f"{inner_clean}[]"
    elif origin is dict:
        result = "object"
    # Handle basic types
    elif base_type is str:
        result = "string"
    elif base_type is int:
        result = "int"
    elif base_type is float:
        result = "float"
    elif base_type is bool:
        result = "boolean"
    # Handle Enums
    elif isinstance(base_type, type) and issubclass(base_type, Enum):
        result = f"enum({base_type.__name__})"
    # Default to class name
    elif hasattr(base_type, "__name__"):
        result = base_type.__name__
    else:
        result = str(base_type)

    # Add optional marker with ? suffix
    if is_optional:
        result = f"{result}?"

    return result


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Unwrap Optional/Union types to get base type and optionality."""
    import types
    from typing import Union

    origin = get_origin(annotation)

    # Check for Union (including Optional which is Union[T, None])
    # Handle both Union (typing.Union) and UnionType (| syntax)
    if origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = get_args(annotation)
        # Filter out NoneType
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0], True
        # Multiple non-None types - return first
        return non_none_args[0] if non_none_args else annotation, True

    return annotation, False
