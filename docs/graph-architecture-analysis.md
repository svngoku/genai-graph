# Graph Architecture Analysis

## Scope

This note analyzes the current graph-definition and graph-construction architecture across two repositories:

- `genai-graph`: the generic graph library and ingestion/runtime code
- `ekg-atos`: the current main project using that library

The goal is not to propose a full rewrite. The goal is to identify where the design is already strong, where responsibilities are blurred, and what changes would make the system simpler to understand, easier to test, and less coupled to the original EKG use case.

## Executive Summary

The current design has a solid core idea:

- use Pydantic models as the domain source of truth
- describe graph extraction with `GraphNode`, `GraphRelation`, and `GraphSchema`
- support multiple source types through factories
- produce a canonical resolved schema for documentation, visualization, and querying

That core is worth keeping.

The main problems are architectural, not conceptual:

1. `genai-graph` is not fully generic yet. It still contains an `ekg` subpackage, project-specific BAML assets, project-specific graph schemas, and workflow/config conventions.
2. `GraphSchema` currently mixes declarative schema definition with traversal, auto-deduction, mutation, merge support, and validation heuristics.
3. `ResolvedSchema` is a good idea, but its enrichment path is hardwired to project-specific BAML parsing.
4. The authoring API is still fairly low-level and stringly-typed for humans creating new graphs.
5. Testing is skewed toward integration and regression slices. The semantic core of `GraphSchema` is still under-tested as an independent unit.
6. Documentation and notebooks explain execution better than authoring. There is not yet a short didactic path that shows how to define a graph from scratch.

The most important design move is to split the system into four clear layers:

1. domain models
2. graph definition
3. schema compilation and enrichment
4. source ingestion and graph materialization

Everything else becomes easier once that split is explicit in code and docs.

## What The Current Design Gets Right

### 1. Pydantic-first modeling is the right center of gravity

Keeping Pydantic models as the canonical representation of structured entities is the right decision. It gives:

- runtime validation
- one place to define field shapes
- good leverage for introspection
- a stable base for export, ingestion, and documentation

This is the right long-term abstraction. The redesign should reinforce it, not replace it.

### 2. `GraphNode` and `GraphRelation` are the correct conceptual primitives

The API in `genai_graph/kg/schema/core.py` is directionally correct:

- `GraphNode` describes how a Pydantic class becomes a node table
- `GraphRelation` describes graph edges between node kinds
- `GraphSchema` groups them under a root extraction model

This is already simpler and more explicit than hiding graph behavior entirely in decorators or magic reflection.

### 3. Multiple source families are a real strength

The source abstractions are already useful and worth preserving:

- `JsonFileBackedFactory` for BAML-extracted documents
- `TableBackedFactory` for CSV or Excel style data
- `Neo4jImportFactory` for legacy/imported graph data
- `SimilarityFactory` for learned or derived relationships
- `DocumentDirectoryFactory` for generic document graphs

This is a meaningful product capability. The problem is not having too many sources. The problem is that some source concerns are mixed with graph-definition concerns.

### 4. `ResolvedSchema` is a valuable concept

The idea in `genai_graph/kg/schema/resolved.py` is good: one canonical schema representation used by:

- markdown rendering for prompts
- D3 or HTML visualization
- vector index metadata
- query-time schema loading

That is the correct direction. The implementation just needs cleaner inputs and a more generic enrichment pipeline.

### 5. Canonical shared node definitions are useful

The `canonical_nodes.py` pattern in `ekg-atos` is worth keeping. It centralizes:

- primary key choice
- display name choice
- vector-indexed fields
- shared node identity conventions across factories

This is one of the best current ideas for usability because it lets graph authors reuse stable entity definitions across multiple source-specific graphs.

## Current Architecture

### Logical flow today

At a high level, the system works like this:

1. a factory loads source records as Pydantic objects
2. the factory returns a `GraphSchema`
3. `GraphSchema` introspects the model graph and deduces node field paths, relation field paths, and excluded fields
4. extraction code walks those paths and emits node and relation records
5. backend code materializes them into Ladybug
6. `ResolvedSchema` renders documentation, visualization, and vector index metadata

That end-to-end story is coherent.

