"""Test PRIMARY KEY configuration in Kuzu tables."""

from pydantic import BaseModel

from genai_graph.core.graph_backend import create_in_memory_backend
from genai_graph.core.graph_core import create_schema
from genai_graph.core.graph_schema import GraphNode, GraphSchema


class TestPrimaryKeyConfiguration:
    """Test different PRIMARY KEY configurations."""

    def test_custom_field_as_primary_key(self) -> None:
        """Test using a custom field (e.g., opportunity_id) as PRIMARY KEY."""
        backend = create_in_memory_backend()

        class Opportunity(BaseModel):
            opportunity_id: str
            name: str

        node = GraphNode(
            node_class=Opportunity,
            name_from="name",
            key_from="opportunity_id",  # Custom field as primary key
        )

        schema = GraphSchema(
            root_model_class=Opportunity,
            nodes=[node],
            relations=[],
        )

        create_schema(backend, schema.nodes, schema.relations)

        # Verify table structure
        result = backend.execute("CALL table_info('Opportunity') RETURN *")
        table_info = []
        for row in result:
            table_info.append({"name": row[1], "primary_key": row[4]})

        # Find opportunity_id field and verify it's the primary key
        opportunity_id_field = next((f for f in table_info if f["name"] == "opportunity_id"), None)
        assert opportunity_id_field is not None, "opportunity_id field not found"
        assert opportunity_id_field["primary_key"] is True, "opportunity_id should be primary key"

    def test_auto_id_primary_key(self) -> None:
        """Test using AUTO_ID for UUID auto-generated PRIMARY KEY.

        AUTO_ID generates a UUID stored as STRING, allowing each node instance
        to have a unique identifier even if other fields like 'name' are duplicated.
        """
        backend = create_in_memory_backend()

        class Customer(BaseModel):
            name: str
            segment: str | None = None

        node = GraphNode(
            node_class=Customer,
            name_from="name",
            key_from="AUTO_ID",  # Auto-generated UUID stored as STRING
        )

        schema = GraphSchema(
            root_model_class=Customer,
            nodes=[node],
            relations=[],
        )

        create_schema(backend, schema.nodes, schema.relations)

        # Verify table structure
        result = backend.execute("CALL table_info('Customer') RETURN *")
        table_info = []
        for row in result:
            table_info.append({"name": row[1], "type": row[2], "primary_key": row[4]})

        # Verify id is the primary key and is STRING type (for UUID storage)
        id_field = next((f for f in table_info if f["name"] == "id"), None)
        assert id_field is not None, "id field not found"
        assert id_field["primary_key"] is True, "id should be primary key"
        assert "STRING" in id_field["type"], "id should be STRING type for UUID storage"

    def test_field_name_as_primary_key(self) -> None:
        """Test using 'name' field itself as PRIMARY KEY."""
        backend = create_in_memory_backend()

        class TechnicalComponent(BaseModel):
            name: str
            type: str | None = None

        node = GraphNode(
            node_class=TechnicalComponent,
            name_from="name",
            key_from="name",  # Use name field as primary key for deduplication
        )

        schema = GraphSchema(
            root_model_class=TechnicalComponent,
            nodes=[node],
            relations=[],
        )

        create_schema(backend, schema.nodes, schema.relations)

        # Verify table structure
        result = backend.execute("CALL table_info('TechnicalComponent') RETURN *")
        table_info = []
        for row in result:
            table_info.append({"name": row[1], "primary_key": row[4]})

        # Verify name is the primary key
        name_field = next((f for f in table_info if f["name"] == "name"), None)
        assert name_field is not None, "name field not found"
        assert name_field["primary_key"] is True, "name should be primary key"

    def test_computed_key_as_primary_key(self) -> None:
        """Test using a computed lambda as PRIMARY KEY."""
        backend = create_in_memory_backend()

        class SWArchitectureDocument(BaseModel):
            document_date: str
            title: str

        node = GraphNode(
            node_class=SWArchitectureDocument,
            name_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
            key_from=lambda data, base: f"Architecture:{data.get('document_date', 'unknown')}",
        )

        schema = GraphSchema(
            root_model_class=SWArchitectureDocument,
            nodes=[node],
            relations=[],
        )

        create_schema(backend, schema.nodes, schema.relations)

        # Verify table structure
        result = backend.execute("CALL table_info('SWArchitectureDocument') RETURN *")
        table_info = []
        for row in result:
            table_info.append({"name": row[1], "type": row[2], "primary_key": row[4]})

        # When using a callable key_from, an 'id' STRING field is created as primary key
        id_field = next((f for f in table_info if f["name"] == "id"), None)
        assert id_field is not None, "id field not found"
        assert id_field["primary_key"] is True, "id should be primary key"
        assert id_field["type"] == "STRING", "id should be STRING type for computed keys"
