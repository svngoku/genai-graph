"""Generate HTML visualizations for KG schemas."""

from __future__ import annotations

import json
import os
from typing import Any

from genai_graph.kg.schema.schema_html_template import SCHEMA_HTML_TEMPLATE


def generate_schema_html(schema_data: dict[str, Any], destination_file_path: str | None = None) -> str:
    """Generate a D3.js HTML page to visualize a KG schema.

    Args:
        schema_data: D3-ready schema data as produced by ``build_schema_d3_data``.
        destination_file_path: Optional file path to write the HTML to.

    Returns:
        The HTML content.
    """

    html_content = SCHEMA_HTML_TEMPLATE.replace("{schema_data}", json.dumps(schema_data))

    if destination_file_path is not None:
        os.makedirs(os.path.dirname(destination_file_path) or ".", exist_ok=True)
        with open(destination_file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    return html_content
