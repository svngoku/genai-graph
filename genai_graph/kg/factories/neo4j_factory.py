"""Neo4j JSONL-backed factory for Knowledge Graph construction.

This factory reads structured data from Neo4j JSONL exports and transforms
them according to a mapping specification.
"""

import json
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from pydantic import BaseModel
from upath import UPath

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.schema.core import GraphSchema

if TYPE_CHECKING:
    from genai_graph.kg.ingest.extract import RelationshipRecord
    from genai_graph.kg.ingest.merge import NodeDataCollection


class Neo4jFactory(KgFactory):
    """KG factory that reads structured data from Neo4j JSONL exports.

    This factory analyzes and processes Neo4j JSONL export files, transforming
    the nodes and relationships according to a mapping specification. It handles
    large JSONL files efficiently with streaming processing.

    Configuration attributes:
        neo4j_export_file: Path to the Neo4j JSONL export file.
    """

    neo4j_export_file: str

    # Caches for processed data
    _schema_info: Any = None
    _node_data: dict[str, list[dict[str, Any]]] = {}
    _rel_data: dict[str, list[dict[str, Any]]] = {}
    _initialized: bool = False

    # Neo4j ID to node type mapping for relationship resolution
    _neo4j_id_to_label: dict[str, str] = {}

    # Class-level cache to track which export files have been initialized
    _initialized_files: ClassVar[set[str]] = set()

    model_config = {
        "arbitrary_types_allowed": True,
    }

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level initialization cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh file discovery.
        """
        cls._initialized_files.clear()
        logger.debug(f"Cleared Neo4jFactory cache ({cls.__name__})")

    def model_post_init(self, _context: object) -> None:
        """Initialize and analyze the Neo4j JSONL file.

        Uses class-level cache to avoid redundant processing when the same
        factory is instantiated multiple times.
        """
        from genai_tk.utils.file_patterns import resolve_config_path

        resolved_path = resolve_config_path(self.neo4j_export_file)
        export_path = UPath(resolved_path)

        if not export_path.exists():
            logger.warning(f"Neo4j export file not found: {export_path}")
            self._initialized = False
            return

        # Check if this file has already been processed
        file_key = str(export_path)
        if file_key in Neo4jFactory._initialized_files:
            logger.debug(f"Skipping duplicate Neo4j JSONL analysis for {export_path}")
            self._initialized = True
            return

        logger.info(f"Analyzing Neo4j JSONL export: {export_path}")
        self._analyze_and_load(export_path)
        Neo4jFactory._initialized_files.add(file_key)
        self._initialized = True

    def _analyze_and_load(self, export_path: UPath) -> None:
        """Analyze and load data from Neo4j JSONL file.

        This method performs streaming processing of the JSONL file,
        building node and relationship data structures.

        Args:
            export_path: Path to the JSONL file
        """
        from genai_graph.neo4j_import.schema_analyzer import SchemaAnalyzer

        # First pass: analyze schema
        analyzer = SchemaAnalyzer(str(export_path))
        self._schema_info = analyzer.analyze()

        # Second pass: collect transformed data
        self._node_data = {}
        self._rel_data = {}

        with export_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if line_num % 10000 == 0:
                    logger.debug(f"Processing line {line_num}...")

                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(f"Line {line_num}: Invalid JSON - {e}")
                    continue

                record_type = record.get("type")

                if record_type == "node":
                    self._process_node_record(record)
                elif record_type == "relationship":
                    self._process_rel_record(record)

        logger.info(
            f"Loaded {sum(len(v) for v in self._node_data.values())} nodes "
            f"and {sum(len(v) for v in self._rel_data.values())} relationships"
        )

    def _process_node_record(self, record: dict[str, Any]) -> None:
        """Process a single node record from JSONL.

        Args:
            record: The JSON record for a node
        """
        node_id = str(record.get("id", ""))
        labels = record.get("labels", [])
        properties = record.get("properties", {})

        # Track the primary label for this neo4j ID (used for relationship resolution)
        if labels:
            self._neo4j_id_to_label[node_id] = labels[0]

        for label in labels:
            if label not in self._node_data:
                self._node_data[label] = []

            node_record = {
                "_neo4j_id": node_id,
                **properties,
            }
            self._node_data[label].append(node_record)

    def _process_rel_record(self, record: dict[str, Any]) -> None:
        """Process a single relationship record from JSONL.

        Args:
            record: The JSON record for a relationship
        """
        rel_type = record.get("label", "UNKNOWN")
        start_node = record.get("start", {})
        end_node = record.get("end", {})
        properties = record.get("properties", {})

        start_id = str(start_node.get("id", ""))
        end_id = str(end_node.get("id", ""))
        start_labels = start_node.get("labels", [])
        end_labels = end_node.get("labels", [])

        # Create a key for this relationship type
        from_label = start_labels[0] if start_labels else "Unknown"
        to_label = end_labels[0] if end_labels else "Unknown"
        rel_key = f"{rel_type}__{from_label}__{to_label}"

        if rel_key not in self._rel_data:
            self._rel_data[rel_key] = []

        rel_record = {
            "_from_id": start_id,
            "_to_id": end_id,
            "_from_label": from_label,
            "_to_label": to_label,
            **properties,
        }
        self._rel_data[rel_key].append(rel_record)

    def get_schema_info(self) -> Any:
        """Return the analyzed schema information."""
        return self._schema_info

    def get_node_labels(self) -> dict[str, str]:  # type: ignore[override]
        """Return all discovered node labels.

        Note: This implementation returns a simplified dict[str, str] mapping
        instead of the base class's dict[str, str] with descriptions.
        """
        return {label: label for label in self._node_data.keys()}

    def get_relationship_types(self) -> list[str]:
        """Return all discovered relationship types."""
        return list(self._rel_data.keys())

    def get_nodes_by_label(self, label: str) -> list[dict[str, Any]]:
        """Get all nodes with a specific label.

        Args:
            label: The node label to filter by

        Returns:
            List of node data dictionaries
        """
        return self._node_data.get(label, [])

    def get_relationships_by_type(self, rel_type: str) -> list[dict[str, Any]]:
        """Get all relationships of a specific type.

        Args:
            rel_type: The relationship type key (format: TYPE__FromLabel__ToLabel)

        Returns:
            List of relationship data dictionaries
        """
        return self._rel_data.get(rel_type, [])

    def get_all_node_ids(self) -> list[str]:
        """Get all unique node IDs from the export.

        Returns:
            List of neo4j node IDs
        """
        ids = set()
        for nodes in self._node_data.values():
            for node in nodes:
                ids.add(node.get("_neo4j_id", ""))
        return sorted(ids)

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load structured data by key (neo4j node ID).

        This method is called during document ingestion. The key is expected
        to be a neo4j node ID, and this returns transformed data according
        to the schema mapping defined in build_schema.

        Args:
            key: The neo4j node ID

        Returns:
            Pydantic model instance or None if not found
        """
        # Subclasses must implement the mapping logic
        # Default implementation returns None - override in subclass
        return self._map_node_to_model(key)

    def _map_node_to_model(self, node_id: str) -> BaseModel | None:
        """Map a Neo4j node to a Pydantic model instance.

        Override this in subclasses to implement custom mapping logic.

        Args:
            node_id: The neo4j node ID

        Returns:
            Pydantic model instance or None
        """
        # Default: return None - subclasses must override
        return None

    def build_schema(self) -> GraphSchema:
        """Build and return the graph schema configuration.

        Subclasses must implement this to provide their specific schema.
        """
        raise NotImplementedError("Subclasses must implement build_schema()")