### Where the boundaries are blurred

The same high-level flow is currently distributed across several overlapping responsibilities:

- `GraphSchema` is both a declaration and a compiler-like object
- `ResolvedSchema` is both a canonical form and a BAML-specific enrichment consumer
- factories both describe graph shape and own source-loading or cache behavior
- graph identity is partly explicit and partly inferred from Python class names
- registry merge behavior depends on naming conventions rather than explicit graph identity

This is why the design feels non-orthogonal. Most parts are individually reasonable, but the boundaries between them are not crisp enough.

## Main Architectural Problems

### 1. `genai-graph` still contains project code

This is the clearest design problem.

Today, `genai-graph` still contains:

- `genai_graph/ekg/schema/architecture_doc.py`
- `genai_graph/ekg/schema/canonical_nodes.py`
- `genai_graph/ekg/schema/common_nodes.py`
- `genai_graph/ekg/schema/crm_export.py`
- `genai_graph/ekg/schema/learned_graph.py`
- `genai_graph/ekg/schema/rainbow_review.py`
- `genai_graph/ekg/schema/rfq_review.py`
- `genai_graph/ekg/schema/stratnav.py`
- `genai_graph/ekg/baml_client/...`

The same graph families also exist in `ekg-atos`:

- `ekg_atos/schema/architecture_doc.py`
- `ekg_atos/schema/canonical_nodes.py`
- `ekg_atos/schema/common_nodes.py`
- `ekg_atos/schema/crm_export.py`
- `ekg_atos/schema/learned_graph.py`
- `ekg_atos/schema/rainbow_review.py`
- `ekg_atos/schema/rfq_review.py`
- `ekg_atos/schema/stratnav.py`

The duplication is not only in code. It also exists in workflow config:

- `genai-graph/config/workflows/graph_construction.yaml`
- `ekg-atos/config/workflows/graph_construction.yaml`

This creates four kinds of cost:

- library and application boundaries are unclear
- documentation examples can drift
- workflow configs can diverge silently
- refactors become harder because the same architecture exists in two places

### Recommendation

`genai-graph` should contain only generic graph infrastructure.

Move all EKG-specific graph definitions, common nodes, BAML-generated types, and project workflows fully into `ekg-atos`.

The generic repo should expose extension points, not bundled example production graphs.

### 2. `GraphSchema` does too much work

`GraphSchema` is currently the conceptual center of the system, but it is carrying too many responsibilities:

- schema declaration
- graph traversal over nested Pydantic types
- field-path deduction
- relation-path deduction
- excluded-field computation
- validation and warnings
- merged-root tracking for combined schemas
- mutation of child node and relation objects via private attributes

This is the main reason the design feels complicated.

### Why this matters

The class is named like a declarative model, but it behaves partly like a compiler and partly like a mutable execution plan. That makes it harder to reason about:

- when something is user input vs derived state
- what invariants hold before validation vs after validation
- how to test logic without constructing full factories or backends

### Recommendation

Keep `GraphSchema` as a mostly declarative definition object, then introduce a separate compilation step.

For example:

```python
class GraphSchema(BaseModel):
    root_model_class: type[BaseModel] | None = None
    nodes: list[GraphNode]
    relations: list[GraphRelation]


class CompiledGraphSchema(BaseModel):
    definition: GraphSchema
    node_bindings: list[CompiledNodeBinding]
    relation_bindings: list[CompiledRelationBinding]
    warnings: list[str]
```

Then move these behaviors out of `GraphSchema`:

- model traversal
- path inference
- reachability checks
- relation disambiguation
- excluded field derivation
- merge-time normalization

That would make the system more layered and far easier to unit test.

### 3. `ResolvedSchema` is generic in purpose but project-specific in enrichment

`ResolvedSchema` is intended to be the canonical enriched schema representation. That is good.

The problem is that the enrichment path is hardwired in `genai_graph/kg/schema/_helpers.py` to parse BAML descriptions from:

- `genai_graph.ekg.baml_client.inlinedbaml`

This is not a generic dependency. It couples the generic schema rendering layer to one project's BAML assets.

### Why this matters

Not every graph will come from BAML.

