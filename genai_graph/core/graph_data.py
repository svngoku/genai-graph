"""Unified graph data model.

This module provides the core `GraphData` model that represents a graph
as two DataFrames (nodes and edges) plus metadata. This is the fundamental
unit of the graph system - everything is a graph, and merging is the main operation.

Design principles:
- A graph is just nodes DataFrame + edges DataFrame + metadata
- Graphs can be merged to create larger graphs
- No distinction between "subgraph" and "graph" - everything is a graph
- Prepared for future async I/O to Kuzu
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field


class GraphData(BaseModel):
    """A graph represented as DataFrames.

    This is the fundamental unit of the graph system. A graph consists of:
    - nodes: dict mapping node_type -> DataFrame with node properties
    - edges: dict mapping edge_type -> DataFrame with edge properties
    - metadata: arbitrary metadata about this graph

    The node DataFrames must have a primary key column (configurable per type).
    The edge DataFrames must have 'from_id', 'to_id', 'from_type', 'to_type' columns.

    Example:
        ```python
        graph = GraphData(
            name="my_graph",
            nodes={
                "Person": pd.DataFrame([{"id": "p1", "name": "Alice"}]),
                "Company": pd.DataFrame([{"id": "c1", "name": "Acme"}]),
            },
            edges={
                "WORKS_AT": pd.DataFrame([{
                    "from_id": "p1", "from_type": "Person",
                    "to_id": "c1", "to_type": "Company"
                }]),
            },
        )

        # Merge with another graph
        combined = graph.merge(other_graph)
        ```
    """

    name: str = ""
    """Human-readable name for this graph."""

    nodes: dict[str, pd.DataFrame] = Field(default_factory=dict)
    """Node DataFrames by node type."""

    edges: dict[str, pd.DataFrame] = Field(default_factory=dict)
    """Edge DataFrames by edge type."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Arbitrary metadata about this graph."""

    primary_keys: dict[str, str] = Field(default_factory=dict)
    """Primary key field name per node type. Defaults to 'id' if not specified."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    """When this graph was created."""

    model_config = {"arbitrary_types_allowed": True}

    # -------------------------------------------------------------------------
    # Accessors
    # -------------------------------------------------------------------------

    def get_primary_key(self, node_type: str) -> str:
        """Get the primary key field for a node type."""
        return self.primary_keys.get(node_type, "id")

    def node_count(self) -> int:
        """Total node count across all types."""
        return sum(len(df) for df in self.nodes.values())

    def edge_count(self) -> int:
        """Total edge count across all types."""
        return sum(len(df) for df in self.edges.values())

    def node_types(self) -> list[str]:
        """List of node types in this graph."""
        return list(self.nodes.keys())

    def edge_types(self) -> list[str]:
        """List of edge types in this graph."""
        return list(self.edges.keys())

    def is_empty(self) -> bool:
        """Check if this graph has no data."""
        return self.node_count() == 0 and self.edge_count() == 0

    # -------------------------------------------------------------------------
    # Mutation helpers
    # -------------------------------------------------------------------------

    def add_nodes(self, node_type: str, df: pd.DataFrame, primary_key: str = "id") -> None:
        """Add nodes of a given type.

        If nodes of this type already exist, they are concatenated.

        Args:
            node_type: The type/label for these nodes
            df: DataFrame with node properties (must include primary_key column)
            primary_key: Name of the primary key column
        """
        if primary_key not in df.columns:
            raise ValueError(f"DataFrame must have primary key column '{primary_key}'")

        self.primary_keys[node_type] = primary_key

        if node_type in self.nodes:
            self.nodes[node_type] = pd.concat([self.nodes[node_type], df], ignore_index=True)
        else:
            self.nodes[node_type] = df.copy()

    def add_edges(self, edge_type: str, df: pd.DataFrame) -> None:
        """Add edges of a given type.

        The DataFrame must have columns: from_id, to_id, from_type, to_type.
        Additional columns are treated as edge properties.

        Args:
            edge_type: The relationship type name
            df: DataFrame with edge data
        """
        required_cols = {"from_id", "to_id", "from_type", "to_type"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Edge DataFrame missing required columns: {missing}")

        if edge_type in self.edges:
            self.edges[edge_type] = pd.concat([self.edges[edge_type], df], ignore_index=True)
        else:
            self.edges[edge_type] = df.copy()

    # -------------------------------------------------------------------------
    # Merge operation - the core graph operation
    # -------------------------------------------------------------------------

    def merge(self, other: GraphData) -> GraphData:
        """Merge another graph into this one, returning a new GraphData.

        Nodes are merged by primary key (duplicates are deduplicated).
        Edges are concatenated (duplicates may exist).

        This is the fundamental operation for combining graphs.

        Args:
            other: Another GraphData to merge

        Returns:
            New GraphData containing the merged result
        """
        merged_nodes: dict[str, pd.DataFrame] = {}
        merged_edges: dict[str, pd.DataFrame] = {}
        merged_primary_keys: dict[str, str] = {**self.primary_keys}

        # Merge nodes by type
        all_node_types = set(self.nodes.keys()) | set(other.nodes.keys())
        for node_type in all_node_types:
            dfs_to_merge = []
            pk = self.primary_keys.get(node_type) or other.primary_keys.get(node_type) or "id"
            merged_primary_keys[node_type] = pk

            if node_type in self.nodes:
                dfs_to_merge.append(self.nodes[node_type])
            if node_type in other.nodes:
                dfs_to_merge.append(other.nodes[node_type])

            if dfs_to_merge:
                combined = pd.concat(dfs_to_merge, ignore_index=True)
                # Deduplicate by primary key, keeping last
                if pk in combined.columns:
                    combined = combined.drop_duplicates(subset=[pk], keep="last")
                merged_nodes[node_type] = combined

        # Merge edges by type (simple concatenation)
        all_edge_types = set(self.edges.keys()) | set(other.edges.keys())
        for edge_type in all_edge_types:
            dfs_to_merge = []
            if edge_type in self.edges:
                dfs_to_merge.append(self.edges[edge_type])
            if edge_type in other.edges:
                dfs_to_merge.append(other.edges[edge_type])

            if dfs_to_merge:
                merged_edges[edge_type] = pd.concat(dfs_to_merge, ignore_index=True)

        # Merge metadata (other takes precedence)
        merged_metadata = {**self.metadata, **other.metadata}

        return GraphData(
            name=f"{self.name}+{other.name}" if self.name and other.name else self.name or other.name,
            nodes=merged_nodes,
            edges=merged_edges,
            metadata=merged_metadata,
            primary_keys=merged_primary_keys,
        )

    # -------------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------------

    @classmethod
    def empty(cls, name: str = "") -> GraphData:
        """Create an empty graph."""
        return cls(name=name)

    @classmethod
    def from_node_edge_dicts(
        cls,
        name: str,
        nodes_dict: dict[str, list[dict[str, Any]]],
        edges_list: list[dict[str, Any]],
        primary_keys: dict[str, str] | None = None,
    ) -> GraphData:
        """Create GraphData from dictionaries (backward compatibility helper).

        Args:
            name: Graph name
            nodes_dict: dict mapping node_type -> list of node property dicts
            edges_list: list of edge dicts with from_type, from_id, to_type, to_id, name, properties
            primary_keys: optional dict mapping node_type -> primary key field name

        Returns:
            GraphData instance
        """
        primary_keys = primary_keys or {}

        nodes: dict[str, pd.DataFrame] = {}
        for node_type, node_list in nodes_dict.items():
            if node_list:
                nodes[node_type] = pd.DataFrame(node_list)

        # Convert edges list to DataFrames by edge type
        edges: dict[str, pd.DataFrame] = {}
        for edge in edges_list:
            edge_type = edge.get("name", "RELATED_TO")
            edge_data = {
                "from_type": edge.get("from_type", ""),
                "from_id": edge.get("from_id", ""),
                "to_type": edge.get("to_type", ""),
                "to_id": edge.get("to_id", ""),
            }
            # Add any additional properties
            for k, v in edge.get("properties", {}).items():
                edge_data[k] = v

            if edge_type not in edges:
                edges[edge_type] = []
            edges[edge_type].append(edge_data)

        # Convert edge lists to DataFrames
        for edge_type, edge_list in list(edges.items()):
            if isinstance(edge_list, list):
                edges[edge_type] = pd.DataFrame(edge_list)

        return cls(
            name=name,
            nodes=nodes,
            edges=edges,
            primary_keys=primary_keys,
        )

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_parquet(self, base_path: str) -> dict[str, str]:
        """Export this graph to Parquet files.

        Creates files:
        - {base_path}/nodes/{node_type}.parquet for each node type
        - {base_path}/edges/{edge_type}.parquet for each edge type

        Args:
            base_path: Base directory for output files

        Returns:
            dict mapping type names to file paths
        """
        from pathlib import Path

        base = Path(base_path)
        paths: dict[str, str] = {}

        # Export nodes
        nodes_dir = base / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        for node_type, df in self.nodes.items():
            path = nodes_dir / f"{node_type}.parquet"
            df.to_parquet(path, index=False)
            paths[f"nodes/{node_type}"] = str(path)
            logger.debug(f"Exported {len(df)} nodes of type {node_type} to {path}")

        # Export edges
        edges_dir = base / "edges"
        edges_dir.mkdir(parents=True, exist_ok=True)
        for edge_type, df in self.edges.items():
            path = edges_dir / f"{edge_type}.parquet"
            df.to_parquet(path, index=False)
            paths[f"edges/{edge_type}"] = str(path)
            logger.debug(f"Exported {len(df)} edges of type {edge_type} to {path}")

        return paths

    @classmethod
    def from_parquet(cls, base_path: str, name: str = "") -> GraphData:
        """Load a graph from Parquet files.

        Args:
            base_path: Base directory containing nodes/ and edges/ subdirs
            name: Optional name for the loaded graph

        Returns:
            GraphData instance
        """
        from pathlib import Path

        base = Path(base_path)
        nodes: dict[str, pd.DataFrame] = {}
        edges: dict[str, pd.DataFrame] = {}

        # Load nodes
        nodes_dir = base / "nodes"
        if nodes_dir.exists():
            for path in nodes_dir.glob("*.parquet"):
                node_type = path.stem
                nodes[node_type] = pd.read_parquet(path)
                logger.debug(f"Loaded {len(nodes[node_type])} nodes of type {node_type}")

        # Load edges
        edges_dir = base / "edges"
        if edges_dir.exists():
            for path in edges_dir.glob("*.parquet"):
                edge_type = path.stem
                edges[edge_type] = pd.read_parquet(path)
                logger.debug(f"Loaded {len(edges[edge_type])} edges of type {edge_type}")

        return cls(name=name, nodes=nodes, edges=edges)

    def __repr__(self) -> str:
        return (
            f"GraphData(name={self.name!r}, "
            f"nodes={self.node_count()} ({len(self.nodes)} types), "
            f"edges={self.edge_count()} ({len(self.edges)} types))"
        )
