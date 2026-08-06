"""Document Graph — parse Markdown documents into a heading hierarchy.

This package extracts each Markdown document's heading structure as a flat list
of sections (with parent links), so that it can be ingested into the graph as
a navigable ``Document -> Section -> Section`` tree — no chunking or embeddings
required.

Submodules (imported directly to avoid pulling in the factory/backend stack
just for parsing):

- `genai_graph.kg.document_graph.tree_parser` — `parse_markdown_tree()`, `FlatSection`
- `genai_graph.kg.document_graph.ingest` — `ingest_document_graph()`, `drop_document_graph()`
"""

from genai_graph.kg.document_graph.tree_parser import FlatSection, parse_markdown_tree

__all__ = ["FlatSection", "parse_markdown_tree"]
