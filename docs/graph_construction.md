# Knowledge Graph Construction Process

This document describes how knowledge graphs are constructed from multiple heterogeneous data sources.

## Related Documentation

- **[BAML Extraction Guide](baml_extraction_guide.md)** - Extract data from text documents using BAML
- **[Primary Key Implementation](primary_key_implementation.md)** - Node deduplication strategy
- **[KG Create Enhancements](kg_create_enhancements.md)** - Advanced KG creation features

## Overview

The KG construction pipeline combines data from multiple sources (Neo4j exports, databases, BAML extractions) into a unified Kuzu graph database. The process handles:

- **Type unification**: Different sources may define the same entity (e.g., `Account` vs `Customer`)
- **Data deduplication**: MERGE operations prevent duplicate nodes and relationships
- **Schema evolution**: Adding columns when importing from different schema versions
- **Batch processing**: Efficient import of large datasets via parquet files

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Neo4j Export   │     │  Database/Excel │     │  BAML Extract   │
│    (JSONL)      │     │   (Tables)      │     │    (JSON)       │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Neo4jFactory    │     │TableBackedFactory│    │JsonFileFactory  │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   GraphSchema Merge    │
                    │  (dedupe by class name)│
                    └────────────┬───────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   Kuzu Database        │
                    │  (MERGE from DataFrame)│
                    └────────────────────────┘
```

## Graph Factories

Factories convert source data into graph nodes and relationships. Three main types:

### 1. JsonFileBackedFactory

**Use case**: Process BAML-extracted JSON files from text documents  
**Documentation**: See [BAML Extraction Guide](baml_extraction_guide.md) for complete details

```python
class ReviewedOpportunityGraph(JsonFileBackedFactory, BaseModel):
    def get_model_class(self) -> type[BaseModel]:
        return ReviewedOpportunity
```

### 2. Neo4jImportFactory

**Use case**: Import from Neo4j JSONL exports  
**Key feature**: Schema-based mappings with automatic type conversion

```python
class StratnavGraph(Neo4jImportFactory):
    def get_node_mappings(self) -> list[Neo4jNodeMapping]:
        return [
            Neo4jNodeMapping(
                neo4j_label="Account",      # Neo4j label
                node_class=Customer,        # Target Pydantic class
                key_field="name",           # Primary key
                property_mappings={         # Field mapping
                    "irisCode": "iris_code",
                    "subMarket": "segment",
                },
            ),
        ]
```

### 3. TableBackedFactory

**Use case**: Import from database tables or Excel files via pandas  
**Key feature**: Row-by-row transformation with custom mapper function

```python
class CrmExtractGraph(TableBackedFactory):
    def get_model_class(self) -> type[BaseModel]:
        return Opportunity
    
    def mapper_function(self, row: dict) -> Opportunity:
        return Opportunity(
            opportunity_id=row["Atos Opportunity ID"],
            customer=Customer(name=row["Account Name"]),
        )
```

## Common Nodes for Type Unification

The `genai_graph.ekg.schema.common_nodes` module defines canonical types used across factories to ensure node deduplication.

**Key principle**: Classes with the same `__name__` create the same Kuzu table, regardless of which module they're imported from.

### Currently defined canonical types

| Canonical type | Extends | Used by factories | Notes |
|---------------|---------|-------------------|-------|
| `Customer` | `BamlCustomer` | Stratnav (Account→Customer), RainbowReview, CRM | Extended with iris_code, country, etc. |
| `Geo` | `BamlGeo` | Stratnav (GEO→Geo), RainbowReview | Geographic location |
| `Partner` | `BamlPartner` | Stratnav (TechnologyPartner→Partner), RainbowReview | Technology vendors, subcontractors |
| `Opportunity` | `BamlOpportunity` | RainbowReview, CRM | Extended with lead, win_loss |

### Adding a new canonical type

When an entity appears in multiple data sources under different names
(e.g. Neo4j `TechnologyPartner` and BAML `Partner`), unify them:

1. **Create a canonical class** in `common_nodes.py` extending the BAML type:
```python
from genai_graph.ekg.baml_client.types import Partner as BamlPartner

class Partner(BamlPartner):
    """Partner organization (canonical type for deduplication)."""
