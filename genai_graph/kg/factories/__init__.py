"""Data source factories for Knowledge Graph construction.

This package provides factories that load data from various sources:
- KgFactory: Abstract base class for all factories
- JsonFileBackedFactory: Load data from JSON files
- TableBackedFactory: Load data from SQL database tables
- Neo4jFactory: Load data from Neo4j JSONL exports
"""

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.factories.json_factory import JsonFileBackedFactory
from genai_graph.kg.factories.neo4j_factory import Neo4jFactory, Neo4jImportFactory
from genai_graph.kg.factories.table_factory import TableBackedFactory

__all__ = [
    "KgFactory",
    "JsonFileBackedFactory",
    "TableBackedFactory",
    "Neo4jFactory",
    "Neo4jImportFactory",
]
