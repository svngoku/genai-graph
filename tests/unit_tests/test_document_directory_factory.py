"""Unit tests for generic Document + Chunk node types and DocumentDirectoryFactory."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
class TestDocumentModel:
    """Tests for the generic Document model."""

    def test_required_fields(self) -> None:
        from genai_graph.kg.nodes.document import Document

        doc = Document(content_hash="abc123", path="/data/file.md", filename="file.md")
        assert doc.path == "/data/file.md"
        assert doc.filename == "file.md"

    def test_optional_defaults(self) -> None:
        from genai_graph.kg.nodes.document import Document

        doc = Document(content_hash="abc123", path="/data/file.md", filename="file.md")
        assert doc.file_size is None
        assert doc.mime_type is None
        assert doc.access_level == "public"
        assert doc.allowed_roles == []
        assert doc.allowed_users == []

    def test_access_control(self) -> None:
        from genai_graph.kg.nodes.document import Document

        doc = Document(
            content_hash="secret123",
            path="/data/secret.md",
            filename="secret.md",
            access_level="confidential",
            allowed_roles=["admin"],
        )
        assert doc.access_level == "confidential"
        assert doc.allowed_roles == ["admin"]


@pytest.mark.unit
class TestChunkModel:
    """Tests for the generic Chunk model."""

    def test_required_fields(self) -> None:
        from genai_graph.kg.nodes.document import Chunk

        chunk = Chunk(chunk_id="file.md::0", document_path="/data/file.md", text="Hello world", chunk_index=0)
        assert chunk.chunk_id == "file.md::0"
        assert chunk.document_path == "/data/file.md"
        assert chunk.text == "Hello world"
        assert chunk.chunk_index == 0

    def test_optional_defaults(self) -> None:
        from genai_graph.kg.nodes.document import Chunk

        chunk = Chunk(chunk_id="f::0", document_path="/data/f.md", text="text", chunk_index=0)
        assert chunk.start_offset is None
        assert chunk.end_offset is None
        assert chunk.token_count is None
        assert chunk.embedding is None

    def test_with_embedding(self) -> None:
        from genai_graph.kg.nodes.document import Chunk

        emb = [0.1, 0.2, 0.3]
        chunk = Chunk(chunk_id="f::0", document_path="/data/f.md", text="text", chunk_index=0, embedding=emb)
        assert chunk.embedding == emb


@pytest.mark.unit
class TestGraphNodeSingletons:
    """Tests for generic DocumentNode and ChunkNode singletons."""

    def test_document_node_keys(self) -> None:
        from genai_graph.kg.nodes.document import Document, DocumentNode

        assert DocumentNode.key_from == "content_hash"
        assert DocumentNode.name_from == "filename"
        assert DocumentNode.node_class is Document
        assert DocumentNode.explicitly_defined is True

    def test_chunk_node_keys(self) -> None:
        from genai_graph.kg.nodes.document import Chunk, ChunkNode

        assert ChunkNode.key_from == "chunk_id"
        assert ChunkNode.node_class is Chunk

    def test_contains_relation(self) -> None:
        from genai_graph.kg.nodes.document import CONTAINS_DOC, ChunkNode, DocumentNode

        assert CONTAINS_DOC.name == "CONTAINS"
        assert CONTAINS_DOC.from_node is DocumentNode
        assert CONTAINS_DOC.to_node is ChunkNode

    def test_next_relation(self) -> None:
        from genai_graph.kg.nodes.document import NEXT_CHUNK, ChunkNode

        assert NEXT_CHUNK.name == "NEXT"
        assert NEXT_CHUNK.from_node is ChunkNode
        assert NEXT_CHUNK.to_node is ChunkNode


@pytest.mark.unit
class TestDocumentDirectoryFactory:
    """Tests for the DocumentDirectoryFactory."""

    def test_build_schema_has_document_and_chunk(self) -> None:
        from genai_graph.kg.factories.document_factory import DocumentDirectoryFactory

        factory = DocumentDirectoryFactory(data_root="/tmp/docs")
        schema = factory.build_schema()

        node_names = [n.node_class.__name__ for n in schema.nodes]
        rel_names = [r.name for r in schema.relations]

        assert "Document" in node_names
        assert "Chunk" in node_names
        assert "CONTAINS" in rel_names
        assert "NEXT" in rel_names

    def test_build_document_from_file(self, tmp_path: Path) -> None:
        from genai_graph.kg.factories.document_factory import DocumentDirectoryFactory

        test_file = tmp_path / "sample.md"
        test_file.write_text("# Hello\n\nThis is a test document.")

        factory = DocumentDirectoryFactory(data_root=str(tmp_path))
        doc = factory.get_struct_data_by_key(str(test_file))

        assert doc is not None
        assert doc.filename == "sample.md"
        assert doc.path == str(test_file)
        assert doc.file_size > 0

    def test_get_keys_discovers_files(self, tmp_path: Path) -> None:
        from genai_graph.kg.factories.document_factory import DocumentDirectoryFactory

        (tmp_path / "a.md").write_text("# A")
        (tmp_path / "b.txt").write_text("B content")
        (tmp_path / "skip.json").write_text("{}")

        factory = DocumentDirectoryFactory(
            data_root=str(tmp_path),
            include=["*.md", "*.txt"],
        )
        keys = factory.get_keys()

        filenames = [Path(k).name for k in keys]
        assert "a.md" in filenames
        assert "b.txt" in filenames
        assert "skip.json" not in filenames

    def test_build_document_chunks_fallback(self, tmp_path: Path) -> None:
        """When chonkie fails, fallback paragraph chunker should work."""
        from genai_graph.kg.factories.document_factory import DocumentDirectoryFactory

        test_file = tmp_path / "doc.md"
        # Write content large enough to produce multiple chunks with small chunk_size
        test_file.write_text("# Paragraph 1\n\nThis is paragraph 1.\n\n# Paragraph 2\n\nThis is paragraph 2.")

        # Use a very small chunk_size to force multiple chunks
        factory = DocumentDirectoryFactory(data_root=str(tmp_path), chunk_size=5, overlap=0)
        chunks = factory.build_document_chunks(str(test_file))

        assert len(chunks) >= 1
        # Verify chunk fields
        assert chunks[0].chunk_index == 0
        # Verify all chunks refer to same document
        for chunk in chunks:
            assert chunk.document_path == str(test_file)

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from genai_graph.kg.factories.document_factory import DocumentDirectoryFactory

        factory = DocumentDirectoryFactory(data_root=str(tmp_path))
        result = factory.get_struct_data_by_key(str(tmp_path / "nonexistent.md"))

        assert result is None

    def test_sample_queries_present(self) -> None:
        from genai_graph.kg.factories.document_factory import DocumentDirectoryFactory

        factory = DocumentDirectoryFactory(data_root="/tmp")
        queries = factory.get_sample_queries()

        assert len(queries) > 0
        assert any("Document" in q for q in queries)
