"""Unit tests for Document node and DocumentMixin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genai_graph.kg.factories.document_mixin import DocumentMixin, _mtime_iso
from genai_graph.kg.nodes.document import Document, DocumentNode


class TestDocumentModel:
    def test_required_fields(self) -> None:
        doc = Document(content_hash="abc123", path="/data/foo.json", filename="foo.json")
        assert doc.content_hash == "abc123"
        assert doc.path == "/data/foo.json"
        assert doc.filename == "foo.json"

    def test_optional_fields_default_none(self) -> None:
        doc = Document(content_hash="abc123", filename="foo.json")
        assert doc.file_size is None
        assert doc.mime_type is None
        assert doc.modified_at is None
        assert doc.path is None

    def test_access_control_defaults(self) -> None:
        doc = Document(content_hash="abc123", filename="foo.json")
        assert doc.access_level == "public"
        assert doc.allowed_roles == []
        assert doc.allowed_users == []

    def test_access_control_explicit(self) -> None:
        doc = Document(
            content_hash="secret123",
            path="/data/secret.json",
            filename="secret.json",
            access_level="confidential",
            allowed_roles=["admin"],
            allowed_users=["alice", "bob"],
        )
        assert doc.access_level == "confidential"
        assert doc.allowed_roles == ["admin"]
        assert doc.allowed_users == ["alice", "bob"]

    def test_full_attributes(self) -> None:
        doc = Document(
            content_hash="abc123",
            path="/data/foo.json",
            filename="foo.json",
            file_size=1024,
            mime_type="application/json",
            modified_at="2026-01-01T00:00:00+00:00",
        )
        assert doc.file_size == 1024
        assert doc.mime_type == "application/json"
        assert doc.content_hash == "abc123"


class TestDocumentNode:
    def test_node_key_from_content_hash(self) -> None:
        assert DocumentNode.key_from == "content_hash"

    def test_node_name_from_filename(self) -> None:
        assert DocumentNode.name_from == "filename"

    def test_node_class(self) -> None:
        assert DocumentNode.node_class is Document

    def test_explicitly_defined(self) -> None:
        assert DocumentNode.explicitly_defined is True


class TestDocumentMixin:
    def test_create_document_node_populates_fields(self, tmp_path: Path) -> None:
        """create_document_node should read file attributes and return a Document."""
        test_file = tmp_path / "sample.json"
        test_file.write_text('{"hello": "world"}')

        mixin = DocumentMixin()

        with patch("genai_tk.utils.hashing.file_digest", return_value="deadbeef"):
            doc = mixin.create_document_node(Path(test_file))

        assert doc.path == str(test_file)
        assert doc.filename == "sample.json"
        assert doc.file_size == test_file.stat().st_size
        assert doc.mime_type == "application/json"
        assert doc.content_hash == "deadbeef"
        assert doc.modified_at is not None

    def test_create_document_node_handles_stat_error(self, tmp_path: Path) -> None:
        """create_document_node should not raise when stat fails."""
        mixin = DocumentMixin()
        non_existent = Path(tmp_path / "ghost.json")

        doc = mixin.create_document_node(non_existent)

        assert doc.path == str(non_existent)
        assert doc.file_size is None
        assert doc.modified_at is None

    def test_get_document_schema_elements_returns_mentions_relation(self) -> None:
        """get_document_schema_elements should return DocumentNode + MENTIONS relation."""
        from pydantic import BaseModel

        from genai_graph.kg.schema.core import GraphNode, GraphRelation

        class FakeRoot(BaseModel):
            id: str

        root_node = GraphNode(node_class=FakeRoot, name_from="id", key_from="id")

        mixin = DocumentMixin()
        nodes, relations = mixin.get_document_schema_elements(root_node)

        assert len(nodes) == 1
        assert nodes[0] is DocumentNode

        assert len(relations) == 1
        rel: GraphRelation = relations[0]
        assert rel.name == "MENTIONS"
        assert rel.from_node is DocumentNode
        assert rel.to_node is root_node


class TestMtimeIso:
    def test_returns_iso_string(self) -> None:
        result = _mtime_iso(0.0)
        assert result.startswith("1970-")

    def test_is_utc(self) -> None:
        result = _mtime_iso(0.0)
        assert "+00:00" in result or result.endswith("Z")
