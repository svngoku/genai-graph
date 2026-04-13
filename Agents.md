# Agent Guidelines

This document provides guidelines for AI coding agents working on this codebase.

See also **[Agents_Skills.md](Agents_Skills.md)** for step-by-step procedures
(merging node types, assessing dead fields, adding nodes, etc.).

## Data Modeling Guidelines

### Use Pydantic Models (Strongly Preferred)

This project uses **Pydantic v2** as the standard for data modeling. All new code should use Pydantic models for:

- Data transfer objects (DTOs)
- Configuration classes
- API request/response models
- Domain models
- Result objects from functions

**DO:**
```python
from pydantic import BaseModel, Field

class UserResult(BaseModel):
    """Result of user lookup operation."""
    user_id: str
    name: str
    email: str | None = None
    tags: list[str] = Field(default_factory=list)
    
    # Use model_config instead of class Config
    model_config = {"frozen": True}  # For immutable models
```

**DON'T:**
```python
# Avoid dataclasses - use Pydantic instead
from dataclasses import dataclass

@dataclass
class UserResult:  # ❌ Use Pydantic BaseModel
    user_id: str
    name: str
```

### JSON Serialization

Always use Pydantic's built-in serialization instead of `json.dumps()`:

**DO:**
```python
# Serialize Pydantic model to JSON string
json_string = model.model_dump_json(indent=2)

# Serialize to dict first if needed
data_dict = model.model_dump()

# Parse JSON to Pydantic model
model = MyModel.model_validate_json(json_string)

# Parse dict to Pydantic model
model = MyModel.model_validate(data_dict)
```

**DON'T:**
```python
import json

# Avoid manual JSON serialization when Pydantic models are involved
json_string = json.dumps(model.__dict__)  # ❌
json_string = json.dumps(model.model_dump())  # ❌ Use model_dump_json() instead
```

**Exception:** Direct `json.loads()`/`json.dumps()` is acceptable when:
- Reading/writing raw JSON files that aren't Pydantic models
- Interacting with external APIs that return plain JSON
- Processing JSONL files line by line (e.g., Neo4j import/export)
- Embedding JSON in HTML templates (e.g., D3.js visualization data)

### Avoid Anonymous Data Structures

**DON'T use:**
- Unnamed tuples: `return (status, count, message)`
- Plain dicts for structured data: `return {"status": "ok", "count": 5}`
- dataclasses (use Pydantic instead)

**DO use:**
```python
class OperationResult(BaseModel):
    status: str
    count: int
    message: str | None = None

def process() -> OperationResult:
    return OperationResult(status="ok", count=5)
```

### Type Hints and Optional Fields

Use modern Python type hints (3.10+ syntax):

```python
from pydantic import BaseModel, Field

class Config(BaseModel):
    # Use | None instead of Optional[str]
    name: str | None = None
    
    # Use list[str] instead of List[str]
    tags: list[str] = Field(default_factory=list)
    
    # Use dict[str, Any] instead of Dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
```

### Model Configuration

Use `model_config` dict instead of inner `Config` class:

```python
class MyModel(BaseModel):
    path: Path
    
    model_config = {
        "arbitrary_types_allowed": True,  # For non-JSON types like Path, UPath
        "frozen": True,  # For immutable models
        "extra": "forbid",  # Reject unknown fields
    }
```

## Legacy Code

### Remove Backward Compatibility Code

When you encounter code marked as "backward compatibility", "legacy", or similar:

1. Evaluate if the compatibility is still needed (usually it's not)
2. If the code path is not exercised by tests, consider removing it
3. Simplify the code by removing fallback paths
4. Update any callers to use the modern API

### Avoid Creating New Compatibility Layers

Don't add `# for backward compatibility` code. Instead:
- Migrate callers to the new API
- Use deprecation warnings if a transition period is needed
- Remove old APIs in the same PR when possible

## Error Handling

Use Pydantic validation for input validation:

```python
from pydantic import BaseModel, field_validator

class QueryInput(BaseModel):
    query: str
    limit: int = 10
    
    @field_validator('limit')
    @classmethod
    def validate_limit(cls, v: int) -> int:
        if v < 1 or v > 1000:
            raise ValueError('limit must be between 1 and 1000')
        return v
```

## Summary

1. **Pydantic v2** is the standard for all data models
2. **Avoid** `dataclass`, unnamed tuples, and plain dicts for structured data
3. **Use** `model_dump_json()` instead of `json.dumps()` for Pydantic models
4. **Remove** legacy/backward compatibility code when safe
5. **Use** modern Python 3.10+ type hints (`str | None`, `list[str]`)

## Knowledge Graph Backend

This project uses **Ladybug** as the graph database backend. Ladybug is a maintained fork of Kuzu with full API compatibility and identical Cypher query support.

### Important Notes for LLM Integration

- Ladybug and Kuzu databases are fully compatible at the data and schema level
- All Cypher queries work identically on both platforms
- Existing Kuzu knowledge base documents and examples can be used directly
- The `KuzuBackend` class in `genai_graph/kg/backend.py` has been updated to use Ladybug
- JSON import/export formats are identical between the two systems

### Resources

- **Ladybug Repository**: https://github.com/LadybugDB/ladybug
- **Ladybug Documentation**: Available in the GitHub repository
- **Cypher Query Support**: Full Cypher dialect support (compatible with Kuzu syntax)
- **Neo4j Import**: Use the `cli neo4j import` command to convert Neo4j exports to Ladybug format
