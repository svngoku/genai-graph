# Workflows in genai-graph: Knowledge Graph Orchestration

The **Workflow Engine** (from genai-tk) is used in genai-graph to orchestrate knowledge graph
creation pipelines. KG build definitions live in `config/ekg_workflows.yaml` using the standard
workflow DSL — the same DSL used for RAG ingestion, anonymization, and pre-processing pipelines.

**Key benefits over the old `ekg.yaml` + `kg_configs` approach:**
- Single CLI entry point: `cli workflow run` for all pipelines
- `--dry-run` to see the full step plan before executing
- `--set KEY=VAL` to override any parameter inline
- Sub-workflow composition via `invoke: {kind: workflow}` (replaces `import:` in ekg.yaml)
- Step templates eliminate repetition across similar KG configs
- KG configs readable by anyone familiar with the workflow DSL

---

## Quick Start

### List Available KG Workflows

```bash
uv run cli workflow list
# Profiles include: kg_one_rainbow, kg_rainbow_add_crm, kg_stratnav_subset,
#                   kg_stratnav_subset_rainbow_crm, kg_learned,
#                   ppt2pdf_rainbow, markdownize_rainbow, full_rainbow_pipeline
```

### Run a KG Creation Workflow

```bash
# Dry-run: resolve the workflow, show the plan
uv run cli workflow run kg_one_rainbow --dry-run

# Execute: create the knowledge graph
uv run cli workflow run kg_one_rainbow

# Composite: rainbow + CRM + StratNav
uv run cli workflow run kg_stratnav_subset_rainbow_crm --dry-run

# Override values inline
uv run cli workflow run kg_one_rainbow --set delete_first=true
uv run cli workflow run kg_one_rainbow --set force_rebuild=true --set export_html=false

# Full pre-processing + KG pipeline
uv run cli workflow run full_rainbow_pipeline --dry-run
```

### Or use the `cli kg create` shorthand

```bash
# Equivalent to: cli workflow run kg_one_rainbow --set delete_first=true
cli kg create one_rainbow

# Force-rebuild parquet caches
cli kg create one_rainbow --force

# Dry-run
cli kg create one_rainbow --dry-run

# Skip HTML export (faster)
cli kg create one_rainbow --no-export-html

# Override any value
cli kg create one_rainbow --set force_rebuild=true
```

---

## Architecture

### Two Config Files

| File | Purpose |
|------|---------|
| `config/ekg_workflows.yaml` | KG build workflows — step templates, workflows, profiles |
| `config/workflows.yaml` | Pre-processing workflows — ppt2pdf, markdownize, full pipeline |

Both are merged into the global config via `:merge:` in `app_conf.yaml`, so all workflows
are available from a single `cli workflow` command.

### The `kg_build_step` Function

All KG workflows call `genai_graph.orchestration.workflow_steps.kg_build_step`, which:

1. Accepts `graphs` (inline list of factory configs) + `kg_name` (database identity)
2. Registers the inline graph config in the KgManager singleton
3. Clears factory caches (prevents cross-contamination)
4. Runs the full `create_kg_flow()` Prefect pipeline
5. Returns `{kg_name, total_processed, total_failed, warnings_count, db_path}`

Graph factory configurations are defined inline in the workflow YAML `with:` block — no
separate `config_name` reference required.

---

## Workflow Definitions

### Simple KG Workflows (single factory)

```yaml
step_templates:
  kg_build:
    invoke:
      kind: callable
      target: genai_graph.orchestration.workflow_steps.kg_build_step
    with:
      kg_name: "${values.kg_name}"
      delete_first: "${values.delete_first}"
      export_html: "${values.export_html}"
      force_rebuild: "${values.force_rebuild}"

workflows:
  one_rainbow:
    description: "Single CNES TMA VENUS rainbow review"
    defaults:
      delete_first: false
      export_html: true
      force_rebuild: false
    steps:
      - id: build
        ref: kg_build
        with:
          graphs:
            - factory: genai_graph.ekg.schema.rainbow_review.ReviewedOpportunityGraph
              data_root: '${paths.rainbow_json}'
              include: ['*CNES*TMA*VENUS*']
              exclude: [fake/*]
              recursive: true
```

