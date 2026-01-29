"""Merge nodes and relationships into the graph database.

This module provides utilities for adding nodes and edges to the graph,
handling automatic merging based on key fields (typically 'name').

Uses Kuzu's DataFrame MERGE capability for efficient batch operations:
- LOAD FROM df MERGE (n:NodeType {key: key}) for batch node merging
- LOAD FROM df MATCH ... CREATE for batch relationship creation
"""

from datetime import datetime
from typing import Any

import pandas as pd
from genai_tk.core.prompts import dedent_ws
from loguru import logger

from genai_graph.core.graph_backend import GraphBackend
from genai_graph.core.kg_manager import KgManager


def _should_update_value(value: Any) -> bool:
    """Return True when a value should overwrite an existing node property."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


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


def build_merge_query(
    node_type: str,
    node_data: dict[str, Any],
    key_field: str = "id",
    is_auto_id: bool = False,
) -> tuple[str, str]:
    """Build queries for upserting a node using MATCH + conditional CREATE.

    Since Kuzu doesn't support MERGE with ON CREATE/ON MATCH, we use two queries:
    1. Check if node exists (MATCH)
    2. Either UPDATE or CREATE based on existence

    Args:
        node_type: Label/type of the node
        node_data: Dictionary of node properties
        key_field: Primary key field name
        is_auto_id: Whether this uses AUTO_ID (SERIAL) primary key

    Returns:
        Tuple of (check_query, props_str_for_create)
    """
    # Get the merge value (primary key)
    merge_value = node_data.get(key_field)
    if merge_value is None and not is_auto_id:
        raise ValueError(f"Node data missing required key field '{key_field}'")

    # Format merge value
    if not is_auto_id:
        merge_value_formatted = _format_value_for_cypher(merge_value)

        # Query 1: Check if node exists and get its id plus current naming info
        # Note: alternate_names is optional, we'll handle missing fields in the merge logic
        check_query = dedent_ws(f"""
            MATCH (n:{node_type} {{{key_field}: {merge_value_formatted}}})
            RETURN n.{key_field} as id, n._created_at as created_at
            LIMIT 1
            """)
    else:
        # For AUTO_ID, we can't match on id since it doesn't exist yet in the data
        # We'll return an empty check (always creates new node)
        # In practice, this is handled differently - we never call MATCH for AUTO_ID
        check_query = ""

    # Query 2a: Update existing node (timestamp only)
    # Query 2b: Create new node with all properties
    # We'll return a template that the caller will use based on check results

    # Metadata fields that are handled separately in the schema
    # Original 'name' is preserved as '_original_name', and 'name' is set from name_from
    # For example: "created_at" -> "_created_at", "updated_at" -> "_updated_at"
    excluded_metadata_fields = {"created_at", "updated_at", "dedup_key"}

    # For AUTO_ID, also exclude the key_field (id) from CREATE props since it's auto-generated
    if is_auto_id:
        excluded_metadata_fields.add(key_field)

    # Build properties for CREATE
    create_props = []
    for key, value in node_data.items():
        # Skip metadata fields without _ prefix (they're duplicates)
        if key in excluded_metadata_fields:
            continue
        # Generic handling for dicts / struct-like values is sufficient
        # We format dicts as STRUCT literals. Empty dicts are mapped to NULL.
        formatted_value = _format_value_for_cypher(value)
        create_props.append(f"{key}: {formatted_value}")

    props_str = ", ".join(create_props)

    # Return both check query and upsert info
    # The caller will decide which operation to perform
    return check_query.strip(), props_str


def merge_node_in_graph(
    conn: GraphBackend,
    node_type: str,
    node_data: dict[str, Any],
    key_field: str = "id",
    is_auto_id: bool = False,
    context: KgManager | None = None,
) -> tuple[bool, str]:
    """Merge a single node into the graph database.

    Executes a check-then-upsert operation: first checks if node exists,
    then either updates timestamp or creates new node.

    Args:
        conn: Graph database connection (kuzu.Connection or similar)
        node_type: Node label/type
        node_data: Node properties dictionary
        key_field: Primary key field name for this node type
        is_auto_id: Whether this uses AUTO_ID (SERIAL) primary key
        context: Optional KgContext for collecting warnings

    Returns:
        Tuple of (was_created: bool, node_id: str)
    """
    try:
        timestamp = datetime.utcnow().isoformat() + "Z"

        # For AUTO_ID nodes, we always create (no merge logic)
        if is_auto_id:
            # Build props for CREATE (without id field)
            _, props_str = build_merge_query(
                node_type=node_type,
                node_data=node_data,
                key_field=key_field,
                is_auto_id=True,
            )
            create_query = f"CREATE (n:{node_type} {{{props_str}}}) RETURN n.{key_field} as id"
            result = conn.execute(create_query)
            df = result.get_as_df()

            if df.empty:
                warning_msg = f"CREATE returned no ID for {node_type}"
                logger.warning(warning_msg)
                if context:
                    context.add_warning(warning_msg)
                return True, ""

            node_id = str(df.iloc[0]["id"])
            return True, node_id

        # Build queries for non-AUTO_ID nodes
        check_query, props_str = build_merge_query(
            node_type=node_type,
            node_data=node_data,
            key_field=key_field,
            is_auto_id=False,
        )

        # Step 1: Check if node exists
        result = conn.execute(check_query)
        df = result.get_as_df()

        if not df.empty:
            # Node exists - update timestamp and other fields
            row = df.iloc[0]
            existing_id = str(row["id"])

            # Build SET clause dynamically. On matches, update non-empty
            # properties from the incoming node_data so later sources (e.g. DB
            # pulls) can take precedence over earlier ones.
            set_clauses = [f"n._updated_at = '{timestamp}'"]

            excluded_update_fields = {
                key_field,  # Exclude the primary key field (e.g., "id" or "opportunity_id")
                "name",
                "created_at",
                "updated_at",
                "_created_at",
                "_updated_at",
                "_original_name",
            }

            for key, value in node_data.items():
                if key in excluded_update_fields:
                    continue
                if not _should_update_value(value):
                    continue
                formatted = _format_value_for_cypher(value)
                set_clauses.append(f"n.{key} = {formatted}")

            set_sql = ", ".join(set_clauses)
            update_query = dedent_ws(f"""
                MATCH (n:{node_type})
                WHERE n.{key_field} = '{existing_id.replace("'", "\\'")}'
                SET {set_sql}
                RETURN n.{key_field} as id
                """)
            conn.execute(update_query)
            return False, existing_id
        else:
            # Node doesn't exist - create it
            create_query = f"CREATE (n:{node_type} {{{props_str}}}) RETURN n.{key_field} as id"
            result = conn.execute(create_query)
            df = result.get_as_df()

            if df.empty:
                warning_msg = f"CREATE returned no ID for {node_type}"
                logger.warning(warning_msg)
                if context:
                    context.add_warning(warning_msg)
                return True, ""

            node_id = str(df.iloc[0]["id"])
            return True, node_id

    except Exception as e:
        import traceback as tb

        logger.error(f"Error merging {node_type} node: {e}")
        logger.error(f"Node data: {node_data.get(key_field, 'unknown')}")
        logger.error(tb.format_exc())
        raise


# =============================================================================
# DataFrame-based batch merge operations
# =============================================================================


def _prepare_node_dataframe(
    node_list: list[dict[str, Any]],
    key_field: str,
    is_auto_id: bool,
) -> pd.DataFrame:
    """Prepare a DataFrame from a list of node dictionaries for batch merge.

    Handles data cleaning:
    - Removes excluded metadata fields
    - Adds timestamps if not present

    Args:
        node_list: List of node data dictionaries
        key_field: Primary key field name
        is_auto_id: Whether this uses AUTO_ID (SERIAL) primary key

    Returns:
        DataFrame ready for LOAD FROM MERGE operation
    """
    if not node_list:
        return pd.DataFrame()

    # Fields to exclude from the DataFrame
    excluded_metadata_fields = {"created_at", "updated_at", "dedup_key"}
    if is_auto_id:
        excluded_metadata_fields.add(key_field)

    # Clean each node dict
    cleaned_nodes = []
    timestamp = datetime.utcnow().isoformat() + "Z"

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
    node_type_to_key_field: dict[str, str],
    node_type_to_is_auto_id: dict[str, bool],
    context: KgManager | None = None,
) -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], str]]:
    """Merge multiple nodes using DataFrame-based batch operations.

    Uses Kuzu's LOAD FROM df MERGE capability for efficient batch inserts.
    This is significantly faster than individual MERGE queries.

    Args:
        conn: Graph database connection (kuzu.Connection or GraphBackend)
        nodes_dict: Mapping of node_type to list of node data dicts
        node_type_to_key_field: Mapping of node_type to primary key field name
        node_type_to_is_auto_id: Mapping of node_type to whether it uses AUTO_ID
        context: Optional KgManager for collecting warnings

    Returns:
        Tuple of:
        - Statistics dict: {node_type: {created, matched, total}}
        - ID mapping: {(node_type, original_id): merged_global_id}
    """
    stats: dict[str, dict[str, int]] = {}
    id_mapping: dict[tuple[str, str], str] = {}

    for node_type, node_list in nodes_dict.items():
        if not node_list:
            continue

        primary_key_field = node_type_to_key_field.get(node_type, "id")
        is_auto_id = node_type_to_is_auto_id.get(node_type, False)

        logger.debug(f"Merging {len(node_list)} {node_type} nodes via DataFrame...")

        type_stats = {"created": 0, "matched": 0, "total": len(node_list)}

        # Prepare DataFrame
        df = _prepare_node_dataframe(node_list, primary_key_field, is_auto_id)

        if df.empty:
            stats[node_type] = type_stats
            continue

        # Get columns for SET clauses
        on_create_cols, on_match_cols = _get_columns_for_set_clause(df, primary_key_field)

        # Get the Kuzu connection (handle both GraphBackend and raw connection)
        kuzu_conn = conn.conn if hasattr(conn, "conn") else conn  # type: ignore[union-attr]

        try:
            if is_auto_id:
                # For AUTO_ID nodes, we use 'name' as the merge key for deduplication
                # This prevents duplicates when multiple subgraphs reference the same entity
                if "name" not in df.columns:
                    raise ValueError(f"AUTO_ID node type {node_type} requires 'name' column for deduplication")

                # Build MERGE query using 'name' as the merge key
                # Exclude both the SERIAL primary key and 'name' from SET clauses
                merge_cols = [c for c in df.columns if c not in (primary_key_field, "name")]
                on_create_set = ", ".join([f"n.{c} = {c}" for c in merge_cols]) if merge_cols else "n._updated_at = _updated_at"
                on_match_set = ", ".join([f"n.{c} = {c}" for c in merge_cols if c != "_created_at"]) if merge_cols else "n._updated_at = _updated_at"

                merge_query = f"""
                    LOAD FROM df
                    MERGE (n:{node_type} {{name: name}})
                    ON CREATE SET {on_create_set}
                    ON MATCH SET {on_match_set}
                """
                kuzu_conn.execute(merge_query)

                # For AUTO_ID nodes, store name->name mapping (since relationships match on name)
                # We don't need to query back serial IDs because relationships use 'name' field
                name_list = df["name"].tolist()
                for name_val in name_list:
                    # Store name as both key and value - relationships will match on name
                    id_mapping[(node_type, str(name_val))] = str(name_val)

                type_stats["created"] = len(df)  # Approximation
            else:
                # Build MERGE query with ON CREATE/ON MATCH SET
                on_create_set = ", ".join([f"n.{c} = {c}" for c in on_create_cols])
                on_match_set = ", ".join([f"n.{c} = {c}" for c in on_match_cols])

                # Update the timestamp for ON MATCH
                timestamp = datetime.utcnow().isoformat() + "Z"
                if "_updated_at" in on_match_cols:
                    # Will be set from DataFrame, but let's ensure it's current
                    df["_updated_at"] = timestamp

                merge_query = f"""
                    LOAD FROM df
                    MERGE (n:{node_type} {{{primary_key_field}: {primary_key_field}}})
                    ON CREATE SET {on_create_set}
                    ON MATCH SET {on_match_set}
                """

                kuzu_conn.execute(merge_query)

                # For statistics: count how many already existed vs new
                # We can't easily track this with batch MERGE, so estimate based on total
                # A future enhancement could query before/after counts
                type_stats["created"] = len(df)  # Approximation
                type_stats["matched"] = 0  # Can't distinguish in batch mode

                # Build ID mapping - for non-AUTO_ID, the key value IS the ID
                for _, row in df.iterrows():
                    key_value = row.get(primary_key_field, "")
                    if key_value:
                        key_str = str(key_value)
                        id_mapping[(node_type, key_str)] = key_str

        except Exception as e:
            logger.error(f"Error in batch merge for {node_type}: {e}")
            logger.error("Falling back to individual node merges...")

            # Fallback to individual merges
            for node_data in node_list:
                original_id = node_data.get(primary_key_field, "")
                try:
                    was_created, merged_id = merge_node_in_graph(
                        conn=conn,
                        node_type=node_type,
                        node_data=node_data,
                        key_field=primary_key_field,
                        is_auto_id=is_auto_id,
                        context=context,
                    )
                    if was_created:
                        type_stats["created"] += 1
                    else:
                        type_stats["matched"] += 1
                    if original_id and merged_id:
                        id_mapping[(node_type, original_id)] = merged_id
                except Exception as inner_e:
                    logger.error(f"Error merging individual node: {inner_e}")
                    if context:
                        context.add_warning(f"Failed to merge {node_type} node: {inner_e}")

        stats[node_type] = type_stats
        logger.debug(f"  {node_type}: {type_stats['total']} processed via batch merge")

    return stats, id_mapping


def merge_relationships_batch(
    conn: GraphBackend,
    relationships: list[Any],
    node_type_to_key_field: dict[str, str],
    node_type_to_is_auto_id: dict[str, bool],
    id_mapping: dict[tuple[str, str], str],
) -> int:
    """Merge relationships using DataFrame-based batch operations.

    Groups relationships by type and uses LOAD FROM df MATCH ... CREATE
    for efficient batch relationship creation.

    Args:
        conn: Graph database connection
        relationships: List of RelationshipRecord objects
        node_type_to_key_field: Mapping of node_type to primary key field name
        node_type_to_is_auto_id: Mapping of node_type to whether it uses AUTO_ID
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
        merged_from_id = id_mapping.get((from_type, str(from_id)), str(from_id))
        merged_to_id = id_mapping.get((to_type, str(to_id)), str(to_id))

        # Determine match fields based on whether nodes use AUTO_ID
        from_is_auto_id = node_type_to_is_auto_id.get(from_type, False)
        to_is_auto_id = node_type_to_is_auto_id.get(to_type, False)
        from_match_field = "name" if from_is_auto_id else node_type_to_key_field.get(from_type, "id")
        to_match_field = "name" if to_is_auto_id else node_type_to_key_field.get(to_type, "id")

        group_key = (from_type, to_type, rel_name)
        if group_key not in rel_groups:
            rel_groups[group_key] = []

        rel_data = {
            "from_id": merged_from_id,
            "to_id": merged_to_id,
            "from_match_field": from_match_field,
            "to_match_field": to_match_field,
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

        # Get the match fields from first relationship (all should be the same)
        from_match_field = rel_list[0]["from_match_field"]
        to_match_field = rel_list[0]["to_match_field"]

        # Build DataFrame - remove match field info before creating DataFrame
        df_data = []
        property_cols = set()
        for rel_data in rel_list:
            row = {
                "from_id": rel_data["from_id"],
                "to_id": rel_data["to_id"],
            }
            for k, v in rel_data.items():
                if k not in ("from_id", "to_id", "from_match_field", "to_match_field"):
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
            MATCH (from:{from_type} {{{from_match_field}: from_id}}), (to:{to_type} {{{to_match_field}: to_id}})
            CREATE (from)-[:{rel_name}{props_str}]->(to)
        """
        try:
            kuzu_conn.execute(create_rel_query)
            total_created += len(df)

        except Exception as e:
            logger.error(f"Error in batch relationship creation for {rel_name}: {e}")
            logger.error(f"Query: {create_rel_query}")
            # Fallback: create relationships individually
            for _, row in df.iterrows():
                try:
                    from_id_escaped = str(row["from_id"]).replace("'", "\\'")
                    to_id_escaped = str(row["to_id"]).replace("'", "\\'")

                    if property_cols:
                        prop_parts = []
                        for c in property_cols:
                            val = row.get(c)
                            if val is None:
                                prop_parts.append(f"{c}: NULL")
                            elif isinstance(val, str):
                                prop_parts.append(f"{c}: '{val.replace(chr(39), chr(92) + chr(39))}'")
                            else:
                                prop_parts.append(f"{c}: {val}")
                        single_props_str = " {" + ", ".join(prop_parts) + "}"
                    else:
                        single_props_str = ""

                    single_query = f"""
                        MATCH (from:{from_type} {{{from_match_field}: '{from_id_escaped}'}}),
                              (to:{to_type} {{{to_match_field}: '{to_id_escaped}'}})
                        CREATE (from)-[:{rel_name}{single_props_str}]->(to)
                    """
                    kuzu_conn.execute(single_query)
                    total_created += 1
                except Exception as inner_e:
                    logger.warning(f"Failed to create individual relationship: {inner_e}")

    return total_created
