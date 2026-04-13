"""Merge nodes and relationships into the graph database.

This module provides utilities for adding nodes and edges to the graph,
handling automatic merging based on key fields (typically 'name').

Uses Kuzu's DataFrame MERGE capability for efficient batch operations:
- LOAD FROM df MERGE (n:NodeType {key: key}) for batch node merging
- LOAD FROM df MATCH ... CREATE for batch relationship creation
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.kg.backend import KgBackend
from genai_graph.kg.manager import KgManager

if TYPE_CHECKING:
    from genai_graph.kg.ingest.extract import RelationshipRecord
    from genai_graph.kg.schema.core import GraphNode


# =============================================================================
# Type definitions for node data structures
# =============================================================================

# A single node's properties as a dictionary
NodeProperties = dict[str, Any]

# A list of nodes of the same type
NodeList = list[NodeProperties]


class NodeDataCollection(BaseModel):
    """Collection of nodes grouped by their type.

    This provides a typed wrapper around the common pattern of
    `dict[str, list[dict[str, Any]]]` used throughout the graph creation code.

    Each key is a node type name (e.g., "Person", "Opportunity"),
    and each value is a list of property dictionaries for nodes of that type.

    Example:
        ```python
        nodes = NodeDataCollection()
        nodes.add("Person", {"name": "Alice", "age": 30})
        nodes.add("Person", {"name": "Bob", "age": 25})
        nodes.add("Company", {"name": "Acme", "industry": "Tech"})

        # Access all persons
        for person in nodes.get("Person"):
            print(person["name"])

        # Get total count
        print(nodes.total_count())  # 3
        ```
    """

    data: dict[str, NodeList] = Field(default_factory=dict)

    def add(self, node_type: str, properties: NodeProperties) -> None:
        """Add a node with the given properties to the collection."""
        if node_type not in self.data:
            self.data[node_type] = []
        self.data[node_type].append(properties)

    def get(self, node_type: str) -> NodeList:
        """Get all nodes of a given type (empty list if none)."""
        return self.data.get(node_type, [])

    def ensure_type(self, node_type: str) -> None:
        """Ensure a node type exists in the collection (creates empty list if not)."""
        if node_type not in self.data:
            self.data[node_type] = []

    def types(self) -> list[str]:
        """Get all node types in this collection."""
        return list(self.data.keys())

    def items(self) -> list[tuple[str, NodeList]]:
        """Iterate over (node_type, node_list) pairs."""
        return list(self.data.items())

    def total_count(self) -> int:
        """Get total node count across all types."""
        return sum(len(nodes) for nodes in self.data.values())

    def __contains__(self, node_type: str) -> bool:
        return node_type in self.data

    def __getitem__(self, node_type: str) -> NodeList:
        return self.data[node_type]

    def __setitem__(self, node_type: str, nodes: NodeList) -> None:
        self.data[node_type] = nodes

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, list[dict[str, Any]]]) -> NodeDataCollection:
        """Create a NodeDataCollection from a raw dictionary."""
        return cls(data=data)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        """Convert to a raw dictionary (for backward compatibility)."""
        return self.data


# =============================================================================
# Parquet Collector for capturing DataFrames during merge
# =============================================================================


class ParquetCollector(BaseModel):
    """Collects DataFrames during merge operations for parquet export.

    This allows capturing the exact data being merged into the graph,
    avoiding the need to query it back out (which can hit Kuzu bugs).

    Thread-safe: all mutations are protected by a lock so that
    concurrent bundle preparation tasks can safely append data.
    """

    nodes: dict[str, pd.DataFrame] = Field(default_factory=dict)
    relationships: dict[str, pd.DataFrame] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    _lock: Any = None  # threading.Lock, lazily initialised

    def model_post_init(self, _context: Any) -> None:
        import threading

        object.__setattr__(self, "_lock", threading.Lock())

    def add_nodes(self, node_type: str, df: pd.DataFrame) -> None:
        """Add or append node data for a node type (thread-safe)."""
        lock = object.__getattribute__(self, "_lock")
        with lock:
            if node_type in self.nodes:
                self.nodes[node_type] = pd.concat([self.nodes[node_type], df], ignore_index=True)
            else:
                self.nodes[node_type] = df.copy()

    def add_relationships(self, rel_type: str, df: pd.DataFrame) -> None:
        """Add or append relationship data for a relationship type (thread-safe)."""
        lock = object.__getattribute__(self, "_lock")
        with lock:
            if rel_type in self.relationships:
                self.relationships[rel_type] = pd.concat([self.relationships[rel_type], df], ignore_index=True)
            else:
                self.relationships[rel_type] = df.copy()

    def get_node_count(self) -> int:
        """Get total node count across all types."""
        return sum(len(df) for df in self.nodes.values())

    def get_relationship_count(self) -> int:
        """Get total relationship count across all types."""
        return sum(len(df) for df in self.relationships.values())


# Global collector instance - set by KG creation flow
_parquet_collector: ParquetCollector | None = None


def set_parquet_collector(collector: ParquetCollector | None) -> None:
    """Set the global parquet collector for the current KG creation."""
    global _parquet_collector
    _parquet_collector = collector


def get_parquet_collector() -> ParquetCollector | None:
    """Get the global parquet collector."""
    return _parquet_collector


# =============================================================================
# Data Classes for structured return types
# =============================================================================


class MergeStats(BaseModel):
    """Statistics for a single node type merge operation."""

    created: int = 0
    matched: int = 0
    total: int = 0

    def __str__(self) -> str:
        return f"created={self.created}, matched={self.matched}, total={self.total}"


class NodeIdMapping(BaseModel):
    """Mapping from original node IDs to merged database IDs.

    For non-AUTO_ID nodes: maps (node_type, key_value) -> key_value
    For AUTO_ID nodes: maps (node_type, name) -> name (used for relationship matching)
    """

    mapping_data: dict[str, str] = Field(default_factory=dict, alias="_mapping")

    def _make_key(self, node_type: str, original_id: str) -> str:
        """Create a string key from node_type and original_id."""
        return f"{node_type}::{original_id}"

    def add(self, node_type: str, original_id: str, merged_id: str) -> None:
        """Add a mapping entry."""
        self.mapping_data[self._make_key(node_type, original_id)] = merged_id

    def get(self, node_type: str, original_id: str, default: str | None = None) -> str:
        """Get the merged ID for an original ID."""
        result = self.mapping_data.get(self._make_key(node_type, str(original_id)))
        if result is not None:
            return result
        return default if default is not None else str(original_id)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return self._make_key(key[0], key[1]) in self.mapping_data

    def __len__(self) -> int:
        return len(self.mapping_data)

    def items(self) -> list[tuple[tuple[str, str], str]]:
        """Return all mapping items."""
        result = []
        for k, v in self.mapping_data.items():
            parts = k.split(":.", 1)
            if len(parts) == 2:
                result.append(((parts[0], parts[1]), v))
        return result


class NodeMergeResult(BaseModel):
    """Result of a batch node merge operation."""

    stats: dict[str, MergeStats] = Field(default_factory=dict)
    id_mapping: NodeIdMapping = Field(default_factory=NodeIdMapping)

    def get_stats(self, node_type: str) -> MergeStats:
        """Get stats for a node type, creating if needed."""
        if node_type not in self.stats:
            self.stats[node_type] = MergeStats()
        return self.stats[node_type]

    def total_nodes(self) -> int:
        """Return total nodes across all types."""
        return sum(s.total for s in self.stats.values())


class NodeTypeConfig(BaseModel):
    """Configuration for how a node type should be merged.

    Encapsulates the primary key field and type hints for merge operations.
    """

    model_config = {"extra": "forbid"}

    node_type: str
    primary_key_field: str = "id"
    # Maps field name -> kuzu_type for top-level node fields (e.g., {"technical_stack": "STRING[]"})
    field_types: dict[str, str] = Field(default_factory=dict)
    # Maps struct field name -> dict of {sub_field_name: kuzu_type}
    struct_field_types: dict[str, dict[str, str]] = Field(default_factory=dict)

    @classmethod
    def from_graph_node(cls, node: GraphNode) -> NodeTypeConfig:
        """Create config from a GraphNode definition."""
        from genai_graph.kg.schema.doc_generator import _get_kuzu_type_for_field

        node_type = node.node_class.__name__
        key_from = node.key_from

        if key_from == "AUTO_ID" or callable(key_from):
            primary_key_field = "id"
        else:
            primary_key_field = key_from

        # Extract type information for top-level node fields
        field_types: dict[str, str] = {}
        if hasattr(node.node_class, "model_fields"):
            for field_name, field_info in node.node_class.model_fields.items():
                kuzu_type = _get_kuzu_type_for_field(field_info.annotation)
                field_types[field_name] = kuzu_type

        # Extract type information for embedded struct classes
        struct_field_types: dict[str, dict[str, str]] = {}
        for emb_class in getattr(node, "embedded_struct_classes", []) or []:
            # Find the field name that holds this embedded class
            from genai_graph.kg.schema.core import find_embedded_field_for_class

            field_name = find_embedded_field_for_class(node.node_class, emb_class)
            if field_name and hasattr(emb_class, "model_fields"):
                emb_field_types: dict[str, str] = {}
                for sub_field_name, sub_field_info in emb_class.model_fields.items():
                    kuzu_type = _get_kuzu_type_for_field(sub_field_info.annotation)
                    emb_field_types[sub_field_name] = kuzu_type
                struct_field_types[field_name] = emb_field_types

        return cls(
            node_type=node_type,
            primary_key_field=primary_key_field,
            field_types=field_types,
            struct_field_types=struct_field_types,
        )


class NodeTypeRegistry(BaseModel):
    """Registry of node type configurations for merge operations."""

    configs: dict[str, NodeTypeConfig] = Field(default_factory=dict, alias="_configs")

    def register(self, config: NodeTypeConfig) -> None:
        """Register a node type configuration."""
        self.configs[config.node_type] = config

    def add_type(self, node_type: str, key_field: str = "id") -> None:
        """Add a node type with default configuration.

        This is a convenience method for dynamic schema creation where
        we don't have GraphNode definitions.

        Args:
            node_type: The node type name (table name)
            key_field: The primary key field name (default: "id")
        """
        config = NodeTypeConfig(
            node_type=node_type,
            primary_key_field=key_field,
        )
        self.register(config)

    def get(self, node_type: str) -> NodeTypeConfig:
        """Get config for a node type, with sensible defaults."""
        if node_type in self.configs:
            return self.configs[node_type]
        # Return default config if not registered
        return NodeTypeConfig(node_type=node_type)

    def __contains__(self, node_type: str) -> bool:
        return node_type in self.configs

    @classmethod
    def from_graph_nodes(cls, nodes: list[GraphNode]) -> NodeTypeRegistry:
        """Build registry from a list of GraphNode definitions."""
        registry = cls()
        for node in nodes:
            registry.register(NodeTypeConfig.from_graph_node(node))
        return registry


def _format_value_for_cypher(value: Any) -> str:
    """Format a Python value for use in Cypher-like queries.

    Handles strings (with escaping), lists, dicts (as MAP/STRUCT), None,
    booleans, and numbers according to Cypher syntax requirements.

    This representation is compatible with Kuzu (STRUCT fields) and can also
    be interpreted as nested map properties by future Neo4j backends.

    Args:
        value: Python value to format

    Returns:
        Formatted string ready for Cypher query insertion
    """
    # Check for TypedNull first (must import to check type)
    if hasattr(value, "__class__") and value.__class__.__name__ == "TypedNull":
        # Return the CAST expression directly
        return repr(value)
    elif value is None:
        return "NULL"
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, str):
        # Empty strings should be NULL to avoid type inference issues in STRUCT_PACK
        if value.strip() == "":
            return "NULL"
        # Escape single quotes for Cypher
        escaped = value.replace("'", "\\'")
        return f"'{escaped}'"
    elif isinstance(value, list):
        # Empty lists should be NULL for STRUCT compatibility
        if len(value) == 0:
            return "NULL"
        # Recursively format list elements
        formatted_items = [_format_value_for_cypher(item) for item in value]
        return f"[{', '.join(formatted_items)}]"
    elif isinstance(value, dict):
        # Map / struct literal: {key: value, ...}
        # Empty dicts cannot be represented in Cypher, use NULL instead
        if not value:
            return "NULL"

        # Check if all values are None, empty, or TypedNull - if so, use NULL for the whole struct
        # This avoids Kuzu creating a struct with only NULL fields
        def is_null_like(v: Any) -> bool:
            return (
                v is None
                or (isinstance(v, str) and v.strip() == "")
                or (isinstance(v, list) and len(v) == 0)
                or (hasattr(v, "__class__") and v.__class__.__name__ == "TypedNull")
            )

        if all(is_null_like(v) for v in value.values()):
            return "NULL"

        # Format each value - TypedNull and NULLs will be handled appropriately
        items = [f"{k}: {_format_value_for_cypher(v)}" for k, v in value.items()]
        return "{" + ", ".join(items) + "}"
    elif isinstance(value, (int, float)):
        return str(value)
    elif hasattr(value, "value"):  # Enum types
        escaped = str(value.value).replace("'", "\\'")
        return f"'{escaped}'"
    else:
        # Complex objects - convert to string
        escaped = str(value).replace("'", "\\'")
        return f"'{escaped}'"


# =============================================================================
# DataFrame-based batch merge operations
# =============================================================================


def _prepare_node_dataframe(
    node_list: list[dict[str, Any]],
    key_field: str,
    field_types: dict[str, str] | None = None,
    struct_field_types: dict[str, dict[str, str]] | None = None,
) -> pd.DataFrame:
    """Prepare a DataFrame from a list of node dictionaries for batch merge.

    Handles data cleaning:
    - Removes excluded metadata fields
    - Adds timestamps if not present
    - Converts TypedNull markers to appropriate values based on expected type
    - Uses float('nan') for numeric fields and keeps None/empty for strings

    Args:
        node_list: List of node data dictionaries
        key_field: Primary key field name
        field_types: Optional mapping of top-level field names to their Kuzu types
                    e.g., {"technical_stack": "STRING[]", "margin": "DOUBLE"}
        struct_field_types: Optional mapping of struct field names to their sub-field types
                           e.g., {"financials": {"tcv": "DOUBLE", "name": "STRING"}}

    Returns:
        DataFrame ready for LOAD FROM MERGE operation
    """
    from genai_graph.kg.ingest.extract import TypedNull

    field_types = field_types or {}
    struct_field_types = struct_field_types or {}

    def clean_value(value: Any, field_name: str | None = None, expected_type: str | None = None) -> Any:
        """Recursively clean values for DataFrame/Kuzu compatibility.

        Uses type hints when available to determine appropriate null representation:
        - DOUBLE/INT64: Use float('nan') for None values
        - STRING[]: Use empty list [] for None values
        - STRING: Keep None as-is (Kuzu handles this correctly)
        """
        if isinstance(value, TypedNull):
            # Use the TypedNull's type info if available
            type_name = getattr(value, "type_name", expected_type or "STRING")
            if type_name in ("DOUBLE", "INT64"):
                return float("nan")
            elif type_name.endswith("[]"):
                return []  # Array types should be empty list, not None
            return None  # String/other types
        elif value is None:
            # Use expected_type to determine null representation
            if expected_type in ("DOUBLE", "INT64"):
                return float("nan")
            elif expected_type and expected_type.endswith("[]"):
                return []  # Array types: None → empty list
            return None  # String/other types stay as None
        elif isinstance(value, dict):
            # Look up struct field types if this is a known struct field
            sub_field_types = struct_field_types.get(field_name, {}) if field_name else {}
            if sub_field_types:
                # Embedded STRUCT with defined sub-field types — keep as dict for Ladybug STRUCT column
                return {k: clean_value(v, field_name=k, expected_type=sub_field_types.get(k)) for k, v in value.items()}
            else:
                # No embedded struct definition — the schema stores this as STRING.
                # Serialize to JSON so Ladybug doesn't encounter a Python dict in an
                # object-dtype column (which triggers UNREACHABLE_CODE in numpy_type.cpp).
                import json

                return json.dumps(value, default=str) if value else None
        elif isinstance(value, list):
            return [clean_value(item, field_name=field_name, expected_type=expected_type) for item in value]
        return value

    if not node_list:
        return pd.DataFrame()

    # Fields to exclude from the DataFrame
    excluded_metadata_fields = {"created_at", "updated_at", "dedup_key"}

    # Clean each node dict
    cleaned_nodes = []
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for node_data in node_list:
        cleaned = {}
        for key, value in node_data.items():
            if key in excluded_metadata_fields:
                continue
            # Clean values using type hints - check top-level field_types first
            expected_type = field_types.get(key)
            cleaned[key] = clean_value(value, field_name=key, expected_type=expected_type)

        # Ensure timestamps are present
        if "_created_at" not in cleaned:
            cleaned["_created_at"] = timestamp
        if "_updated_at" not in cleaned:
            cleaned["_updated_at"] = timestamp

        cleaned_nodes.append(cleaned)

    df = pd.DataFrame(cleaned_nodes)

    # Cast float-list (embedding) columns to a pyarrow-backed dtype so Kuzu's
    # LOAD FROM df scanner identifies them as list<float64> rather than STRING.
    # This is necessary because pandas uses dtype=object for Python list cells,
    # which Kuzu would otherwise infer as STRING.
    try:
        import pyarrow as pa

        for col in list(df.columns):
            if df[col].dtype != object:
                continue
            # Sample the first non-null value
            non_null = df[col].dropna()
            if non_null.empty:
                continue
            sample = non_null.iloc[0]
            if (
                isinstance(sample, list)
                and sample
                and isinstance(sample[0], (int, float))
                and not isinstance(sample[0], bool)
            ):
                # Cast to an Arrow-backed dtype so Kuzu's LOAD FROM df scanner
                # identifies the column as list<float64> instead of STRING.
                # None/NaN → null (preserved by from_pandas=True in pa.array).
                raw = [v if isinstance(v, list) else None for v in df[col]]
                arrow_arr = pa.array(raw, type=pa.list_(pa.float64()), from_pandas=True)
                df[col] = pd.Series(pd.arrays.ArrowExtensionArray(arrow_arr), index=df.index)
    except Exception:
        pass  # Non-critical: fall back to object dtype if arrow not available

    return df


def _get_columns_for_set_clause(
    df: pd.DataFrame,
    key_field: str,
    exclude_on_match: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Get column names for ON CREATE SET and ON MATCH SET clauses.

    Args:
        df: DataFrame with node data
        key_field: Primary key field (excluded from SET)
        exclude_on_match: Fields to exclude from ON MATCH SET (like _created_at)

    Returns:
        Tuple of (on_create_columns, on_match_columns)
    """
    if exclude_on_match is None:
        exclude_on_match = {"_created_at"}  # Don't update creation timestamp on match

    all_columns = [c for c in df.columns if c != key_field]
    on_match_columns = [c for c in all_columns if c not in exclude_on_match]

    return all_columns, on_match_columns