### Multi-Factory Workflows (multiple data sources in one graph)

```yaml
workflows:
  rainbow_add_crm:
    description: "Rainbow reviews + architecture docs + CRM export"
    steps:
      - id: build
        ref: kg_build
        with:
          graphs:
            - factory: genai_graph.ekg.schema.rainbow_review.ReviewedOpportunityGraph
              data_root: '${paths.rainbow_json}'
              include: ['*CNES*']
              recursive: true
            - factory: genai_graph.ekg.schema.architecture_doc.ArchitectureDocumentGraph
              data_root: '${paths.add_json}'
              include: ['*CNES*']
              recursive: true
            - factory: genai_graph.ekg.schema.crm_export.CrmExtractGraph
              files: ['${paths.ekg_data}/crm_export/report.xlsx']
              filter_by_existing:
                node_label: Opportunity
                property: opportunity_id
```

### Composite Workflows using `invoke: {kind: workflow}`

The `invoke: {kind: workflow, target: <name>}` step type replaces the old `import:` mechanism
in `ekg.yaml`.  Each referenced workflow is expanded in place — its steps are prefixed with
`{step_id}.`:

```yaml
workflows:
  # Old ekg.yaml: stratnav_subset_rainbow_crm: { import: [rainbow_add_crm, stratnav_subset] }
  # New workflow DSL:
  stratnav_subset_rainbow_crm:
    description: "Rainbow + CRM + StratNav combined graph"
    steps:
      - id: rainbow_crm
        invoke:
          kind: workflow
          target: rainbow_add_crm       # Expands to: rainbow_crm.build

      - id: stratnav
        invoke:
          kind: workflow
          target: stratnav_subset       # Expands to: stratnav.build
        wait_for: [rainbow_crm]         # Automatically resolves to terminal step: rainbow_crm.build
```

**Dry-run shows the expanded steps:**

```
cli workflow run kg_stratnav_subset_rainbow_crm --dry-run

   Id                  │ Invoke              │ Wait For
───────────────────────┼─────────────────────┼──────────────────
   rainbow_crm.build   │ ...kg_build_step    │ -
   stratnav.build      │ ...kg_build_step    │ rainbow_crm.build
```

Both steps write to the **same database** (`kg_name` from profile) — sequential, additive ingestion.

### Deep Composition (transitive `invoke: {kind: workflow}`)

```yaml
workflows:
  learned_stratnav_subset_rainbow_crm:
    description: "StratNav + Rainbow + CRM with learned similarity relationships"
    steps:
      - id: base
        invoke:
          kind: workflow
          target: stratnav_subset_rainbow_crm  # Expands transitively:
                                               # base.rainbow_crm.build → base.stratnav.build

      - id: similarities
        ref: kg_build
        wait_for: [base]                       # Resolved to: base.stratnav.build (terminal)
        with:
          graphs:
            - factory: genai_graph.ekg.schema.learned_graph.L3TechApproachMatcher
              similarities:
                - relationship: POSSIBLE_OFFERING
                  from: TechnicalApproach.architecture
                  to: L3.description
                  threshold: 0.8
                  top_k: 5
```

**Dry-run output:**

```
   Id                      │ Wait For
───────────────────────────┼────────────────────
   base.rainbow_crm.build  │ -
   base.stratnav.build     │ base.rainbow_crm.build
   similarities            │ base.stratnav.build
```

---

## Profiles

Profiles bind workflows to a `kg_name` and any parameter overrides:

```yaml
workflow_profiles:
  kg_one_rainbow:
    workflow: one_rainbow
    values:
      kg_name: one_rainbow            # Database name in kg_outputs/

  kg_stratnav_subset_rainbow_crm:
    workflow: stratnav_subset_rainbow_crm
    values:
      kg_name: stratnav_subset_rainbow_crm

  kg_learned:
    workflow: learned_stratnav_subset_rainbow_crm
    values:
      kg_name: learned_stratnav_subset_rainbow_crm
```

