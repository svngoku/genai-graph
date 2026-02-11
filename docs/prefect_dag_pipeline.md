# Prefect DAG Pipeline for KG Construction

## Overview

The KG construction pipeline is built as a **Prefect DAG** (Directed Acyclic Graph) that
decomposes knowledge graph creation into discrete, observable tasks. It replaces the
earlier sequential, recursive approach with a structured flow that supports:

- **Dependency resolution** — imports are topologically sorted and processed in order
- **Smart caching** — fingerprint-based validation avoids unnecessary rebuilds
- **Parallel exports** — export tasks run concurrently via a thread pool
- **Cross-import warning aggregation** — warnings from all phases (including imported KGs) are collected and reported together
- **Dual Prefect mode** — runs in-process (ephemeral) by default, or against a deployed
  Prefect server when `GENAI_PREFECT_API_URL` is set

## Architecture

### Flow Structure

```
create_kg_flow
│
├── 1. delete_backend_task          (optional — --delete-first)
├── 2. resolve_config_task          → (config_name, kg_cfg)
│      └── resolve_import_dag()     → ImportDag (topological sort)
├── 3. initialize_backend_task      → KgBackend (Kuzu)
│
├── 4. Import phase (serial — one-at-a-time)
│      ├── import_kg_task("crm_export")
│      │      ├── validate_parquet_cache()
│      │      ├── [sub-flow: create_kg_flow] if cache stale
│      │      ├── create schema from import
│      │      └── import_from_parquet()
│      └── import_kg_task("rainbow_add_crm")
│             └── ...
│
├── 5. load_factories_task          → list[GraphBundle]
├── 6. create_schema_task           → bundles with schema
│
├── 7. Ingestion phase (serial — Kuzu single-writer)
│      ├── ingest_bundle_task(bundle_0)
│      ├── ingest_bundle_task(bundle_1)
│      └── ...
│
├── 8. summarize_warnings_task
│
└── 9. Export phase (parallel — ThreadPoolTaskRunner)
       ├── export_warnings_task
       ├── export_schema_task
       ├── export_info_task
       ├── export_parquet_task      → manifest.json with fingerprints
       └── export_html_task         (optional)
```

### Import DAG Resolution

When a KG configuration declares `imports:`, the pipeline resolves the full import tree
into a flat, topologically sorted execution plan **before** any task runs.

```yaml
# config/ekg.yaml
stratnav_subset_rainbow_crm:
  imports: [rainbow_add_crm]
  graphs: [...]

rainbow_add_crm:
  imports: [crm_export]
  graphs: [...]

crm_export:
  graphs: [...]
```

The resolver (`resolve_import_dag()`) produces:

```
execution_order: [crm_export, rainbow_add_crm]
```

Each import becomes a separate `import_kg_task` visible in the Prefect UI.
Diamond dependencies (A imports B and C, both import D) are handled correctly —
D is built once.

### Concurrency Model

**Kuzu constraint:** Kuzu is an embedded database with a single-writer lock.
Multiple threads cannot write simultaneously.

The pipeline uses `ThreadPoolTaskRunner(max_workers=4)`:

| Phase | Concurrency | Reason |
|-------|-------------|--------|
| Import | Serial | Each import may trigger a sub-flow that writes to Kuzu |
| Schema creation | Serial | Writes to Kuzu catalog |
| Ingestion | Serial (per-bundle) | Kuzu single-writer constraint |
| **Export** | **Parallel** | Read-only tasks — no DB mutation |

The `ParquetCollector` (which accumulates DataFrames during ingestion) is **thread-safe**
via `threading.Lock`, enabling safe concurrent access from parallel export tasks.

## Smart Caching

### Fingerprint System

Each parquet export includes three fingerprints in its `manifest.json`:

| Field | Source | Detects |
|-------|--------|---------|
| `schema_fingerprint` | `GraphSchema.fingerprint()` | Changes in node types, key fields, relations, properties |
| `factory_config_hash` | `KgFactory.config_fingerprint()` | Changes in factory parameters (data root, patterns, etc.) |
| `source_content_hash` | `file_digest()` per source file | Changes in actual input data files |

**Example manifest.json:**

