"""Unit tests for KgManager: config models, path layout, outcomes and warnings logs."""

from __future__ import annotations

from pathlib import Path

import pytest

from genai_graph.kg.manager import (
    KgConfig,
    KgGraphConfig,
    KgManager,
    KgOutcome,
    KgProfileConfig,
    _extract_graphs_from_workflow,
)


@pytest.fixture
def kg_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KgManager:
    """KgManager whose kg_outputs root is redirected to a temp directory."""
    from genai_graph.kg import manager as manager_mod

    class _FakeCfg:
        def get_dir_path(self, key: str, create_if_not_exists: bool = False) -> Path:
            assert key == "paths.kg_outputs"
            if create_if_not_exists:
                tmp_path.mkdir(parents=True, exist_ok=True)
            return tmp_path

    monkeypatch.setattr(manager_mod, "global_config", lambda: _FakeCfg())

    ekg_config = KgConfig(
        kg_config="test_profile",
        kg_tag="t1",
        kg_configs={
            "test_profile": KgProfileConfig(graphs=[KgGraphConfig(factory="pkg.mod.MyFactory")]),
            "other_profile": KgProfileConfig(),
        },
    )
    return KgManager(ekg_config=ekg_config, profile="test_profile", tag="t1")


class TestExtractGraphsFromWorkflow:
    def test_pipeline_with_single_graph(self) -> None:
        workflows = {"wf": {"pipeline": [{"with": {"graph": {"factory": "a.B"}}}]}}
        assert _extract_graphs_from_workflow("wf", workflows) == [{"factory": "a.B"}]

    def test_pipeline_with_graphs_list(self) -> None:
        workflows = {"wf": {"pipeline": [{"with": {"graphs": [{"factory": "a.B"}, {"factory": "c.D"}]}}]}}
        assert _extract_graphs_from_workflow("wf", workflows) == [{"factory": "a.B"}, {"factory": "c.D"}]

    def test_legacy_steps_key(self) -> None:
        workflows = {"wf": {"steps": [{"with": {"graph": {"factory": "a.B"}}}]}}
        assert _extract_graphs_from_workflow("wf", workflows) == [{"factory": "a.B"}]

    def test_subworkflow_via_run(self) -> None:
        workflows = {
            "parent": {"pipeline": [{"run": "child"}]},
            "child": {"pipeline": [{"with": {"graph": {"factory": "a.B"}}}]},
        }
        assert _extract_graphs_from_workflow("parent", workflows) == [{"factory": "a.B"}]

    def test_subworkflow_via_invoke_v1(self) -> None:
        workflows = {
            "parent": {"steps": [{"invoke": {"kind": "workflow", "target": "child"}}]},
            "child": {"steps": [{"with": {"graph": {"factory": "a.B"}}}]},
        }
        assert _extract_graphs_from_workflow("parent", workflows) == [{"factory": "a.B"}]

    def test_cycle_does_not_recurse_forever(self) -> None:
        workflows = {
            "a": {"pipeline": [{"run": "b"}]},
            "b": {"pipeline": [{"run": "a"}, {"with": {"graph": {"factory": "x.Y"}}}]},
        }
        assert _extract_graphs_from_workflow("a", workflows) == [{"factory": "x.Y"}]

    def test_unknown_workflow_returns_empty(self) -> None:
        assert _extract_graphs_from_workflow("nope", {}) == []


class TestPathLayout:
    def test_db_and_artifact_paths(self, kg_manager: KgManager, tmp_path: Path) -> None:
        assert kg_manager.db_path == tmp_path / "test_profile" / "test_profile-t1.db"
        assert kg_manager.html_path.name == "test_profile-t1.html"
        assert kg_manager.schema_path.name == "test_profile-t1-schema.txt"
        assert kg_manager.schema_json_path.name == "test_profile-t1-schema.json"
        assert kg_manager.schema_html_path.name == "test_profile-t1-schema.html"
        assert kg_manager.info_path.name == "test_profile-t1-info.md"
        assert kg_manager.outcomes_file.name == "test_profile-t1-outcomes.jsonl"
        assert kg_manager.warnings_file.name == "test_profile-t1-warnings.log"
        assert kg_manager.warnings_md_path.name == "test_profile-t1-warnings.md"

    def test_paths_are_cached(self, kg_manager: KgManager) -> None:
        assert kg_manager.db_path is kg_manager.db_path

    def test_reset_cached_paths(self, kg_manager: KgManager) -> None:
        first = kg_manager.db_path
        kg_manager.reset_cached_paths()
        assert kg_manager._db_path is None
        assert kg_manager.db_path == first

    def test_per_profile_variants(self, kg_manager: KgManager, tmp_path: Path) -> None:
        other_db = kg_manager.get_db_path_for("other_profile")
        assert other_db == tmp_path / "other_profile" / "other_profile-t1.db"
        assert kg_manager.get_html_path_for("other_profile").parent.name == "other_profile"

    def test_ensure_directories(self, kg_manager: KgManager, tmp_path: Path) -> None:
        kg_manager.ensure_directories()
        assert (tmp_path / "test_profile").is_dir()
        kg_manager.ensure_directories_for("other_profile")
        assert (tmp_path / "other_profile").is_dir()


