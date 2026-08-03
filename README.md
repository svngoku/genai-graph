# GenAI Graph

Hybrid **Knowledge Graph** /  **GraphRAG** framework built on top of [genai-tk](https://github.com/tclatos/genai-tk).

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
│  • Auto-deduces field paths from the root Pydantic model                     │
│  • Computes excluded fields (relation endpoints, p_ edge properties)         │
│  • Merges schemas from multiple factories (dedup by node label)              │
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
| **Pydantic v2** node types | Typed schema, automatic validation, easy serialization |
| **Factory pattern** | Each data source is an independent unit; compose multiple sources per KG |
| **`table_name` identity** | Override the Ladybug label independently of the Python class name |
| **Parquet cache** | Intermediate DataFrames cached per-source; only changed sources re-run |
| **Workflow DSL** (genai-tk) | YAML-driven pipelines with dry-run, `--set` overrides, sub-workflow composition |
| **BAML** for LLM extraction | Structured extraction with typed schemas, retry logic, streaming |

---

## Where GenAI Graph Fits

GenAI Graph extends genai-tk's **three domains**:

| Domain | GenAI Graph adds |
|--------|-----------------|
| **🧠 Core GenAI** | BAML schemas for structured extraction; `DocumentDirectoryFactory` |
| **🤖 Agents** | Cypher tool integration; `KGQueryAgent`; graph-aware system prompts |
| **⚙️ Workflows** | `kg_create_step`, multi-source KG pipeline profiles |

For the toolkit foundation see [genai-tk](https://github.com/tclatos/genai-tk).

---

## Quick Start

```bash
# Install
uv sync

# Build a knowledge graph (define your own — see below)
just kg my_graph

# Launch Streamlit webapp
just webapp

# CLI help
uv run cli --help
```

---

## Defining a Knowledge Graph

### 1. Define your domain models (plain Pydantic)

```python
from pydantic import BaseModel, Field

class Company(BaseModel):
    name: str
    sector: str | None = None

class Person(BaseModel):
    name: str
    role: str | None = None

class Project(BaseModel):
    title: str
    client: Company           # → FOR_CLIENT relation auto-detected
    team: list[Person] = Field(default_factory=list)  # → HAS_MEMBER
```

### 2. Declare the schema

```python
from genai_graph.kg.schema import GraphNode, GraphRelation, GraphSchema

company_node = GraphNode(node_class=Company, name_from="name", key_from="name")
person_node  = GraphNode(node_class=Person,  name_from="name", key_from="name")
project_node = GraphNode(node_class=Project, name_from="title", key_from="title")

schema = GraphSchema(
    root_model_class=Project,
    nodes=[project_node, company_node, person_node],
    relations=[
        GraphRelation(from_node=project_node, to_node=company_node, name="FOR_CLIENT"),
        GraphRelation(from_node=project_node, to_node=person_node,  name="HAS_MEMBER"),
    ],
)
```

`GraphSchema` auto-deduces field paths and excluded fields at construction time.
Any label collisions or orphaned nodes produce `UserWarning` for early feedback.

### 3. Ingest and query

```python
from genai_graph.kg.ingest import create_graph, restart_database

backend = restart_database()   # in-memory; or create_backend_from_config(...)

project = Project(
    title="Cloud Migration",
    client=Company(name="Acme Corp", sector="Retail"),
    team=[Person(name="Alice", role="Lead")],
)
create_graph(backend, project, schema)

df = backend.execute_get_as_df(
    "MATCH (p:Project)-[:FOR_CLIENT]->(c:Company) RETURN p.title, c.name"
)
print(df)
```

### 4. Visualise the schema

```python
from genai_graph.kg.schema import ResolvedSchema

resolved = ResolvedSchema.from_graph_schema(schema)
print(resolved.to_markdown())    # table summary
resolved.to_html("schema.html") # interactive D3 diagram
```

### 5. Create a factory for persistent ingestion

```python
from genai_graph.kg.factories import JsonFileBackedFactory

class ProjectGraph(JsonFileBackedFactory):
    schema = GraphSchema(root_model_class=Project, nodes=[...], relations=[...])
    source_model = Project
    source_dir = "data/projects"   # directory of *.json files
```

### 6. Wire up a workflow profile

```yaml
# config/workflows/graph_construction.yaml
workflows:
  kg_build:
    steps:
      - id: build
        uses: genai_graph.orchestration.workflow_steps.kg_create_step
        inputs:
          config_name: my_graph
```

```bash
cli workflow run kg_build --dry-run   # preview
cli workflow run kg_build             # build
```

---

## Document Pipeline

End-to-end: raw documents → queryable knowledge graph

```bash
# 1. Convert PPT/PDF to Markdown
just office2pdf   # or: cli workflow run office2pdf_documents
just markdownize

# 2. Extract structured data with BAML (LLM)
cli baml extract '${paths.docs_md}' '${paths.docs_json}' \
  --function ExtractMyEntities --include "*.md" --recursive

# 3. Build the knowledge graph
just kg my_graph   # or: cli kg create my_graph

# 4. View in browser
cli kg view
```

---

## Knowledge Graph CLI

```bash
# Create / rebuild
cli kg create                          # default workflow profile
cli kg create my_graph                 # specific profile
cli kg create my_graph --force         # ignore fingerprint cache
cli kg create my_graph --dry-run       # preview steps

# Inspect
cli kg schema                          # node/relationship schema
cli kg info                            # DB stats
cli kg cypher "MATCH (n) RETURN labels(n), count(*)"
cli kg query "Which companies have the most projects?"
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

## Documentation

| Doc | Topic |
|-----|-------|
| [docs/graph-definition-guide.md](docs/graph-definition-guide.md) | **Start here** — 5-minute guide: models → schema → ingest → query |
| [docs/graph-authoring-patterns.md](docs/graph-authoring-patterns.md) | Pattern catalog: JSON, tables, Neo4j, documents, similarity, canonical reuse |
| [docs/schema-compilation.md](docs/schema-compilation.md) | Field-path deduction, `table_name`, exclusion mechanics, compiler functions |
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

## Notebooks

Interactive examples in `notebooks/`:

| Notebook | What it shows |
|----------|---------------|
| [01_define_graph_from_scratch.ipynb](notebooks/01_define_graph_from_scratch.ipynb) | Full pipeline: models → schema → ingest → Cypher → HTML viz |
| [cypher_examples.ipynb](notebooks/cypher_examples.ipynb) | Cypher patterns: basic, traversal, aggregation, filtering |
| [document_graph_demo.ipynb](notebooks/document_graph_demo.ipynb) | Document+Chunk ingestion from a markdown directory |
| [cypher_query_development.ipynb](notebooks/cypher_query_development.ipynb) | Interactive Cypher development helper |

```bash
just test-notebooks   # run all notebooks as tests
```

---

## Development

```bash
just install-dev   # install with dev dependencies
just fmt           # format with ruff
just lint          # lint with ruff
just test          # run all tests
just test-notebooks  # run notebooks as tests
just webapp        # launch Streamlit app
just check         # fmt + lint + test
```