def merge_nodes_batch(
    conn: KgBackend,
    nodes: NodeDataCollection,
    registry: NodeTypeRegistry,
    context: KgManager | None = None,
) -> NodeMergeResult:
    """Merge multiple nodes using DataFrame-based batch operations.

    Uses Kuzu's LOAD FROM df MERGE capability for efficient batch inserts.
    This is significantly faster than individual MERGE queries.

    Args:
        conn: Graph database connection (kuzu.Connection or KgBackend)
        nodes: Node data collection
        registry: Node type configuration registry
        context: Optional KgManager for collecting warnings

    Returns:
        NodeMergeResult containing statistics and ID mappings
    """
    result = NodeMergeResult()

    for node_type, node_list in nodes.items():
        if not node_list:
            continue

        config = registry.get(node_type)
        primary_key_field = config.primary_key_field

        logger.debug(f"Merging {len(node_list)} {node_type} nodes via DataFrame...")

        type_stats = MergeStats(total=len(node_list))

        # Prepare DataFrame with type hints for both top-level and struct fields
        df = _prepare_node_dataframe(
            node_list,
            primary_key_field,
            field_types=config.field_types,
            struct_field_types=config.struct_field_types,
        )

        if df.empty:
            result.stats[node_type] = type_stats
            continue

        # Filter DataFrame to only include fields that are in the node schema
        # This prevents errors when data contains extra fields not in the schema
        # When field_types is empty (dynamic Neo4j imports), skip filtering to preserve all data
        if config.field_types:
            valid_fields = set(config.field_types.keys()) | {
                primary_key_field,
                "name",
                "_created_at",
                "_updated_at",
                "_original_name",
            }
            # Also include struct field names from extra_classes
            valid_fields.update(config.struct_field_types.keys())
            # Also include dynamically-added embedding columns (not in the Pydantic model,
            # but added by extract_graph_data for index_fields).
            valid_fields.update(col for col in df.columns if col.endswith("_embedding"))

            # Filter columns to only valid fields
            df_columns_to_keep = [col for col in df.columns if col in valid_fields]
            filtered_out = [col for col in df.columns if col not in valid_fields]

            if filtered_out:
                logger.debug(
                    f"Filtering out {len(filtered_out)} extra columns from {node_type} data "
                    f"not in schema: {', '.join(filtered_out[:5])}{'...' if len(filtered_out) > 5 else ''}"
                )

            df = df[df_columns_to_keep]

        # Get columns for SET clauses
        on_create_cols, on_match_cols = _get_columns_for_set_clause(df, primary_key_field)

        # Get the Kuzu connection (handle both KgBackend and raw connection)
        kuzu_conn = conn.conn if hasattr(conn, "conn") else conn  # type: ignore[union-attr]

        try:
            # Build MERGE query with ON CREATE/ON MATCH SET
            on_create_set = ", ".join([f"n.{c} = {c}" for c in on_create_cols])
            on_match_set = ", ".join([f"n.{c} = {c}" for c in on_match_cols])

            # Update the timestamp for ON MATCH
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if "_updated_at" in on_match_cols:
                df["_updated_at"] = timestamp

            merge_query = f"""
                LOAD FROM df
                MERGE (n:{node_type} {{{primary_key_field}: {primary_key_field}}})
                ON CREATE SET {on_create_set}
                ON MATCH SET {on_match_set}
            """

            # debug(merge_query)
            # debug(df)
            kuzu_conn.execute(merge_query)

            # Collect DataFrame for parquet export if collector is active
            collector = get_parquet_collector()
            if collector is not None:
                collector.add_nodes(node_type, df)

            # Stats - we can't easily distinguish created vs matched in batch mode
            type_stats.created = len(df)  # Approximation

            # Build ID mapping
            # For AUTO_ID nodes (primary_key_field="id"), relationships use 'name' as lookup key
            # So we need to map name → UUID for relationship creation
            for _, row in df.iterrows():
                key_value = row.get(primary_key_field, "")
                if key_value:
                    key_str = str(key_value)
                    # Always map key → key (for non-AUTO_ID lookups)
                    result.id_mapping.add(node_type, key_str, key_str)

                    # For AUTO_ID nodes, also map name → UUID
                    # (relationships use name as from_id/to_id but MATCH uses id)
                    if primary_key_field == "id" and "name" in row:
                        name_value = str(row["name"])
                        if name_value and name_value != key_str:
                            result.id_mapping.add(node_type, name_value, key_str)

        except Exception as e:
            error_msg = str(e)
            # Enhance error message with context
            if "Cannot find property" in error_msg:
                # Extract property name from error
                import re

                match = re.search(r"Cannot find property (\w+)", error_msg)
                if match:
                    missing_prop = match.group(1)
                    schema_fields = list(config.field_types.keys())[:10]
                    logger.error(
                        f"Schema mismatch for {node_type}: property '{missing_prop}' not in database schema. "
                        f"Schema fields: {', '.join(schema_fields)}. "
                        f"This usually means the field exists in data but wasn't defined in the node's Pydantic model."
                    )
            else:
                logger.error(f"Error in batch merge for {node_type}: {e}")
            raise

        result.stats[node_type] = type_stats
        logger.debug(f"  {node_type}: {type_stats.total} processed via batch merge")

    return result


