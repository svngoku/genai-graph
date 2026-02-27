# Error Reporting Improvements

This document describes the improvements made to error reporting in the KG creation pipeline to make troubleshooting easier.

## Problem

Previously, when KG creation failed, error messages were cryptic and didn't provide enough context:

- "Key field 'country' not found or empty in data for Geo" - didn't show what fields WERE available
- "Binder exception: Cannot find property opportunity for n." - didn't explain this meant a schema mismatch
- No indication of what fields were being filtered out during DataFrame processing

## Improvements

### 1. Missing Key Field Errors

**Before:**
```
Key field 'country' not found or empty in data for Geo
```

**After:**
```
Key field 'country' not found or empty in data for Geo. 
Available fields: geo_code, name, location, services, metadata (and 3 more). 
Consider using key_from='AUTO_ID' if this field may be missing.
```

**Benefits:**
- Shows first 10 available fields to help identify the correct field name
- Suggests using `AUTO_ID` as a solution for optional fields
- Indicates total number of fields if more than 10 exist

### 2. Schema Mismatch Errors

**Before:**
```
Error in batch merge for Customer: Binder exception: Cannot find property opportunity for n.
```

**After:**
```
Schema mismatch for Customer: property 'opportunity' not in database schema. 
Schema fields: name, iris_code, country, business_line, revenue, location, services. 
This usually means the field exists in data but wasn't defined in the node's Pydantic model.
```

**Benefits:**
- Clearly identifies it as a schema mismatch
- Shows which property is missing from the schema
- Lists the actual schema fields
- Explains the likely cause

### 3. DataFrame Column Filtering

**New logging when columns are filtered:**
```
Filtering out 2 extra columns from Customer data not in schema: opportunity, extra_field
```

**Benefits:**
- Visibility into which columns are being removed
- Shows first 5 filtered columns (with "..." if more exist)
- Helps identify when data has unexpected fields

### 4. Document Processing Errors

**Enhanced error messages with helpful tips:**

For missing key fields:
```
Failed to process /path/to/file.json: Key field 'id' not found...
💡 Tip: If this field may be missing in some documents, consider using key_from='AUTO_ID' for auto-generated IDs
```

For schema mismatches:
```
Failed to process /path/to/file.json: Cannot find property...
💡 Tip: Schema mismatch - field exists in data but not in node definition. 
Check that your Pydantic model matches the data structure.
```

## Implementation Details

### Files Modified

1. **genai_graph/kg/schema/core.py** - `get_key_value()`
   - Enhanced error message to show available fields
   - Added suggestion to use `AUTO_ID`
   - Shows field preview (first 10) with count indicator

2. **genai_graph/kg/ingest/merge.py** - `merge_nodes_batch()`
   - Added DataFrame column filtering with logging
   - Enhanced schema mismatch error detection
   - Better context for property binding errors

3. **genai_graph/kg/ingest/documents.py** - `add_documents_to_graph()`
   - Added contextual tips based on error type
   - Friendly emoji indicators for user-facing tips

### Testing

New test suite: `tests/unit_tests/test_error_reporting.py`

Tests verify:
- ✅ Missing key field shows available fields
- ✅ Field preview shows "(and X more)" for large field lists
- ✅ AUTO_ID suggestion is included
- ✅ Computed key shows appropriate error message
- ✅ AUTO_ID never fails even with empty data

## Usage Examples

### Example 1: Fixing Missing Key Field

**Error:**
```
Key field 'id' not found or empty in data for Person. 
Available fields: name, email, role, organization. 
Consider using key_from='AUTO_ID' if this field may be missing.
```

**Solution:**
```python
GraphNode(
    node_class=Person,
    name_from="name",
    key_from="AUTO_ID",  # ✅ Use AUTO_ID instead of missing 'id' field
    description="Person node",
)
```

### Example 2: Fixing Schema Mismatch

**Error:**
```
Schema mismatch for Customer: property 'opportunity' not in database schema. 
Schema fields: name, iris_code, country, business_line. 
This usually means the field exists in data but wasn't defined in the node's Pydantic model.
```

**Root Cause:** The `Customer` class in your data has an `opportunity` field, but the Pydantic model doesn't define it.

**Solution:** Either:
1. Add the field to your Pydantic model:
   ```python
   class Customer(BaseModel):
       name: str
       opportunity: str | None = None  # ✅ Add missing field
   ```

2. Or mark it as explicitly_defined and exclude it via relationships:
   ```python
   # The relationship will handle the opportunity field
   GraphRelation(
       from_node=customer_node,     # GraphNode instance
       to_node=opportunity_node,    # GraphNode instance
       name="HAS_OPPORTUNITY",
       field_paths=[("", "opportunity")],
   )
   ```

## Best Practices

1. **Use AUTO_ID for optional keys**: If a field might be missing in some documents, use `key_from='AUTO_ID'` instead of `key_from='field_name'`

2. **Match Pydantic models to data**: Ensure your Pydantic model definitions include all fields present in your data, or explicitly exclude them via relationships

3. **Check debug logs**: Enable debug logging to see column filtering details:
   ```python
   import logging
   logging.getLogger("genai_graph.kg.ingest.merge").setLevel(logging.DEBUG)
   ```

4. **Validate sample data**: Before processing large datasets, test with a single document to catch schema issues early