class TestProfileConfig:
    def test_get_profile_config(self, kg_manager: KgManager) -> None:
        cfg = kg_manager.get_profile_config()
        assert cfg.graphs[0].factory == "pkg.mod.MyFactory"

    def test_get_profile_config_unknown_raises(self, kg_manager: KgManager) -> None:
        kg_manager.profile = "ghost"
        with pytest.raises(KeyError, match="ghost"):
            kg_manager.get_profile_config()

    def test_get_profile_dict(self, kg_manager: KgManager) -> None:
        d = kg_manager.get_profile_dict()
        assert d["graphs"][0]["factory"] == "pkg.mod.MyFactory"

    def test_activate_returns_profile_and_tag(self, kg_manager: KgManager) -> None:
        assert kg_manager.activate() == ("test_profile", "t1")


class TestOutcomesAndWarnings:
    def test_log_and_read_outcomes(self, kg_manager: KgManager) -> None:
        kg_manager.log_outcome("create", "success", "all good", details={"n": 3})
        kg_manager.log_outcome("export", "failed", "boom")

        outcomes = kg_manager.get_recent_outcomes()
        assert len(outcomes) == 2
        # Newest first
        assert outcomes[0].operation == "export"
        assert outcomes[1].operation == "create"
        assert outcomes[1].details == {"n": 3}

    def test_get_recent_outcomes_empty(self, kg_manager: KgManager) -> None:
        assert kg_manager.get_recent_outcomes() == []

    def test_outcome_limit(self, kg_manager: KgManager) -> None:
        for i in range(15):
            kg_manager.log_outcome(f"op{i}", "ok", "m")
        outcomes = kg_manager.get_recent_outcomes(limit=5)
        assert len(outcomes) == 5
        assert outcomes[0].operation == "op14"

    def test_log_and_read_warnings(self, kg_manager: KgManager) -> None:
        kg_manager.log_warnings(["w1", "w2"])
        kg_manager.log_warnings(["w3"])
        warnings = kg_manager.get_recent_warnings()
        assert any("w3" in w for w in warnings)
        assert any("w1" in w for w in warnings)

    def test_log_warnings_empty_noop(self, kg_manager: KgManager) -> None:
        kg_manager.log_warnings([])
        assert not kg_manager.warnings_file.exists()

    def test_get_recent_warnings_empty(self, kg_manager: KgManager) -> None:
        assert kg_manager.get_recent_warnings() == []


class TestInMemoryWarnings:
    def test_add_and_dedup(self, kg_manager: KgManager) -> None:
        kg_manager.add_warning("a")
        kg_manager.add_warning("b")
        kg_manager.add_warning("a")
        assert kg_manager.get_warnings() == ["a", "b"]
        assert kg_manager.has_warnings()

    def test_add_empty_ignored(self, kg_manager: KgManager) -> None:
        kg_manager.add_warning("")
        assert not kg_manager.has_warnings()

    def test_clear(self, kg_manager: KgManager) -> None:
        kg_manager.add_warning("x")
        kg_manager.clear_warnings()
        assert kg_manager.get_warnings() == []


class TestGetInfo:
    def test_info_without_artifacts(self, kg_manager: KgManager) -> None:
        info = kg_manager.get_info()
        assert info["profile"] == "test_profile"
        assert info["tag"] == "t1"
        assert info["exists"] is False

    def test_info_with_artifacts(self, kg_manager: KgManager) -> None:
        kg_manager.ensure_directories()
        kg_manager.db_path.write_bytes(b"x" * 2048)
        kg_manager.html_path.write_text("<html></html>")
        kg_manager.schema_path.write_text("schema")
        kg_manager.schema_json_path.write_text("{}")
        kg_manager.schema_html_path.write_text("<html></html>")
        kg_manager.log_outcome("op", "ok", "msg")
        kg_manager.log_warnings(["w"])
        kg_manager.warnings_md_path.write_text("# Warnings")

        info = kg_manager.get_info()
        assert info["exists"] is True
        assert info["database"]["size_mb"] > 0
        assert info["html_export"] is not None
        assert info["schema"] is not None
        assert info["schema_json"] is not None
        assert info["schema_html"] is not None
        assert info["outcomes"]["count"] == 1
        assert info["warnings"]["count"] > 0
        assert info["warnings_report"]["size_bytes"] > 0

    def test_clear_all(self, kg_manager: KgManager, tmp_path: Path) -> None:
        kg_manager.ensure_directories()
        kg_manager.db_path.write_bytes(b"db")
        kg_manager.clear_all()
        assert not (tmp_path / "test_profile").exists()


class TestKgOutcomeModel:
    def test_jsonl_roundtrip(self) -> None:
        outcome = KgOutcome(timestamp="2026-01-01T00:00:00", operation="op", status="ok", message="m")
        parsed = KgOutcome.model_validate_json(outcome.model_dump_json())
        assert parsed == outcome
