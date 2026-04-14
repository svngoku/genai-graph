"""Generate an interactive HTML visualization of a Cypher graph

This module builds a simple JSON model, and embeds it in
an HTML page rendered with D3 force-directed layout.

It's inspired from code in Cognee.

Features:
- Interactive D3.js force-directed graph visualization
- Node types legend panel (right side) with click-to-highlight
- Selection details panel (left side) showing node/edge properties
- Zoom controls and drag-to-move functionality
- Tooltips on hover for quick inspection

Usage example:
```python
from genai_graph.kg.export.html import generate_html

# Generate visualization from a graph backend
html_content = generate_html(
    connection=graph_backend,
    destination_file_path="my_graph.html",
)
```
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from genai_graph.kg.backend import KgBackend
from genai_graph.kg.export.html_template import HTML_TEMPLATE
from genai_graph.kg.ingest.extract import NodeRecord, RelationshipRecord

# from genai_graph.demos.ekg.graph_schema import GraphNode, GraphRelationConfig, GraphSchema


def _get_node_raw_name(node_dict: dict[str, Any], node_type: str) -> str:
    """Extract the raw name for a node without truncation (for ID generation).

    Args:
        node_dict: Node properties dictionary
        node_type: The node type/table name

    Returns:
        Raw name for the node (no truncation)
    """
    # PRIORITY 1: Check for the 'name' field (user-chosen node name)
    if "name" in node_dict and node_dict["name"] is not None:
        value = str(node_dict["name"]).strip()
        if value:
            return value

    # PRIORITY 2: Common name fields to check in order of preference
    name_fields = ["title", "description", "label", "_original_name", "id"]

    for field in name_fields:
        if field in node_dict and node_dict[field] is not None:
            value = str(node_dict[field]).strip()
            if value:
                return value

    # PRIORITY 3: If no name field found, use the first non-empty string field
    for key, value in node_dict.items():
        if isinstance(value, str) and value.strip() and key not in ["type", "id", "name"]:
            return str(value)

    # Fallback to node type
    return node_type


def _get_node_display_name(node_dict: dict[str, Any], node_type: str, max_length: int = 30) -> str:
    """Generate a display name for a node based on its properties.

    Args:
        node_dict: Node properties dictionary
        node_type: The node type/table name
        max_length: Maximum length for the display name

    Returns:
        Display name for the node
    """
    # Get the raw name first
    raw_name = _get_node_raw_name(node_dict, node_type)

    # Apply truncation only for display
    if len(raw_name) > max_length:
        return raw_name[:max_length] + "..."
    return raw_name


def _get_node_color(node_type: str, custom_colors: dict[str, str] | None = None) -> str:
    """Get color for a node type.

    Args:
        node_type: The node type/table name
        custom_colors: Optional custom color mapping

    Returns:
        Hex color code for the node
    """
    if custom_colors and node_type in custom_colors:
        return custom_colors[node_type]

    # Generate a consistent color based on node type hash
    import hashlib

    # Create a hash of the node type
    hash_object = hashlib.md5(node_type.encode())
    hex_hash = hash_object.hexdigest()

    # Use first 6 characters as color, but ensure it's not too dark
    color = "#" + hex_hash[:6]

    # Brighten the color if it's too dark
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)

    # Ensure minimum brightness
    min_brightness = 100
    if r < min_brightness:
        r = min(255, r + min_brightness)
    if g < min_brightness:
        g = min(255, g + min_brightness)
    if b < min_brightness:
        b = min(255, b + min_brightness)

    return f"#{r:02x}{g:02x}{b:02x}"


def _normalize_graph_obj(obj: Any) -> Any:
    """Normalize a node/relationship dict returned by the graph database.

    Kuzu uses lowercase internal keys (_id, _label, _src, _dst).
    Ladybug (the Kuzu fork) uses uppercase (_ID, _LABEL, _SRC, _DST).
    This normalises to lowercase so the rest of the code works with either backend.
    """
    if not isinstance(obj, dict):
        return obj
    return {(k.lower() if k.startswith("_") else k): v for k, v in obj.items()}


def _serialize_kuzu_id(kuzu_id: Any) -> str:
    """Serialize a Kuzu/Ladybug internal ID to a consistent string format.

    IDs are dicts like {'offset': 0, 'table': 0} or simple values.

    Args:
        kuzu_id: The internal ID (dict or other)

    Returns:
        A consistent string representation
    """
    if isinstance(kuzu_id, dict):
        table = kuzu_id.get("table", 0)
        offset = kuzu_id.get("offset", 0)
        return f"{table}:{offset}"
    return str(kuzu_id)


def _generate_html_content(nodes_list: list[dict[str, Any]], links_list: list[dict[str, Any]]) -> str:
    """Generate HTML content from nodes and links lists.

    Args:
        nodes_list: List of node dictionaries with id, name, type, color, and properties
        links_list: List of link dictionaries with source, target, relation, weight info

    Returns:
        HTML content as a string with embedded D3.js visualization
    """
    html_content = HTML_TEMPLATE.replace("{nodes}", json.dumps(nodes_list))
    html_content = html_content.replace("{links}", json.dumps(links_list))

    return html_content


def _fetch_graph_data(
    connection: KgBackend,
    node_configs: list | None = None,
    relation_configs: list | None = None,
    query: str = "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 1000",
    union: bool = True,
    supplemental_node_types: list[str] | None = None,
    supplemental_limit: int = 2000,
) -> tuple[list[NodeRecord], list[RelationshipRecord]]:
    """Fetch all nodes and edges from the graph database via the provided connection/backend.

    Args:
        connection: Object exposing an execute() method (e.g. KgBackend or kuzu.Connection)
        node_configs: Optional list of node configurations (legacy or new format)
        relation_configs: Optional list of relation configurations (legacy or new format)
        query: Cypher query to fetch relationships and nodes (default: "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 1000")
               Can be customized to filter by node types, limit results, etc.
               Must return columns named 'n', 'r', 'm' for source node, relationship, target node.
        union: If True and query contains multiple statements, union the results. Default True.
        supplemental_node_types: Node type labels to fetch in full regardless of the relationship
            query result (e.g. ``["L3"]``). Prevents nodes from being missed when the LIMIT on
            the relationship query is reached before all instances of the type are seen.
        supplemental_limit: Maximum number of nodes per supplemental node type to retrieve.

    Returns:
        Tuple of (nodes, relationships) where:
        - nodes: list of NodeRecord instances
        - relationships: list of RelationshipRecord instances
    """
    nodes: list[NodeRecord] = []
    relationships: list[RelationshipRecord] = []

    # Optional filtering based on provided node / relation configs
    allowed_node_labels: set[str] | None = None
    allowed_rel_types: set[str] | None = None

    if node_configs:
        try:
            labels: set[str] = set()
            for cfg in node_configs:
                node_class = getattr(cfg, "node_class", None)
                if node_class is not None and hasattr(node_class, "__name__"):
                    labels.add(node_class.__name__)
            if labels:
                allowed_node_labels = labels
        except Exception:
            allowed_node_labels = None

    if relation_configs:
        try:
            rel_types: set[str] = set()
            for cfg in relation_configs:
                name = getattr(cfg, "name", None)
                if isinstance(name, str):
                    rel_types.add(name)
            if rel_types:
                allowed_rel_types = rel_types
        except Exception:
            allowed_rel_types = None

    try:
        # Mapping from Kuzu internal ID to UUID and node data
        kuzu_id_to_node_data: dict[str, dict[str, Any]] = {}

        # Execute the relationship query
        rel_df = connection.execute_get_as_df(query, union=union)

        # Process all nodes and relationships from the query result
        for _, row in rel_df.iterrows():
            src_node = _normalize_graph_obj(row["n"])
            dst_node = _normalize_graph_obj(row["m"])
            rel_obj = _normalize_graph_obj(row["r"])

            # Check if rel_obj is a Kuzu/Ladybug path object (variable-length path result)
            # Path objects have structure: {'_nodes': [...], '_rels': [...]}
            is_path = isinstance(rel_obj, dict) and "_rels" in rel_obj and "_nodes" in rel_obj

            if is_path:
                # Unpack path: process all intermediate nodes and relationships
                path_nodes = [_normalize_graph_obj(n) for n in rel_obj.get("_nodes", [])]
                path_rels = [_normalize_graph_obj(r) for r in rel_obj.get("_rels", [])]

                # Build list of all nodes in path: src -> intermediates -> dst
                all_path_nodes = [src_node] + path_nodes + [dst_node]

                # Process all nodes in the path
                for node_obj in all_path_nodes:
                    if not isinstance(node_obj, dict):
                        continue

                    # Get Kuzu internal ID for deduplication
                    kuzu_id = _serialize_kuzu_id(node_obj.get("_id"))
                    if not kuzu_id or kuzu_id in kuzu_id_to_node_data:
                        continue

                    # Get node type (label)
                    node_type = node_obj.get("_label", "Unknown")

                    # Apply node label filtering
                    if allowed_node_labels and node_type not in allowed_node_labels:
                        continue

                    # Extract node properties (skip internal fields)
                    node_dict = {}
                    for key, val in node_obj.items():
                        if key in ("_created_at", "_updated_at", "_original_name"):
                            node_dict[key] = str(val).strip() or str(val)
                        elif not key.startswith("_") and val is not None:
                            node_dict[key] = str(val).strip() or str(val)

                    # Generate display name and metadata
                    node_name = _get_node_display_name(node_dict, node_type)
                    node_dict["type"] = node_type
                    node_dict["name"] = node_name

                    # Generate UUID for this node
                    node_uuid = str(uuid.uuid4())

                    # Store in mapping
                    kuzu_id_to_node_data[kuzu_id] = {
                        "uuid": node_uuid,
                        "type": node_type,
                        "node_dict": node_dict,
                    }

                    nodes.append(NodeRecord(node_id=node_uuid, properties=node_dict))

                # Process all relationships in the path
                for path_rel in path_rels:
                    if not isinstance(path_rel, dict):
                        continue

                    rel_type = path_rel.get("_label", "RELATED_TO")

                    # Apply relationship type filtering
                    if allowed_rel_types and rel_type not in allowed_rel_types:
                        continue

                    # Extract edge properties (non-internal fields)
                    edge_props = {}
                    for key, value in path_rel.items():
                        if not key.startswith("_") and value is not None:
                            edge_props[key] = value

                    # Get source and destination from _src and _dst in the relationship
                    src_kuzu_id = _serialize_kuzu_id(path_rel.get("_src"))
                    dst_kuzu_id = _serialize_kuzu_id(path_rel.get("_dst"))

                    if not src_kuzu_id or not dst_kuzu_id:
                        continue

                    src_data = kuzu_id_to_node_data.get(src_kuzu_id)
                    dst_data = kuzu_id_to_node_data.get(dst_kuzu_id)

                    if src_data and dst_data:
                        relationships.append(
                            RelationshipRecord(
                                from_type=src_data["type"],
                                from_id=src_data["uuid"],
                                to_type=dst_data["type"],
                                to_id=dst_data["uuid"],
                                name=rel_type,
                                properties=edge_props,
                            )
                        )
            else:
                # Simple relationship (not a path) - original processing logic
                # Process source and destination nodes
                for node_obj in [src_node, dst_node]:
                    if not isinstance(node_obj, dict):
                        continue

                    # Get Kuzu internal ID for deduplication
                    kuzu_id = _serialize_kuzu_id(node_obj.get("_id"))
                    if not kuzu_id or kuzu_id in kuzu_id_to_node_data:
                        continue

                    # Get node type (label)
                    node_type = node_obj.get("_label", "Unknown")

                    # Apply node label filtering
                    if allowed_node_labels and node_type not in allowed_node_labels:
                        continue

                    # Extract node properties (skip internal fields)
                    node_dict = {}
                    for key, val in node_obj.items():
                        if key in ("_created_at", "_updated_at", "_original_name"):
                            node_dict[key] = str(val).strip() or str(val)
                        elif not key.startswith("_") and val is not None:
                            node_dict[key] = str(val).strip() or str(val)

                    # Generate display name and metadata
                    node_name = _get_node_display_name(node_dict, node_type)
                    node_dict["type"] = node_type
                    node_dict["name"] = node_name

                    # Generate UUID for this node
                    node_uuid = str(uuid.uuid4())

                    # Store in mapping
                    kuzu_id_to_node_data[kuzu_id] = {
                        "uuid": node_uuid,
                        "type": node_type,
                        "node_dict": node_dict,
                    }

                    nodes.append(NodeRecord(node_id=node_uuid, properties=node_dict))

                # Process relationship
                rel_type = "RELATED_TO"
                edge_props = {}
                if isinstance(rel_obj, dict):
                    rel_type = rel_obj.get("_label", "RELATED_TO")
                    # Extract edge properties (non-internal fields)
                    for key, value in rel_obj.items():
                        if not key.startswith("_") and value is not None:
                            edge_props[key] = value

                # Apply relationship type filtering
                if allowed_rel_types and rel_type not in allowed_rel_types:
                    continue

                # Get UUIDs for source and destination
                src_kuzu_id = _serialize_kuzu_id(src_node.get("_id")) if isinstance(src_node, dict) else None
                dst_kuzu_id = _serialize_kuzu_id(dst_node.get("_id")) if isinstance(dst_node, dict) else None

                if not src_kuzu_id or not dst_kuzu_id:
                    continue

                src_data = kuzu_id_to_node_data.get(src_kuzu_id)
                dst_data = kuzu_id_to_node_data.get(dst_kuzu_id)

                if src_data and dst_data:
                    relationships.append(
                        RelationshipRecord(
                            from_type=src_data["type"],
                            from_id=src_data["uuid"],
                            to_type=dst_data["type"],
                            to_id=dst_data["uuid"],
                            name=rel_type,
                            properties=edge_props,
                        )
                    )

        # Fetch isolated nodes (nodes without relationships)
        try:
            isolated_query = "MATCH (n) WHERE NOT (n)-[]-() RETURN n"
            isolated_df = connection.execute_get_as_df(isolated_query, union=False)

            for _, row in isolated_df.iterrows():
                node_obj = _normalize_graph_obj(row["n"])
                if not isinstance(node_obj, dict):
                    continue

                # Get Kuzu internal ID for deduplication
                kuzu_id = _serialize_kuzu_id(node_obj.get("_id"))
                if not kuzu_id or kuzu_id in kuzu_id_to_node_data:
                    continue

                # Get node type (label)
                node_type = node_obj.get("_label", "Unknown")

                # Apply node label filtering
                if allowed_node_labels and node_type not in allowed_node_labels:
                    continue

                # Extract node properties
                node_dict = {}
                for key, val in node_obj.items():
                    if key in ("_created_at", "_updated_at", "_original_name"):
                        node_dict[key] = str(val).strip() or str(val)
                    elif not key.startswith("_") and val is not None:
                        node_dict[key] = str(val).strip() or str(val)

                # Generate display name and metadata
                node_name = _get_node_display_name(node_dict, node_type)
                node_dict["type"] = node_type
                node_dict["name"] = node_name

                # Generate UUID for this node
                node_uuid = str(uuid.uuid4())

                nodes.append(NodeRecord(node_id=node_uuid, properties=node_dict))

        except Exception as e:
            print(f"Warning: Could not fetch isolated nodes: {e}")

        # Supplemental fetch: ensure every instance of the selected node types is present
        # even when the relationship query hit its LIMIT before covering all of them.
        if supplemental_node_types:
            for sup_type in supplemental_node_types:
                try:
                    sup_query = f"MATCH (n:{sup_type}) RETURN n LIMIT {supplemental_limit}"
                    sup_df = connection.execute_get_as_df(sup_query, union=False)

                    for _, row in sup_df.iterrows():
                        node_obj = _normalize_graph_obj(row["n"])
                        if not isinstance(node_obj, dict):
                            continue

                        kuzu_id = _serialize_kuzu_id(node_obj.get("_id"))
                        if not kuzu_id or kuzu_id in kuzu_id_to_node_data:
                            continue  # already present from relationship query

                        node_type = node_obj.get("_label", sup_type)

                        node_dict = {}
                        for key, val in node_obj.items():
                            if key in ("_created_at", "_updated_at", "_original_name"):
                                node_dict[key] = str(val).strip() or str(val)
                            elif not key.startswith("_") and val is not None:
                                node_dict[key] = str(val).strip() or str(val)

                        node_name = _get_node_display_name(node_dict, node_type)
                        node_dict["type"] = node_type
                        node_dict["name"] = node_name

                        node_uuid = str(uuid.uuid4())
                        kuzu_id_to_node_data[kuzu_id] = {
                            "uuid": node_uuid,
                            "type": node_type,
                            "node_dict": node_dict,
                        }
                        nodes.append(NodeRecord(node_id=node_uuid, properties=node_dict))

                except Exception as e:
                    print(f"Warning: Could not fetch supplemental nodes for type '{sup_type}': {e}")

    except Exception as e:
        print(f"Error in _fetch_graph_data: {e}")
        return [], []

    return nodes, relationships


def generate_html(
    connection: KgBackend,
    destination_file_path: str | None = None,
    node_configs: list | None = None,
    relation_configs: list | None = None,
    custom_colors: dict[str, str] | None = None,
    query: str = "MATCH (n)-[r]->(m) RETURN n, r, m",
    union: bool = True,
    filter_orphan_nodes: bool = False,
    selected_node_types: list[str] | None = None,
) -> str:
    """Generate an HTML graph visualization from a graph connection/backend.

    Args:
        connection: Object exposing an execute() method (e.g. KgBackend or kuzu.Connection) connected to a database that uses
            a schema with Node(id, name, type, properties) and EDGE(relationship_name, properties).
        destination_file_path: Optional path to write the HTML file. If omitted,
            the file will be saved as "graph_visualization.html" in the user's home directory.
        node_configs: Optional list of node configurations (legacy or new format)
        relation_configs: Optional list of relation configurations (legacy or new format)
        custom_colors: Optional mapping of node types to hex color codes
        query: Cypher query to fetch relationships and nodes (default: "MATCH (n)-[r]->(m) RETURN n, r, m")
               Can be customized to filter by node types, limit results, etc.
               Must return columns named 'n', 'r', 'm' for source node, relationship, target node.
        union: If True and query contains multiple statements, union the results. Default True.
        filter_orphan_nodes: If True, remove nodes that don't appear in any relationship.
            Useful when using multi-hop queries that may include intermediate nodes. Default False.
            Nodes whose type is in ``selected_node_types`` are always preserved regardless of this flag.
        selected_node_types: Node type labels the user explicitly chose to display.  All instances
            of these types are fetched via a supplemental query so that LIMIT constraints on the
            relationship query cannot leave them out.  Nodes of these types are also exempt from
            ``filter_orphan_nodes`` pruning.

    Returns:
        The HTML content as a string.
    """
    nodes_data, relationships_data = _fetch_graph_data(
        connection,
        node_configs,
        relation_configs,
        query,
        union,
        supplemental_node_types=selected_node_types,
    )

    # Build visualization model using generic color assignment

    nodes_list: list[dict[str, Any]] = []
    for node_record in nodes_data:
        node_info = dict(node_record.properties)  # shallow copy
        node_info["id"] = str(node_record.node_id)
        node_type = node_info.get("type", "Unknown")
        node_info["color"] = _get_node_color(node_type, custom_colors)
        node_info["name"] = node_info.get("name", str(node_record.node_id))
        # Trim noisy timestamp fields if present
        node_info.pop("updated_at", None)
        node_info.pop("created_at", None)
        nodes_list.append(node_info)

    links_list: list[dict[str, Any]] = []
    for rel_record in relationships_data:
        source_s = str(rel_record.from_id)
        target_s = str(rel_record.to_id)
        relation = rel_record.name
        edge_info = rel_record.properties or {}

        # Extract weight variations
        all_weights: dict[str, float] = {}
        primary_weight: float | None = None

        if "weight" in edge_info:
            try:
                primary_weight = float(edge_info["weight"])  # best effort
                all_weights["default"] = primary_weight
            except (TypeError, ValueError):
                pass

        if "weights" in edge_info and isinstance(edge_info["weights"], dict):
            for k, v in edge_info["weights"].items():
                try:
                    all_weights[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            if primary_weight is None and all_weights:
                primary_weight = next(iter(all_weights.values()))

        for key, value in edge_info.items():
            if key.startswith("weight_"):
                try:
                    all_weights[key[7:]] = float(value)
                except (TypeError, ValueError):
                    continue

        links_list.append(
            {
                "source": source_s,
                "target": target_s,
                "relation": relation,
                "weight": primary_weight,
                "all_weights": all_weights,
                "relationship_type": edge_info.get("relationship_type"),
                "edge_info": edge_info,
            }
        )

    # Optionally filter out orphan nodes - nodes that don't appear in any relationship.
    # Nodes whose type is in selected_node_types are always kept: the user explicitly
    # asked to see them, and their relationships may have been cut off by LIMIT.
    if filter_orphan_nodes:
        connected_node_ids: set[str] = set()
        for link in links_list:
            connected_node_ids.add(link["source"])
            connected_node_ids.add(link["target"])
        selected_type_set: set[str] = set(selected_node_types) if selected_node_types else set()
        nodes_list = [
            node for node in nodes_list if node["id"] in connected_node_ids or node.get("type") in selected_type_set
        ]

    html_content = _generate_html_content(nodes_list, links_list)

    if not destination_file_path:
        home_dir = os.path.expanduser("~")
        destination_file_path = os.path.join(home_dir, "graph_visualization.html")

    os.makedirs(os.path.dirname(destination_file_path) or ".", exist_ok=True)
    with open(destination_file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_content
