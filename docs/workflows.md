# Workflows in genai-graph: Knowledge Graph Orchestration

The **Workflow Engine** (from genai-tk) orchestrates knowledge graph creation and
document pre-processing pipelines. Workflow definitions live in
`config/workflows/*.yaml` using the standard workflow DSL — the same DSL genai-tk
uses for RAG ingestion, anonymization, and BAML extraction.

genai-graph itself only ships **domain-agnostic** workflows (document conversion, a
generic single-factory KG build primitive, generic Document/Markdown-tree graphs).
Domain-specific workflows (data paths, concrete graph factories) belong to the
project that imports genai-graph — see `ekg-atos/config/workflows/` for a worked
example (rainbow/RFQ presets, multi-factory KG builds).

> **Note:** genai-graph does not ship `config/workflows/*.yaml` as installed package
> data — it's dev-only config, discovered only when running `cli` from inside the
> genai-graph repo itself. A downstream project (ekg-atos, rfq_pricing) that depends
> on genai-graph as a package must define its own full workflow entries (`run:` /
> `defaults:` / `params:`) for `office2pdf_documents` / `markdownize_documents`, not
> just presets — see `ekg-atos/config/workflows/data_injection.yaml` for the pattern.

**Key benefits:**
- Single CLI entry point: `cli workflow run` for all pipelines
- `--dry-run` to see the full step plan before executing
- `--set KEY=VAL` to override any parameter inline
- Sub-workflow composition: a pipeline step's `run:` can reference another
  workflow name — it's inlined automatically (step IDs prefixed `{step_id}.`)
