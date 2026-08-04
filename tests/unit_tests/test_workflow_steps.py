"""Unit tests for workflow step wrappers and profile integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from genai_graph.orchestration.workflow_steps import kg_build_step, kg_create_step


class TestKgCreateStep:
    @patch("genai_graph.orchestration.flows.create_kg_flow")
    def test_basic_invocation(self, mock_flow: Any) -> None:
        mock_result = MagicMock()
        mock_result.stats.total_processed = 10
        mock_result.stats.total_failed = 1
        mock_result.warnings = ["warn1"]
        mock_result.db_path = "/tmp/kg/test"
        mock_flow.return_value = mock_result

        result = kg_create_step(config_name="one_rainbow")

        assert result["config_name"] == "one_rainbow"
        assert result["total_processed"] == 10
        assert result["total_failed"] == 1
        assert result["warnings_count"] == 1
        mock_flow.assert_called_once_with(
            config_name="one_rainbow",
            delete_first=False,
            export_html=True,
            force_rebuild=False,
        )

    @patch("genai_graph.orchestration.flows.create_kg_flow")
    def test_with_force_and_delete(self, mock_flow: Any) -> None:
        mock_result = MagicMock()
        mock_result.stats.total_processed = 5
        mock_result.stats.total_failed = 0
        mock_result.warnings = []
        mock_result.db_path = "/tmp/kg/test2"
        mock_flow.return_value = mock_result

        result = kg_create_step(
            config_name="stratnav_subset_rainbow_crm",
            delete_first=True,
            force_stage="parquet",
            export_html=False,
        )

        assert result["total_failed"] == 0
        mock_flow.assert_called_once_with(
            config_name="stratnav_subset_rainbow_crm",
            delete_first=True,
            export_html=False,
            force_rebuild=True,
        )

    @patch("genai_graph.orchestration.flows.create_kg_flow")
    def test_clears_factory_caches(self, mock_flow: Any) -> None:
        mock_result = MagicMock()
        mock_result.stats.total_processed = 0
        mock_result.stats.total_failed = 0
        mock_result.warnings = []
        mock_result.db_path = "/tmp/kg"
        mock_flow.return_value = mock_result

        with (
            patch("genai_graph.kg.factories.JsonFileBackedFactory") as mock_json,
            patch("genai_graph.kg.factories.TableBackedFactory") as mock_table,
            patch("genai_graph.kg.factories.Neo4jFactory") as mock_neo4j,
        ):
            kg_create_step(config_name="test")
            mock_json.clear_cache.assert_called_once()
            mock_table.clear_cache.assert_called_once()
            mock_neo4j.clear_cache.assert_called_once()


class TestKgBuildStep:
    """Tests for kg_build_step — inline graph config variant."""

    def _mock_result(self, processed: int = 5, failed: int = 0) -> MagicMock:
        r = MagicMock()
        r.stats.total_processed = processed
        r.stats.total_failed = failed
        r.warnings = []
        r.db_path = "/tmp/kg/inline"
        return r

    def _minimal_graph(self) -> dict:
        return {"factory": "some_project.schema.my_graph.MyGraph"}

    @patch("genai_graph.orchestration.flows.create_kg_flow")
    @patch("genai_graph.orchestration.workflow_steps._clear_factory_caches")
    @patch("genai_graph.kg.manager.get_kg_manager")
    def test_basic_build(self, mock_mgr: Any, mock_clear: Any, mock_flow: Any) -> None:
        mock_flow.return_value = self._mock_result()
        manager = MagicMock()
        mock_mgr.return_value = manager

        result = kg_build_step(graph=self._minimal_graph(), kg_name="my_kg")

        assert result["config_name"] == "my_kg"
        assert result["total_processed"] == 5
        mock_flow.assert_called_once_with(
            config_name="my_kg",
            delete_first=False,
            export_html=True,
            force_rebuild=False,
        )
        mock_clear.assert_called_once()

    @patch("genai_graph.orchestration.flows.create_kg_flow")
    @patch("genai_graph.orchestration.workflow_steps._clear_factory_caches")
    @patch("genai_graph.kg.manager.get_kg_manager")
    def test_registers_inline_profile(self, mock_mgr: Any, mock_clear: Any, mock_flow: Any) -> None:
        """kg_build_step must register the inline graph as a temporary KG profile."""
        mock_flow.return_value = self._mock_result()
        manager = MagicMock()
        mock_mgr.return_value = manager

        kg_build_step(graph=self._minimal_graph(), kg_name="inline_test")

        # Verify profile was written into the manager and profile was set
        manager.ekg_config.kg_configs.__setitem__.assert_called_once()
        call_args = manager.ekg_config.kg_configs.__setitem__.call_args
        assert call_args[0][0] == "inline_test"  # key
        assert manager.profile == "inline_test"

    @patch("genai_graph.orchestration.flows.create_kg_flow")
    @patch("genai_graph.orchestration.workflow_steps._clear_factory_caches")
    @patch("genai_graph.kg.manager.get_kg_manager")
    def test_result_dict_has_required_keys(self, mock_mgr: Any, mock_clear: Any, mock_flow: Any) -> None:
        mock_flow.return_value = self._mock_result(processed=7, failed=2)
        mock_mgr.return_value = MagicMock()

        result = kg_build_step(graph=self._minimal_graph(), kg_name="test")

        assert set(result.keys()) == {"config_name", "total_processed", "total_failed", "warnings_count", "db_path"}
        assert result["total_processed"] == 7
        assert result["total_failed"] == 2

    @patch("genai_graph.orchestration.flows.create_kg_flow")
    @patch("genai_graph.orchestration.workflow_steps._clear_factory_caches")
    @patch("genai_graph.kg.manager.get_kg_manager")
    def test_force_rebuild_passed_through(self, mock_mgr: Any, mock_clear: Any, mock_flow: Any) -> None:
        mock_flow.return_value = self._mock_result()
        mock_mgr.return_value = MagicMock()

        kg_build_step(graph=self._minimal_graph(), kg_name="k", force_stage="parquet", delete_first=True)

        mock_flow.assert_called_once_with(
            config_name="k",
            delete_first=True,
            export_html=True,
            force_rebuild=True,
        )
