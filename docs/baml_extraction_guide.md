# BAML Extraction and Graph Factory Creation Guide

This guide describes how to modify BAML extraction schemas and create graph factories to build knowledge graphs from text documents.

## Quick Reference

| Task | Files to Modify | Command |
|------|----------------|---------|
| Add/modify extracted fields | `baml_src/schema/*.baml` | `baml-cli generate` |
| Update graph schema | `ekg/schema/*_review.py` | N/A |
| Configure KG | `config/ekg.yaml` | N/A |
| Extract from documents | N/A | `cli baml extract` |
| Build knowledge graph | N/A | `cli kg create` |

## Architecture Overview

```
Text Documents (MD/PDF)
    ↓
BAML Extraction (LLM) → JSON files
    ↓
JsonFileBackedFactory → Pydantic Models
    ↓
GraphSchema → Node/Relationship Configuration
    ↓
Kuzu Graph Database
```

## Step 1: Define BAML Schema

BAML schemas define what information to extract from documents using LLMs.

**Location**: `genai_graph/ekg/baml_src/schema/*.baml`

### Example: Adding a New Field

```baml
// In rainbow_review.baml
class ReviewedOpportunity {
  opportunity Opportunity
  start_date string? @description("Planned start date")
  
  // Add new field here
  statement_of_work KeyStatementOfWorkElement? @description("Key requirements and objectives")
  
  team Person[] @description("Team members")
}

// Define the new class
class KeyStatementOfWorkElement {
  objectives string[]? @description("Key business objectives")
  scope string? @description("Scope of work description")
  requirements string[]? @description("Key requirements")
  success_metrics string[]? @description("Success criteria")
}
```

### BAML Class Types

- **Simple types**: `string`, `int`, `float`, `bool`
- **Optional**: `string?` (can be null)
- **Arrays**: `string[]`, `Person[]`
- **Nested objects**: `KeyStatementOfWorkElement` (reference to another class)
- **Enums**: `OpportunityStatus` (predefined values)
- **Maps**: `map<string, string>` (key-value pairs)

### Field Annotations

- `@description("text")` - Helps LLM understand what to extract
- `@alias("alternate name")` - Use different name in output
- `@@description(#"multi-line"#)` - Class-level description

### Generate Python Types

After modifying BAML files:

```bash
cd genai_graph/ekg
baml-cli generate
```

This generates `baml_client/types.py` with Pydantic models.

## Step 2: Create or Update Graph Factory

Graph factories define how extracted data becomes nodes and relationships in the graph.

**Location**: `genai_graph/ekg/schema/*_review.py`

### Basic Factory Structure

```python
from pydantic import BaseModel
from genai_graph.ekg.baml_client.types import ReviewedOpportunity
from genai_graph.kg.factories import JsonFileBackedFactory
from genai_graph.kg.schema import GraphSchema, GraphNode, GraphRelation

class ReviewedOpportunityGraph(JsonFileBackedFactory, BaseModel):
    """Factory for processing reviewed opportunity documents."""
    
    def get_model_class(self) -> type[BaseModel]:
        """Return the root Pydantic model class."""
        return ReviewedOpportunity
    
    def build_schema(self) -> GraphSchema:
        """Define nodes and relationships."""
        from genai_graph.ekg.baml_client.types import (
            Opportunity,
            Customer,
            Person,
            KeyStatementOfWorkElement,
        )
        
        nodes = [
            GraphNode(
                node_class=Opportunity,
                name_from="name",
                key_from="opportunity_id",
                description="Core opportunity information",
                index_fields=["name", "status"],
            ),
            # ... more nodes
        ]
        
        relations = [
            GraphRelation(
                from_node=ReviewedOpportunity,
                to_node=Opportunity,
                name="REVIEWS",
                description="Links review to opportunity",
            ),
            # ... more relationships
        ]
        
        return GraphSchema(
            root_model_class=ReviewedOpportunity,
            nodes=nodes,
            relations=relations
        )
```

### Node Configuration

```python
GraphNode(
    node_class=Person,           # Pydantic class
    name_from="name",            # Field for display name
    key_from="AUTO_ID",          # Primary key (AUTO_ID or field name)
    description="Team member",   # Documentation
    index_fields=["name"],       # Fields to index for search
)
```

#### Key Field Options

- `"AUTO_ID"` - Auto-generate unique sequential ID
- `"name"` - Use a specific field as primary key
- Lambda function for complex logic