```

2. **Import from `common_nodes`** in all factories (not from `baml_client.types`):
```python
from genai_graph.ekg.schema.common_nodes import Customer, Geo, Partner
```

3. **Map the Neo4j label** to the canonical class:
```python
Neo4jNodeMapping(
    neo4j_label="TechnologyPartner",  # Original Neo4j label
    node_class=Partner,               # Canonical class (table name = "Partner")
    ...
)
```

**Critical**: The canonical class `__name__` must match the BAML type's `__name__` (e.g. both are `"Partner"`), so all factories write to the same Kuzu table.

### Example: Customer

```python
from genai_graph.ekg.baml_client.types import Customer as BamlCustomer

class Customer(BamlCustomer):
    """Extended Customer with fields from multiple sources."""
    
    # Fields from Neo4j/Stratnav import (Account)
    iris_code: str | None = Field(default=None)
    country: str | None = Field(default=None)
    business_line: str | None = Field(default=None)
    
    # Fields from BAML extraction
    location: Geo | None = None
    services: list[L3] = Field(default_factory=list)
```

**Usage in factories**:
```python
# Import from common_nodes, not from baml_client.types
from genai_graph.ekg.schema.common_nodes import Customer, Geo, Partner, Opportunity

# All factories creating Customer nodes will share the same table
```

**Benefits**:
- **Deduplication**: Same customer from different sources → single node
- **Schema evolution**: New fields added without breaking existing data
- **Type safety**: Pydantic validation across all sources

## Schema Merging

When combining multiple graph factories, the `GraphRegistry.build_combined_schema()` method:

1. **Deduplicates nodes by class name** (not class identity)
2. **Deduplicates relationships by (from_name, to_name, rel_name)**
3. **Preserves metadata** from the first-seen definition (descriptions, index fields)
4. **Validates consistency** across merged schemas

```python
# Merge example - stratnav_subset_rainbow_crm
#   - StratnavGraph defines: Customer (from Neo4j Account)
#   - ReviewedOpportunityGraph defines: Customer (from BAML extraction)
#   - Result: Single Customer node type with combined properties
```

Nodes and relationships are identified by name, not Python class identity. This allows different factories to contribute to the same graph structure.

## Data Merging with Kuzu

The ingest layer uses Kuzu's `MERGE` operations for deduplication. Primary keys determine when to create vs. update nodes.

### Nodes
```cypher
LOAD FROM df
MERGE (n:Customer {name: name})
ON CREATE SET n.country = country, n.segment = segment
ON MATCH SET n.country = country, n.segment = segment
```

- **First import**: Creates node with all properties
- **Subsequent imports**: Updates properties on existing node
- **Key selection**: See [Primary Key Implementation](primary_key_implementation.md)

### Relationships

Relationships use row-by-row MERGE + SET to support edge properties:

```cypher
-- Without edge properties
MATCH (a:Customer {name: $from_id}), (b:Person {name: $to_id})
MERGE (a)-[:HAS_CONTACT]->(b)

-- With edge properties (from p_*_ fields)
MATCH (a:ReviewedOpportunity {id: $from_id}), (b:Partner {name: $to_id})
MERGE (a)-[r:HAS_PARTNER]->(b)
SET r.role = $role
```

Relationships are unique by (from_node, to_node, rel_type). Edge properties
(from `p_*_` fields on the target node class) are stored on the relationship.

## Import/Export via Parquet

KG configurations can import from other configurations via parquet cache, enabling incremental builds.

```yaml
# config/ekg.yaml
stratnav_subset_rainbow_crm:
  import:
    - rainbow_add_crm         # Imports nodes/rels from parquet
    - stratnav_subset
  graphs:
    - factory: genai_graph.ekg.schema.my_factory:MyGraph
```

**Import process**:
1. **Recursively creates schemas** from imported KG configurations
2. **Detects schema changes** and adds missing columns via `ALTER TABLE ADD`
3. **Converts array types** (numpy → Python lists) for Kuzu compatibility
4. **Imports data** using MERGE from parquet files (nodes first, then relationships)

**Benefits**:
- **Faster iteration**: Avoid re-processing unchanged data sources
- **Modular composition**: Combine pre-built graphs
- **Schema evolution**: Handles backward compatibility automatically

**Cache location**: `/home/tcl/kg_outputs/{kg_name}/parquet/`

## Configuration

KG configurations are defined in `config/ekg.yaml`:

```yaml
paths:
  rainbow_md: ${paths.ekg_data}/rainbow/md/
  rainbow_json: ${paths.ekg_data}/rainbow/json/

