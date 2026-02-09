# Knowledge Graph Construction Process

This document describes how knowledge graphs are constructed from multiple heterogeneous data sources.

## Overview

The KG construction pipeline combines data from multiple sources (Neo4j exports, databases, BAML extractions) into a unified Kuzu graph database. The process handles:

- **Type unification**: Different sources may define the same entity (e.g., `Account` vs `Customer`)
- **Data deduplication**: MERGE operations prevent duplicate nodes and relationships
- **Schema evolution**: Adding columns when importing from different schema versions

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

### 1. Neo4jFactory (`genai_graph.kg.factories.neo4j_factory`)

Imports from Neo4j JSONL exports. Mappings define how Neo4j labels map to Kuzu node types:

```python
class StratnavGraph(Neo4jFactory):
    node_mappings = [
        Neo4jNodeMapping(
            neo4j_label="Account",      # Neo4j label
            node_class=Customer,        # Target Pydantic class
            key_field="name",           # Primary key
            description="Customer organization"
        ),
    ]
```

### 2. TableBackedFactory (`genai_graph.kg.factories.table_backed`)

Imports from database tables or Excel files via pandas:

```python
class CrmExtractGraph(TableBackedFactory):
    TOP_CLASS = Opportunity
    
    def mapper_function(self, row: dict) -> Opportunity:
        return Opportunity(
            opportunity_id=row["Atos Opportunity ID"],
            customer=Customer(name=row["Account Name"]),
        )
```

### 3. JsonFileBackedFactory (`genai_graph.kg.factories.json_file_backed`)

Processes JSON files from BAML extractions:

```python
class ReviewedOpportunityGraph(JsonFileBackedFactory):
    TOP_CLASS = ReviewedOpportunity
```

## Common Nodes for Type Unification

The `genai_graph.ekg.schema.common_nodes` module defines canonical types that should be used across factories:

```python
from genai_graph.ekg.baml_client.types import Customer as BamlCustomer

class Customer(BamlCustomer):
    """Extended Customer with fields from multiple sources."""
    
    # Fields from Neo4j/Stratnav
    country: str | None = None
    business_line: str | None = None
    
    # Fields from BAML extraction
    location: GeoLocation | None = None
```

**Key principle**: Classes with the same `__name__` create the same Kuzu table. So `baml_client.types.Customer` and `common_nodes.Customer` both create a "Customer" table.

## Schema Merging

When combining multiple graph factories, the `GraphRegistry.build_combined_schema()` method:

1. **Deduplicates nodes by class name** (not class identity)
2. **Deduplicates relationships by (from_name, to_name, rel_name)**
3. **Preserves descriptions** from the first-seen definition

```python
# In registry.py
for node in schema.nodes:
    node_name = node.node_class.__name__
    if node_name in seen_node_names:
        continue  # Skip duplicate
    seen_node_names.add(node_name)
    merged_nodes.append(node)
```

## Data Merging with Kuzu

The ingest layer uses Kuzu's `MERGE` operations for deduplication:

### Nodes
```cypher
LOAD FROM df
MERGE (n:Customer {name: name})
ON CREATE SET n.country = country, n.segment = segment
ON MATCH SET n.country = country, n.segment = segment
```

### Relationships
```cypher
LOAD FROM df
MATCH (a:Customer {name: from_id}), (b:Person {name: to_id})
MERGE (a)-[:HAS_CONTACT]->(b)
```

## Import/Export via Parquet

KG configurations can import from other configurations via parquet:

```yaml
# config/ekg.yaml
stratnav_subset_rainbow_crm:
  import:
    - one_rainbow_with_db    # Imports from parquet cache
    - stratnav_subset
```

The import process:
1. **Creates schemas** from imported KG configurations (recursively)
2. **Adds missing columns** via `ALTER TABLE ADD` for schema evolution
3. **Converts numpy arrays** to Python lists for Kuzu compatibility
4. **Imports data** using MERGE from parquet files

## Configuration

KG configurations are defined in `config/ekg.yaml`:

```yaml
kg_configs:
  one_rainbow_with_db:
    import:
      - crm_export          # Import CRM data first
    graphs:
      - factory: "genai_graph.ekg.schema.rainbow_review:ReviewedOpportunityGraph"
        data_root: ${paths.rainbow_json}
        include: ["*_CNES_*"]
```

## CLI Commands

```bash
# Create a specific KG
cli kg create --kg stratnav_subset_rainbow_crm --delete-first

# View schema
cli kg schema

# Execute Cypher queries
cli kg cypher "MATCH (c:Customer) RETURN c.name LIMIT 10"
```

## Data Source Priority

Sources are processed in order, with earlier sources considered more authoritative:

1. **Neo4j imports** - Curated enterprise data
2. **Database/Excel imports** - CRM exports, structured data
3. **BAML extractions** - LLM-extracted data from documents

When MERGE operations encounter existing data, properties from later sources update but don't override existing values unless explicitly configured.

## Warnings and How to Handle Them

During KG creation, various warnings may appear. Here's a reference guide:

### Schema Warnings

#### "Multiple valid paths found for RELATION_NAME"
```
Multiple valid paths found for HAS_CONTACT (Customer → Person). 
Using: customer → lead. Alternatives: customer → customer.employees.
```
**Cause**: A relationship can be inferred from multiple field paths in the Pydantic model.  
**Solution**: This is informational. The system auto-selects the first valid path. To specify explicitly:
```python
GraphRelation(
    from_node=Customer,
    to_node=Person,
    name="HAS_CONTACT",
    field_paths=["customer.employees"],  # Explicit path
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

#### "Failed to import X nodes/relationships"
```
Failed to import Customer nodes: Binder exception: Cannot find property country for n.
```
**Cause**: Schema mismatch between parquet data and current schema definition.  
**Solution**: Clear parquet caches and rebuild source graphs:
```bash
rm -rf /home/tcl/kg_outputs/*/parquet
cli kg create --kg source_graph --delete-first
cli kg create --kg target_graph --delete-first
```

#### "Scanning of type <class 'numpy.ndarray'> has not been implemented"
**Cause**: Parquet contains numpy arrays that need conversion.  
**Solution**: The system now auto-converts numpy arrays to Python lists. If this persists, clear parquet caches.

### Document Processing Warnings

#### "Subgraph root model must expose a 'metadata' map field"
```
Subgraph root model 'MyModel' must expose a 'metadata' map field
```
**Cause**: The root Pydantic model doesn't have a `metadata: dict` field.  
**Solution**: Add `metadata: dict[str, str] | None = None` to your root model.

## Suppressing Warnings

For combined schemas where validation warnings from different sources conflict:
```python
import warnings
warnings.filterwarnings("ignore", category=UserWarning, message="Graph schema validation:")
```

For production deployments, warnings are logged but don't block execution. Critical issues raise exceptions instead.
