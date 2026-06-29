# Graph Definition Guide

Define a knowledge graph in genai-graph: from Python models to a queryable Ladybug database in five minutes.

## 1. Define Your Domain Models

Use standard Pydantic models. Nested fields become relations.

```python
from pydantic import BaseModel

class Company(BaseModel):
    name: str
    sector: str | None = None

class Person(BaseModel):
    name: str
    title: str | None = None

class Project(BaseModel):
    title: str
    client: Company        # → FOR_CLIENT relation auto-detected
    lead: Person | None = None  # → HAS_LEAD relation auto-detected
```

## 2. Declare Graph Nodes

Wrap each model in a `GraphNode` and specify identity fields.

```python
from genai_graph.kg.schema import GraphNode

company_node = GraphNode(node_class=Company, name_from="name", key_from="name")
person_node  = GraphNode(node_class=Person,  name_from="name", key_from="name")
project_node = GraphNode(node_class=Project, name_from="title", key_from="title")
```

| Parameter | Purpose |
|-----------|---------|
| `name_from` | Field whose value is used as the human-readable display name |
| `key_from` | Field used as the unique identifier (primary key) in Ladybug |
| `description` | Optional free-text description for documentation and LLM prompts |
| `table_name` | Override the Ladybug table/label name (defaults to class name) |

## 3. Declare Relations

Relations can be explicit or auto-detected from field paths.

```python
from genai_graph.kg.schema import GraphRelation

client_rel = GraphRelation(
    from_node=project_node,
    to_node=company_node,
    name="FOR_CLIENT",          # Cypher relation type
    field_paths=[{"from": "", "to": "client"}],  # Optional: explicit path
)
lead_rel = GraphRelation(from_node=project_node, to_node=person_node, name="HAS_LEAD")
```

> **Auto-deduction**: If you omit `field_paths`, genai-graph traverses the root model's fields
> to find where each node type appears. This covers most cases. Use explicit `field_paths` when
> the same node type appears in multiple locations or in nested lists.

## 4. Build a GraphSchema

```python
from genai_graph.kg.schema import GraphSchema

schema = GraphSchema(
    root_model_class=Project,      # Entry point for field-path traversal
    nodes=[project_node, company_node, person_node],
    relations=[client_rel, lead_rel],
)
```

`GraphSchema` validates the schema at construction time. Any label collisions or orphaned nodes
produce `UserWarning` messages so you can catch problems early.

## 5. Create a Factory

Wrap the schema in a `JsonFileBackedFactory` (or another factory) that handles ingestion.

```python
from genai_graph.kg.factories import JsonFileBackedFactory

class ProjectGraph(JsonFileBackedFactory):
    schema = GraphSchema(
        root_model_class=Project,
        nodes=[project_node, company_node, person_node],
        relations=[client_rel, lead_rel],
    )
    source_model = Project
```

## 6. Ingest Data

```python
from pathlib import Path
from genai_graph.kg.backend import create_backend_from_config

backend = create_backend_from_config("my_graph")
graph = ProjectGraph(backend=backend)

projects = [
    Project(title="Alpha", client=Company(name="Acme", sector="Tech"), lead=Person(name="Alice")),
    Project(title="Beta",  client=Company(name="Acme"), lead=None),
]
graph.ingest(projects)
```

## 7. Query the Graph

```python
results = backend.execute_cypher("""
    MATCH (p:Project)-[:FOR_CLIENT]->(c:Company)
    RETURN p.title, c.name
""")
for row in results:
    print(row)
```

## Visualise the Schema

```python
from genai_graph.kg.schema import ResolvedSchema

resolved = ResolvedSchema.from_graph_schema(schema)
print(resolved.to_markdown())       # markdown table
resolved.to_html_file("schema.html")  # interactive D3 graph
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Two models with the same class name | Set `table_name="UniqueName"` on the `GraphNode` |
| Relations not auto-detected | Check that `root_model_class` is the model that contains the nested reference |
| Duplicate keys on ingest | Ensure `key_from` resolves to a unique value per entity |
| Missing `p_` prefix for edge properties | Prefix relation-specific properties with `p_` to separate them from node properties |

## Next Steps

- [Graph Authoring Patterns](graph-authoring-patterns.md) — JSON files, CRM tables, Neo4j exports, document ingestion
- [Schema Compilation Reference](schema-compilation.md) — field-path deduction rules, `table_name`, exclusion mechanics