Even for BAML-backed graphs, enrichment should be optional and pluggable. Otherwise `ResolvedSchema` is forced to know too much about one extraction technology.

### Recommendation

Introduce an enrichment-provider interface.

Example:

```python
class SchemaMetadataProvider(Protocol):
    def class_description(self, cls: type[BaseModel]) -> str: ...
    def field_description(self, cls: type[BaseModel], field_name: str) -> str: ...
    def enum_descriptions(self) -> dict[str, dict[str, str]]: ...
```

Then provide implementations such as:

- `PydanticMetadataProvider`
- `BamlMetadataProvider`
- `YamlMetadataProvider`

`ResolvedSchema.from_graph_schema()` should accept one provider or a provider chain instead of importing project-specific BAML assets directly.

### 4. Identity currently depends too much on class names and strings

The current merge rules deduplicate nodes by `node_class.__name__`, and relationships are deduplicated by `(from_name, to_name, rel_name)`.

This is pragmatic, but it is also fragile.

### Risks

- renaming a Pydantic class can silently change table identity
- two unrelated classes with the same name collide
- cross-repo reuse depends on naming discipline rather than explicit graph identity
- authoring depends on string field names such as `name_from`, `key_from`, `field_paths`, and relation path overrides

The current approach works because the current graphs are written by a small set of people with shared conventions. It will become harder to sustain as the system becomes more reusable.

### Recommendation

Make graph identity explicit.

For example, add stable identifiers such as:

```python
class GraphNode(BaseModel):
    node_class: type[BaseModel]
    label: str | None = None
    table_name: str | None = None
    key_from: str | Callable[..., str] = "AUTO_ID"
    name_from: str | Callable[..., str]
```

Then normalize once during compilation:

- node storage identity
- display label
- merge identity
- query/render identity

Do not rely on `__name__` as the only durable identity rule.

### 5. The source abstraction mixes sources and derived graph transforms

`JsonFileBackedFactory`, `TableBackedFactory`, and `Neo4jImportFactory` are source loaders.

`SimilarityFactory` is different. It does not primarily load source entities. It computes derived graph relationships from existing graph content.

That means the current `KgFactory` abstraction is handling at least two concepts:

- source ingestion
- graph transformation or enrichment

### Recommendation

Split the abstraction into two families:

- `GraphSource` or `RecordSource`
- `GraphTransform`

Example:

```python
class GraphSource(ABC):
    def build_schema(self) -> GraphSchema: ...
    def iter_records(self) -> Iterable[BaseModel]: ...


class GraphTransform(ABC):
    def input_requirements(self) -> GraphRequirementSet: ...
    def output_schema(self) -> GraphSchema: ...
    def run(self, backend: KgBackend) -> TransformResult: ...
```

This would make learned relationships, post-processing, document chunking, enrichment, and future resolution steps much clearer.

### 6. The authoring experience is still more low-level than it needs to be

Today a graph author often has to do several manual things:

- create or extend Pydantic models
- create module-level `GraphNode` singletons
- manually choose `name_from` and `key_from`
- manually declare `GraphRelation`s
- sometimes override `field_paths`
- sometimes remember extra classes for embedded structs
- sometimes coordinate canonical nodes across different source graphs

This is powerful, but it is still fairly expert-oriented.

### Recommendation

Keep the explicit API, but add a higher-level authoring layer on top of it.

This could be done with either:

- lightweight `typing.Annotated` metadata
- decorators
- or a small builder DSL

The right move is not to replace explicit configuration with magic. The right move is to make the common cases much shorter while preserving an escape hatch.

## Ideas For A Better Authoring API

### Add a `GraphBlueprint` builder

Example:

```python
schema = (
    GraphBlueprint(root=ReviewedOpportunity)
    .use(OpportunityNode, CustomerNode, PersonNode)
    .node(TechnicalApproach, name_from="architecture", key_from="AUTO_ID", index_fields=["architecture"])
    .rel("REVIEWS", ReviewedOpportunity, Opportunity)
    .rel("HAS_CUSTOMER", Opportunity, Customer)
    .with_documents()
    .build()
)
```

Advantages:

- very didactic
- easy to teach in docs and notebooks
- hides repetitive list assembly
- allows sensible defaults such as `.with_documents()`

