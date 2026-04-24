# Knowledge Graph Parquet Cache Management

The KG system caches imported graph data in Parquet format so that dependent KG
configurations don't re-process unchanged sources on every build. This document explains
when caches become invalid and how to clear them.

## Cache Location

```
~/kg_outputs/{kg_name}/parquet/   ← one directory per KG name
```

A `manifest.json` inside each directory tracks content fingerprints. If fingerprints match,
the import phase is skipped.

## Why Caches Become Stale

### Struct field-order mismatch (most common)

Ladybug enforces strict field order for `STRUCT` columns. When Pandas/PyArrow serializes
a struct to Parquet it may reorder the fields (e.g. alphabetically), producing a layout
that differs from the current Pydantic/BAML schema order.

**Symptom**: `Binder exception: Expression has data type STRUCT(...) but expected STRUCT(...)`

**Root cause**: Extracted JSON → Pydantic model preserves BAML field order, but the
PyArrow serialization step reorders struct fields before writing to Parquet. The next
import reads the Parquet with the old (reordered) layout and fails schema validation.

Example:
```
BAML/Pydantic order:   objectives, scope, requirements, success_metrics
Parquet cached order:  objectives, requirements, scope, success_metrics  ← reordered
```

### Cascading dependencies

When `graph_A` imports from `graph_B`'s parquet cache, rebuilding `graph_B` alone does
not automatically invalidate `graph_A`'s cache. Both must be cleared.

## Clearing Caches

```bash
# Clear all caches (recommended when BAML schema changes)
cli kg create --kg my_kg --clear-all-caches

# Force rebuild of imported dependencies even if fingerprints match
cli kg create --kg my_kg --force

# Target KG + all its imports, fully fresh
cli kg create --kg my_kg --clear-all-caches --force
```

`--clear-all-caches` recursively deletes all `parquet/` subdirectories under `~/kg_outputs/`
and then rebuilds from scratch.

## When to Clear Caches

| Scenario | Action |
|---|---|
| BAML struct fields reordered | `--clear-all-caches` |
| New fields added to BAML class | `--clear-all-caches` |
| Source data files updated | `--force` |
| Schema validation errors during import | `--clear-all-caches` |
| Schema-mismatch error (`Cannot find property`) | `--clear-all-caches` |

## Known Limitations

- **No automatic invalidation across imports**: If `rainbow_add_crm` is rebuilt but
  `stratnav_subset_rainbow_crm` still has a stale cache pointing to the old data, you
  must explicitly run `--clear-all-caches` or `--force` on the dependent KG.
- **Struct field ordering**: PyArrow does not guarantee preservation of Pydantic field
  order when writing struct columns. This is a PyArrow limitation; the `_pandas_to_arrow_with_structs`
  helper in `artifacts.py` works around it by building an explicit Arrow schema from
  the current Ladybug table definition before importing.

