"""HTML template for KG schema visualization.

This template uses D3.js v5 with a force-directed layout optimized for
ontology and graph schema visualization. Nodes are displayed as rounded
rectangles with hover effects, and edges are curved with animated arrows.
The layout is interactive: nodes can be dragged to rearrange the graph.
"""

SCHEMA_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <script src="https://d3js.org/d3.v5.min.js"></script>
  <script>
    window.addEventListener('load', function() {
      if (window.parent !== window) {
        window.parent.postMessage({type: 'streamlit:componentReady'}, '*');
      }
    });
  </script>
  <style>
    body, html {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: radial-gradient(ellipse at center, #1a1f2e 0%, #0b0f17 100%);
      color: #e6e6e6;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }

    svg { width: 100vw; height: 100vh; display: block; }

    /* Link/Edge styles */
    .link-path {
      fill: none;
      stroke-width: 2px;
      cursor: pointer;
      transition: stroke-width 0.2s, opacity 0.2s;
    }

    .link-path:hover {
      stroke-width: 3px;
    }

    .link-path.selected {
      stroke-width: 3.5px;
      filter: drop-shadow(0 0 8px currentColor);
    }

    .link-path.dimmed { opacity: 0.15; }

    .edge-label {
      font-size: 10px;
      font-weight: 500;
      fill: rgba(255,255,255,0.85);
      pointer-events: none;
      text-shadow: 0 1px 3px rgba(0,0,0,0.8);
    }

    .edge-label-bg {
      fill: rgba(11, 15, 23, 0.75);
      rx: 3;
      ry: 3;
    }

    /* Node styles - ontology/class appearance */
    .node-group { cursor: grab; }
    .node-group:active { cursor: grabbing; }

    .node-shape {
      stroke-width: 2px;
      transition: stroke-width 0.2s, filter 0.2s;
    }

    .node-shape:hover {
      stroke-width: 3px;
    }

    .node-shape.selected {
      stroke-width: 3px;
      filter: drop-shadow(0 0 12px currentColor);
    }

    .node-shape.dimmed { opacity: 0.2; }

    .node-label {
      font-size: 12px;
      font-weight: 600;
      fill: rgba(255,255,255,0.95);
      pointer-events: none;
      dominant-baseline: middle;
      text-anchor: middle;
      text-shadow: 0 1px 2px rgba(0,0,0,0.5);
    }

    .node-label.dimmed { opacity: 0.2; }

    .node-icon {
      font-size: 14px;
      fill: rgba(255,255,255,0.7);
      pointer-events: none;
      dominant-baseline: middle;
      text-anchor: middle;
    }

    /* Tooltip */
    .tooltip {
      position: absolute;
      text-align: left;
      padding: 12px 14px;
      font-size: 12px;
      background: rgba(20, 25, 35, 0.96);
      color: white;
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 8px;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.15s;
      z-index: 1000;
      max-width: 500px;
      max-height: 60vh;
      overflow-y: auto;
      line-height: 1.5;
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
      backdrop-filter: blur(8px);
    }

    /* Details Panel */
    .details-panel {
      position: fixed;
      top: 16px;
      left: 16px;
      width: 380px;
      max-height: 80vh;
      background: rgba(20, 25, 35, 0.95);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 12px;
      padding: 16px;
      overflow-y: auto;
      z-index: 1000;
      font-size: 13px;
      line-height: 1.5;
      display: none;
      box-shadow: 0 12px 48px rgba(0,0,0,0.5);
      backdrop-filter: blur(12px);
    }

    .details-panel.visible { display: block; }

    .details-panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      padding-bottom: 10px;
      border-bottom: 1px solid rgba(255,255,255,0.12);
    }

    .details-panel-title {
      font-size: 15px;
      font-weight: 700;
      color: rgba(255,255,255,0.95);
    }

    .details-panel-close {
      background: rgba(255,255,255,0.1);
      border: none;
      color: rgba(255,255,255,0.7);
      font-size: 16px;
      cursor: pointer;
      padding: 4px 8px;
      border-radius: 4px;
      line-height: 1;
      transition: background 0.2s;
    }

    .details-panel-close:hover {
      background: rgba(255,255,255,0.2);
      color: rgba(255,255,255,0.95);
    }

    /* Control panels */
    .zoom-controls {
      position: fixed;
      top: 16px;
      right: 16px;
      background: rgba(20, 25, 35, 0.9);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 10px;
      padding: 8px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      z-index: 1000;
      backdrop-filter: blur(8px);
    }

    .zoom-btn {
      width: 36px;
      height: 36px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 6px;
      color: white;
      font-size: 16px;
      font-weight: bold;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
    }

    .zoom-btn:hover { background: rgba(255, 255, 255, 0.18); }

    .layout-controls {
      position: fixed;
      bottom: 16px;
      right: 16px;
      background: rgba(20, 25, 35, 0.9);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 10px;
      padding: 8px;
      display: flex;
      gap: 6px;
      z-index: 1000;
      backdrop-filter: blur(8px);
    }

    .layout-btn {
      padding: 8px 12px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 6px;
      color: rgba(255,255,255,0.8);
      font-size: 11px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
    }

    .layout-btn:hover { background: rgba(255, 255, 255, 0.18); color: white; }
    .layout-btn.active { background: rgba(100, 149, 237, 0.3); border-color: rgba(100, 149, 237, 0.5); color: white; }

    /* Helper classes */
    .small-muted { color: rgba(255,255,255,0.6); font-size: 11px; }
    code {
      background: rgba(255,255,255,0.08);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-family: 'JetBrains Mono', 'Fira Code', monospace;
    }
    .kv { margin-bottom: 8px; }
    .kv strong { color: rgba(255,255,255,0.85); font-weight: 600; }
    .section-title {
      margin-top: 14px;
      margin-bottom: 8px;
      font-weight: 700;
      color: rgba(255,255,255,0.9);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .field-row {
      margin: 6px 0;
      padding: 6px 10px;
      background: rgba(255,255,255,0.04);
      border-radius: 4px;
      border-left: 3px solid rgba(100, 149, 237, 0.4);
    }
    .field-row .fname { font-weight: 600; color: rgba(255,255,255,0.9); }
    .field-row .ftype { color: rgba(130, 200, 255, 0.95); font-family: 'JetBrains Mono', monospace; font-size: 11px; }
    .field-row .fdesc { color: rgba(255,255,255,0.5); font-size: 11px; margin-top: 2px; }
  </style>
</head>
<body>
  <svg></svg>
  <div class="tooltip" id="tooltip"></div>

  <div class="details-panel" id="details-panel">
    <div class="details-panel-header">
      <div class="details-panel-title" id="details-title">Select an element</div>
      <button class="details-panel-close" id="details-close" title="Close">&times;</button>
    </div>
    <div id="details-content"></div>
  </div>

  <div class="zoom-controls">
    <button class="zoom-btn" id="zoom-in" title="Zoom In">+</button>
    <button class="zoom-btn" id="zoom-out" title="Zoom Out">−</button>
    <button class="zoom-btn" id="zoom-reset" title="Fit All">⊡</button>
  </div>

  <div class="layout-controls">
    <button class="layout-btn active" id="layout-force" title="Force-directed layout">Force</button>
    <button class="layout-btn" id="layout-radial" title="Radial layout">Radial</button>
    <button class="layout-btn" id="layout-freeze" title="Freeze/unfreeze positions">❄ Freeze</button>
  </div>

  <script>
    var schemaData = {schema_data};
    var nodes = schemaData.nodes || [];
    var links = schemaData.links || [];

    var svg = d3.select('svg');
    var width = window.innerWidth;
    var height = window.innerHeight;

    var container = svg.append('g');
    var tooltip = d3.select('#tooltip');

    // Configuration
    var NODE_WIDTH = 140;
    var NODE_HEIGHT = 40;
    var NODE_RX = 8;

    // Color palette for ontology nodes (semantic colors)
    var nodeColorScale = d3.scaleOrdinal()
      .range([
        '#6366f1', // Indigo
        '#8b5cf6', // Violet
        '#a855f7', // Purple
        '#d946ef', // Fuchsia
        '#ec4899', // Pink
        '#f43f5e', // Rose
        '#ef4444', // Red
        '#f97316', // Orange
        '#f59e0b', // Amber
        '#eab308', // Yellow
        '#84cc16', // Lime
        '#22c55e', // Green
        '#10b981', // Emerald
        '#14b8a6', // Teal
        '#06b6d4', // Cyan
        '#0ea5e9', // Sky
        '#3b82f6', // Blue
      ]);

    function nodeColor(id) {
      return nodeColorScale(id);
    }

    function nodeFill(id) {
      var base = d3.color(nodeColor(id));
      base.opacity = 0.15;
      return base.toString();
    }

    // Edge colors based on source node
    function edgeColor(d) {
      var sourceId = typeof d.source === 'object' ? d.source.id : d.source;
      var base = d3.color(nodeColor(sourceId));
      base.opacity = 0.6;
      return base.toString();
    }

    // Arrow markers - create one per color
    var defs = svg.append('defs');

    // Create gradient for edges
    nodes.forEach(function(n) {
      var color = nodeColor(n.id);
      defs.append('marker')
        .attr('id', 'arrow-' + n.id.replace(/[^a-zA-Z0-9]/g, '_'))
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 8)
        .attr('refY', 0)
        .attr('markerWidth', 6)
        .attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-4L8,0L0,4')
        .attr('fill', color);
    });

    // Default arrow marker
    defs.append('marker')
      .attr('id', 'arrow-default')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 8)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-4L8,0L0,4')
      .attr('fill', 'rgba(255,255,255,0.5)');

    function getArrowMarker(d) {
      var sourceId = typeof d.source === 'object' ? d.source.id : d.source;
      var markerId = 'arrow-' + sourceId.replace(/[^a-zA-Z0-9]/g, '_');
      return 'url(#' + markerId + ')';
    }

    function htmlEscape(s) {
      if (s === null || s === undefined) return '';
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function renderNodeDetails(d) {
      var html = '';
      if (d.description) {
        html += '<div class="kv">' + htmlEscape(d.description) + '</div>';
      }
      html += '<div class="kv"><strong>Primary key:</strong> <code>' + htmlEscape(d.primary_key || '') + '</code></div>';
      if (d.name_from) {
        html += '<div class="kv"><strong>Name from:</strong> <code>' + htmlEscape(d.name_from) + '</code></div>';
      }
      if (d.index_fields && d.index_fields.length > 0) {
        html += '<div class="kv"><strong>Indexed:</strong> ' + d.index_fields.map(function(x) { return '<code>' + htmlEscape(x) + '</code>'; }).join(' ') + '</div>';
      }

      if (d.fields && d.fields.length > 0) {
        html += '<div class="section-title">Properties (' + d.fields.length + ')</div>';
        d.fields.forEach(function(f) {
          var row = '<div class="field-row">' +
            '<span class="fname">' + htmlEscape(f.name) + '</span>' +
            ' <span class="small-muted">:</span> ' +
            '<span class="ftype">' + htmlEscape(f.type_human || '') + '</span>';
          if (f.description) {
            row += '<div class="fdesc">' + htmlEscape(f.description) + '</div>';
          }
          row += '</div>';
          html += row;
        });
      }
      return html;
    }

    function renderEdgeDetails(d) {
      var html = '';
      var sourceId = typeof d.source === 'object' ? d.source.id : d.source;
      var targetId = typeof d.target === 'object' ? d.target.id : d.target;
      html += '<div class="kv"><code>' + htmlEscape(sourceId) + '</code> → <code>' + htmlEscape(targetId) + '</code></div>';
      if (d.description) {
        html += '<div class="kv">' + htmlEscape(d.description) + '</div>';
      }
      if (d.properties && d.properties.length > 0) {
        html += '<div class="section-title">Edge Properties</div>';
        d.properties.forEach(function(p) {
          var row = '<div class="field-row">' +
            '<span class="fname">' + htmlEscape(p.name) + '</span>' +
            ' <span class="small-muted">:</span> ' +
            '<span class="ftype">' + htmlEscape(p.type_human || '') + '</span>';
          if (p.description) {
            row += '<div class="fdesc">' + htmlEscape(p.description) + '</div>';
          }
          row += '</div>';
          html += row;
        });
      }
      return html;
    }

    function showTooltip(html, pageX, pageY) {
      tooltip.html(html)
        .style('left', Math.min(pageX + 15, width - 320) + 'px')
        .style('top', Math.max(pageY - 10, 10) + 'px')
        .style('opacity', 1);
    }

    function hideTooltip() {
      tooltip.style('opacity', 0);
    }

    function clearSelection() {
      d3.selectAll('.node-shape').classed('selected', false);
      d3.selectAll('.link-path').classed('selected', false);
      document.getElementById('details-panel').classList.remove('visible');
    }

    function selectNode(d, element) {
      clearSelection();
      d3.select(element).select('.node-shape').classed('selected', true);
      document.getElementById('details-title').innerHTML = '◉ ' + htmlEscape(d.label);
      document.getElementById('details-content').innerHTML = renderNodeDetails(d);
      document.getElementById('details-panel').classList.add('visible');
    }

    function selectEdge(d, element) {
      clearSelection();
      d3.select(element).classed('selected', true);
      document.getElementById('details-title').innerHTML = '→ ' + htmlEscape(d.label);
      document.getElementById('details-content').innerHTML = renderEdgeDetails(d);
      document.getElementById('details-panel').classList.add('visible');
    }

    document.getElementById('details-close').addEventListener('click', clearSelection);

    // Build node index for quick lookup
    var nodeById = {};
    nodes.forEach(function(n, i) {
      nodeById[n.id] = n;
      n.index = i;
    });

    // Calculate intersection point of a line from center (cx, cy) to target (tx, ty)
    // with the boundary of a rectangle centered at (cx, cy) with given width and height
    function rectIntersect(cx, cy, tx, ty, w, h, padding) {
      var dx = tx - cx;
      var dy = ty - cy;

      if (dx === 0 && dy === 0) {
        return { x: cx, y: cy };
      }

      var halfW = w / 2 + padding;
      var halfH = h / 2 + padding;

      // Calculate intersection with each edge and find the closest one
      var tX = halfW / Math.abs(dx || 0.0001);
      var tY = halfH / Math.abs(dy || 0.0001);
      var t = Math.min(tX, tY);

      return {
        x: cx + dx * t,
        y: cy + dy * t
      };
    }

    // Curved path generator for edges - connects to rectangle boundaries
    function linkArc(d) {
      var sx = d.source.x, sy = d.source.y;
      var tx = d.target.x, ty = d.target.y;

      // Self-loop handling
      if (d.source.id === d.target.id) {
        var loopSize = 50;
        return 'M' + sx + ',' + (sy - NODE_HEIGHT/2) +
               ' C' + (sx - loopSize) + ',' + (sy - loopSize - NODE_HEIGHT/2) +
               ' ' + (sx + loopSize) + ',' + (sy - loopSize - NODE_HEIGHT/2) +
               ' ' + sx + ',' + (sy - NODE_HEIGHT/2);
      }

      // Calculate curvature for multiple edges between same nodes
      var curvature = 0;
      var linksBetween = links.filter(function(l) {
        var ls = typeof l.source === 'object' ? l.source.id : l.source;
        var lt = typeof l.target === 'object' ? l.target.id : l.target;
        var ds = typeof d.source === 'object' ? d.source.id : d.source;
        var dt = typeof d.target === 'object' ? d.target.id : d.target;
        return (ls === ds && lt === dt) || (ls === dt && lt === ds);
      });

      if (linksBetween.length > 1) {
        var idx = linksBetween.indexOf(d);
        curvature = (idx - (linksBetween.length - 1) / 2) * 35;
      }

      // For curved edges, we need to offset the control point and recalculate intersections
      var dx = tx - sx;
      var dy = ty - sy;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist === 0) return 'M' + sx + ',' + sy;

      // Perpendicular direction for curvature
      var perpX = -dy / dist;
      var perpY = dx / dist;

      // Control point at midpoint, offset by curvature
      var midX = (sx + tx) / 2 + perpX * curvature;
      var midY = (sy + ty) / 2 + perpY * curvature;

      // Calculate start point: intersection from source center toward control point
      var startPoint = rectIntersect(sx, sy, midX, midY, NODE_WIDTH, NODE_HEIGHT, 2);

      // Calculate end point: intersection from target center toward control point
      // Add extra padding for the arrow marker
      var endPoint = rectIntersect(tx, ty, midX, midY, NODE_WIDTH, NODE_HEIGHT, 8);

      // For straight edges (no curvature), use direct line intersection
      if (curvature === 0) {
        startPoint = rectIntersect(sx, sy, tx, ty, NODE_WIDTH, NODE_HEIGHT, 2);
        endPoint = rectIntersect(tx, ty, sx, sy, NODE_WIDTH, NODE_HEIGHT, 8);
        
        // Straight line
        return 'M' + startPoint.x + ',' + startPoint.y +
               ' L' + endPoint.x + ',' + endPoint.y;
      }

      // Quadratic Bezier curve
      return 'M' + startPoint.x + ',' + startPoint.y +
             ' Q' + midX + ',' + midY +
             ' ' + endPoint.x + ',' + endPoint.y;
    }

    function edgeLabelPos(d) {
      var sx = d.source.x, sy = d.source.y;
      var tx = d.target.x, ty = d.target.y;

      // For curved edges, offset the label position
      var dx = tx - sx;
      var dy = ty - sy;
      var dist = Math.sqrt(dx * dx + dy * dy);
      
      var curvature = 0;
      var linksBetween = links.filter(function(l) {
        var ls = typeof l.source === 'object' ? l.source.id : l.source;
        var lt = typeof l.target === 'object' ? l.target.id : l.target;
        var ds = typeof d.source === 'object' ? d.source.id : d.source;
        var dt = typeof d.target === 'object' ? d.target.id : d.target;
        return (ls === ds && lt === dt) || (ls === dt && lt === ds);
      });

      if (linksBetween.length > 1) {
        var idx = linksBetween.indexOf(d);
        curvature = (idx - (linksBetween.length - 1) / 2) * 35;
      }

      var perpX = dist > 0 ? -dy / dist : 0;
      var perpY = dist > 0 ? dx / dist : 0;

      return {
        x: (sx + tx) / 2 + perpX * curvature * 0.5,
        y: (sy + ty) / 2 + perpY * curvature * 0.5 - 8
      };
    }

    // Create link group (edges first so nodes render on top)
    var linkGroup = container.append('g').attr('class', 'links');
    var link = linkGroup.selectAll('path')
      .data(links)
      .enter().append('path')
      .attr('class', 'link-path')
      .attr('stroke', edgeColor)
      .attr('marker-end', getArrowMarker)
      .on('mouseover', function(d) {
        showTooltip(renderEdgeDetails(d), d3.event.pageX, d3.event.pageY);
      })
      .on('mouseout', hideTooltip)
      .on('click', function(d) {
        d3.event.stopPropagation();
        selectEdge(d, this);
      });

    // Edge labels
    var edgeLabelGroup = container.append('g').attr('class', 'edge-labels');
    var edgeLabels = edgeLabelGroup.selectAll('g')
      .data(links)
      .enter().append('g');

    edgeLabels.append('rect')
      .attr('class', 'edge-label-bg');

    edgeLabels.append('text')
      .attr('class', 'edge-label')
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .text(function(d) { return d.label; });

    // Size background rects to text
    edgeLabels.each(function() {
      var g = d3.select(this);
      var text = g.select('text');
      var bbox = text.node().getBBox();
      g.select('rect')
        .attr('x', bbox.x - 4)
        .attr('y', bbox.y - 2)
        .attr('width', bbox.width + 8)
        .attr('height', bbox.height + 4);
    });

    // Create node groups
    var nodeGroup = container.append('g').attr('class', 'nodes')
      .selectAll('g')
      .data(nodes)
      .enter().append('g')
      .attr('class', 'node-group');

    // Node rectangles (rounded for ontology look)
    nodeGroup.append('rect')
      .attr('class', 'node-shape')
      .attr('rx', NODE_RX)
      .attr('ry', NODE_RX)
      .attr('x', -NODE_WIDTH / 2)
      .attr('y', -NODE_HEIGHT / 2)
      .attr('width', NODE_WIDTH)
      .attr('height', NODE_HEIGHT)
      .attr('fill', function(d) { return nodeFill(d.id); })
      .attr('stroke', function(d) { return nodeColor(d.id); });

    // Node labels
    nodeGroup.append('text')
      .attr('class', 'node-label')
      .attr('dy', '0.35em')
      .text(function(d) { return d.label; });

    // Node interactions
    nodeGroup
      .on('mouseover', function(d) {
        var html = '<strong style="font-size: 14px;">' + htmlEscape(d.label) + '</strong>';
        html += '<div style="margin-top: 8px;">' + renderNodeDetails(d) + '</div>';
        showTooltip(html, d3.event.pageX, d3.event.pageY);
      })
      .on('mouseout', hideTooltip)
      .on('click', function(d) {
        d3.event.stopPropagation();
        selectNode(d, this);
      });

    // ========================================
    // Force simulation (force-directed graph)
    // ========================================
    var simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links)
        .id(function(d) { return d.id; })
        .distance(180)
        .strength(0.7))
      .force('charge', d3.forceManyBody()
        .strength(-800)
        .distanceMax(500))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide()
        .radius(NODE_WIDTH / 2 + 20)
        .strength(0.8))
      .force('x', d3.forceX(width / 2).strength(0.03))
      .force('y', d3.forceY(height / 2).strength(0.03))
      .alphaDecay(0.02)
      .velocityDecay(0.4);

    function ticked() {
      link.attr('d', linkArc);

      edgeLabels.attr('transform', function(d) {
        var pos = edgeLabelPos(d);
        return 'translate(' + pos.x + ',' + pos.y + ')';
      });

      nodeGroup.attr('transform', function(d) {
        return 'translate(' + d.x + ',' + d.y + ')';
      });
    }

    simulation.on('tick', ticked);

    // Drag behavior
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
      if (!isFrozen) {
        d.fx = null;
        d.fy = null;
      }
    }

    nodeGroup.call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended));

    // Zoom behavior
    var zoom = d3.zoom()
      .scaleExtent([0.2, 4])
      .on('zoom', function() {
        container.attr('transform', d3.event.transform);
      });

    svg.call(zoom);

    // Zoom controls
    d3.select('#zoom-in').on('click', function() {
      svg.transition().duration(300).call(zoom.scaleBy, 1.3);
    });

    d3.select('#zoom-out').on('click', function() {
      svg.transition().duration(300).call(zoom.scaleBy, 0.75);
    });

    function fitAll() {
      if (nodes.length === 0) return;

      var minX = d3.min(nodes, function(d) { return d.x; }) - NODE_WIDTH;
      var maxX = d3.max(nodes, function(d) { return d.x; }) + NODE_WIDTH;
      var minY = d3.min(nodes, function(d) { return d.y; }) - NODE_HEIGHT;
      var maxY = d3.max(nodes, function(d) { return d.y; }) + NODE_HEIGHT;

      var graphW = maxX - minX + 100;
      var graphH = maxY - minY + 100;
      var scale = Math.min(width / graphW, height / graphH, 1.5) * 0.9;
      var centerX = (minX + maxX) / 2;
      var centerY = (minY + maxY) / 2;

      svg.transition().duration(500).call(
        zoom.transform,
        d3.zoomIdentity
          .translate(width / 2, height / 2)
          .scale(scale)
          .translate(-centerX, -centerY)
      );
    }

    d3.select('#zoom-reset').on('click', fitAll);

    // Layout controls
    var isFrozen = false;
    var currentLayout = 'force';

    d3.select('#layout-force').on('click', function() {
      if (currentLayout === 'force') return;
      currentLayout = 'force';
      d3.selectAll('.layout-btn').classed('active', false);
      d3.select(this).classed('active', true);

      // Release fixed positions and restart
      nodes.forEach(function(d) { d.fx = null; d.fy = null; });
      simulation.alpha(0.8).restart();
    });

    d3.select('#layout-radial').on('click', function() {
      if (currentLayout === 'radial') return;
      currentLayout = 'radial';
      d3.selectAll('.layout-btn').classed('active', false);
      d3.select(this).classed('active', true);

      // Compute radial positions
      var radius = Math.min(width, height) / 3;
      var angleStep = (2 * Math.PI) / nodes.length;

      nodes.forEach(function(d, i) {
        d.fx = width / 2 + radius * Math.cos(angleStep * i - Math.PI / 2);
        d.fy = height / 2 + radius * Math.sin(angleStep * i - Math.PI / 2);
      });

      simulation.alpha(0.5).restart();
      setTimeout(fitAll, 600);
    });

    d3.select('#layout-freeze').on('click', function() {
      isFrozen = !isFrozen;
      var btn = d3.select(this);
      btn.classed('active', isFrozen);
      btn.text(isFrozen ? '▶ Unfreeze' : '❄ Freeze');

      if (isFrozen) {
        nodes.forEach(function(d) { d.fx = d.x; d.fy = d.y; });
        simulation.stop();
      } else {
        nodes.forEach(function(d) { d.fx = null; d.fy = null; });
        simulation.alpha(0.3).restart();
      }
    });

    // Background click to clear selection
    svg.on('click', function() {
      if (d3.event.target.tagName === 'svg') {
        clearSelection();
      }
    });

    // Handle window resize
    window.addEventListener('resize', function() {
      width = window.innerWidth;
      height = window.innerHeight;
      simulation.force('center', d3.forceCenter(width / 2, height / 2));
      simulation.force('x', d3.forceX(width / 2).strength(0.03));
      simulation.force('y', d3.forceY(height / 2).strength(0.03));
      simulation.alpha(0.3).restart();
    });

    // Initial fit after simulation stabilizes
    setTimeout(fitAll, 1500);
  </script>
</body>
</html>
"""
