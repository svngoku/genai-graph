# Document Graph

The **Document Graph** is genai-graph's generic representation of a corpus of source
documents: which folder they came from, their file metadata, and — for Markdown —
their heading hierarchy. It is the provenance layer that every entity-extraction
factory (BAML, CRM tables, Neo4j imports, …) attaches to, and it is also useful on
its own as a **vectorless, agentic RAG** substrate: an agent walks a document's table
of contents and reads section text directly, no embeddings required.

## Schema

```
Folder ──CONTAINS──▶ Document ──HAS_SECTION──▶ MarkdownSection ──HAS_SUBSECTION──▶ MarkdownSection ──HAS_SUBSECTION──▶ …
```

| Node | Key | Notes |
|------|-----|-------|
| `Folder` | `folder_id` | A directory, a `.zip` archive, or a single file's parent — the base location documents are read from. |
| `Document` | `content_hash` (xxHash of the raw file bytes) | Provenance anchor for everything derived from a file. Carries `filename`, `relative_path`, `path`, `mime_type`, plus `markdown_hash`, `token_count`, `section_count` for its Markdown rendering. |
| `MarkdownSection` | `section_id` = `{markdown_hash}::{sequence}` | One heading-delimited section (heading line + body up to the next heading of any level). Every document has at least one section — a synthetic level-0 root section captures a heading-less document or its preamble. |

Sections form a flat table with an explicit `parent_section_id` — the hierarchy is
materialized entirely as `HAS_SUBSECTION` edges, so an agent (or a Cypher query)
walks the tree with ordinary graph traversals. A section's `text` is its own content
only (non-overlapping), so concatenating every section of a document in `sequence`
order reconstructs the original Markdown exactly.

**Identity and dedup:** `Document` and `MarkdownSection` are both keyed by content
hash, so re-ingesting unchanged files is a no-op MERGE. When an entity-extraction
factory (see below) also produces a `Document` node for the same file, it MERGEs
into the *same* node — a document's provenance and its extracted entities share one
graph node.