kg_configs:
  one_rainbow_with_db:
    import:
      - crm_export              # Import CRM data first
    graphs:
      - factory: "genai_graph.ekg.schema.rainbow_review:ReviewedOpportunityGraph"
        data_root: ${paths.rainbow_json}
        include: 
          - "*CNES*TMA*VENUS*"
        exclude:
          - "fake/*"
        recursive: true
        file_embedding:
          metadata: ["Opportunity.opportunity_id", "Customer.name"]
```

**Configuration fields**:
- `import`: List of KG names to import (processed first)
- `data_root`: Base directory for data files
- `include`: Glob patterns for files to include
- `exclude`: Glob patterns for files to exclude
- `recursive`: Search subdirectories
- `file_embedding`: Fields to include in document metadata

## CLI Commands

```bash
# Create specific KG
cli kg create --kg stratnav_subset_rainbow_crm

# Create all KGs
cli kg create --all-graphs

# Rebuild from scratch (clear parquet cache)
cli kg create --kg my_kg --delete-first

# View schema details
cli kg schema --kg my_kg

# Execute Cypher queries
cli kg cypher --kg my_kg "MATCH (c:Customer) RETURN c.name LIMIT 10"

# Export to Neo4j
cli kg export --kg my_kg --format neo4j

# View HTML visualization
# Automatically generated at: /home/tcl/kg_outputs/{kg_name}/{kg_name}-dev.html
```

## Data Source Priority

Sources are processed in order defined in the configuration. Data merging behavior:

- **MERGE operations**: Update existing nodes/relationships with new properties
- **Primary keys**: Determine when to create vs. update (see [Primary Key Implementation](primary_key_implementation.md))
- **Property updates**: Later sources update properties on existing nodes
- **No overwrites**: Existing non-null values are preserved unless explicitly configured

**Typical order**:
1. **Neo4j imports** - Curated enterprise data (e.g., service catalog, customer master)
2. **Database/Excel imports** - CRM exports, operational data
3. **BAML extractions** - LLM-extracted data from documents (fills gaps, adds context)

**Example**: If Neo4j defines `Customer.country` and BAML extraction doesn't, the Neo4j value persists.

## Warnings and How to Handle Them

### Structured Warnings Report

As of the latest version, KG creation generates a **comprehensive warnings report** in Markdown format. This report provides better visibility into cross-graph issues and categorizes warnings with actionable suggestions.

**Report Location**: `{kg_outputs}/{profile}-{tag}-warnings.md`

The report includes:
- **Categorized warnings**: Duplicate relationships, missing nodes, orphaned nodes, schema failures
- **Structured tables**: Easy-to-scan summary of issues
- **Actionable suggestions**: Specific recommendations for each category
- **Cross-graph detection**: Spots issues spanning multiple subgraph definitions

**Access methods**:
1. View the report file directly
2. Follow the link in `{profile}-{tag}-info.md`
3. Check CLI output at the end of KG creation

See [KG Create Enhancements](kg_create_enhancements.md#warnings-reporting) for detailed examples and usage.

### Common Warning Types

During KG creation, various warnings may appear. Here's a reference guide:

### Schema Warnings

#### "No graphs are registered in the GraphRegistry"
```
No graphs are registered in the GraphRegistry.
The following factories failed to load:
  - genai_graph.ekg.schema.my_factory:MyGraph: ImportError: cannot import name ...
