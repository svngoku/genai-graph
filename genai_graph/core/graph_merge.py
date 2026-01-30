"""Merge nodes and relationships into the graph database.

This module provides utilities for adding nodes and edges to the graph,
handling automatic merging based on key fields (typically 'name').

Uses Kuzu's DataFrame MERGE capability for efficient batch operations:
- LOAD FROM df MERGE (n:NodeType {key: key}) for batch node merging
- LOAD FROM df MATCH ... CREATE for batch relationship creation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger

from genai_graph.core.graph_backend import GraphBackend
from genai_graph.core.kg_manager import KgManager

if TYPE_CHECKING:
    from genai_graph.core.graph_schema import GraphNode


# =============================================================================
# Data Classes for structured return types
# =============================================================================


@dataclass
class MergeStats:
    """Statistics for a single node type merge operation."""

    created: int = 0
    matched: int = 0
    total: int = 0

    def __str__(self) -> str:
        return f"created={self.created}, matched={self.matched}, total={self.total}"


@dataclass
class NodeIdMapping:
    """Mapping from original node IDs to merged database IDs.

    For non-AUTO_ID nodes: maps (node_type, key_value) -> key_value
    For AUTO_ID nodes: maps (node_type, name) -> name (used for relationship matching)
    """

    _mapping: dict[tuple[str, str], str] = field(default_factory=dict)

    def add(self, node_type: str, original_id: str, merged_id: str) -> None:
        """Add a mapping entry."""
        self._mapping[(node_type, original_id)] = merged_id

    def get(self, node_type: str, original_id: str, default: str | None = None) -> str:
        """Get the merged ID for an original ID."""
        result = self._mapping.get((node_type, str(original_id)))
        if result is not None:
            return result
        return default if default is not None else str(original_id)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._mapping

    def __len__(self) -> int:
        return len(self._mapping)

    def items(self) -> list[tuple[tuple[str, str], str]]:
        """Return all mapping items."""
        return list(self._mapping.items())


@dataclass
class NodeMergeResult:
    """Result of a batch node merge operation."""

    stats: dict[str, MergeStats] = field(default_factory=dict)
    id_mapping: NodeIdMapping = field(default_factory=NodeIdMapping)

    def get_stats(self, node_type: str) -> MergeStats:
        """Get stats for a node type, creating if needed."""
        if node_type not in self.stats:
            self.stats[node_type] = MergeStats()
        return self.stats[node_type]

    def total_nodes(self) -> int:
        """Return total nodes across all types."""
        return sum(s.total for s in self.stats.values())


@dataclass
class NodeTypeConfig:
    """Configuration for how a node type should be merged.

    Encapsulates the primary key field for merge operations.
    """

    node_type: str
    primary_key_field: str = "id"

    @classmethod
    def from_graph_node(cls, node: GraphNode) -> NodeTypeConfig:
        """Create config from a GraphNode definition."""
        node_type = node.node_class.__name__
        key_from = node.key_from

        if key_from == "AUTO_ID" or callable(key_from):
            # AUTO_ID generates UUID, callable computes key - both stored in 'id' field
            return cls(node_type=node_type, primary_key_field="id")
        else:
            # Use the specified field as primary key
            return cls(node_type=node_type, primary_key_field=key_from)


@dataclass
class NodeTypeRegistry:
    """Registry of node type configurations for merge operations."""

    _configs: dict[str, NodeTypeConfig] = field(default_factory=dict)

    def register(self, config: NodeTypeConfig) -> None:
        """Register a node type configuration."""
        self._configs[config.node_type] = config

    def get(self, node_type: str) -> NodeTypeConfig:
        """Get config for a node type, with sensible defaults."""
        if node_type in self._configs:
            return self._configs[node_type]
        # Return default config if not registered
        return NodeTypeConfig(node_type=node_type)

    def __contains__(self, node_type: str) -> bool:
        return node_type in self._configs

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
) -> pd.DataFrame:
    """Prepare a DataFrame from a list of node dictionaries for batch merge.

    Handles data cleaning:
    - Removes excluded metadata fields
    - Adds timestamps if not present

    Args:
        node_list: List of node data dictionaries
        key_field: Primary key field name

    Returns:
        DataFrame ready for LOAD FROM MERGE operation
    """
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
            # Keep the value as-is for DataFrame - Kuzu handles Python types directly
            cleaned[key] = value

        # Ensure timestamps are present
        if "_created_at" not in cleaned:
            cleaned["_created_at"] = timestamp
        if "_updated_at" not in cleaned:
            cleaned["_updated_at"] = timestamp

        cleaned_nodes.append(cleaned)

    return pd.DataFrame(cleaned_nodes)


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
    conn: GraphBackend,
    nodes_dict: dict[str, list[dict[str, Any]]],
    registry: NodeTypeRegistry,
    context: KgManager | None = None,
) -> NodeMergeResult:
    """Merge multiple nodes using DataFrame-based batch operations.

    Uses Kuzu's LOAD FROM df MERGE capability for efficient batch inserts.
    This is significantly faster than individual MERGE queries.

    Args:
        conn: Graph database connection (kuzu.Connection or GraphBackend)
        nodes_dict: Mapping of node_type to list of node data dicts
        registry: Node type configuration registry
        context: Optional KgManager for collecting warnings

    Returns:
        NodeMergeResult containing statistics and ID mappings
    """
    result = NodeMergeResult()

    for node_type, node_list in nodes_dict.items():
        if not node_list:
            continue

        config = registry.get(node_type)
        primary_key_field = config.primary_key_field

        logger.debug(f"Merging {len(node_list)} {node_type} nodes via DataFrame...")

        type_stats = MergeStats(total=len(node_list))

        # Prepare DataFrame
        df = _prepare_node_dataframe(node_list, primary_key_field)

        if df.empty:
            result.stats[node_type] = type_stats
            continue

        # Get columns for SET clauses
        on_create_cols, on_match_cols = _get_columns_for_set_clause(df, primary_key_field)

        # Get the Kuzu connection (handle both GraphBackend and raw connection)
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
            logger.error(f"Error in batch merge for {node_type}: {e}")
            raise

        result.stats[node_type] = type_stats
        logger.debug(f"  {node_type}: {type_stats.total} processed via batch merge")

    return result


def merge_relationships_batch(
    conn: GraphBackend,
    relationships: list[Any],
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
        # Handle both RelationshipRecord and tuple formats
        if hasattr(rel, "from_type"):
            from_type = rel.from_type
            from_id = rel.from_id
            to_type = rel.to_type
            to_id = rel.to_id
            rel_name = rel.name
            properties = rel.properties or {}
        elif isinstance(rel, tuple) and len(rel) >= 5:
            from_type, from_id, to_type, to_id, rel_name = rel[:5]
            properties = rel[5] if len(rel) > 5 else {}
        else:
            continue

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

        # Build property assignment for CREATE
        if property_cols:
            prop_assignments = ", ".join([f"{c}: {c}" for c in property_cols])
            props_str = f" {{{prop_assignments}}}"
        else:
            props_str = ""

        # Use MATCH + CREATE for relationships
        create_rel_query = f"""
            LOAD FROM df
            MATCH (from:{from_type} {{{from_key_field}: from_id}}), (to:{to_type} {{{to_key_field}: to_id}})
            CREATE (from)-[:{rel_name}{props_str}]->(to)
        """
        try:
            kuzu_conn.execute(create_rel_query)
            total_created += len(df)

        except Exception as e:
            logger.error(f"Error in batch relationship creation for {rel_name}: {e}")
            logger.error(f"Query: {create_rel_query}")
            raise

    return total_created