```json
{
  "config_name": "crm_export",
  "exported_at": "2026-02-11T23:09:44.849808",
  "node_tables": ["Opportunity", "Customer", "Person"],
  "rel_tables": ["HAS_CONTACT", "OWNS"],
  "node_count": 1936,
  "rel_count": 1452,
  "schema_fingerprint": "122b6775568586e2",
  "factory_config_hash": "d70a8188a44c30f9",
  "source_content_hash": "a3b7c9d1e5f20814"
}
```

### Cache Validation Flow

When `import_kg_task` runs for a dependency:

```
1. Load manifest.json from parquet output directory
2. If no manifest → rebuild
3. If manifest has no fingerprints (legacy) → treat as valid
4. Compute current fingerprints from live factories/schemas
5. Compare each non-None field pair
6. If all match → skip rebuild, load from parquet
7. If any mismatch → log reasons, rebuild
```

The comparison is **backward compatible**: a `None` on either side (manifest or current)
is treated as "unknown" and does not trigger a mismatch.

### What Triggers a Rebuild

| Change | Fingerprint affected | Example |
|--------|---------------------|---------|
| Add/remove a node type | `schema_fingerprint` | Adding a `Partner` node class |
| Change a key field | `schema_fingerprint` | Changing `key_from="id"` to `key_from="code"` |
| Add a relationship | `schema_fingerprint` | New `HAS_PARTNER` relation |
| Change factory data root | `factory_config_hash` | Pointing to a different input directory |
| Modify include/exclude patterns | `factory_config_hash` | Changing file pattern filters |
| Edit a source data file | `source_content_hash` | Updating an Excel or JSON file |
| Change BAML schema structure | `schema_fingerprint` | Adding a field to the Pydantic output model |

### Hashing

All fingerprints use **xxHash XXH3-64** (via `genai_tk.utils.hashing.buffer_digest()`),
chosen for speed and good distribution. It is not cryptographic — the purpose is
change detection, not security.

## CLI Usage

### Basic Commands

```bash
# Create a single KG
cli kg create --kg one_rainbow

# Create with import dependencies (auto-resolved)
cli kg create --kg one_rainbow_with_db --delete-first

# Create all configured KGs
cli kg create --all-graphs

# Force rebuild of all imported dependencies (ignore cache)
cli kg create --kg one_rainbow_with_db --force-rebuild

# Clear all parquet caches first
cli kg create --kg one_rainbow_with_db --clear-all-caches
```

### Flags

| Flag | Description |
|------|-------------|
| `--kg NAME` | Specify KG configuration(s) to build (repeatable) |
| `--all-graphs` | Build all KGs defined in `ekg.yaml` |
| `--delete-first` | Delete existing Kuzu DB before creation |
| `--force-rebuild` | Rebuild imported KG dependencies even if cache fingerprints match |
| `--clear-all-caches` | Clear all parquet output directories before creation |
| `--no-html` | Skip HTML visualization export |

### Terminal Output

The pipeline logs cache validation decisions via loguru (visible in terminal):

```
23:10:02-INFO | tasks.py:326 import_kg_task- Parquet cache for 'crm_export'
  is valid (exported at 2026-02-11T23:09:44.849808) — skipping rebuild
23:10:03-INFO | artifacts.py:1203 import_from_parquet- Imported from parquet
  'crm_export': 1936 nodes, 1452 rels
```

When the cache is stale:

```
23:08:58-INFO | tasks.py:331 import_kg_task- Parquet cache for 'crm_export'
  is stale: schema structure changed (122b6775… → 9a4c3e21…) — rebuilding
```

## Prefect Integration

### Ephemeral Mode (Default)

By default, the flow runs with an **in-process ephemeral Prefect client** — no server,
no database, no agents required. This is configured via `ephemeral_prefect_settings()`
from `genai_tk.extra.prefect.runtime`.

### Deployed Server Mode

Set the environment variable `GENAI_PREFECT_API_URL` to connect to a running Prefect server:

```bash
export GENAI_PREFECT_API_URL=http://localhost:4200/api
cli kg create --kg one_rainbow_with_db
```

In this mode, flows and tasks are visible in the Prefect UI with full DAG visualization,
task run logs, and the markdown summary artifact.

### Prefect Artifacts

Each flow run creates a **markdown artifact** (`kg-create-summary`) summarizing:

