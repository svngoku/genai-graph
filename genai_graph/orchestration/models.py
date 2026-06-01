"""Data models for Prefect orchestration tasks.

These models represent the data structures used during KG creation workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from genai_graph.kg.export import HtmlExportResult
from genai_graph.kg.factories import KgFactory
from genai_graph.kg.ingest import DocumentStats
from genai_graph.kg.schema import GraphSchema


class GraphFilter(BaseModel):
    """Describes how to filter factory keys against nodes already in the graph.

    When set on a graph config entry via ``filter_by_existing``, only factory
    keys whose values match existing node PKs are ingested.  This allows
    deferred, filtered ingestion for large data sources (e.g. CRM exports).
    """

    node_label: str
    """Kuzu node table name to match against (e.g. ``'Opportunity'``)."""
    property: str
    """Node property holding the primary key value (e.g. ``'opportunity_id'``)."""


class GraphBundle(BaseModel):
    """In-memory representation of a configured graph during KG creation."""

    config: dict[str, Any]
    factory: KgFactory
    schema_obj: GraphSchema | None = None


class WarningsCollector(BaseModel):
    """Serializable warning accumulator that can be passed across Prefect tasks.

    Unlike the singleton ``KgManager.warnings`` list, this model can be
    returned from tasks/subflows and merged in the parent flow, enabling
    cross-import warning aggregation.
    """

    warnings: list[str] = Field(default_factory=list)
    source: str = ""
    """Label identifying the origin (e.g. config name, import name)."""

    def add(self, message: str) -> None:
        """Append a warning if not already recorded."""
        if message and message not in self.warnings:
            self.warnings.append(message)

    def merge(self, other: WarningsCollector) -> None:
        """Absorb warnings from *other* (with source prefix)."""
        prefix = f"[{other.source}] " if other.source else ""
        for w in other.warnings:
            tagged = f"{prefix}{w}"
            if tagged not in self.warnings:
                self.warnings.append(tagged)

    @property
    def count(self) -> int:
        return len(self.warnings)


class BundleResult(BaseModel):
    """Result of processing a single graph bundle (schema + ingestion)."""

    factory_path: str = ""
    stats: DocumentStats = Field(default_factory=DocumentStats)
    warnings: WarningsCollector = Field(default_factory=WarningsCollector)


class ImportResult(BaseModel):
    """Result of importing a single KG dependency from parquet."""

    config_name: str
    nodes_imported: int = 0
    rels_imported: int = 0
    warnings: WarningsCollector = Field(default_factory=WarningsCollector)
    skipped: bool = False
    """True when a valid parquet cache was reused."""


class KgRunResult(BaseModel):
    """Aggregated result of a KG creation run."""

    config_name: str
    db_path: Path
    stats: DocumentStats
    warnings: list[str]
    import_results: list[ImportResult] = Field(default_factory=list)
    bundle_results: list[BundleResult] = Field(default_factory=list)
    html_export: HtmlExportResult | None = None