There are no `Chunk` nodes in the Document Graph — no chunking or embeddings are
produced. Chunking/embedding based RAG is a separate, unrelated path (see
[`DocumentDirectoryFactory`](#documentdirectoryfactory) below); the Document Graph is
for heading-based, vectorless navigation.

## Factories

| Factory | Produces | Use it when |
|---------|----------|--------------|
| `genai_graph.kg.factories.document_graph_factory.DocumentGraphFactory` | `Folder → Document → MarkdownSection` | You want the navigable heading hierarchy over a Markdown corpus. |
| `genai_graph.kg.factories.document_factory.DocumentDirectoryFactory` | Plain `Document` nodes | You just need file-level provenance (no sections), e.g. as a base class for a custom RAG pipeline. |
| `genai_graph.kg.factories.markdown_baml_factory.MarkdownBamlFactory` | Your entity nodes + a provenance `Document` node (`MENTIONS` relation) | You want to extract structured entities (Opportunity, Risk, Person, …) from Markdown via a BAML function, run inline (no separate `cli baml extract` step). |
| `genai_graph.kg.factories.json_factory.JsonFileBackedFactory` | Your entity nodes + a provenance `Document` node | Your entities are *already* extracted as JSON files (e.g. produced by a prior `cli baml extract` run), one directory per model name. |

### `DocumentGraphFactory`

```python
from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.document_graph.ingest import ingest_document_graph
from genai_graph.kg.factories.document_graph_factory import DocumentGraphFactory

backend = KuzuBackend()
backend.connect("./data/kg/tree.db")

factory = DocumentGraphFactory(sources=["./docs"], include=["*.md"])
result = ingest_document_graph(backend, factory)
```

`sources` accepts a mix of directories, individual files, and `.zip` archives — each
becomes a `Folder`. `ingest_document_graph` builds the schema, parses each file's
heading hierarchy, and MERGEs everything in one call; pass `force=True` to rebuild
sections for documents already in the graph (e.g. after a heading edit).

### `MarkdownBamlFactory` (inline BAML entity extraction)

Subclass it to extract structured entities directly from Markdown, without a
separate JSON-extraction step. Extracted results are cached as JSON build artifacts
(keyed by mtime) so re-runs are cheap:

```python
from genai_graph.kg.factories import MarkdownBamlFactory
from genai_graph.kg.schema import GraphSchema
from pydantic import BaseModel

class MyEntityGraph(MarkdownBamlFactory):
    def build_schema(self) -> GraphSchema:
        nodes, relations = self.get_document_schema_elements(MyEntityNode)
        return GraphSchema(root_model_class=MyEntity, nodes=[MyEntityNode, *nodes], relations=relations)

    def extract_from_markdown(self, md_text: str) -> BaseModel:
        from genai_tk.extra.structured.baml_processor import BamlStructuredProcessor
        processor = BamlStructuredProcessor(model_cls=MyEntity, function_name="ExtractMyEntity", kvstore_id="")
        return processor.analyze_document("doc", md_text)
```

`md_root` selects the Markdown directory to scan; `json_cache_root` (optional)
enables the JSON extraction cache. Because `MarkdownBamlFactory` mixes in
`DocumentMixin`, calling `get_document_schema_elements(root_node)` in `build_schema()`
adds the provenance `Document` node and a `MENTIONS` relation from `Document` to your
entity's root node — the same `Document` node the `DocumentGraphFactory` produces for
the same file, so both MERGE together.

## Building a KG from documents end to end

`docgraph_build_step` (`genai_graph.orchestration.workflow_steps.docgraph_build_step`)
ties markdownization, entity extraction, and the document graph together into **one**
database:

1. Optionally markdownize `sources` (PPT/PDF/… or pre-existing Markdown — already-
   Markdown files are copied through unchanged) via `genai_tk.workflow.markdownize.markdownize_flow`.
2. Run each configured entity `factory` (e.g. a `MarkdownBamlFactory` subclass) into
   a single KG named `kg_name`.
3. Optionally ingest the `Folder → Document → MarkdownSection` graph over the same
   Markdown into the *same* database.

This is what backs both `cli docgraph run` (ad-hoc sources) and a project's own named
workflow profiles consumed via `cli kg create <name>` — see
[docs/workflows.md](workflows.md) for the full workflow-engine reference and force-stage
semantics.

## CLI

`cli docgraph` is the generic, document-focused command group:

```bash
# Markdownize + build the document graph directly on a Ladybug DB
cli docgraph build ./docs --db ./data/kg/tree.db
cli docgraph build ./RFQ.zip --db ./data/kg/tree.db --profile fast

# Run a project-defined docgraph_build workflow (markdownize + entity factories + document graph)
cli docgraph run --workflow rainbow_extract -s "some_file.pptx"

# Navigate an ingested graph
cli docgraph list --db ./data/kg/tree.db
cli docgraph toc <filename-or-hash> --db ./data/kg/tree.db
cli docgraph cat <filename-or-hash> --db ./data/kg/tree.db
cli docgraph search "keyword" --db ./data/kg/tree.db
cli docgraph tui --db ./data/kg/tree.db
```

`cli kg create <name>` runs the same workflow engine against a **predefined** set of
documents (a named workflow profile in a project's `config/workflows/*.yaml`), while
`cli docgraph run` targets **ad-hoc** sources passed with `-s`/`--source`. Both are
thin CLI layers over `resolve_workflow_invocation` + `execute_workflow`.

## Querying

`genai_graph.kg.query.document_graph_tools` provides read-only helpers used by the
CLI, an agent's tools, and the Textual TUI:

```python
from genai_graph.kg.query.document_graph_tools import (
    list_documents, get_document_toc, get_section_content, reconstruct_document, search_sections,
)
```

Documents can be addressed by content hash (full or prefix), `markdown_hash`,
filename, or source path. `create_document_graph_tools(db_path)` wraps these as
LangChain `BaseTool`s for an agent.

## Related docs

- [docs/workflows.md](workflows.md) — the workflow DSL, force stages, `cli docgraph`/`cli kg create` CLI reference
- [docs/graph-definition-guide.md](graph-definition-guide.md) — defining a `GraphSchema` from Pydantic models (entity extraction, not the document graph itself)
- [docs/graph-authoring-patterns.md](graph-authoring-patterns.md) — pattern catalog including document ingestion
- [docs/baml_extraction_guide.md](baml_extraction_guide.md) — BAML schema → entity graph factory patterns
