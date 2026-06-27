# GenAI Graph

Hybrid **GraphRAG** framework built on top of [genai-tk](https://github.com/tclatos/genai-tk).

Ingests heterogeneous sources — Neo4j exports, Excel/CSV tables, LLM-extracted documents
(via BAML) — into a unified [Ladybug](https://github.com/LadybugDB/ladybug) graph database,
then exposes the graph through a Streamlit webapp, a CLI, and Cypher-aware agents.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              Data Sources                                    │
│  Neo4j export (JSONL)   Excel / CSV tables   Documents (PDF, PPTX, MD)      │
└─────────────┬──────────────────┬────────────────────────┬────────────────────┘
              │                  │                        │
              ▼                  ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Factory Layer                                      │
│  Neo4jImportFactory   TableBackedFactory   JsonFileBackedFactory            │
│                                             DocumentDirectoryFactory         │
│                                             (BAML extraction → JSON)         │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │  DataFrames of typed Pydantic nodes
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        GraphSchema / KG Manager                              │
│  • Merges schemas from all factories                                         │
│  • Resolves type aliases (e.g. Account → Customer)                           │
│  • Fingerprint-based caching (skip unchanged sources)                        │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │  MERGE statements (no duplicates)
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              Ladybug Graph Database  (Kuzu-compatible Cypher)                │
│  Node tables  ·  Relationship tables  ·  Vector embeddings (optional)        │
└──────────────────────┬──────────────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────────┐
          ▼            ▼                ▼
    CLI (kg/neo4j) Streamlit webapp  Cypher agents
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| **Ladybug** as graph backend | Maintained Kuzu fork — full Cypher compatibility, active development |
| **Pydantic v2** node types | Typed schema, automatic validation, easy serialization to/from Parquet |
| **Factory pattern** | Each data source is an independent unit; compose multiple sources per KG |
| **Parquet cache** | Intermediate DataFrames cached per-source; only changed sources re-run |
| **Workflow DSL** (genai-tk) | YAML-driven pipelines with dry-run, `--set` overrides, sub-workflow composition |
| **BAML** for LLM extraction | Structured extraction with typed schemas, retry logic, streaming |

---

## Where GenAI Graph Fits

GenAI Graph extends genai-tk's **three domains**:

| Domain | GenAI Graph adds |
|--------|-----------------|
| **🧠 Core GenAI** | BAML schemas for structured extraction; `DocumentDirectoryFactory` |
| **🤖 Agents** | Cypher tool integration; `EKGQueryAgent`; graph-aware system prompts |
| **⚙️ Workflows** | `kg_create_step`, multi-source KG pipeline profiles |

For the toolkit foundation see [genai-tk](https://github.com/tclatos/genai-tk).

---

## Quick Start

```bash
# Install
uv sync

# Build a knowledge graph
just kg one_rainbow

# Launch Streamlit webapp
just webapp

# CLI help
uv run cli --help
```

---

## Defining a Knowledge Graph

### 1. Declare node and relationship types

```python
# yourproject/schema/nodes.py
from genai_graph.kg.schema.core import GraphNode, GraphRelation
from pydantic import Field

class Customer(GraphNode):
    name: str                     # primary key (default)
    iris_code: str | None = None
    segment: str | None = None

class Opportunity(GraphNode):
    title: str
    amount: float | None = None

class OpportunityRelation(GraphRelation):
    source_type: type[GraphNode] = Customer
    target_type: type[GraphNode] = Opportunity
    relationship_name: str = "HAS_OPPORTUNITY"
```

### 2. Create a factory for each data source

```python
# yourproject/schema/my_factory.py
from genai_graph.kg.factories import JsonFileBackedFactory
from pydantic import BaseModel

class ReviewedOpportunityGraph(JsonFileBackedFactory, BaseModel):
    """Builds Customer + Opportunity nodes from BAML-extracted JSON files."""

    def get_model_class(self) -> type[BaseModel]:
        return ReviewedOpportunity          # Your BAML-generated Pydantic model

    def get_node_extractors(self):
        return [CustomerExtractor(), OpportunityExtractor()]
```

### 3. Wire up a workflow profile

```yaml
# config/workflows/kg_build.yaml
workflows:
  kg_my_graph:
    steps:
      - id: build
        uses: genai_graph.orchestration.workflow_steps.kg_create_step
        inputs:
          config_name: my_graph
          delete_first: false
          force_rebuild: false
          export_html: true
```

```bash
cli workflow run kg_my_graph --dry-run   # preview
cli workflow run kg_my_graph             # build
```

---

## Document Pipeline

End-to-end: raw documents → queryable knowledge graph

```bash
# 1. Convert PPT/PDF to Markdown
just ppt2pdf   # or: cli workflow run ppt2pdf_documents
just markdownize

# 2. Extract structured data with BAML (LLM)
cli baml extract '${paths.rainbow_md}' '${paths.rainbow_json}' \
  --function ExtractRainbow --include "*.md" --recursive

# 3. Build the knowledge graph
just kg one_rainbow
# or: cli kg create one_rainbow

# 4. View in browser
cli kg view
```

---

## Knowledge Graph CLI

```bash
# Create / rebuild
cli kg create                          # default workflow profile
cli kg create one_rainbow              # specific profile
cli kg create one_rainbow --force      # ignore fingerprint cache
cli kg create one_rainbow --dry-run    # preview steps

# Inspect
cli kg schema                          # node/relationship schema
cli kg info                            # DB stats
cli kg cypher "MATCH (n) RETURN labels(n), count(*)"
cli kg query "Which customers have the most opportunities?"
cli kg view                            # open HTML visualization
```

---

## Neo4j Import

```bash
# Analyze a Neo4j JSONL export
cli neo4j analyze export.jsonl -o schema.cypher

# Create a small test subset
cli neo4j subset export.jsonl subset.jsonl --max-nodes 20 --max-rels 20

# Import into Ladybug
cli neo4j import export.jsonl --db path/to/ladybug_db -f

# Query
cli neo4j query "MATCH (n) RETURN labels(n), count(*)" --db path/to/ladybug_db
```

---

## Fake Data Generation

```bash
# Fake Rainbow JSON for testing
cli baml run FakeRainbowJson \
  -i "Project for ESA; Marc Ferrer as sales lead" \
  --out-dir '${paths.rainbow_json}/fake' --out-file fake_esa_1.json
```

---

## Documentation

| Doc | Topic |
|-----|-------|
| [docs/graph_construction.md](docs/graph_construction.md) | Factories, canonical types, schema merging, CLI reference |
| [docs/workflows.md](docs/workflows.md) | Workflow DSL for KG pipelines; `kg_create_step`; profiles |
| [docs/baml_extraction_guide.md](docs/baml_extraction_guide.md) | BAML schema → JSON → graph factory patterns |
| [docs/primary_key_implementation.md](docs/primary_key_implementation.md) | `key_from` options: field, AUTO_ID, lambda, None-skip |
| [docs/prefect_dag_pipeline.md](docs/prefect_dag_pipeline.md) | Prefect DAG internals, concurrency model |
| [docs/kg_explorer.md](docs/kg_explorer.md) | Streamlit KG Explorer (Cypher UI, Text-to-Cypher) |
| [docs/cache_management.md](docs/cache_management.md) | Parquet cache invalidation |
| [Agents.md](Agents.md) | Agent coding guidelines and architecture invariants |
| [Agents_Skills.md](Agents_Skills.md) | Step-by-step procedures for common codebase tasks |

For BAML fundamentals see [genai-tk BAML docs](https://github.com/tclatos/genai-tk/blob/main/docs/baml.md).

---

## Development

```bash
just install-dev   # install with dev dependencies
just fmt           # format with ruff
just lint          # lint with ruff
just test          # run all tests (219 tests)
just webapp        # launch Streamlit app
just check         # fmt + lint + test
```

