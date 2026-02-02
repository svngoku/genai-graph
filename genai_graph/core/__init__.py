"""Core module - CLI commands for KG operations.

The main KG functionality has been moved to genai_graph.kg subpackages.
Import from there for:
- genai_graph.kg.manager: KgManager and configuration
- genai_graph.kg.backend: KgBackend and database backends
- genai_graph.kg.schema: GraphSchema, GraphNode, GraphRelation, GraphRegistry
- genai_graph.kg.factories: KgFactory and implementations
- genai_graph.kg.ingest: Graph extraction and merge utilities
- genai_graph.kg.export: HTML and artifact export
- genai_graph.kg.query: Text-to-Cypher and agent utilities
"""
