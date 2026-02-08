"""Build D3-friendly JSON representations of KG schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from genai_graph.kg.schema.core import GraphSchema, find_embedded_field_for_class
from genai_graph.kg.schema.doc_generator import (
    _get_field_description,
    _get_kuzu_type_for_field,
    _get_node_description,
    _get_relation_properties,
    _humanize_type_compact,
    _parse_baml_descriptions,
)


def build_schema_d3_data(schema: GraphSchema, graph_names: list[str] | None = None) -> dict[str, Any]:
    """Build a D3-ready JSON model for a graph schema.

    The resulting data structure is optimized for direct use with D3 force-layout
    (or other graph layouts): nodes are a list with stable string IDs, and edges
    reference nodes by those IDs.

    Args:
        schema: Schema to export.
        graph_names: Optional list of graph factory names used to build the schema.

    Returns:
        Dictionary with keys: meta, nodes, links.
    """

    baml_docs = _parse_baml_descriptions()

    # Track embedded classes to exclude from main node listing.
    embedded_class_names: set[str] = set()
    for node in schema.nodes:
        for embedded_class in getattr(node, "embedded_struct_classes", []) or []:
            embedded_class_names.add(embedded_class.__name__)

    nodes_out: list[dict[str, Any]] = []

    for node in schema.nodes:
        node_name = node.node_class.__name__
        if node_name in embedded_class_names:
            continue

        description = _get_node_description(node, baml_docs)

        # Primary key as displayed in docs/GUI.
        if node.key_from == "AUTO_ID" or callable(node.key_from):
            primary_key = "id"
        else:
            primary_key = str(node.key_from)

        if isinstance(node.name_from, str):
            name_from = node.name_from
        else:
            name_from = "<callable>"

        fields_out: list[dict[str, Any]] = []

        model_fields = getattr(node.node_class, "model_fields", {})
        for field_name, field_info in model_fields.items():
            # Do not print the raw metadata map.
            if field_name == "metadata":
                continue

            if field_name in node.excluded_fields:
                continue

            field_type_human = _humanize_type_compact(field_info.annotation)

            # ForwardRefs are relationship targets.
            if "ForwardRef" in field_type_human:
                continue

            field_desc = _get_field_description(node.node_class, field_name, field_info, baml_docs)
            field_type_kuzu = _get_kuzu_type_for_field(field_info.annotation)

            embedded_class = None
            for emb_class in getattr(node, "embedded_struct_classes", []) or []:
                emb_field_name = find_embedded_field_for_class(node.node_class, emb_class)
                if emb_field_name == field_name:
                    embedded_class = emb_class
                    break

            if embedded_class:
                embedded_fields = getattr(embedded_class, "model_fields", {})
                for sub_field_name, sub_field_info in embedded_fields.items():
                    sub_field_type_human = _humanize_type_compact(sub_field_info.annotation)
                    sub_field_type_kuzu = _get_kuzu_type_for_field(sub_field_info.annotation)
                    sub_field_desc = _get_field_description(
                        embedded_class,
                        sub_field_name,
                        sub_field_info,
                        baml_docs,
                    )

                    fields_out.append(
                        {
                            "name": f"{field_name}.{sub_field_name}",
                            "type_human": sub_field_type_human,
                            "type_kuzu": sub_field_type_kuzu,
                            "description": sub_field_desc,
                            "indexed": False,
                            "embedded": True,
                            "parent_field": field_name,
                            "embedded_class": embedded_class.__name__,
                        }
                    )
            else:
                fields_out.append(
                    {
                        "name": field_name,
                        "type_human": field_type_human,
                        "type_kuzu": field_type_kuzu,
                        "description": field_desc,
                        "indexed": field_name in (node.index_fields or []),
                        "embedded": False,
                    }
                )

        # If this node exposes a provenance field, document metadata.source.
        if "metadata" in model_fields:
            fields_out.append(
                {
                    "name": "metadata.source",
                    "type_human": "string",
                    "type_kuzu": "STRING",
                    "description": "source of the document",
                    "indexed": False,
                    "embedded": True,
                    "parent_field": "metadata",
                    "embedded_class": "metadata",
                }
            )

        nodes_out.append(
            {
                "id": node_name,
                "label": node_name,
                "description": description,
                "primary_key": primary_key,
                "name_from": name_from,
                "index_fields": list(node.index_fields or []),
                "fields": fields_out,
            }
        )

    links_out: list[dict[str, Any]] = []

    for rel in schema.relations:
        source = rel.from_node.__name__
        target = rel.to_node.__name__

        props_out: list[dict[str, Any]] = []
        for prop_name, prop_type_human, prop_desc in _get_relation_properties(rel.to_node, baml_docs):
            raw_field_name = f"p_{prop_name}_"
            field_info = getattr(rel.to_node, "model_fields", {}).get(raw_field_name)
            prop_type_kuzu = _get_kuzu_type_for_field(field_info.annotation) if field_info else "STRING"

            props_out.append(
                {
                    "name": prop_name,
                    "type_human": prop_type_human,
                    "type_kuzu": prop_type_kuzu,
                    "description": prop_desc,
                }
            )

        links_out.append(
            {
                "id": f"{source}::{rel.name}::{target}",
                "source": source,
                "target": target,
                "label": rel.name,
                "description": rel.description or "",
                "field_paths": [
                    {
                        "from": from_path or "",
                        "to": to_path or "",
                    }
                    for from_path, to_path in (rel.field_paths or [])
                ],
                "properties": props_out,
            }
        )

    root_name = schema.root_model_class.__name__ if schema.root_model_class else None

    return {
        "meta": {
            "format": "genai_graph.schema_d3",
            "format_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "graphs": list(graph_names or []),
            "root_model": root_name,
        },
        "nodes": nodes_out,
        "links": links_out,
    }
