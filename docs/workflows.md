# Workflows in genai-graph: Knowledge Graph Orchestration

The **Workflow Engine** (from genai-tk) is used in genai-graph to orchestrate knowledge graph creation pipelines. This document explains how to:

- Define multi-step KG pipelines using YAML
- Create reusable profiles for different data sources
- Chain together document preparation (ppt2pdf → markdownize) with KG creation
- Run full pipelines from the CLI with `--dry-run` support

---

## Quick Start

### List Available KG Workflows

```bash
uv run cli workflow list

# Output shows:
#   Workflows:
#     - ppt2pdf_documents
#     - markdownize_documents
#     - kg_create
#     - full_kg_pipeline
#
#   Profiles:
#     - ppt2pdf_rainbow
#     - markdownize_rainbow
#     - kg_one_rainbow
#     - kg_rainbow_add_crm
#     - full_rainbow_pipeline
```

### Run a KG Creation Workflow

```bash
# Dry-run: resolve the workflow, show the plan, don't execute
uv run cli workflow run full_rainbow_pipeline --dry-run

# Execute: create the knowledge graph
uv run cli workflow run full_rainbow_pipeline

# Override values at the CLI
uv run cli workflow run kg_one_rainbow --set force_rebuild=true --set export_html=true
```

---

## Workflow Definitions

### Single-Step Workflows

**PPT-to-PDF Conversion** (`ppt2pdf_documents`):

```yaml
workflows:
  ppt2pdf_documents:
    description: "Convert PowerPoint files to PDF"
    steps:
      - id: ppt_to_pdf
        uses: genai_tk.workflow.prefect.flows.ppt2pdf_flow.ppt2pdf_flow
        inputs:
          root_dir: "${profile.ppt_dir}"
          output_dir: "${profile.pdf_dir}"
        params:
          batch_size: "${profile.batch_size}"
```

**Markdown Conversion** (`markdownize_documents`):

```yaml
workflows:
  markdownize_documents:
    description: "Convert documents to Markdown"
    steps:
      - id: to_markdown
        uses: genai_tk.workflow.prefect.flows.markdownize_flow.markdownize_flow
        inputs:
          root_dir: "${profile.root_dir}"
          output_dir: "${profile.output_dir}"
        params:
          converter: "${profile.converter}"
          batch_size: "${profile.batch_size}"
```

**Knowledge Graph Creation** (`kg_create`):

```yaml
workflows:
  kg_create:
    description: "Create knowledge graph from documents"
    steps:
      - id: create_kg
        uses: genai_graph.orchestration.workflow_steps.kg_create_step
        inputs:
          config_name: "${profile.config_name}"
        params:
          delete_first: "${profile.delete_first}"
          export_html: "${profile.export_html}"
          force_rebuild: "${profile.force_rebuild}"
```

The `kg_create_step` wrapper:
- Clears internal caches (JsonFileBackedFactory, TableBackedFactory, Neo4jFactory)
- Sets up ephemeral Prefect context
- Calls the existing `create_kg_flow()`
- Returns metadata: {config_name, total_processed, total_failed, warnings_count, db_path}

### Multi-Step Pipelines

**Full KG Pipeline** (`full_kg_pipeline`):

Chains PPT conversion → Markdown conversion → KG creation:

```yaml
workflows:
  full_kg_pipeline:
    description: "PPT → PDF → Markdown → Knowledge Graph"
    steps:
      - id: ppt_to_pdf
        uses: genai_tk.workflow.prefect.flows.ppt2pdf_flow.ppt2pdf_flow
        inputs:
          root_dir: "${profile.ppt_dir}"
          output_dir: "${profile.pdf_dir}"
        params:
          batch_size: "${profile.batch_size}"

      - id: to_markdown
        uses: genai_tk.workflow.prefect.flows.markdownize_flow.markdownize_flow
        needs: [ppt_to_pdf]
        inputs:
          root_dir: "${profile.pdf_dir}"
          output_dir: "${profile.md_dir}"
        params:
          converter: "${profile.converter}"

      - id: create_kg
        uses: genai_graph.orchestration.workflow_steps.kg_create_step
        needs: [to_markdown]
        inputs:
          config_name: "${profile.config_name}"
        params:
          delete_first: "${profile.delete_first}"
          export_html: true
```

