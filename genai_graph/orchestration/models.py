"""Data models for Prefect orchestration tasks.

These models represent the data structures used during KG creation workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from upath import UPath

from genai_graph.core.graph_documents import DocumentStats
from genai_graph.core.graph_schema import GraphSchema
from genai_graph.core.kg_exports import HtmlExportResult
from genai_graph.core.subgraph_factories import SubgraphFactory


class SubgraphBundle(BaseModel):
    """In-memory representation of a configured subgraph during KG creation."""

    config: dict[str, Any]
    factory: SubgraphFactory
    # Schema type is kept as Any to avoid circular imports in type checkers
    schema_obj: GraphSchema | None = None


class KgRunResult(BaseModel):
    """Aggregated result of a KG creation run."""

    config_name: str
    db_path: Path | UPath
    stats: DocumentStats
    warnings: list[str]
    html_export: HtmlExportResult | None = None
