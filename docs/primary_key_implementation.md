# PRIMARY KEY Implementation

## Overview

This document describes the implementation of flexible PRIMARY KEY support in GraphNode, allowing users to specify custom primary keys for Kuzu database tables.

## Feature: `key_from` Parameter

The `GraphNode` class now supports a `key_from` parameter that determines the PRIMARY KEY for the generated Kuzu table.

### Supported Values

1. **`"AUTO_ID"`** (default): Uses Kuzu's SERIAL type for auto-incrementing integer IDs
2. **Field name (string)**: Uses an existing model field as the PRIMARY KEY
3. **Callable/Lambda**: Computes a custom key value dynamically

### Examples

#### 1. AUTO_ID (SERIAL)
```python
GraphNode(
    node_class=Customer,
    name_from="name",
    key_from="AUTO_ID",  # Creates: id SERIAL PRIMARY KEY
)
```
**Result:** `CREATE NODE TABLE Customer(id SERIAL, name STRING, ..., PRIMARY KEY(id))`

#### 2. Custom Field
```python
GraphNode(
    node_class=Opportunity,
    name_from="name",
    key_from="opportunity_id",  # Uses existing field as PRIMARY KEY
)
```
**Result:** `CREATE NODE TABLE Opportunity(opportunity_id STRING, name STRING, ..., PRIMARY KEY(opportunity_id))`

#### 3. Field Name for Deduplication
```python
GraphNode(
    node_class=Person,
    name_from="name",
    key_from="name",  # Use name field as PRIMARY KEY
)
```
**Result:** `CREATE NODE TABLE Person(name STRING, ..., PRIMARY KEY(name))`

#### 4. Computed Key (Lambda)
```python
GraphNode(
    node_class=Architecture,
    name_from="name",
    key_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
)
```
**Result:** `CREATE NODE TABLE Architecture(id STRING, name STRING, ..., PRIMARY KEY(id))`
- The lambda computes a composite key value stored in the `id` field

## Implementation Details

### Schema Generation (`graph_core.py`)

The `create_schema()` function generates appropriate CREATE NODE TABLE statements:

```python
if key_from == "AUTO_ID":
    # Add SERIAL field
    kuzu_fields.append("id SERIAL")
    primary_key_clause = "PRIMARY KEY(id)"
elif callable(key_from):
    # Add computed key field
    kuzu_fields.append("id STRING")
    primary_key_clause = "PRIMARY KEY(id)"
else:
    # Use existing field as PRIMARY KEY
    primary_key_clause = f"PRIMARY KEY({key_from})"
```

### Data Extraction (`graph_core.py`)

The `extract_graph_data()` function populates the correct primary key field:

```python
if key_from == "AUTO_ID":
    # Don't set id field - let database auto-generate
    primary_key_field = "id"
    key_value = str(uuid.uuid4())  # For id_registry tracking only
elif callable(key_from):
    # Compute and store key value in 'id' field
    primary_key_field = "id"
    key_value = node_info.get_key_value(item_data, node_type)
    item_data[primary_key_field] = key_value
else:
    # Use specified field as primary key
    primary_key_field = key_from
    key_value = node_info.get_key_value(item_data, node_type)
    item_data[primary_key_field] = key_value
```

### Merge Logic (`graph_merge.py`)

The merge functions now accept node-specific primary key field information:

```python
def merge_nodes_batch(
    conn: GraphBackend,
    nodes_dict: dict[str, list[dict[str, Any]]],
    node_type_to_key_field: dict[str, str],
    node_type_to_is_auto_id: dict[str, bool],
    context: KgManager | None = None,
) -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], str]]:
```

**Key behaviors:**
- **AUTO_ID nodes**: Always create new nodes (no merge), exclude `id` from INSERT properties
- **Custom key nodes**: Match on the specified key field, include field in INSERT properties

### ID Registry

The `id_registry` is used to track node identities for relationship creation:
- **AUTO_ID**: Uses a temporary UUID for tracking, replaced by actual SERIAL value after INSERT
- **Custom key**: Uses the actual key value (e.g., "9000559500" for Opportunity)
- **Computed key**: Uses the computed value (e.g., "Architecture:2024-01-15")

## Migration Notes

### Changes from Previous Implementation

1. **Removed `key` parameter**: Replaced with `key_from` for consistency with `name_from`
2. **Removed `deduplication_key`**: No longer needed - use `key_from` instead
3. **Removed SERIAL logic**: Now handled through `key_from="AUTO_ID"`
4. **Simplified merge logic**: Primary key field is now determined per node type

### Schema File Updates

All schema files have been updated:

**Before:**
```python
GraphNode(
    node_class=Opportunity,
    name_from="name",
    key="opportunity_id",
    deduplication_key="opportunity_id",
)
```

**After:**
```python
GraphNode(
    node_class=Opportunity,
    name_from="name",
    key_from="opportunity_id",
)
```

## Testing

Comprehensive tests in `tests/integration_tests/test_primary_key.py`:

1. ✅ `test_custom_field_as_primary_key` - Tests using a custom field (opportunity_id)
2. ✅ `test_auto_id_primary_key` - Tests AUTO_ID with SERIAL
3. ✅ `test_field_name_as_primary_key` - Tests using name field for deduplication
4. ✅ `test_computed_key_as_primary_key` - Tests lambda-based key computation

All tests verify:
- Correct PRIMARY KEY in database schema
- Successful data insertion
- Proper node retrieval by primary key

## Benefits

1. **Flexibility**: Support for SERIAL, custom fields, and computed keys
2. **Simplicity**: Single parameter (`key_from`) replaces multiple concepts
3. **Consistency**: Follows same pattern as `name_from`
4. **Correctness**: Proper PRIMARY KEY constraints in Kuzu database
5. **Deduplication**: Natural deduplication on custom keys (e.g., CRM IDs)

## Future Enhancements

Possible improvements:
- Multi-field composite keys
- Alternate key constraints (UNIQUE indexes)
- Automatic key generation strategies (UUID, nanoid, etc.)
