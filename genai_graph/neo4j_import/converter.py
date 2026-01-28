"""Converter for Neo4j JSONL exports to Kuzu-compatible JSON files.

Transforms Neo4j JSONL exports into separate JSON files for nodes and relationships
that can be imported into Kuzu using COPY FROM statements.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from faker import Faker
from loguru import logger
from pydantic import BaseModel, Field

from genai_graph.neo4j_import.schema_analyzer import SchemaAnalyzer, SchemaInfo


class ConversionStats(BaseModel):
    """Statistics from the conversion process."""

    nodes_processed: int = 0
    relationships_processed: int = 0
    node_files_created: list[str] = Field(default_factory=list)
    rel_files_created: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class Neo4jToKuzuConverter:
    """Converts Neo4j JSONL exports to Kuzu-compatible JSON files."""

    def __init__(self, jsonl_path: str | Path) -> None:
        """Initialize the converter with a JSONL file path."""
        self.jsonl_path = Path(jsonl_path)
        self.schema: SchemaInfo | None = None
        self._node_data: dict[str, list[dict]] = defaultdict(list)
        self._rel_data: dict[str, list[dict]] = defaultdict(list)
        self._node_id_to_labels: dict[str, list[str]] = {}

    def analyze_schema(self) -> SchemaInfo:
        """Analyze the JSONL file to extract schema."""
        analyzer = SchemaAnalyzer(self.jsonl_path)
        self.schema = analyzer.analyze()
        return self.schema

    def convert(
        self,
        output_dir: str | Path,
        analyze_first: bool = True,
    ) -> ConversionStats:
        """Convert JSONL file to Kuzu-compatible JSON files.

        Args:
            output_dir: Directory to write output JSON files.
            analyze_first: Whether to analyze schema first (default True).

        Returns:
            ConversionStats with conversion results.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        stats = ConversionStats()

        if analyze_first and self.schema is None:
            self.analyze_schema()

        logger.info(f"Converting JSONL to Kuzu JSON files in: {output_path}")

        # First pass: collect all data
        self._collect_data(stats)

        # Write node files
        nodes_dir = output_path / "nodes"
        nodes_dir.mkdir(exist_ok=True)

        for label, nodes in self._node_data.items():
            file_path = nodes_dir / f"{label}.json"
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(nodes, f, indent=2, ensure_ascii=False)
            stats.node_files_created.append(str(file_path))
            logger.info(f"Created node file: {file_path} ({len(nodes)} nodes)")

        # Write relationship files
        rels_dir = output_path / "relationships"
        rels_dir.mkdir(exist_ok=True)

        for rel_key, rels in self._rel_data.items():
            # rel_key format: "REL_TYPE__FromLabel__ToLabel"
            file_name = rel_key.replace("__", "_")
            file_path = rels_dir / f"{file_name}.json"
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(rels, f, indent=2, ensure_ascii=False)
            stats.rel_files_created.append(str(file_path))
            logger.info(f"Created relationship file: {file_path} ({len(rels)} rels)")

        return stats

    def _collect_data(self, stats: ConversionStats) -> None:
        """Collect all node and relationship data from JSONL."""
        self._node_data.clear()
        self._rel_data.clear()
        self._node_id_to_labels.clear()

        with self.jsonl_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if line_num % 10000 == 0:
                    logger.debug(f"Processing line {line_num}...")

                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    stats.errors.append(f"Line {line_num}: Invalid JSON - {e}")
                    continue

                record_type = record.get("type")

                if record_type == "node":
                    self._process_node_for_conversion(record, stats)
                elif record_type == "relationship":
                    self._process_relationship_for_conversion(record, stats)

    def _process_node_for_conversion(self, record: dict, stats: ConversionStats) -> None:
        """Process a node record for conversion."""
        stats.nodes_processed += 1

        node_id = str(record.get("id", ""))
        labels = record.get("labels", [])
        properties = record.get("properties", {})

        # Store ID -> labels mapping
        self._node_id_to_labels[node_id] = labels

        # Create a node record for each label
        for label in labels:
            node_record = {"_neo4j_id": node_id}

            # Add all properties, converting complex types to strings
            for prop_name, prop_value in properties.items():
                node_record[prop_name] = self._convert_value(prop_value)

            self._node_data[label].append(node_record)

    def _process_relationship_for_conversion(
        self, record: dict, stats: ConversionStats
    ) -> None:
        """Process a relationship record for conversion."""
        stats.relationships_processed += 1

        rel_type = record.get("label", "UNKNOWN")
        start_node = record.get("start", {})
        end_node = record.get("end", {})
        properties = record.get("properties", {})

        start_id = str(start_node.get("id", ""))
        end_id = str(end_node.get("id", ""))

        start_labels = start_node.get("labels", [])
        end_labels = end_node.get("labels", [])

        # Create relationship records for each combination of labels
        for from_label in start_labels or ["UnknownNode"]:
            for to_label in end_labels or ["UnknownNode"]:
                rel_key = f"{rel_type}__{from_label}__{to_label}"

                rel_record = {
                    "from": start_id,
                    "to": end_id,
                }

                # Add all properties
                for prop_name, prop_value in properties.items():
                    rel_record[prop_name] = self._convert_value(prop_value)

                self._rel_data[rel_key].append(rel_record)

    def _convert_value(self, value: Any) -> Any:
        """Convert a value to a Kuzu-compatible format."""
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value


