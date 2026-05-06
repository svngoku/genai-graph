"""Canonical GraphNode instances for shared entity types.

These singletons are the single source of truth for how each common entity
is stored in Kuzu: primary key, display name field, and which fields get
vector indexes (including the embedding model pinned per field).

Usage in a factory's build_schema():

```python
from genai_graph.ekg.schema.canonical_nodes import CustomerNode, L3Node, OpportunityNode

nodes = [
    OpportunityNode,
    CustomerNode,
    L3Node,
    # ... factory-specific nodes ...
]
```

Embedding model pinning
-----------------------
``index_fields`` accepts plain strings (uses ``kg_build.embeddings.default``) or
``(field_name, model_id)`` tuples that pin a specific model for that field.
L3.description uses ``ada_002@openai`` (1536-dim) because Stratnav ships
pre-computed OpenAI ada-002 vectors for that field; pinning the model here
ensures all KGs that include L3 nodes create a ``FLOAT[1536]`` column,
avoiding dimension mismatches when combining sub-graphs.
"""

from genai_graph.ekg.baml_client.types import Person
from genai_graph.ekg.schema.common_nodes import L3, Customer, Document, Geo, Opportunity, Partner
from genai_graph.kg.schema import GraphNode

# L3: Level 3 service offering from the service catalog.
# description_embedding is pinned to ada_002@openai (1536-dim) because Stratnav
# ships pre-computed OpenAI ada-002 vectors; all sub-graphs must agree on this
# dimension to allow merging without a cast error.
L3Node: GraphNode = GraphNode(
    node_class=L3,
    name_from="name",
    key_from="code",
    description="Level 3 service offering from service catalog",
    index_fields=[("description", "ada_002@openai")],
    explicitly_defined=True,
)

OpportunityNode: GraphNode = GraphNode(
    node_class=Opportunity,
    name_from="name",
    key_from="opportunity_id",
    description="Core opportunity information",
    index_fields=["description"],
)

CustomerNode: GraphNode = GraphNode(
    node_class=Customer,
    name_from="name",
    key_from="name",
    description="Customer organization details",
    explicitly_defined=True,  # Reachable via various multi-hop paths across factories
)

PersonNode: GraphNode = GraphNode(
    node_class=Person,
    name_from="name",
    key_from="name",
    description="Individual contacts and team members",
)

PartnerNode: GraphNode = GraphNode(
    node_class=Partner,
    name_from="name",
    key_from="name",
    description="Partner organization (technology vendor, subcontractor, etc.)",
)

GeoNode: GraphNode = GraphNode(
    node_class=Geo,
    name_from="name",
    key_from="name",
    description="Geographic region or country",
)

DocumentNode: GraphNode = GraphNode(
    node_class=Document,
    name_from="filename",
    key_from="path",
    description="Source document from which graph data was extracted",
    explicitly_defined=True,
)