The `kg_name` determines the output directory: `${paths.kg_outputs}/{kg_name}/{kg_name}-{tag}.db`.

---

## CLI Reference

### `cli workflow run` (low-level, full control)

```bash
# Dry-run any workflow
uv run cli workflow run kg_one_rainbow --dry-run
uv run cli workflow run kg_learned --dry-run
uv run cli workflow run full_rainbow_pipeline --dry-run

# Execute
uv run cli workflow run kg_one_rainbow
uv run cli workflow run kg_rainbow_add_crm
uv run cli workflow run kg_stratnav_subset_rainbow_crm

# Override parameters
uv run cli workflow run kg_one_rainbow --set force_rebuild=true
uv run cli workflow run kg_one_rainbow --set delete_first=true --set force_rebuild=true
uv run cli workflow run kg_one_rainbow --set export_html=false
```

### `cli kg create` (high-level shorthand)

The `cli kg create` command wraps `cli workflow run kg_{name}` with convenient boolean flags.
CLI flag values **always override** workflow YAML defaults.

```bash
# Create with delete_first=true (default), export_html=true (default)
cli kg create one_rainbow

# Force rebuild of parquet caches
cli kg create one_rainbow --force

# Skip HTML export (faster iteration)
cli kg create one_rainbow --no-export-html

# Keep existing database (merge/upsert instead of recreate)
cli kg create one_rainbow --no-delete-first

# Dry-run shows the resolved plan including effective flag values
cli kg create one_rainbow --dry-run

# Combine with raw --set overrides
cli kg create one_rainbow --set force_rebuild=true --no-export-html

# Run all kg_* profiles
cli kg create --all
```

---

## Full Pre-Processing + KG Pipeline

The `full_kg_pipeline` workflow chains document preparation with KG creation:

```
PPT files → ppt_to_pdf → markdownize → create_kg (rainbow_add_crm sub-workflow)
```

```bash
# See the complete 3-step plan
uv run cli workflow run full_rainbow_pipeline --dry-run

# Execute all steps
uv run cli workflow run full_rainbow_pipeline
```

The `create_kg` step uses `invoke: {kind: workflow, target: rainbow_add_crm}` — it expands
and runs all the rainbow + CRM graph factories in the same database.

---

## Adding a New KG Config

Add a workflow and optionally a profile to `config/ekg_workflows.yaml`:

```yaml
workflows:
  my_new_kg:
    description: "My new knowledge graph"
    defaults:
      delete_first: false
      export_html: true
      force_rebuild: false
    steps:
      - id: build
        ref: kg_build
        with:
          graphs:
            - factory: genai_graph.ekg.schema.my_schema.MyGraph
              data_root: '${paths.ekg_data}/my_data/'
              include: ['*.json']
              recursive: true

workflow_profiles:
  kg_my_new:
    workflow: my_new_kg
    values:
      kg_name: my_new_kg
```

Then run:

```bash
uv run cli workflow run kg_my_new --dry-run
uv run cli workflow run kg_my_new
# or:
cli kg create my_new
```

---

## Troubleshooting

### Data directory not found

```
WARNING | json_factory.py - Data root directory not found: /path/to/data
```

The path in the graph config (`data_root`) doesn't exist. Check that `paths.ekg_data` in
your config points to the correct location and the data subdirectory exists.

### Database already exists

Use `--set delete_first=true` (or `cli kg create` which defaults to `delete_first=true`):

```bash
uv run cli workflow run kg_one_rainbow --set delete_first=true
cli kg create one_rainbow   # delete_first=true by default
```

### Force rebuild of parquet cache

```bash
uv run cli workflow run kg_one_rainbow --set force_rebuild=true
cli kg create one_rainbow --force
```

### Vector index error during merge

If you see `Cannot set property vec ... because it is used in one or more indexes`,
the database already has vector indexes from a previous run.  Use `delete_first=true`
(or `cli kg create`) to start from a clean database:

```bash
cli kg create one_rainbow   # always deletes first by default
```
