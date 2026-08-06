"""Unit tests for JsonFileBackedFactory using real JSON files in a temp directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from genai_graph.kg.factories.json_factory import JsonFileBackedFactory
from genai_graph.kg.schema.core import GraphNode, GraphSchema


class Review(BaseModel):
    id: str
    title: str
    score: float | None = None


class ReviewFactory(JsonFileBackedFactory):
    def build_schema(self) -> GraphSchema:
        return GraphSchema(
            root_model_class=Review,
            nodes=[GraphNode(node_class=Review, name_from="title", key_from="id")],
            relations=[],
        )


class NoRootFactory(JsonFileBackedFactory):
    def build_schema(self) -> GraphSchema:
        return GraphSchema(root_model_class=None, nodes=[], relations=[])


@pytest.fixture(autouse=True)
def _clear_factory_cache():
    JsonFileBackedFactory.clear_cache()
    yield
    JsonFileBackedFactory.clear_cache()


@pytest.fixture
def json_root(tmp_path: Path) -> Path:
    """Directory layout produced by 'baml extract': <root>/**/Review/*.json + manifest.json."""
    model_dir = tmp_path / "Review"
    model_dir.mkdir()
    (model_dir / "r1.json").write_text(json.dumps({"id": "r1", "title": "First", "score": 0.5}))
    (model_dir / "r2.json").write_text(json.dumps({"id": "r2", "title": "Second"}))
    (model_dir / "manifest.json").write_text(json.dumps({"source": "report.md"}))
    # A *nested model directory*: recursion finds Review/ at any depth
    nested_model = tmp_path / "sub" / "Review"
    nested_model.mkdir(parents=True)
    (nested_model / "r3.json").write_text(json.dumps({"id": "r3", "title": "Third"}))
    return tmp_path


class TestFileDiscovery:
    def test_discovers_model_files_recursively(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root))
        paths = factory.get_all_file_paths()
        assert len(paths) == 3  # r1, r2, nested/r3 — manifest.json excluded
        names = sorted(p.name for p in paths)
        assert names == ["r1.json", "r2.json", "r3.json"]

    def test_non_recursive_excludes_nested_model_dirs(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root), recursive=False)
        names = sorted(p.name for p in factory.get_all_file_paths())
        assert names == ["r1.json", "r2.json"]

    def test_include_patterns(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root), include=["r1*"])
        names = [p.name for p in factory.get_all_file_paths()]
        assert names == ["r1.json"]

    def test_exclude_patterns(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root), exclude=["**/r2*"])
        names = sorted(p.name for p in factory.get_all_file_paths())
        assert names == ["r1.json", "r3.json"]

    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        factory = ReviewFactory(data_root=str(tmp_path / "ghost"))
        assert factory.get_all_file_paths() == []

    def test_requires_root_model_class(self, json_root: Path) -> None:
        with pytest.raises(ValueError, match="root_model_class"):
            NoRootFactory(data_root=str(json_root))

    def test_second_instance_uses_cache(self, json_root: Path) -> None:
        first = ReviewFactory(data_root=str(json_root))
        assert len(first.get_all_file_paths()) == 3
        # Second instance for the same (root, model) skips discovery
        second = ReviewFactory(data_root=str(json_root))
        assert second.get_all_file_paths() == []
        # After clearing, discovery runs again
        JsonFileBackedFactory.clear_cache()
        third = ReviewFactory(data_root=str(json_root))
        assert len(third.get_all_file_paths()) == 3


class TestDataLoading:
    def test_get_struct_data_by_key(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root))
        path = factory.get_all_file_paths()[0]
        review = factory.get_struct_data_by_key(str(path))
        assert isinstance(review, Review)
        assert review.id.startswith("r")

    def test_loads_all_fields(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root))
        r1 = json_root / "Review" / "r1.json"
        review = factory.get_struct_data_by_key(str(r1))
        assert review is not None
        assert review.title == "First"
        assert review.score == 0.5

    def test_invalid_json_returns_none(self, json_root: Path) -> None:
        bad = json_root / "Review" / "bad.json"
        bad.write_text("{not json")
        factory = ReviewFactory(data_root=str(json_root))
        assert factory.get_struct_data_by_key(str(bad)) is None

    def test_schema_violation_returns_none(self, json_root: Path) -> None:
        wrong = json_root / "Review" / "wrong.json"
        wrong.write_text(json.dumps({"unexpected": "fields only"}))
        factory = ReviewFactory(data_root=str(json_root))
        assert factory.get_struct_data_by_key(str(wrong)) is None

    def test_missing_file_returns_none(self, json_root: Path) -> None:
        factory = ReviewFactory(data_root=str(json_root))
        assert factory.get_struct_data_by_key(str(json_root / "Review" / "ghost.json")) is None
