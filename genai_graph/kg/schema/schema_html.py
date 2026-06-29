"""Generate HTML visualizations for KG schemas."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from genai_graph.kg.schema.schema_html_template import SCHEMA_HTML_TEMPLATE

_D3_INLINE_TAG = '  <script src="https://d3js.org/d3.v5.min.js"></script>'
_D3_BUNDLE_PATH = Path(__file__).parent / "d3.v5.min.js"


def _d3_script_tag() -> str:
    """Return an inline <script> tag with D3 v5 bundled, falling back to CDN."""
    if _D3_BUNDLE_PATH.exists():
        return f"<script>{_D3_BUNDLE_PATH.read_text(encoding='utf-8')}</script>"
    return _D3_INLINE_TAG


def generate_schema_html(schema_data: dict[str, Any], destination_file_path: str | None = None) -> str:
    """Generate a D3.js HTML page to visualize a KG schema.

    Args:
        schema_data: D3-ready schema data as produced by ``build_schema_d3_data``.
        destination_file_path: Optional file path to write the HTML to.

    Returns:
        The HTML content.
    """

    html_content = SCHEMA_HTML_TEMPLATE.replace("{schema_data}", json.dumps(schema_data))
    html_content = html_content.replace(_D3_INLINE_TAG, _d3_script_tag())

    if destination_file_path is not None:
        os.makedirs(os.path.dirname(destination_file_path) or ".", exist_ok=True)
        with open(destination_file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    return html_content
