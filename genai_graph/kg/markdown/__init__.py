"""Markdown Knowledge Tree — parse Markdown documents into a heading hierarchy.

This package extracts each Markdown document's heading structure as a flat list
of sections (with parent links), so that it can be ingested into the graph as
a navigable ``Document -> Section -> Section`` tree — no chunking or embeddings
required.

Submodules (imported directly to avoid pulling in the factory/backend stack
just for parsing):

- `genai_graph.kg.markdown.tree_parser` — `parse_markdown_tree()`, `FlatSection`
- `genai_graph.kg.markdown.ingest` — `ingest_markdown_tree()`, `drop_markdown_tree()`
"""

from genai_graph.kg.markdown.tree_parser import FlatSection, parse_markdown_tree

__all__ = ["FlatSection", "parse_markdown_tree"]