- Import results (cached vs rebuilt)
- Bundle processing stats
- Document counts (processed, failed, nodes, relationships)
- Warnings

## Module Structure

```
genai_graph/orchestration/
├── __init__.py          # Public API exports
├── dag.py               # ImportDag, ImportNode, resolve_import_dag()
├── models.py            # WarningsCollector, BundleResult, ImportResult, KgRunResult
├── flows.py             # create_kg_flow (main Prefect flow)
└── tasks.py             # All @task definitions (12 tasks)
```

### Key Classes

| Class | Module | Purpose |
|-------|--------|---------|
| `ImportDag` | `dag.py` | Topologically sorted import plan |
| `ImportNode` | `dag.py` | Single node in the import dependency graph |
| `WarningsCollector` | `models.py` | Serializable, mergeable warning accumulator |
| `BundleResult` | `models.py` | Result of processing one subgraph bundle |
| `ImportResult` | `models.py` | Result of importing one KG dependency |
| `KgRunResult` | `models.py` | Aggregated result of a full KG creation run |
| `GraphBundle` | `models.py` | In-memory bundle: config + factory + schema |
| `CacheFingerprints` | `artifacts.py` | Current fingerprints for cache comparison |
| `ParquetManifest` | `artifacts.py` | Persisted manifest with fingerprint fields |

### Key Functions

| Function | Module | Purpose |
|----------|--------|---------|
| `resolve_import_dag()` | `dag.py` | Resolve import tree → topological order |
| `validate_parquet_cache()` | `artifacts.py` | Check if cached parquet is still valid |
| `compute_fingerprints_for_config()` | `artifacts.py` | Compute live fingerprints |
| `GraphSchema.fingerprint()` | `schema/core.py` | Hash schema structure (nodes, rels, fields) |
| `KgFactory.config_fingerprint()` | `factories/base.py` | Hash factory configuration |

## Design Decisions

### Why `cache_policy=NO_CACHE` on All Tasks

Prefect's default cache policy attempts to serialize task inputs for hash computation.
Several objects passed between tasks are inherently unpicklable:

- `KgBackend` (Kuzu) — contains weakrefs to the database connection
- `ParquetCollector` — contains a `threading.Lock`
- `GraphBundle` — contains factory instances with complex state

Rather than implementing custom `cache_key_fn` for each task, all tasks use
`cache_policy=NO_CACHE`. This is appropriate because:

1. **Caching is handled at the parquet level** — the fingerprint system provides
   meaningful, content-aware caching
2. **Tasks are side-effect-heavy** — they create schemas, ingest documents, write files
3. **Prefect task caching is session-scoped** — it doesn't persist across CLI invocations

### Why `ThreadPoolTaskRunner` (Not `ConcurrentTaskRunner`)

Kuzu is an embedded database — it runs in the same process. Using process-based
concurrency would require serializing the database connection, which isn't possible.
A thread pool shares the process's address space while allowing concurrent I/O for
export tasks.

### Why Serial Ingestion

Kuzu enforces a single-writer constraint. While the `ThreadPoolTaskRunner` has 4 workers,
ingestion tasks are submitted **one at a time** (each `.submit().result()` blocks before
the next). Only the export phase exploits parallelism.

### Why Not Async Kuzu

Kuzu v0.11.3 offers `AsyncConnection`, but it is backed by a thread pool internally —
there is no true async I/O. The engineering cost of converting the entire pipeline to
async would not yield meaningful performance gains given the single-writer constraint.

## Testing

Unit tests cover all orchestration components:

```bash
# Run orchestration tests
uv run pytest tests/unit_tests/test_orchestration_dag.py -v

# Run all unit tests
uv run pytest tests/unit_tests/ -v
```

Test classes:

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestWarningsCollector` | 5 | merge, dedup, prefix, empty |
| `TestResolveImportDag` | 8 | linear, diamond, cycle detection, no imports |
| `TestResultModels` | 2 | BundleResult, ImportResult defaults |
| `TestCacheFingerprints` | 7 | matches, mismatches, None tolerance, reasons |
| `TestGraphSchemaFingerprint` | 3 | determinism, sensitivity, format |
| `TestKgFactoryConfigFingerprint` | 1 | hash stability |
| `TestParquetCollectorThreadSafety` | 2 | concurrent add_nodes, add_relationships |
