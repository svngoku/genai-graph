# Knowledge Graph Parquet Cache Invalidation Strategy

**Date:** 2024  
**Issue:** struct field order mismatch causing parquet cache incompatibility  
**Status:** Implemented temporary solution with `--clear-all-caches`

## Executive Summary

The Knowledge Graph system caches imported graph data in Parquet format to improve performance when building dependent KG configurations. However, when the BAML schema changes (e.g., reordering fields in a struct), the cached Parquet files become incompatible with the new schema, causing "Binder exception: Expression has data type STRUCT(...) but expected STRUCT(...)" errors.

**Root Cause:** Pandas/PyArrow does not preserve Pydantic model field order when serializing struct types to Parquet. The schema defined in BAML/Pydantic specifies one field order, but the Parquet serialization may use a different order (e.g., alphabetical). When Ladybug tries to import this cached data, the struct field order mismatch causes a hard error.

**Immediate Solution:** Added `--clear-all-caches` flag to force regeneration of all Parquet caches.

## Problem Description

### The Issue

Consider this BAML/Pydantic struct definition:

```baml
class KeyStatementOfWorkElement {
  objectives string[]
  scope string
  requirements string[]
  success_metrics string[]
}
```

When this is:
1. Extracted via BAML → generates JSON with fields in BAML order
2. Parsed into Pydantic model → preserves BAML order in `model_fields`
3. Added to Pandas DataFrame → becomes a Python dict
4. Serialized to Parquet → **PyArrow may reorder struct fields** (not documented behavior)

The cached Parquet file may contain:
```
STRUCT(objectives STRING[], requirements STRING[], scope STRING, success_metrics STRING[])
```

But Ladybug expects (based on current BAML schema):
```
STRUCT(objectives STRING[], scope STRING, requirements STRING[], success_metrics STRING[])
```

### When the Problem Occurs

1. **Schema Evolution**: When you reorder struct fields in BAML, the cache becomes invalid
2. **KG Imports**: The `stratnav_subset_rainbow_crm` KG imports from `rainbow_add_crm` KG's parquet cache
3. **Cascading Dependencies**: Changes to `rainbow_add_crm` don't automatically invalidate `stratnav_subset_rainbow_crm`

### Current Workaround

```bash
# Manual cache clearing
rm -rf ~/kg_outputs/*/parquet/

# Or use new --clear-all-caches flag
cli kg create stratnav_subset_rainbow_crm --clear-all-caches
```

## Implementation Status

### ✅ Completed (2024)

1. **`--clear-all-caches` Flag**
   - Added to `cli kg create` command
   - Implemented `clear_all_parquet_caches()` function in `artifacts.py`
   - Recursively deletes all `parquet/` subdirectories in `kg_outputs/`
   - Returns count of cleared caches for user feedback

2. **Improved Error Reporting**
   - Detects struct field order mismatch errors specifically
   - Provides clear explanation: "Schema mismatch detected. This usually happens when the BAML schema changed but cached parquet files have the old structure."
   - Suggests solution: "Run 'cli kg create --clear-all-caches' to regenerate all caches."
   - Shows technical details for debugging

3. **Documentation**
   - Updated CLI help text
   - Added usage example to docstring
   - Created this technical memo

## Future Improvements

1. **Fix struct field ordering at source** — Force PyArrow to preserve Pydantic field order when serializing structs (most robust long-term fix)
2. **Schema version tracking** — Add `schema_hash` to `ParquetManifest` for automatic cache invalidation
3. **Dependency graph tracking** — Propagate invalidation across dependent KG configs