#### Embedding Structs in Parent Nodes

To avoid creating separate nodes for simple structs, use `extra_classes`:

```python
GraphNode(
    node_class=ReviewedOpportunity,
    extra_classes=[FinancialMetrics, KeyStatementOfWorkElement],
    name_from=lambda data, _: "Rainbow:" + str(data.get("start_date")),
    key_from="AUTO_ID",
)
```

Properties from `FinancialMetrics` and `KeyStatementOfWorkElement` will be embedded as columns in the `ReviewedOpportunity` table.

### Relationship Configuration

```python
GraphRelation(
    from_node=Opportunity,      # Source node class
    to_node=Customer,           # Target node class
    name="HAS_CUSTOMER",        # Relationship type
    description="Opportunity belongs to customer",
    field_paths=["opportunity.customer"],  # Optional: explicit path
)
```

The system auto-deduces relationship paths by traversing the Pydantic model structure.

## Step 3: Configure KG Creation

**Location**: `config/ekg.yaml`

```yaml
kg_configs:
  my_kg:
    graphs:
      - factory: genai_graph.ekg.schema.rainbow_review:ReviewedOpportunityGraph
        data_root: ${paths.rainbow_json}
        include: 
          - "*CNES*TMA*VENUS*"  # File pattern
        exclude: 
          - "fake/*"
        recursive: true
        file_embedding:
          metadata: ["Opportunity.opportunity_id", "Customer.name"]
```

### Import from Other KGs

```yaml
kg_configs:
  combined_kg:
    import:
      - base_kg              # Import nodes/rels from another KG
    graphs:
      - factory: genai_graph.ekg.schema.my_factory:MyGraph
        data_root: ${paths.my_data}
```

## Step 4: Extract Data from Documents

```bash
# Extract with BAML
cli baml extract \
  '${paths.rainbow_md}/real' \
  '${paths.rainbow_json}' \
  --function ExtractRainbow \
  --include "*CNES*.md" \
  --force
```

This runs LLM extraction on markdown files and generates JSON.

## Step 5: Build Knowledge Graph

```bash
# Build single KG
cli kg create --kg my_kg

# Build all KGs
cli kg create --all-graphs

# Rebuild from scratch
cli kg create --kg my_kg --delete-first
```

## Common Patterns

### Pattern 1: Simple Embedded Properties

For simple structs that should be embedded (not separate nodes):

**BAML**:
```baml
class ReviewedOpportunity {
  financials FinancialMetrics
}

class FinancialMetrics {
  tcv float?
  annual_revenue float?
}
```

**Factory**:
```python
GraphNode(
    node_class=ReviewedOpportunity,
    extra_classes=[FinancialMetrics],  # Embed into parent
    key_from="AUTO_ID",
)
```

**Result**: `ReviewedOpportunity` table has columns `tcv` and `annual_revenue`.

### Pattern 2: Separate Nodes with Relationships

For complex entities that should be separate nodes:

**BAML**:
```baml
class ReviewedOpportunity {
  partners Partner[]  // List of partners
}

class Partner {
  name string
  p_role_ string?
}
```

**Factory**:
```python
nodes = [
    GraphNode(node_class=ReviewedOpportunity, ...),
    GraphNode(node_class=Partner, key_from="AUTO_ID"),  # Separate node
]

relations = [
    GraphRelation(
        from_node=ReviewedOpportunity,
        to_node=Partner,
        name="HAS_PARTNER",
    ),
]
```

**Result**: Separate `Partner` nodes with `HAS_PARTNER` relationships.

### Pattern 3: Relationship Properties

For storing properties on relationships (edge properties):

**BAML**:
```baml
class Competitor {
  name KnownCompetitor
  p_name_ string    // Properties with p_*_ prefix become edge properties
  p_comment_ string?
}
```

**Factory**: Properties starting with `p_` are automatically extracted as relationship properties.

### Pattern 4: Cross-Factory Node Unification (Canonical Types)

To ensure nodes from different factories merge into the same table, use canonical types from `common_nodes.py`.

**When to use**: 
- Entity types that appear in multiple data sources (Customer, Geo, Person, etc.)
- Need deduplication across factories (e.g., same customer from BAML and Neo4j exports)

**⚠️ CRITICAL**: The canonical wrapper class must have the **same `__name__`** as the BAML type for table deduplication to work!