This is probably the best usability improvement for new authors.

## Recommended Target Architecture

The target architecture should be explicit about four layers.

### Layer 1. Domain models

Project-owned Pydantic classes.

Examples:

- `ekg_atos/schema/common_nodes.py`
- BAML-generated model types
- Neo4j-import mapping target types

This layer should not depend on ingestion runtime details.

### Layer 2. Graph definition

A small, declarative package that contains:

- `GraphNode`
- `GraphRelation`
- `GraphSchema`
- optional builder helpers such as `GraphBlueprint`

This layer should be light, explicit, and mostly free of inference side effects.

### Layer 3. Schema compilation and enrichment

This layer should transform definitions into compiled or resolved forms.

Possible responsibilities:

- model traversal
- field-path binding
- relation-path binding
- exclusion inference
- reachability checks
- metadata enrichment
- rendering support

Suggested objects:

- `SchemaCompiler`
- `CompiledGraphSchema`
- `ResolvedSchema`
- `SchemaMetadataProvider`

This is where most logic currently inside `GraphSchema` should move.

### Layer 4. Ingestion and materialization

This layer should own:

- source reading
- caching
- extraction
- merge semantics
- vector index creation
- backend persistence

This layer consumes `CompiledGraphSchema` or `ResolvedSchema`; it should not have to deduce graph meaning on its own.

## Concrete Refactoring Proposal

### Phase 1. Finish the repository split

This is the highest-value move.

1. Remove the `genai_graph/ekg` package from `genai-graph`.
2. Keep all EKG-specific schemas, canonical nodes, common nodes, and BAML clients in `ekg-atos`.
3. Remove duplicated workflow definitions from `genai-graph/config/workflows/graph_construction.yaml`.
4. Replace any remaining hardcoded `ekg` naming in generic code and docs with `kg` or `graph`.
5. Make schema metadata providers injectable so the generic repo no longer imports `genai_graph.ekg.baml_client.inlinedbaml`.

This will immediately improve clarity without changing the core runtime.

### Phase 2. Introduce a compiler boundary

1. Keep `GraphNode`, `GraphRelation`, and `GraphSchema` as input objects.
2. Create `SchemaCompiler` that produces `CompiledGraphSchema`.
3. Move all current `GraphSchema` deduction logic into the compiler.
4. Make `ResolvedSchema` consume compiled schema plus metadata providers.

At that point the architecture becomes easier to reason about:

- graph authors define
- compiler binds
- renderer enriches
- ingestion executes

### Phase 3. Make identity explicit

1. add explicit node label or storage name support
2. stop using `__name__` as the only durable merge key
3. normalize merge identity in the compiler
4. add validation for accidental identity collisions

This is especially important if the system will support more independent projects.

### Phase 4. Improve the authoring API

1. keep explicit `GraphNode` and `GraphRelation`
2. add `GraphBlueprint` for common cases
3. optionally support `Annotated` metadata for local field-level hints
4. keep manual overrides for advanced cases

This gives usability without hiding too much logic.

## Testing Strategy

The next round of tests should target `GraphSchema` as the semantic core, independent of concrete factories.

That matches the stated need very well.

### What is tested reasonably well today

The current test suite already covers several useful slices:

- end-to-end schema creation
- embedded struct regressions
- some factory behavior
- some merge and workflow behavior
- some error reporting

Those tests are valuable and should be kept.

### What is missing

There is still not a sufficiently rich direct test suite for schema semantics themselves.

The most important missing test categories are:

### 1. Pure schema compilation tests

Test only Pydantic models plus graph definitions.

Cases:

- root node binding
- nested model path discovery
- optional nested model path discovery
- list nested model path discovery
- multiple occurrences of the same type
- disambiguation of relation candidates
- explicit `field_paths` overriding inferred paths

### 2. Identity and merge tests

Cases:

- same label, different classes
- same class, different labels
- merge conflict diagnostics
- explicit identity overriding class-name identity

### 3. Exclusion and projection tests

Cases:

- relation targets excluded from node properties
- embedded structs preserved as embedded fields
- relation property extraction from `p_*_` fields
- metadata field treatment

### 4. Callable behavior tests

Cases:

