# Workflows in genai-graph: Knowledge Graph Orchestration

The **Workflow Engine** (from genai-tk) is used in genai-graph to orchestrate knowledge graph
creation pipelines. KG build definitions live in `config/ekg_workflows.yaml` using the standard
workflow DSL — the same DSL used for RAG ingestion, anonymization, and pre-processing pipelines.

**Key benefits over the old `ekg.yaml` + `kg_configs` approach:**
- Single CLI entry point: `cli workflow run` for all pipelines
- `--dry-run` to see the full step plan before executing
- `--set KEY=VAL` to override any parameter inline
- Sub-workflow composition via `uses_workflow:` (replaces `import:` in ekg.yaml)
- Step templates eliminate repetition across similar KG configs
- KG configs readable by anyone familiar with the workflow DSL

---

## Quick Start

### List Available KG Workflows

```bash
uv run cli workflow list
# Output:
#   Workflows: one_rainbow, rainbow_add_crm, stratnav_subset,
#              stratnav_subset_rainbow_crm, learned_stratnav_subset_rainbow_crm,
#              crm_export, one_rainbow_with_db, several_rainbow,
#              ppt2pdf_documents, markdownize_documents, full_kg_pipeline
#
#   Profiles: kg_one_rainbow, kg_rainbow_add_crm, kg_stratnav_subset,
#             kg_stratnav_subset_rainbow_crm, kg_learned,
#             ppt2pdf_rainbow, markdownize_rainbow, full_rainbow_pipeline
```

### Run a KG Creation Workflow

```bash
# Dry-run: resolve the workflow, show the plan
uv run cli workflow run kg_one_rainbow --dry-run

# Execute: create the knowledge graph
uv run cli workflow run kg_one_rainbow

# Composite: rainbow + CRM + StratNav (3 sub-steps)
uv run cli workflow run kg_stratnav_subset_rainbow_crm --dry-run

# Override values inline
uv run cli workflow run kg_one_rainbow --set delete_first=true
uv run cli workflow run kg_one_rainbow --set force_rebuild=true --set export_html=false

# Full pre-processing + KG pipeline
uv run cli workflow run full_rainbow_pipeline --dry-run
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

This replaces the old `kg_create_step` (which required a `config_name` reference to `kg_configs`
in `ekg.yaml`). Graph factory configurations are now defined inline in the workflow YAML.

---

## Workflow Definitions

### Simple KG Workflows (single factory)

```yaml
step_templates:
  kg_build:
    uses: genai_graph.orchestration.workflow_steps.kg_build_step
    concurrency: serial
    params:
      kg_name: "${profile.kg_name}"
      delete_first: "${profile.delete_first}"
      export_html: "${profile.export_html}"
      force_rebuild: "${profile.force_rebuild}"

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
        inputs:
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
        inputs:
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
              filter_by_existing:              # Only import rows that match existing nodes
                node_label: Opportunity
                property: opportunity_id
```

### Composite Workflows using `uses_workflow`

The `uses_workflow:` step type replaces the old `import:` mechanism in `ekg.yaml`.
Each referenced workflow is expanded in place — its steps are prefixed with `{step_id}.`:

```yaml
workflows:
  # Old ekg.yaml: stratnav_subset_rainbow_crm: { import: [rainbow_add_crm, stratnav_subset] }
  # New workflow DSL:
  stratnav_subset_rainbow_crm:
    description: "Rainbow + CRM + StratNav combined graph"
    steps:
      - id: rainbow_crm
        uses_workflow: rainbow_add_crm       # Expands to: rainbow_crm.build

      - id: stratnav
        uses_workflow: stratnav_subset       # Expands to: stratnav.build
        needs: [rainbow_crm]                 # Automatically resolves to terminal step: rainbow_crm.build
```

**Dry-run shows the expanded steps:**

```
cli workflow run kg_stratnav_subset_rainbow_crm --dry-run

   Id                  │ Uses                           │ Needs
───────────────────────┼────────────────────────────────┼──────────────────
   rainbow_crm.build   │ ...kg_build_step               │ -
   stratnav.build      │ ...kg_build_step               │ rainbow_crm.build
```

Both steps write to the **same database** (`kg_name` from profile) — sequential, additive ingestion.

### Deep Composition (transitive `uses_workflow`)

```yaml
workflows:
  learned_stratnav_subset_rainbow_crm:
    description: "StratNav + Rainbow + CRM with learned similarity relationships"
    steps:
      - id: base
        uses_workflow: stratnav_subset_rainbow_crm  # Expands transitively:
                                                     # base.rainbow_crm.build → base.stratnav.build

      - id: similarities
        ref: kg_build
        needs: [base]                               # Resolved to: base.stratnav.build (terminal)
        inputs:
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
   Id                      │ Needs
───────────────────────────┼────────────────────
   base.rainbow_crm.build  │ -
   base.stratnav.build     │ base.rainbow_crm.build
   similarities            │ base.stratnav.build
```

---

## Profiles

Profiles bind workflows to concrete `kg_name` and parameter overrides:

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

### Dry-run any workflow

```bash
uv run cli workflow run kg_one_rainbow --dry-run
uv run cli workflow run kg_learned --dry-run
uv run cli workflow run full_rainbow_pipeline --dry-run
```

### Execute

```bash
uv run cli workflow run kg_one_rainbow
uv run cli workflow run kg_rainbow_add_crm
uv run cli workflow run kg_stratnav_subset_rainbow_crm
uv run cli workflow run kg_learned
```

### Override parameters

```bash
# Force-rebuild parquet caches
uv run cli workflow run kg_one_rainbow --set force_rebuild=true

# Fresh database + rebuild
uv run cli workflow run kg_one_rainbow --set delete_first=true --set force_rebuild=true

# Disable HTML export (faster)
uv run cli workflow run kg_one_rainbow --set export_html=false
```

---

## Full Pre-Processing + KG Pipeline

The `full_kg_pipeline` workflow chains document preparation with KG creation:

```
PPT files → ppt_to_pdf → markdownize → create_kg (rainbow_add_crm workflow)
```

```bash
# See the complete 3-step plan
uv run cli workflow run full_rainbow_pipeline --dry-run

# Execute all steps
uv run cli workflow run full_rainbow_pipeline
```

The `create_kg` step uses `uses_workflow: rainbow_add_crm` — it expands and runs all
the rainbow + CRM graph factories in the same database.

---

## Adding a New KG Config

To add a new KG configuration, add a workflow and optionally a profile to `config/ekg_workflows.yaml`:

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
        inputs:
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

```bash
uv run cli workflow run kg_one_rainbow --set delete_first=true
```

### Force rebuild of parquet cache

```bash
uv run cli workflow run kg_one_rainbow --set force_rebuild=true
```

### Check workflow expansion

Use `--dry-run` to see how sub-workflows are expanded before executing:

```bash
uv run cli workflow run kg_learned --dry-run
```

### See Also

- [genai-tk docs/workflows.md](../../genai-tk/docs/workflows.md) — Core workflow engine documentation
- [config/ekg_workflows.yaml](../config/ekg_workflows.yaml) — All KG workflow definitions
- [config/workflows.yaml](../config/workflows.yaml) — Pre-processing workflow definitions
- [genai_graph/orchestration/workflow_steps.py](../genai_graph/orchestration/workflow_steps.py) — `kg_build_step` implementation


---