**Step 1**: Define canonical type in `common_nodes.py` with matching name:
```python
from genai_graph.ekg.baml_client.types import Customer as BamlCustomer
from genai_graph.ekg.baml_client.types import Geo as BamlGeo

class Customer(BamlCustomer):
    """Extended Customer with fields from multiple sources."""
    iris_code: str | None = None      # From Neo4j
    country: str | None = None         # From Neo4j

class Geo(BamlGeo):  # ✅ Name matches BamlGeo.__name__ = "Geo"
    """Canonical geographic location type."""
    # Wraps BAML Geo for consistency across factories
```

**❌ WRONG**: Naming it differently breaks deduplication:
```python
class GeoLocation(BamlGeo):  # ❌ GeoLocation.__name__ ≠ "Geo"
    """This will create a DIFFERENT table than Geo!"""
```

**Step 2**: Import canonical types in ALL factories that use them:
```python
# In rainbow_review.py (BAML factory)
from genai_graph.ekg.schema.common_nodes import Customer, Geo

# In stratnav.py (Neo4j factory) 
from genai_graph.ekg.schema.common_nodes import Customer, Geo

# Use canonical types in GraphNode configurations
GraphNode(node_class=Geo, ...)  # ✅ Uses canonical type
```

**Why this matters**: Table names are derived from `node_class.__name__`. If `Geo.__name__` = "Geo" in one factory and `GeoLocation.__name__` = "GeoLocation" in another, they create separate tables even though they represent the same entity!

**Result**: All factories create nodes in the same table (e.g., all geo data goes to `Geo` table).

## Workflow Summary

### Initial Setup
1. Define BAML schema (`*.baml`)
2. Run `baml-cli generate`
3. Create factory class (`*_review.py`)
4. Configure in `ekg.yaml`

### Iteration
1. **Modify extraction**: Edit BAML → regenerate → re-extract
2. **Modify graph**: Edit factory → rebuild KG
3. **Add fields**: Update BAML → regenerate → update factory → re-extract → rebuild

### Testing
```bash
# Extract sample
cli baml extract SOURCE DEST --function ExtractRainbow --include "test*.md"

# Build test KG
cli kg create --kg test_kg

# Query
cli kg cypher "MATCH (n) RETURN labels(n)[0], count(n)"
```

## Troubleshooting

### Issue: "Called Option::unwrap() on a None value"
**Cause**: BAML fingerprinting error after schema changes  
**Solution**: Sometimes happens with complex schemas. The JSON files are generated successfully despite the error.

### Issue: "Expression X has data type STRING but expected STRUCT"
**Cause**: Old JSON files don't have new fields  
**Solution**: Re-run extraction with `--force` to regenerate all JSON files

### Issue: "Cannot import name 'GEO' from baml_client.types"
**Cause**: Case mismatch - BAML generates `Geo` not `GEO`  
**Solution**: Fix imports to match generated class names (check `baml_client/types.py`)

### Issue: "Cannot find property X for n"
**Cause**: Schema evolved but old parquet caches exist  
**Solution**: Delete parquet caches and rebuild:
```bash
rm -rf /home/tcl/kg_outputs/*/parquet
cli kg create --all-graphs
```

### Issue: Warning about multiple paths
**Cause**: Relationship can be inferred from multiple field paths  
**Solution**: This is informational. Specify `field_paths=[...]` explicitly if needed.

## Best Practices

1. **Start small**: Extract a few fields, verify, then expand
2. **Use descriptions**: Help LLM understand what to extract
3. **Test extraction**: Process 1-2 documents before full batch
4. **Check JSON output**: Verify extracted data before building graph
5. **Use common_nodes**: Define canonical types for cross-factory deduplication
6. **Version BAML schemas**: Track extraction schema evolution
7. **Consistent naming**: Use `p_*_` prefix for relationship properties
8. **Document schemas**: Add descriptions to all nodes and relationships

## Reference Commands

```bash
# BAML generation
cd genai_graph/ekg && baml-cli generate

# Extract data
cli baml extract SOURCE DEST --function ExtractRainbow --force

# Build graphs
cli kg create --kg my_kg
cli kg create --all-graphs
cli kg create --kg my_kg --delete-first

# Query graphs
cli kg cypher "MATCH (n:Customer) RETURN n.name LIMIT 10"
cli kg schema
```