- `name_from` callables
- `key_from` callables
- skipped-node behavior when computed keys return `None`
- stable error reporting when callables fail

### 5. Renderer and enrichment tests

Cases:

- `ResolvedSchema` markdown output
- HTML and D3 rendering shape
- enum inclusion
- vector index metadata
- BAML provider vs non-BAML provider behavior

### Suggested test layout

Add a test structure that reflects the future layered architecture:

- `tests/unit_tests/schema/test_definition.py`
- `tests/unit_tests/schema/test_compiler.py`
- `tests/unit_tests/schema/test_identity.py`
- `tests/unit_tests/schema/test_rendering.py`
- `tests/unit_tests/schema/test_metadata_providers.py`

Then keep factory and backend tests separate:

- `tests/unit_tests/factories/...`
- `tests/integration_tests/...`

This would make it much easier to evolve `GraphSchema` without fear.

## Documentation And Notebook Plan

The documentation gap is real.

`genai-graph` has some notebooks, but they are not currently a didactic authoring path. `ekg-atos` currently has no notebooks at all.

The documentation should teach graph authorship, not only graph execution.

### Recommended docs

### 1. `docs/graph-definition-guide.md`

Teach:

- what belongs in domain models
- what belongs in `GraphNode`
- what belongs in `GraphRelation`
- when to use canonical nodes
- when to use embedded structs vs relationships

### 2. `docs/graph-authoring-patterns.md`

Teach:

- JSON-backed graph pattern
- table-backed graph pattern
- Neo4j-import graph pattern
- learned-relationship pattern
- provenance pattern with `DocumentNode`

### 3. `docs/schema-compilation.md`

Teach:

- what inference exists
- what identity rules exist
- what metadata providers add
- what gets rendered into the saved schema JSON

### 4. `docs/migration-from-ekg-specific-layout.md`

Teach:

- what moved from `genai-graph` to `ekg-atos`
- how to update imports
- how to migrate configs and workflows

### Recommended notebooks

### Notebook 1. Define a tiny graph from scratch

Show:

- two Pydantic models
- two `GraphNode`s
- one `GraphRelation`
- compiled schema output
- rendered markdown schema

### Notebook 2. Build one graph from three sources

Show:

- one JSON-backed source
- one table-backed source
- one imported legacy source
- merge identity and dedup behavior

### Notebook 3. Add learned relationships on top

Show:

- vector-indexed fields
- similarity transform
- derived relationship creation
- why transforms should be separate from sources

These would make the architecture much easier to teach and maintain.

## Specific Design Choices I Would Keep

Even after refactoring, I would keep the following ideas:

- Pydantic as the main data-modeling layer
- explicit `GraphNode` and `GraphRelation` objects
- a canonical resolved schema object for rendering and query support
- reusable canonical node definitions for shared entity types
- provenance through generic `Document` nodes
- support for multiple source families

These are good foundations.

## Specific Design Choices I Would Change First

If the goal is high impact with moderate risk, I would change these first:

1. remove the `genai_graph.ekg` package and duplicated workflow definitions
2. make BAML metadata enrichment pluggable
3. split `GraphSchema` from compilation logic
4. add explicit graph identity instead of relying only on class names
5. add a direct unit-test suite for schema semantics
6. add one didactic graph-authoring guide and one tiny notebook

That sequence gives architectural clarity before API polish.

## Recommended North Star

The ideal mental model for the future system is this:

- a project defines Pydantic domain models
- a project declares graph intent in a small explicit schema layer
- generic compiler logic resolves paths and identities
- optional metadata providers enrich the schema
- source loaders and transforms execute against that compiled schema
- renderers and query tools consume the same canonical resolved representation

That would make the design:

- simpler
- more orthogonal
- easier to test
- easier to teach
- easier to reuse outside the original EKG project

## Final Recommendation

The architecture does not need a revolution. It needs a separation pass.

The most important move is to make `genai-graph` truly generic and to treat `GraphSchema` as a declarative input rather than a mutable all-in-one engine.

If that is done, then `ResolvedSchema`, `Annotated` metadata, decorators, builder helpers, richer docs, and better tests can all fit naturally into a cleaner design instead of accumulating on top of an already blurred boundary.