---

## Profiles

Profiles bind workflows to specific data sources and configurations.

### Rainbow Dataset Examples

**PPT-to-PDF Profile** (`ppt2pdf_rainbow`):

```yaml
workflow_profiles:
  ppt2pdf_rainbow:
    workflow: ppt2pdf_documents
    values:
      ppt_dir: "${paths.data_root}/rainbow/ppts"
      pdf_dir: "${paths.data_root}/rainbow/pdfs"
      batch_size: 5
```

**Markdownize Profile** (`markdownize_rainbow`):

```yaml
workflow_profiles:
  markdownize_rainbow:
    workflow: markdownize_documents
    values:
      root_dir: "${paths.data_root}/rainbow/pdfs"
      output_dir: "${paths.data_root}/rainbow/markdown"
      converter: markitdown
      batch_size: 10
```

**Single KG Creation** (`kg_one_rainbow`):

```yaml
workflow_profiles:
  kg_one_rainbow:
    workflow: kg_create
    values:
      config_name: rainbow          # References genai_graph.config.agents.rainbow
      delete_first: false
      export_html: true
      force_rebuild: false
```

**KG with Additional CRM Data** (`kg_rainbow_add_crm`):

```yaml
workflow_profiles:
  kg_rainbow_add_crm:
    workflow: kg_create
    values:
      config_name: rainbow_crm      # Multi-source config
      delete_first: false
      export_html: true
      force_rebuild: false
```

**Full 3-Step Pipeline** (`full_rainbow_pipeline`):

```yaml
workflow_profiles:
  full_rainbow_pipeline:
    workflow: full_kg_pipeline
    values:
      ppt_dir: "${paths.data_root}/rainbow/ppts"
      pdf_dir: "${paths.data_root}/rainbow/pdfs"
      md_dir: "${paths.data_root}/rainbow/markdown"
      batch_size: 5
      converter: markitdown
      config_name: rainbow
      delete_first: false
      export_html: true
```

### Using Multiple Data Sources

To ingest a different dataset, create a new profile:

```yaml
workflow_profiles:
  markdownize_strateg_nav:
    workflow: markdownize_documents
    values:
      root_dir: "${paths.data_root}/strategic_nav/pdfs"
      output_dir: "${paths.data_root}/strategic_nav/markdown"
      converter: mistral          # Use Mistral OCR for better quality
      batch_size: 3

  kg_strateg_nav:
    workflow: kg_create
    values:
      config_name: strategic_nav  # References strateg_nav config
      delete_first: true
      export_html: true
      force_rebuild: true

  kg_stratnav_subset_rainbow_crm:
    workflow: full_kg_pipeline
    values:
      ppt_dir: "${paths.data_root}/strategic_nav/ppts"
      pdf_dir: "${paths.data_root}/strategic_nav/pdfs"
      md_dir: "${paths.data_root}/strategic_nav/markdown"
      batch_size: 3
      converter: mistral
      config_name: strateg_nav_rainbow_crm
      delete_first: false
      export_html: true
```

---

## CLI Usage

### List All Available Workflows & Profiles

```bash
uv run cli workflow list
```

### Dry-Run a Pipeline

See the execution plan without running:

```bash
# Single-step workflow
uv run cli workflow run kg_one_rainbow --dry-run

# Full 3-step pipeline
uv run cli workflow run full_rainbow_pipeline --dry-run
```

Output example:

