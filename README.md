# GenAI Graph

Knowledge graph construction pipeline built on top of [genai-tk](../genai-tk/README.md).

Combines heterogeneous data sources (Neo4j exports, database/Excel files, LLM-extracted
documents via BAML) into a unified [Ladybug](https://github.com/LadybugDB/ladybug) graph
database with Streamlit and CLI interfaces.

---

## Documentation

| Doc | Topic |
|-----|-------|
| [docs/graph_construction.md](docs/graph_construction.md) | Architecture, factories, canonical types, schema merging, CLI reference |
| [docs/baml_extraction_guide.md](docs/baml_extraction_guide.md) | BAML schema → JSON → graph factory patterns |
| [docs/primary_key_implementation.md](docs/primary_key_implementation.md) | `key_from` options: field, AUTO_ID, lambda, None-skip |
| [docs/prefect_dag_pipeline.md](docs/prefect_dag_pipeline.md) | Prefect DAG internals, import ordering, concurrency model |
| [docs/kg_explorer.md](docs/kg_explorer.md) | KG Explorer Streamlit page (Cypher UI, Text-to-Cypher) |
| [docs/cache_management.md](docs/cache_management.md) | Parquet cache invalidation: when and how to clear |
| [Agents_Skills.md](Agents_Skills.md) | Step-by-step procedures for common codebase tasks |

For BAML tool fundamentals (writing `.baml`, generating types, CLI) see the
[genai-tk BAML docs](../genai-tk/docs/baml.md).

---

## Quick Start

```bash
# Install
uv sync

# Run Streamlit app
make webapp

# CLI help
uv run cli --help
```

---

## Document Pipeline

End-to-end pipeline from raw documents to a queryable knowledge graph:

```bash
# 1. Convert PDFs / PowerPoints to Markdown
cli tools ppt2pdf '${paths.rainbow_ppt}' '${paths.rainbow_pdf}' --recursive
cli tools markdownize '${paths.rainbow_pdf}' '${paths.rainbow_md}' --recursive

# 2. Extract structured data with BAML (LLM)
cli baml extract '${paths.rainbow_md}' '${paths.rainbow_json}' \
  --function ExtractRainbow \
  --include "*.md" \
  --recursive

# 3. Build the knowledge graph
cli kg create --kg my_kg

# 4. View in browser
cli kg view
```

---

## Knowledge Graph CLI

```bash
# Create / rebuild
cli kg create                        # Uses kg_config from config
cli kg create --kg <name>            # Specific KG
cli kg create --all-graphs           # All KGs in ekg.yaml
cli kg create --kg <name> --force        # Ignore fingerprint cache
cli kg create --kg <name> --clear-all-caches     # Clear parquet caches first

# Inspect
cli kg schema                        # Node/relationship schema
cli kg info                          # DB stats and subgraph overview
cli kg cypher "MATCH (n) RETURN labels(n), count(*)"
cli kg query "Which customers have the most opportunities?"
cli kg view                          # Open HTML visualization in browser
```

---

## Neo4j Import

```bash
# Analyze a Neo4j JSONL export
cli neo4j analyze path/to/export.jsonl -o path/to/schema.cypher

# Create a small test subset
cli neo4j subset path/to/export.jsonl path/to/subset.jsonl \
  --max-nodes 20 --max-rels 20

# Import into Ladybug
cli neo4j import path/to/export.jsonl --db path/to/ladybug_db -f

# Query the imported database
cli neo4j query "MATCH (n) RETURN labels(n), count(*)" --db path/to/ladybug_db

# Database info
cli neo4j info --db path/to/ladybug_db
```

---

## Fake Data Generation

Generate fake JSON files for testing:

```bash
# Fake Rainbow (opportunity review) JSON
cli baml run FakeRainbowJson \
  -i "Project for ESA; Marc Ferrer as sales lead" \
  --out-dir '${paths.rainbow_json}/fake' \
  --out-file fake_esa_1.json

# Fake Architecture Document JSON
cli baml run FakeArchitectureJson \
  -i "IT platform for CNES with 3-tier, Java based" \
  --out-dir '${paths.add_json}/fake' \
  --out-file fake_add_CNES_1.json
```

---

## Development

```bash
make install-dev   # Install with dev dependencies
make fmt           # Format with ruff
make lint          # Lint with ruff
make test          # Run all tests
make webapp        # Launch Streamlit app
```

