"""Generate an interactive HTML visualization of a Cypher graph

This module  builds a simple JSON model, and embeds it in
an HTML page rendered with D3 force-directed layout.

It's inspired from code in Cognee.

Usage example:
```

```
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from genai_graph.core.graph_backend import GraphBackend
from genai_graph.core.graph_core import NodeRecord, RelationshipRecord

# Import new schema types

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

    # If no name field found, use the first non-empty string field
    for key, value in node_dict.items():
        if isinstance(value, str) and value.strip() and key not in ["type", "id"]:
            truncated = str(value)[:max_length]
            return truncated + ("..." if len(str(value)) > max_length else "")

    # Fallback to node type
    return node_type


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


def _serialize_kuzu_id(kuzu_id: Any) -> str:
    """Serialize a Kuzu internal ID to a consistent string format.

    Kuzu IDs can be dicts like {'offset': 0, 'table': 0} or simple values.
    This ensures we get a consistent string representation.

    Args:
        kuzu_id: The Kuzu internal ID (dict or other)

    Returns:
        A consistent string representation
    """
    if isinstance(kuzu_id, dict):
        # Kuzu returns IDs as {'offset': int, 'table': int}
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
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <script src="https://d3js.org/d3.v5.min.js"></script>
        <style>
            body, html { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: linear-gradient(90deg, #101010, #1a1a2e); color: white; font-family: 'Inter', sans-serif; }

            svg { width: 100vw; height: 100vh; display: block; }
            .links line { stroke: rgba(255, 255, 255, 0.4); stroke-width: 2px; }
            .links line.weighted { stroke: rgba(255, 215, 0, 0.7); }
            .links line.multi-weighted { stroke: rgba(0, 255, 127, 0.8); }
            .nodes circle { stroke: white; stroke-width: 0.5px; filter: drop-shadow(0 0 5px rgba(255,255,255,0.3)); }
            .node-label { font-size: 8px; font-weight: bold; fill: white; text-anchor: middle; dominant-baseline: middle; font-family: 'Inter', sans-serif; pointer-events: none; }
            .edge-label { font-size: 3px; fill: rgba(255, 255, 255, 0.7); text-anchor: middle; dominant-baseline: middle; font-family: 'Inter', sans-serif; pointer-events: none; }
            
            .tooltip {
                position: absolute;
                text-align: left;
                padding: 8px;
                font-size: 8px;
                background: rgba(0, 0, 0, 0.95);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 4px;
                pointer-events: none;
                opacity: 0;
                transition: opacity 0.2s;
                z-index: 1000;
                max-width: 500px;
                word-wrap: break-word;
                max-height: 80vh;
                overflow-y: auto;
                line-height: 1.4;
            }
            
            /* Larger tooltips when embedded in iframe (Streamlit) */
            body.in-iframe .tooltip {
                padding: 12px;
                font-size: 14px;
            }
            
            .zoom-controls {
                position: fixed;
                top: 20px;
                right: 20px;
                background: rgba(0, 0, 0, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 8px;
                padding: 10px;
                display: flex;
                flex-direction: column;
                gap: 8px;
                z-index: 1000;
            }
            
            .zoom-btn {
                width: 36px;
                height: 36px;
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 4px;
                color: white;
                font-size: 18px;
                font-weight: bold;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: all 0.2s;
            }
            
            .zoom-btn:hover {
                background: rgba(255, 255, 255, 0.2);
                border-color: rgba(255, 255, 255, 0.5);
                transform: scale(1.05);
            }
            
            .zoom-btn:active {
                transform: scale(0.95);
            }
        </style>
    </head>
    <body>
        <svg></svg>
        <div class="tooltip" id="tooltip"></div>
        <div class="zoom-controls">
            <button class="zoom-btn" id="zoom-in" title="Zoom In">+</button>
            <button class="zoom-btn" id="zoom-out" title="Zoom Out">−</button>
            <button class="zoom-btn" id="zoom-reset" title="Fit All" style="font-size: 14px;">⊡</button>
        </div>
        <script>
            // Detect if running in iframe (Streamlit) and adjust styles
            var inIframe = window.self !== window.top;
            if (inIframe) {
                document.body.classList.add('in-iframe');
            }
            
            var nodes = {nodes};
            var links = {links};

            var svg = d3.select("svg"),
                width = window.innerWidth,
                height = window.innerHeight;

            var container = svg.append("g");
            var tooltip = d3.select("#tooltip");

            var simulation = d3.forceSimulation(nodes)
                .force("link", d3.forceLink(links).id(d => d.id).strength(0.1))
                .force("charge", d3.forceManyBody().strength(-275))
                .force("center", d3.forceCenter(width / 2, height / 2))
                .force("x", d3.forceX().strength(0.1).x(width / 2))
                .force("y", d3.forceY().strength(0.1).y(height / 2));

            var link = container.append("g")
                .attr("class", "links")
                .selectAll("line")
                .data(links)
                .enter().append("line")
                .attr("stroke-width", d => {
                    if (d.weight) return Math.max(2, d.weight * 5);
                    if (d.all_weights && Object.keys(d.all_weights).length > 0) {
                        var avgWeight = Object.values(d.all_weights).reduce((a, b) => a + b, 0) / Object.values(d.all_weights).length;
                        return Math.max(2, avgWeight * 5);
                    }
                    return 2;
                })
                .attr("class", d => {
                    if (d.all_weights && Object.keys(d.all_weights).length > 1) return "multi-weighted";
                    if (d.weight || (d.all_weights && Object.keys(d.all_weights).length > 0)) return "weighted";
                    return "";
                })
                .on("mouseover", function(d) {
                    // Create tooltip content for edge
                    var content = "<strong>Edge Information</strong><br/>";
                    content += "Relationship: " + d.relation + "<br/>";

                    // Show all weights
                    if (d.all_weights && Object.keys(d.all_weights).length > 0) {
                        content += "<strong>Weights:</strong><br/>";
                        Object.keys(d.all_weights).forEach(function(weightName) {
                            content += "&nbsp;&nbsp;" + weightName + ": " + d.all_weights[weightName] + "<br/>";
                        });
                    } else if (d.weight !== null && d.weight !== undefined) {
                        content += "Weight: " + d.weight + "<br/>";
                    }

                    if (d.relationship_type) {
                        content += "Type: " + d.relationship_type + "<br/>";
                    }
                    // Add other edge properties
                    if (d.edge_info) {
                        Object.keys(d.edge_info).forEach(function(key) {
                            if (key !== 'weight' && key !== 'weights' && key !== 'relationship_type' && 
                                key !== 'source_node_id' && key !== 'target_node_id' && 
                                key !== 'relationship_name' && key !== 'updated_at' && 
                                !key.startsWith('weight_')) {
                                content += key + ": " + d.edge_info[key] + "<br/>";
                            }
                        });
                    }

                    tooltip.html(content)
                        .style("left", (d3.event.pageX + 10) + "px")
                        .style("top", (d3.event.pageY - 10) + "px")
                        .style("opacity", 1);
                })
                .on("mouseout", function(d) {
                    tooltip.style("opacity", 0);
                });

            var edgeLabels = container.append("g")
                .attr("class", "edge-labels")
                .selectAll("text")
                .data(links)
                .enter().append("text")
                .attr("class", "edge-label")
                .text(d => {
                    var label = d.relation;
                    if (d.all_weights && Object.keys(d.all_weights).length > 1) {
                        // Show count of weights for multiple weights
                        label += " (" + Object.keys(d.all_weights).length + " weights)";
                    } else if (d.weight) {
                        label += " (" + d.weight + ")";
                    } else if (d.all_weights && Object.keys(d.all_weights).length === 1) {
                        var singleWeight = Object.values(d.all_weights)[0];
                        label += " (" + singleWeight + ")";
                    }
                    return label;
                });

            var nodeGroup = container.append("g")
                .attr("class", "nodes")
                .selectAll("g")
                .data(nodes)
                .enter().append("g");

            var node = nodeGroup.append("circle")
                .attr("r", 13)
                .attr("fill", d => d.color)
                .call(d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended));

            nodeGroup.append("text")
                .attr("class", "node-label")
                .attr("dy", 4)
                .attr("text-anchor", "middle")
                .text(d => d.name);

            nodeGroup.on("mouseover", function(d) {
                // Helper function to create tree-like HTML representation
                function createTreeHTML(obj, indent = 0) {
                    var html = "";
                    var indentStr = "&nbsp;".repeat(indent * 4);
                    
                    for (var key in obj) {
                        // Filter out unwanted properties
                        if (key === 'color' || key === 'index' || key === 'id' || 
                            key === 'x' || key === 'y' || key === 'vx' || key === 'vy' || 
                            key === 'fx' || key === 'fy') {
                            continue;
                        }
                        
                        var value = obj[key];
                        
                        if (value === null || value === undefined) {
                            continue;
                        }
                        
                        if (typeof value === 'object' && !Array.isArray(value)) {
                            // Nested object
                            html += indentStr + "<strong>" + key + ":</strong><br/>";
                            html += createTreeHTML(value, indent + 1);
                        } else if (Array.isArray(value)) {
                            // Array
                            html += indentStr + "<strong>" + key + ":</strong> [" + value.length + " items]<br/>";
                            value.forEach(function(item, idx) {
                                if (typeof item === 'object') {
                                    html += indentStr + "&nbsp;&nbsp;[" + idx + "]:<br/>";
                                    html += createTreeHTML(item, indent + 2);
                                } else {
                                    html += indentStr + "&nbsp;&nbsp;[" + idx + "]: " + item + "<br/>";
                                }
                            });
                        } else {
                            // Simple value
                            var displayValue = String(value);
                            if (displayValue.length > 100) {
                                displayValue = displayValue.substring(0, 100) + "...";
                            }
                            html += indentStr + "<strong>" + key + ":</strong> " + displayValue + "<br/>";
                        }
                    }
                    
                    return html;
                }
                
                var titleFontSize = inIframe ? '16px' : '10px';
                var content = "<strong style='font-size: " + titleFontSize + ";'>" + d.type + "</strong><br/><br/>";
                content += createTreeHTML(d);
                
                tooltip.html(content)
                    .style("left", (d3.event.pageX + 10) + "px")
                    .style("top", (d3.event.pageY - 10) + "px")
                    .style("opacity", 1);
            })
            .on("mouseout", function(d) {
                tooltip.style("opacity", 0);
            });
            
            simulation.on("tick", function() {
                link.attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);

                edgeLabels
                    .attr("x", d => (d.source.x + d.target.x) / 2)
                    .attr("y", d => (d.source.y + d.target.y) / 2 - 5);

                nodeGroup.attr("transform", d => "translate(" + d.x + "," + d.y + ")");
            });

            var zoom = d3.zoom().on("zoom", function() {
                container.attr("transform", d3.event.transform);
            });
            
            svg.call(zoom);
            
            // Zoom control buttons
            d3.select("#zoom-in").on("click", function() {
                svg.transition().duration(300).call(zoom.scaleBy, 1.3);
            });
            
            d3.select("#zoom-out").on("click", function() {
                svg.transition().duration(300).call(zoom.scaleBy, 0.7);
            });
            
            d3.select("#zoom-reset").on("click", function() {
                // Calculate bounds of all nodes
                var minX = d3.min(nodes, d => d.x);
                var maxX = d3.max(nodes, d => d.x);
                var minY = d3.min(nodes, d => d.y);
                var maxY = d3.max(nodes, d => d.y);
                
                var graphWidth = maxX - minX;
                var graphHeight = maxY - minY;
                var centerX = (minX + maxX) / 2;
                var centerY = (minY + maxY) / 2;
                
                // Calculate scale to fit with padding
                var padding = 100;
                var scaleX = (width - padding * 2) / graphWidth;
                var scaleY = (height - padding * 2) / graphHeight;
                var scale = Math.min(scaleX, scaleY, 1); // Don't zoom in past 1x
                
                // Calculate translation to center
                var translateX = width / 2 - centerX * scale;
                var translateY = height / 2 - centerY * scale;
                
                svg.transition().duration(750).call(
                    zoom.transform,
                    d3.zoomIdentity.translate(translateX, translateY).scale(scale)
                );
            });

            function dragstarted(d) {
                if (!d3.event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            }

            function dragged(d) {
                d.fx = d3.event.x;
                d.fy = d3.event.y;
            }

            function dragended(d) {
                if (!d3.event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            }

            window.addEventListener("resize", function() {
                width = window.innerWidth;
                height = window.innerHeight;
                svg.attr("width", width).attr("height", height);
                simulation.force("center", d3.forceCenter(width / 2, height / 2));
                simulation.alpha(1).restart();
            });
        </script>
    </body>
    </html>
    """

    html_content = html_template.replace("{nodes}", json.dumps(nodes_list))
    html_content = html_content.replace("{links}", json.dumps(links_list))

    return html_content