```
  Workflow Resolution  
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│ Requested: full_rainbow_pipeline
│ Workflow: full_kg_pipeline
│ Profile: full_rainbow_pipeline
│ Steps: 3
└────────────────────────────────┘

         Effective Values         
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│ ppt_dir: /data/rainbow/ppts    │
│ pdf_dir: /data/rainbow/pdfs    │
│ md_dir: /data/rainbow/markdown │
│ batch_size: 5                  │
│ converter: markitdown          │
│ config_name: rainbow           │
│ export_html: true              │
└────────────────────────────────┘

   Workflow Steps (Topologically Sorted)  
┏━━━━━━━━━━━┬━━━━━━━━━━┬━━━━━━━━━━━┐
│ id         │ uses     │ needs     │
├────────────┼──────────┼───────────┤
│ ppt_to_pdf │ ppt2pdf… │ []        │
│ to_markdown│ markdown…│ [ppt_t…]  │
│ create_kg  │ kg_creat…│ [to_mar…] │
└────────────┴──────────┴───────────┘
```

### Execute a Workflow

```bash
# Single KG creation
uv run cli workflow run kg_one_rainbow

# Full 3-step pipeline
uv run cli workflow run full_rainbow_pipeline

# Override parameters
uv run cli workflow run kg_one_rainbow --set force_rebuild=true

# Force re-execution with a fresh database
uv run cli workflow run kg_one_rainbow --set delete_first=true --force
```

---

## Integration with KG Configuration

Workflows reference KG configs via the `config_name` parameter:

```yaml
# In config/agents/langchain.yaml (example kg_one_rainbow profile):
kg_one_rainbow:
  workflow: kg_create
  values:
    config_name: rainbow     # ← Must match a key in genai_graph.config
```

The workflow engine passes `config_name` to `kg_create_step()`, which:

1. Loads the KG config from `genai_graph.config.agents.<config_name>`
2. Clears internal factories to ensure fresh state
3. Runs the KG creation flow
4. Returns metadata about the created graph

---

## Advanced Usage

### Conditional KG Deletion

When re-processing a dataset, you can delete the old KG first:

```yaml
workflow_profiles:
  kg_rainbow_fresh:
    workflow: kg_create
    values:
      config_name: rainbow
      delete_first: true     # ← Delete existing graph
      export_html: true
      force_rebuild: true    # ← Force full rebuild
```

Usage:

```bash
uv run cli workflow run kg_rainbow_fresh --dry-run
uv run cli workflow run kg_rainbow_fresh
```

### HTML Export for Exploration

The workflow can automatically export an interactive HTML explorer:

```yaml
workflow_profiles:
  kg_with_explorer:
    workflow: kg_create
    values:
      config_name: rainbow
      export_html: true      # ← Enable HTML export
```

The HTML file is saved to the database output directory.

### Parallel Processing

Multi-file steps (ppt2pdf, markdownize) use `ThreadPoolTaskRunner` for parallel batch processing:

```yaml
workflow_profiles:
  fast_pipeline:
    workflow: full_kg_pipeline
    values:
      # Large batch sizes = more parallelism
      batch_size: 20         # ← More concurrent conversions
      converter: mistral     # ← Mistral OCR is parallelizable
      # ...
```

KG creation itself runs serially (Ladybug is single-writer), but document preparation is parallelized.

---

## Troubleshooting

### "Cannot import kg_create_step"

Ensure `genai_graph` is installed:

```bash
uv add genai_graph
# or from local development:
uv add --editable /path/to/genai-graph
```

### "Config name not found"

Verify the `config_name` matches a key in your KG config:

```python
# Check available configs
from genai_graph.config import agents_config
print(agents_config.keys())

# Add or update a config in config/agents/langchain.yaml
```

### "Workflow steps appear to hang"

Check logs for details:

```bash
uv run cli workflow run my_profile --logging DEBUG
```

This enables debug-level logging to see task progress.

### "Database already exists"

Use `--set delete_first=true` to clear it first:

```bash
uv run cli workflow run kg_one_rainbow --set delete_first=true
```

---

## See Also

- [Workflow Engine Guide](../genai-tk/docs/workflows.md) — Core system documentation
- [KG Construction](graph_construction.md) — Detailed knowledge graph creation
- [Prefect Integration](../genai-tk/docs/prefect.md) — Prefect flows and runtime
- [genai-tk CLI](../genai-tk/docs/cli.md) — Full CLI command reference