class SubsetCreator:
    """Creates a subset of a Neo4j JSONL export for testing."""

    def __init__(self, jsonl_path: str | Path) -> None:
        """Initialize with the source JSONL file path."""
        self.jsonl_path = Path(jsonl_path)
        self.faker = Faker()

    def create_subset(
        self,
        output_path: str | Path,
        max_nodes_per_label: int = 10,
        max_rels_per_type: int = 20,
        include_all_labels: bool = True,
        anonymize: bool = False,
        seed: int | None = None,
    ) -> dict[str, int]:
        """Create a subset of the JSONL file for testing.

        Args:
            output_path: Path for the output subset JSONL file.
            max_nodes_per_label: Maximum nodes to include per label.
            max_rels_per_type: Maximum relationships to include per type.
            include_all_labels: If True, include at least one node per label.
            anonymize: If True, anonymize string properties with fake data.
            seed: Random seed for reproducibility.

        Returns:
            Dictionary with counts of included nodes and relationships.
        """
        if seed is not None:
            random.seed(seed)
            Faker.seed(seed)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # First pass: collect nodes
        nodes_by_label: dict[str, list[dict]] = defaultdict(list)
        rels_by_type: dict[str, list[dict]] = defaultdict(list)

        logger.info(f"Reading source JSONL: {self.jsonl_path}")

        with self.jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                record_type = record.get("type")

                if record_type == "node":
                    labels = record.get("labels", [])
                    for label in labels:
                        nodes_by_label[label].append(record)
                elif record_type == "relationship":
                    rel_type = record.get("label", "UNKNOWN")
                    rels_by_type[rel_type].append(record)

        # Select subset of nodes
        selected_node_ids: set[str] = set()
        selected_nodes: list[dict] = []

        for label, nodes in nodes_by_label.items():
            # Shuffle and select
            random.shuffle(nodes)
            selected = nodes[:max_nodes_per_label]

            for node in selected:
                node_id = str(node.get("id", ""))
                if node_id not in selected_node_ids:
                    selected_node_ids.add(node_id)
                    if anonymize:
                        node = self._anonymize_node(node)
                    selected_nodes.append(node)

        # Select relationships that connect selected nodes
        selected_rels: list[dict] = []

        for rel_type, rels in rels_by_type.items():
            type_count = 0
            random.shuffle(rels)

            for rel in rels:
                start_id = str(rel.get("start", {}).get("id", ""))
                end_id = str(rel.get("end", {}).get("id", ""))

                # Only include relationships where both nodes are in subset
                if start_id in selected_node_ids and end_id in selected_node_ids:
                    if anonymize:
                        rel = self._anonymize_relationship(rel)
                    selected_rels.append(rel)
                    type_count += 1

                    if type_count >= max_rels_per_type:
                        break

        # Write subset file
        logger.info(f"Writing subset to: {output_path}")

        with output_path.open("w", encoding="utf-8") as f:
            for node in selected_nodes:
                f.write(json.dumps(node, ensure_ascii=False) + "\n")
            for rel in selected_rels:
                f.write(json.dumps(rel, ensure_ascii=False) + "\n")

        stats = {
            "nodes": len(selected_nodes),
            "relationships": len(selected_rels),
            "node_labels": len(nodes_by_label),
            "rel_types": len(rels_by_type),
        }

        logger.info(f"Subset created: {stats}")

        return stats

    def _anonymize_node(self, node: dict) -> dict:
        """Anonymize a node's string properties."""
        node = node.copy()
        properties = node.get("properties", {}).copy()

        for prop_name, prop_value in properties.items():
            if isinstance(prop_value, str) and prop_value:
                properties[prop_name] = self._generate_fake_value(prop_name, prop_value)

        node["properties"] = properties
        return node

    def _anonymize_relationship(self, rel: dict) -> dict:
        """Anonymize a relationship's properties."""
        rel = rel.copy()
        properties = rel.get("properties", {}).copy()

        for prop_name, prop_value in properties.items():
            if isinstance(prop_value, str) and prop_value:
                properties[prop_name] = self._generate_fake_value(prop_name, prop_value)

        rel["properties"] = properties

        # Also anonymize embedded node properties
        if "start" in rel:
            rel["start"] = self._anonymize_node(rel["start"])
        if "end" in rel:
            rel["end"] = self._anonymize_node(rel["end"])

        return rel

    def _generate_fake_value(self, prop_name: str, original_value: str) -> str:
        """Generate a fake value based on property name hints."""
        prop_lower = prop_name.lower()

        if "name" in prop_lower:
            if "company" in prop_lower or "org" in prop_lower:
                return self.faker.company()
            return self.faker.name()
        if "email" in prop_lower:
            return self.faker.email()
        if "phone" in prop_lower:
            return self.faker.phone_number()
        if "address" in prop_lower:
            return self.faker.address().replace("\n", ", ")
        if "city" in prop_lower:
            return self.faker.city()
        if "country" in prop_lower:
            return self.faker.country()
        if "code" in prop_lower:
            return self.faker.bothify(text="???###")
        if "url" in prop_lower or "website" in prop_lower:
            return self.faker.url()
        if "description" in prop_lower or "note" in prop_lower:
            return self.faker.sentence()

        # Default: generate similar length text
        return self.faker.pystr(min_chars=len(original_value), max_chars=len(original_value) + 5)
