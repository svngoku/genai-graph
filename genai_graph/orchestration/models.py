"""Data models for Prefect orchestration tasks.

These models represent the data structures used during KG creation workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from upath import UPath

from genai_graph.kg.export import HtmlExportResult
from genai_graph.kg.factories import KgFactory
from genai_graph.kg.ingest import DocumentStats
from genai_graph.kg.schema import GraphSchema


class GraphBundle(BaseModel):
    """In-memory representation of a configured graph during KG creation."""

    config: dict[str, Any]
    factory: KgFactory
    schema_obj: GraphSchema | None = None


class KgRunResult(BaseModel):
    """Aggregated result of a KG creation run."""

    config_name: str
    db_path: Path | UPath
    stats: DocumentStats
    warnings: list[str]
    html_export: HtmlExportResult | None = None
