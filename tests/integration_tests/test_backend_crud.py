"""Integration tests for KuzuBackend CRUD operations against a real Ladybug database."""

from __future__ import annotations

import pytest

from genai_graph.kg.backend import KuzuBackend


@pytest.fixture
def crud_backend(graph_backend: KuzuBackend) -> KuzuBackend:
    graph_backend.create_node_table("Person", {"id": "STRING", "name": "STRING", "tags": "STRING[]"}, "id")
    graph_backend.create_node_table("Company", {"id": "STRING", "name": "STRING"}, "id")
    graph_backend.create_relationship_table("WORKS_AT", "Person", "Company", {"since": "INT64"})
    return graph_backend


@pytest.mark.integration
class TestTableLifecycle:
    def test_create_node_table_idempotent(self, graph_backend: KuzuBackend) -> None:
        graph_backend.create_node_table("T1", {"id": "STRING"}, "id")
        graph_backend.create_node_table("T1", {"id": "STRING"}, "id")  # second call must not raise
        df = graph_backend.execute_get_as_df("CALL show_tables() RETURN *", union=False)
        assert "T1" in list(df["name"])

    def test_create_relationship_table_idempotent(self, graph_backend: KuzuBackend) -> None:
        graph_backend.create_node_table("A", {"id": "STRING"}, "id")
        graph_backend.create_node_table("B", {"id": "STRING"}, "id")
        graph_backend.create_relationship_table("R1", "A", "B")
        graph_backend.create_relationship_table("R1", "A", "B")  # must not raise
        df = graph_backend.execute_get_as_df("CALL show_tables() RETURN *", union=False)
        assert "R1" in list(df["name"])

    def test_drop_table(self, graph_backend: KuzuBackend) -> None:
        graph_backend.create_node_table("ToDrop", {"id": "STRING"}, "id")
        graph_backend.drop_table("ToDrop")
        df = graph_backend.execute_get_as_df("CALL show_tables() RETURN *", union=False)
        assert "ToDrop" not in list(df["name"])

    def test_drop_table_nonexistent_is_noop(self, graph_backend: KuzuBackend) -> None:
        graph_backend.drop_table("NeverExisted")  # must not raise


@pytest.mark.integration
class TestInsertAndQuery:
    def test_insert_node_and_read_back(self, crud_backend: KuzuBackend) -> None:
        crud_backend.insert_node("Person", {"id": "p1", "name": "Alice", "tags": ["eng", "lead"]})
        df = crud_backend.execute_get_as_df("MATCH (p:Person) RETURN p.id AS id, p.name AS name", union=False)
        assert list(df["id"]) == ["p1"]
        assert list(df["name"]) == ["Alice"]

    def test_insert_node_with_none_and_special_chars(self, crud_backend: KuzuBackend) -> None:
        crud_backend.insert_node("Person", {"id": "p2", "name": "O'Brien", "tags": None})
        df = crud_backend.execute_get_as_df("MATCH (p:Person {id: 'p2'}) RETURN p.name AS name", union=False)
        assert list(df["name"]) == ["O'Brien"]

    def test_merge_node_creates(self, crud_backend: KuzuBackend) -> None:
        created, node_id = crud_backend.merge_node("Person", {"id": "p3", "name": "Carol"})
        assert created is True
        assert node_id == "p3"
        df = crud_backend.execute_get_as_df("MATCH (p:Person {id: 'p3'}) RETURN count(p) AS cnt", union=False)
        assert int(df["cnt"].iloc[0]) == 1

    def test_merge_node_twice_no_duplicate(self, crud_backend: KuzuBackend) -> None:
        crud_backend.merge_node("Person", {"id": "p4", "name": "Dan"})
        crud_backend.merge_node("Person", {"id": "p4", "name": "Dan"})
        df = crud_backend.execute_get_as_df("MATCH (p:Person {id: 'p4'}) RETURN count(p) AS cnt", union=False)
        assert int(df["cnt"].iloc[0]) == 1

    def test_merge_node_without_name_field(self, crud_backend: KuzuBackend) -> None:
        # No 'name' key -> merge on the introspected primary key ('id')
        crud_backend.merge_node("Person", {"id": "p5"})
        df = crud_backend.execute_get_as_df("MATCH (p:Person {id: 'p5'}) RETURN count(p) AS cnt", union=False)
        assert int(df["cnt"].iloc[0]) == 1

    def test_merge_node_falls_back_to_name_key(self, graph_backend: KuzuBackend) -> None:
        # On a table whose PK *is* 'name', merging with only a name works
        graph_backend.create_node_table("City", {"name": "STRING"}, "name")
        graph_backend.merge_node("City", {"name": "Paris"})
        df = graph_backend.execute_get_as_df("MATCH (c:City {name: 'Paris'}) RETURN count(c) AS cnt", union=False)
        assert int(df["cnt"].iloc[0]) == 1

    def test_execute_get_as_df_real_query(self, crud_backend: KuzuBackend) -> None:
        crud_backend.insert_node("Person", {"id": "p6", "name": "Eve", "tags": []})
        df = crud_backend.execute_get_as_df("MATCH (p:Person) RETURN p.name AS name", union=False)
        assert "Eve" in list(df["name"])


@pytest.mark.integration
class TestVectorIndexLifecycle:
    def test_create_and_drop_vector_index(self, graph_backend: KuzuBackend) -> None:
        backend = graph_backend
        backend.execute("CREATE NODE TABLE Doc(id STRING, emb FLOAT[3], PRIMARY KEY(id))")
        backend.execute("CREATE (d:Doc {id: 'd1', emb: [1.0, 0.0, 0.0]})")

        backend.create_vector_index(table_name="Doc", field_name="emb", index_name="doc_emb_index")

        result = backend.query_vector_index("Doc", "doc_emb_index", [1.0, 0.0, 0.0], k=1)
        df = result.get_as_df()
        assert len(df) == 1

        backend.drop_vector_index("Doc", "doc_emb_index")
        with pytest.raises(RuntimeError):
            backend.query_vector_index("Doc", "doc_emb_index", [1.0, 0.0, 0.0], k=1)

    def test_create_vector_index_twice_is_noop(self, graph_backend: KuzuBackend) -> None:
        backend = graph_backend
        backend.execute("CREATE NODE TABLE Doc2(id STRING, emb FLOAT[3], PRIMARY KEY(id))")
        backend.create_vector_index(table_name="Doc2", field_name="emb", index_name="doc2_emb_index")
        backend.create_vector_index(table_name="Doc2", field_name="emb", index_name="doc2_emb_index")  # no raise