def merge_relationships_batch(
    conn: KgBackend,
    relationships: list[RelationshipRecord],
    registry: NodeTypeRegistry,
    id_mapping: NodeIdMapping,
) -> int:
    """Merge relationships using DataFrame-based batch operations.

    Groups relationships by type and uses LOAD FROM df MATCH ... CREATE
    for efficient batch relationship creation.

    Args:
        conn: Graph database connection
        relationships: List of RelationshipRecord objects
        registry: Node type configuration registry
        id_mapping: Mapping from (node_type, original_id) to merged_id

    Returns:
        Number of relationships created
    """
    if not relationships:
        return 0

    # Group relationships by (from_type, to_type, rel_name) for batch processing
    rel_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    for rel in relationships:
        from_type = rel.from_type
        from_id = rel.from_id
        to_type = rel.to_type
        to_id = rel.to_id
        rel_name = rel.name
        properties = rel.properties or {}

        # Translate IDs using mapping
        merged_from_id = id_mapping.get(from_type, str(from_id))
        merged_to_id = id_mapping.get(to_type, str(to_id))

        # Determine match fields based on whether nodes use AUTO_ID
        from_config = registry.get(from_type)
        to_config = registry.get(to_type)
        from_key_field = from_config.primary_key_field
        to_key_field = to_config.primary_key_field

        group_key = (from_type, to_type, rel_name)
        if group_key not in rel_groups:
            rel_groups[group_key] = []

        rel_data = {
            "from_id": merged_from_id,
            "to_id": merged_to_id,
            "from_key_field": from_key_field,
            "to_key_field": to_key_field,
            **properties,
        }
        rel_groups[group_key].append(rel_data)

    # Get the Kuzu connection
    kuzu_conn = conn.conn if hasattr(conn, "conn") else conn  # type: ignore[union-attr]

    total_created = 0

    for (from_type, to_type, rel_name), rel_list in rel_groups.items():
        if not rel_list:
            continue

        logger.debug(f"Creating {len(rel_list)} {rel_name} relationships ({from_type} -> {to_type})...")

        # Get the key fields from first relationship (all should be the same)
        from_key_field = rel_list[0]["from_key_field"]
        to_key_field = rel_list[0]["to_key_field"]

        # Build DataFrame - remove key field info before creating DataFrame
        df_data = []
        property_cols = set()
        for rel_data in rel_list:
            row = {
                "from_id": rel_data["from_id"],
                "to_id": rel_data["to_id"],
            }
            for k, v in rel_data.items():
                if k not in ("from_id", "to_id", "from_key_field", "to_key_field"):
                    row[k] = v
                    property_cols.add(k)
            df_data.append(row)

        df = pd.DataFrame(df_data)

        # Fix DataFrame column types to handle None values properly
        # Kuzu doesn't handle object dtype well - convert to proper types
        # Also, Kuzu's LOAD FROM df requires all columns to exist and be properly typed
        for col in property_cols:
            if col in df.columns:
                # Check column content to infer proper type
                non_null_vals = df[col].dropna()
                if len(non_null_vals) > 0:
                    first_val = non_null_vals.iloc[0]
                    if isinstance(first_val, list):
                        # Convert list properties to JSON strings for Kuzu compatibility
                        import json

                        df[col] = df[col].apply(lambda x: json.dumps(x) if isinstance(x, list) else x)
                    elif isinstance(first_val, bool):
                        # Convert None to False for boolean columns, then cast to bool
                        df[col] = df[col].fillna(False)
                        df[col] = df[col].astype(bool)
                    elif isinstance(first_val, (int, float)):
                        # Keep numeric types as-is (NaN is handled)
                        pass
                    elif isinstance(first_val, str):
                        # For string columns, fill None with empty string
                        df[col] = df[col].fillna("")
                else:
                    # All values are None - fill with empty string for safety
                    df[col] = df[col].fillna("")

        # Filter out properties that have all NaN/None/empty values
        # These cause issues with Kuzu's LOAD FROM df
        non_empty_prop_cols = set()
        for col in property_cols:
            if col in df.columns:
                # Check if column has any non-empty values
                if df[col].notna().any():
                    # For strings, also check if not all empty
                    if df[col].dtype == object:
                        if (df[col] != "").any():
                            non_empty_prop_cols.add(col)
                    else:
                        non_empty_prop_cols.add(col)

        # Use only non-empty property columns
        property_cols = non_empty_prop_cols

        # Use MERGE for relationships to avoid duplicates when the same relationship
        # is created from multiple sources (e.g., both BAML extraction and Neo4j import).
        # This ensures (from)-[r:REL]->(to) is only created once per node pair.
        try:
            if property_cols:
                # Kuzu's LOAD FROM df doesn't support inline property assignment
                # in MERGE for relationships. Use row-by-row creation with SET.
                prop_cols_list = sorted(property_cols)
                for _, row in df.iterrows():
                    from_id_val = row["from_id"]
                    to_id_val = row["to_id"]
                    merge_q = (
                        f"MATCH (from:{from_type} {{{from_key_field}: $from_id}}), "
                        f"(to:{to_type} {{{to_key_field}: $to_id}}) "
                        f"MERGE (from)-[r:{rel_name}]->(to)"
                    )
                    set_parts = []
                    params: dict[str, Any] = {
                        "from_id": from_id_val,
                        "to_id": to_id_val,
                    }
                    for col in prop_cols_list:
                        val = row.get(col)
                        if val is not None and val != "":
                            param_name = f"p_{col}"
                            set_parts.append(f"r.{col} = ${param_name}")
                            params[param_name] = val
                    if set_parts:
                        merge_q += " SET " + ", ".join(set_parts)
                    kuzu_conn.execute(merge_q, parameters=params)
                total_created += len(df)
            else:
                merge_rel_query = f"""
                    LOAD FROM df
                    MATCH (from:{from_type} {{{from_key_field}: from_id}}), (to:{to_type} {{{to_key_field}: to_id}})
                    MERGE (from)-[:{rel_name}]->(to)
                """
                kuzu_conn.execute(merge_rel_query)
                total_created += len(df)

            # Collect DataFrame for parquet export if collector is active
            collector = get_parquet_collector()
            if collector is not None:
                # Add metadata columns for relationship type info
                export_df = df.copy()
                export_df["_from_type"] = from_type
                export_df["_to_type"] = to_type
                export_df["_from_key_field"] = from_key_field
                export_df["_to_key_field"] = to_key_field
                collector.add_relationships(rel_name, export_df)

        except Exception as e:
            logger.error(f"Error in batch relationship creation for {rel_name}: {e}")
            logger.error(f"Query failed for {rel_name} ({from_type} -> {to_type})")
            raise

    return total_created