- A single ordered `--force <stage>` replaces ad-hoc `force` / `force_rebuild` /
  `--remarkdownize` booleans (see [Force stages](#force-stages) below)

---

## Quick Start

### List available workflows

```bash
uv run cli workflow list
# genai-graph itself (generic): document_graph, document_to_kg, markdown_tree,
#                                markdownize_documents, office2pdf_documents
# A downstream project (e.g. ekg-atos) additionally defines: one_rainbow,
#                                rainbow_add_crm, stratnav_subset, full_kg_pipeline, ...
```

### Run a workflow

```bash
# Dry-run: resolve the workflow, show the plan
uv run cli workflow run document_to_kg --dry-run

# Markdownize a directory, zip archive, or file — always one call, no separate
# unzip / office2pdf step (markdownize_flow does that internally)
uv run cli workflow run markdownize_documents --set sources=./RFQ.zip --set md_output_dir=./out/md

# Override values inline
uv run cli workflow run document_to_kg --set sources=./docs --set md_output_dir=./md --set graph='{...}'
```

### Or use `cli doctree build` / `cli kg create` (high-level shorthands)

```bash
# Markdownizes ./RFQ.zip (or a plain folder) then ingests into the Markdown Knowledge Tree
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --profile fast

# Re-run just the Markdown conversion (and everything downstream of it)
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --force md

# Build a KG profile (downstream-project-defined, e.g. ekg-atos's one_rainbow)
cli kg create one_rainbow

# Force rebuild of parquet import caches (and downstream graph/embed stages)
cli kg create one_rainbow --force parquet

# Full clean rebuild, dropping the destination database first
cli kg create one_rainbow --force all
```

---

## Force stages

A single ordered `--force <stage>` replaces the old collection of ad-hoc booleans
(`force`, `force_rebuild`, `--remarkdownize`). **Forcing a stage re-runs it and
everything downstream of it**, since downstream caches are derived from upstream
outputs. Defined in `genai_tk.workflow.force.ForceStage`:

| Stage     | Effect                                                              | Relevant commands |
|-----------|----------------------------------------------------------------------|--------------------|
| `unzip`   | Re-extract `.zip` archives even if already cached                    | `markdownize_documents`, `doctree build` |
| `pdf`     | Re-run Office → PDF conversion                                       | `markdownize_documents`, `doctree build` |
| `md`      | Re-run document → Markdown conversion                                | `markdownize_documents`, `doctree build` |
| `parquet` | Rebuild JSON → parquet import caches                                 | `kg create`, `kg_build` |
| `graph`   | Re-ingest into the graph database (drops the destination store)      | `doctree build`, `kg create`, `kg_build` |
| `embed`   | Recompute embeddings                                                 | `doctree build`, `kg create` |
| `all`     | Force every stage, including dropping the destination store          | all of the above |

`--delete-first` remains a separate, explicit DB-lifecycle flag (independent of
`--force`) — use it when you want a clean database without forcing any upstream
cache to be recomputed.

```bash
# Old (removed): --set delete_first=true / --set force_rebuild=true
# New:
cli kg create one_rainbow --delete-first          # drop DB, reuse all upstream caches
cli kg create one_rainbow --force parquet          # rebuild import caches (implies graph rebuild)
cli kg create one_rainbow --force all              # full clean rebuild
uv run cli workflow run <kg-profile> --force graph # equivalent via the low-level command
```

---

## Architecture

### Config files (genai-graph itself)

| File | Purpose |
|------|---------|
| `config/workflows/generic_workflows.yaml` | `kg_build` (single-factory primitive, hidden), `document_graph`, `markdown_tree` |
| `config/workflows/data_injection.yaml` | `office2pdf_documents`, `markdownize_documents`, `document_to_kg` (generic doc→KG pipeline) |

All are merged into the global config via `:merge:` in `app_conf.yaml`, so all
workflows are available from a single `cli workflow` command.

### The `kg_build_step` function

The generic `kg_build` workflow calls `genai_graph.orchestration.workflow_steps.kg_build_step`, which:

1. Accepts a single `graph` factory config (dict with a `factory` key) + `kg_name`
2. Registers it as a temporary profile in the `KgManager` singleton
3. Clears factory caches (prevents cross-contamination)
4. Runs the full `create_kg_flow()` Prefect pipeline
5. Returns `{config_name, total_processed, total_failed, warnings_count, db_path}`

For multi-factory graphs (several JSON sources merged into one KG), a downstream
project typically calls `kg_create_step` with a `config_name` that resolves to a
richer `KgProfileConfig` (see ekg-atos's `graph_construction.yaml` — `rainbow_add_crm`
combines three factories in one workflow).

---

## Workflow Definitions

### Generic document → Markdown → KG pipeline

```yaml
workflows:
  document_to_kg:
    description: "End-to-end pipeline: documents (zip/dir/files) -> Markdown -> single-factory KG build"
    pipeline:
      - id: markdownize
        run: markdownize_documents
        with:
          sources: "${values.sources}"
          md_output_dir: "${values.md_output_dir}"
          cache_dir: "${values.cache_dir}"
          profile: "${values.profile}"
          force_stage: "${values.force_stage}"
      - id: create_kg
        run: kg_build
        after: [markdownize]
        with:
          graph: "${values.graph}"
          kg_name: "${values.kg_name}"
          force_stage: "${values.force_stage}"
    params:
      sources: {required: true}
      md_output_dir: {required: true}
      graph: {required: true}
```

`markdownize_documents` handles directories, `.zip` archives, and individual files —
raw Office/PDF/image documents *or* pre-existing Markdown (copied through
unchanged) — in one step. There is no separate Office→PDF step in the generic
pipeline; `markdownize_flow` converts Office → PDF → Markdown internally.

### Domain-specific multi-factory workflows (example: ekg-atos)

```yaml
# ekg-atos/config/workflows/graph_construction.yaml
workflows:
  rainbow_add_crm:
    description: "Rainbow reviews + architecture docs + CRM export"
    steps:
      - id: build
        ref: kg_build
        with:
          graphs:
            - factory: ekg_atos.schema.rainbow_review.ReviewedOpportunityGraph
              data_root: '${paths.rainbow_json}'
              include: ['*CNES*']
            - factory: ekg_atos.schema.architecture_doc.ArchitectureDocumentGraph
              data_root: '${paths.add_json}'
            - factory: ekg_atos.schema.crm_export.CrmExtractGraph
              files: ['${paths.ekg_data}/crm_export/report.xlsx']
```

### Sub-workflow composition

A pipeline step's `run:` can reference another workflow name directly — it is
expanded in place, with its steps prefixed `{step_id}.`:

```yaml
workflows:
  full_kg_pipeline:
    pipeline:
      - id: markdownize
        run: markdownize_documents
        with: {...}
      - id: create_kg
        run: rainbow_add_crm      # another workflow, expands to create_kg.build
        after: [markdownize]
```

```bash
cli workflow run full_kg_pipeline/rainbow --dry-run

   Id                  │ Invoke              │ Wait For
───────────────────────┼─────────────────────┼──────────────────
   markdownize.run      │ ...markdownize_flow │ -
   create_kg.build      │ ...kg_build_step    │ markdownize.run
```

---

## CLI Reference

### `cli workflow run` (low-level, full control)

```bash
uv run cli workflow run document_to_kg --dry-run
uv run cli workflow run document_to_kg
uv run cli workflow run document_to_kg --force md
uv run cli workflow run document_to_kg --set export_html=false
```

### `cli kg create` (high-level shorthand)

`cli kg create` wraps `cli workflow run <profile>` with convenient flags. CLI flag
values **always override** workflow YAML defaults.

```bash
cli kg create one_rainbow                       # build (or update) the KG
cli kg create one_rainbow --force parquet       # force rebuild of import caches
cli kg create one_rainbow --force all           # full clean rebuild
cli kg create one_rainbow --no-export-html      # skip HTML export (faster)
cli kg create one_rainbow --no-delete-first     # keep existing database (merge/upsert)
cli kg create one_rainbow --dry-run             # resolve the plan only
cli kg create one_rainbow --set export_html=false
cli kg create --all                             # run every kg_* profile
```

### `cli doctree build` (Markdown Knowledge Tree)

Always markdownizes its sources first, then ingests into the tree database:

```bash
cli doctree build ./docs --db ./data/kg/tree.db
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --profile fast
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --md-output-dir ./out/md --cache-dir ./out/.cache
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --force md      # re-run markdown conversion
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --force graph   # re-ingest, reuse markdown cache
cli doctree build ./RFQ.zip --db ./data/kg/tree.db --delete-first  # drop Section/Chunk tables first
```

---

## Adding a New KG Config

Add a workflow to your project's `config/workflows/*.yaml`:

```yaml
workflows:
  my_new_kg:
    description: "My new knowledge graph"
    run: genai_graph.orchestration.workflow_steps.kg_create_step
    defaults:
      config_name: my_new_kg
      delete_first: false
      export_html: true
```

Then run:

```bash
uv run cli workflow run my_new_kg --dry-run
uv run cli workflow run my_new_kg
# or:
cli kg create my_new_kg
```

---

## Troubleshooting

### Data directory not found

```
WARNING | json_factory.py - Data root directory not found: /path/to/data
```

The path in the graph config (`data_root`) doesn't exist. Check that the relevant
`paths.*` entry in your config points to the correct location and the data
subdirectory exists.

### Database already exists

```bash
cli kg create one_rainbow --delete-first
```

### Force rebuild of parquet cache

```bash
cli kg create one_rainbow --force parquet
```

### Vector index error during merge

`Cannot set property vec ... because it is used in one or more indexes` is a
Ladybug (Kuzu) limitation: HNSW vector-indexed properties cannot be updated in
place via `MERGE … SET`.

**This is handled automatically.** The flow calls `drop_vector_indexes_task` before
the ingestion phase and `create_vector_indexes_task` after. If you still see the
error, use `--delete-first` to start from a completely clean database:

```bash
cli kg create one_rainbow --delete-first
```