class Neo4jImportFactory(Neo4jFactory):
    """Extended Neo4j factory for direct graph import with mappings.

    This factory provides a complete solution for importing Neo4j data with:
    - Node type renaming (e.g., "Account" -> "Customer")
    - Property renaming (e.g., "irisCode" -> "iris_code")
    - Property filtering (include only specified properties)
    - Relationship type renaming
    - Node type filtering (only import specified types)

    Subclasses should define:
    - node_mappings: Dict mapping Neo4j labels to (target_type, property_mappings)
    - relationship_mappings: Dict mapping Neo4j rel types to target rel names
    - included_node_types: Optional set of Neo4j labels to include (all if None)
    - included_rel_types: Optional set of Neo4j rel types to include (all if None)

    The factory bypasses the hierarchical model extraction and directly builds
    NodeDataCollection and RelationshipRecord lists for import.
    """

    @property
    def name(self) -> str:
        """Factory name for registration."""
        return self.__class__.__name__

    def get_node_mappings(self) -> dict[str, tuple[str, dict[str, str]]]:
        """Get node type and property mappings.

        Override this to define your mappings.

        Returns:
            Dict mapping Neo4j label to (target_type, {neo4j_prop: target_prop})

        Example:
            {
                "Account": ("Customer", {"irisCode": "iris_code", "name": "name"}),
                "L3": ("L3Service", {"L3Code": "code", "name": "name"}),
            }
        """
        return {}

    def get_relationship_mappings(self) -> dict[str, str]:
        """Get relationship type mappings.

        Override this to define your relationship mappings.

        Returns:
            Dict mapping Neo4j rel type to target rel name

        Example:
            {
                "HAS_AMBITION": "HAS_AMBITION",
                "USES": "USES_SERVICE",
            }
        """
        return {}

    def get_included_node_types(self) -> set[str] | None:
        """Get the set of Neo4j labels to include.

        Override to filter node types. Return None to include all.

        Returns:
            Set of Neo4j labels to include, or None for all
        """
        return None

    def get_included_rel_types(self) -> set[str] | None:
        """Get the set of Neo4j relationship types to include.

        Override to filter relationship types. Return None to include all.

        Returns:
            Set of Neo4j rel types to include, or None for all
        """
        return None

    def build_schema(self) -> GraphSchema:
        """Build a minimal schema (not used for direct import)."""
        return GraphSchema(root_model_class=None, nodes=[], relations=[])

    def build_nodes_and_relationships(
        self,
    ) -> tuple["NodeDataCollection", list["RelationshipRecord"]]:
        """Build NodeDataCollection and relationships from Neo4j data.

        This method applies the configured mappings to transform Neo4j data
        into the target format for direct import.

        Returns:
            Tuple of (NodeDataCollection, list[RelationshipRecord])
        """
        from datetime import datetime

        from genai_graph.kg.ingest.extract import RelationshipRecord
        from genai_graph.kg.ingest.merge import NodeDataCollection

        nodes_data = NodeDataCollection()
        relationships: list[RelationshipRecord] = []

        node_mappings = self.get_node_mappings()
        rel_mappings = self.get_relationship_mappings()
        included_nodes = self.get_included_node_types()
        included_rels = self.get_included_rel_types()

        # Track neo4j_id -> (target_type, target_id) for relationship resolution
        id_mapping: dict[str, tuple[str, str]] = {}
        now = datetime.utcnow().isoformat() + "Z"

        # Process nodes
        for neo4j_label, node_list in self._node_data.items():
            # Filter by included types
            if included_nodes is not None and neo4j_label not in included_nodes:
                logger.debug(f"Skipping node type {neo4j_label} (not in included types)")
                continue

            # Get mapping config (or use defaults)
            if neo4j_label in node_mappings:
                target_type, prop_mapping = node_mappings[neo4j_label]
            else:
                # No mapping defined - use original label and all properties
                target_type = neo4j_label
                prop_mapping = {}

            for node in node_list:
                neo4j_id = node.get("_neo4j_id", "")

                # Apply property mapping
                if prop_mapping:
                    # Only include mapped properties
                    mapped_props = {}
                    for neo4j_prop, target_prop in prop_mapping.items():
                        if neo4j_prop in node:
                            mapped_props[target_prop] = node[neo4j_prop]
                else:
                    # No mapping - copy all properties except internal ones
                    mapped_props = {k: v for k, v in node.items() if not k.startswith("_")}

                # Ensure 'id' and 'name' fields exist
                if "id" not in mapped_props:
                    # Use neo4j_id or first available unique identifier
                    mapped_props["id"] = neo4j_id

                if "name" not in mapped_props:
                    # Try common name fields
                    for name_field in ["name", "title", "label", "id"]:
                        if name_field in mapped_props:
                            mapped_props["name"] = str(mapped_props[name_field])
                            break
                    else:
                        mapped_props["name"] = neo4j_id

                # Add metadata timestamps
                mapped_props["_created_at"] = now
                mapped_props["_updated_at"] = now

                # Track for relationship resolution
                id_mapping[neo4j_id] = (target_type, mapped_props["id"])

                nodes_data.add(target_type, mapped_props)

        # Process relationships
        for rel_key, rel_list in self._rel_data.items():
            # Parse rel_key format: TYPE__FromLabel__ToLabel
            parts = rel_key.split("__")
            neo4j_rel_type = parts[0] if parts else rel_key

            # Filter by included types
            if included_rels is not None and neo4j_rel_type not in included_rels:
                logger.debug(f"Skipping relationship type {neo4j_rel_type} (not in included types)")
                continue

            # Get mapped relationship name
            target_rel_type = rel_mappings.get(neo4j_rel_type, neo4j_rel_type)

            for rel in rel_list:
                from_neo4j_id = rel.get("_from_id", "")
                to_neo4j_id = rel.get("_to_id", "")

                # Resolve to target types/ids
                if from_neo4j_id not in id_mapping or to_neo4j_id not in id_mapping:
                    # One or both nodes were filtered out
                    continue

                from_type, from_id = id_mapping[from_neo4j_id]
                to_type, to_id = id_mapping[to_neo4j_id]

                # Extract relationship properties (excluding internal fields)
                rel_props = {k: v for k, v in rel.items() if not k.startswith("_")}

                relationships.append(
                    RelationshipRecord(
                        from_type=from_type,
                        from_id=from_id,
                        to_type=to_type,
                        to_id=to_id,
                        name=target_rel_type,
                        properties=rel_props,
                    )
                )

        logger.info(
            f"Built {nodes_data.total_count()} nodes ({', '.join(f'{t}:{len(n)}' for t, n in nodes_data.items())}) "
            f"and {len(relationships)} relationships"
        )

        return nodes_data, relationships