def _fetch_graph_data(
    connection: GraphBackend,
    node_configs: list | None = None,
    relation_configs: list | None = None,
    query: str = "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 1000",
) -> tuple[list[NodeRecord], list[RelationshipRecord]]:
    """Fetch all nodes and edges from the graph database via the provided connection/backend.

    Args:
        connection: Object exposing an execute() method (e.g. GraphBackend or kuzu.Connection)
        node_configs: Optional list of node configurations (legacy or new format)
        relation_configs: Optional list of relation configurations (legacy or new format)
        query: Cypher query to fetch relationships and nodes (default: "MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 1000")
               Can be customized to filter by node types, limit results, etc.
               Must return columns named 'n', 'r', 'm' for source node, relationship, target node.

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
        rel_result = connection.execute(query)
        rel_df = rel_result.get_as_df()

        # Process all nodes and relationships from the query result
        for _, row in rel_df.iterrows():
            src_node = row["n"]
            dst_node = row["m"]
            rel_obj = row["r"]

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
                        node_dict[key] = str(val).strip() if str(val).strip() else str(val)
                    elif not key.startswith("_") and val is not None:
                        node_dict[key] = str(val).strip() if str(val).strip() else str(val)

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
            isolated_result = connection.execute(isolated_query)
            isolated_df = isolated_result.get_as_df()

            for _, row in isolated_df.iterrows():
                node_obj = row["n"]
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
                        node_dict[key] = str(val).strip() if str(val).strip() else str(val)
                    elif not key.startswith("_") and val is not None:
                        node_dict[key] = str(val).strip() if str(val).strip() else str(val)

                # Generate display name and metadata
                node_name = _get_node_display_name(node_dict, node_type)
                node_dict["type"] = node_type
                node_dict["name"] = node_name

                # Generate UUID for this node
                node_uuid = str(uuid.uuid4())

                nodes.append(NodeRecord(node_id=node_uuid, properties=node_dict))

        except Exception as e:
            print(f"Warning: Could not fetch isolated nodes: {e}")

    except Exception as e:
        print(f"Error in _fetch_graph_data: {e}")
        return [], []

    return nodes, relationships


def generate_html(
    connection: GraphBackend,
    destination_file_path: str | None = None,
    node_configs: list | None = None,
    relation_configs: list | None = None,
    custom_colors: dict[str, str] | None = None,
    query: str = "MATCH (n)-[r]->(m) RETURN n, r, m",
) -> str:
    """Generate an HTML graph visualization from a graph connection/backend.

    Args:
        connection: Object exposing an execute() method (e.g. GraphBackend or kuzu.Connection) connected to a database that uses
            a schema with Node(id, name, type, properties) and EDGE(relationship_name, properties).
        destination_file_path: Optional path to write the HTML file. If omitted,
            the file will be saved as "graph_visualization.html" in the user's home directory.
        node_configs: Optional list of node configurations (legacy or new format)
        relation_configs: Optional list of relation configurations (legacy or new format)
        custom_colors: Optional mapping of node types to hex color codes
        query: Cypher query to fetch relationships and nodes (default: "MATCH (n)-[r]->(m) RETURN n, r, m")
               Can be customized to filter by node types, limit results, etc.
               Must return columns named 'n', 'r', 'm' for source node, relationship, target node.

    Returns:
        The HTML content as a string.
    """
    nodes_data, relationships_data = _fetch_graph_data(connection, node_configs, relation_configs, query)

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

    html_content = _generate_html_content(nodes_list, links_list)

    if not destination_file_path:
        home_dir = os.path.expanduser("~")
        destination_file_path = os.path.join(home_dir, "graph_visualization.html")

    os.makedirs(os.path.dirname(destination_file_path) or ".", exist_ok=True)
    with open(destination_file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_content