```
**Cause**: Factory import failed due to syntax error, missing dependency, or wrong module path.  
**Solution**: Check the listed module paths and fix the import errors shown in the message.

#### "Multiple valid paths found for RELATION_NAME"
```
Multiple valid paths found for HAS_CONTACT (Customer → Person). 
Using: customer → customer.employees. Alternatives: customer → lead.
```
**Cause**: A relationship can be inferred from multiple field paths in the Pydantic model.  
**Solution**: The system auto-selects the best path using a **containment-first** heuristic:
1. **Containment preferred**: If the target path starts with the source path (e.g. `customer.employees` starts with `customer`), this path is preferred over lateral paths.
2. **Shallow depth as tiebreaker**: Among containment-equivalent paths, shallower ones are preferred.

If the auto-chosen path is still wrong, specify `field_paths` explicitly as a list of `(from_path, to_path)` tuples:
```python
GraphRelation(
    from_node=Customer,
    to_node=Person,
    name="HAS_CONTACT",
    field_paths=[("customer", "customer.employees")],  # Explicit path
)
```

#### "Multiple relationships defined between X and Y"
```
Multiple relationships defined between L3 and L3: SIMILAR_TO, CROSS_SELL
```
**Cause**: Multiple relationship types exist between the same node types.  
**Solution**: This is expected for rich schemas. No action needed unless relationships should be consolidated.

#### "No field paths found for X in the root model structure"
```
No field paths found for Customer in the root model structure; this node may be orphaned.
```
**Cause**: A node type cannot be reached by traversing fields from the root Pydantic model.  
**Solution**: 
- For **BAML/JSON factories**: Ensure the node type is reachable from the root model
- For **Neo4j factories**: Set `explicitly_defined=True` on `GraphNode` (done automatically for Neo4j imports)
- For **combined schemas**: This warning is suppressed for explicitly-defined nodes

#### "Class X is referenced in relationships but has no GraphNode"
```
Class Partner is referenced in relationships but has no GraphNode
```
**Cause**: A relationship references a node class that wasn't configured.  
**Solution**: Add a `GraphNode` configuration for the missing class.

### Embedded Struct Warnings

#### "Embedded class X is not referenced on Y"
```
Embedded class Financials is not referenced on ReviewedOpportunity
```
**Cause**: A class listed in `extra_classes` isn't actually a field on the parent node.  
**Solution**: Verify the class is referenced as a field, or remove it from `extra_classes`.

#### "Embedded field 'X' on class Y has incompatible type"
```
Embedded field 'financials' on class ReviewedOpportunity has incompatible type list[Financials]
```
**Cause**: Embedded structs must be single objects, not lists.  
**Solution**: Use `Optional[Financials]` instead of `list[Financials]` for embedded fields.

### Import Warnings

#### "Failed to import X nodes/relationships: Cannot find property Y"
**Cause**: Schema mismatch between parquet data and current schema definition.  
**Solution**: Clear parquet caches and rebuild source graphs:
```bash
rm -rf /home/tcl/kg_outputs/*/parquet
cli kg create --kg source_graph --delete-first
cli kg create --kg target_graph --delete-first
```

#### "Failed to import X relationships: Binder exception"
**Cause**: Missing relationship properties in parquet due to schema evolution.  
**Solution**: Same as above - rebuild source graphs with current schema.

#### "Scanning of type <class 'numpy.ndarray'> has not been implemented"
**Cause**: Parquet contains numpy arrays that need conversion.  
**Solution**: System auto-converts numpy arrays to Python lists. If persists, clear parquet caches.

### Document Processing Warnings

#### "Subgraph root model must expose a 'metadata' map field"
```
Subgraph root model 'MyModel' must expose a 'metadata' map field
```
**Cause**: The root Pydantic model doesn't have a `metadata: dict` field.  
**Solution**: Add `metadata: dict[str, str] | None = None` to your root model.

## Suppressing Warnings

Schema validation warnings are informational and don't block execution. To suppress during automated builds:

```python
import warnings
warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation:")
```

Critical errors (schema mismatches, import failures) raise exceptions and must be fixed.

## Development Workflow

### Initial Setup
1. Define data sources (Neo4j exports, databases, BAML extractions)
2. Create factory classes for each source
3. Define canonical types in `common_nodes.py`
4. Configure in `ekg.yaml`
5. Build and validate

### Adding New Data Source
1. Create factory class (extends appropriate base factory)
2. Implement required methods (`get_model_class()`, `build_schema()`)
3. Import canonical types from `common_nodes.py`
4. Add to `ekg.yaml` configuration
5. Test with sample data
6. Build full graph

### Iteration
1. Modify source data → rebuild affected KG
2. Modify schema → rebuild from scratch (`--delete-first`)
3. Add new factory → add to config → rebuild

### Debugging
```bash
# Check schema
cli kg schema --kg my_kg

# Query node counts
cli kg cypher --kg my_kg "MATCH (n) RETURN labels(n)[0], count(n)"

# Inspect relationships
cli kg cypher --kg my_kg "MATCH ()-[r]->() RETURN type(r), count(r)"

# View in browser
open /home/tcl/kg_outputs/my_kg/my_kg-dev.html
```

## Performance Considerations

- **Batch processing**: Data loaded via pandas DataFrames (not row-by-row)
- **Parquet caching**: Avoids re-processing unchanged sources
- **Index fields**: Specified in `GraphNode.index_fields` for faster queries
- **MERGE efficiency**: Primary keys should be indexed fields

For large datasets (>100K nodes), consider:
- Breaking into smaller KG configurations
- Using parquet import/export extensively
- Optimizing primary key selection
