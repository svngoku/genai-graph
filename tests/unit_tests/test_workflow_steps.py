"""Unit tests for workflow step wrappers and profile integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from genai_graph.orchestration.workflow_steps import kg_create_step


class TestKgCreateStep:
    @patch("genai_graph.orchestration.flows.create_kg_flow")
    @patch("genai_tk.extra.prefect.runtime.ephemeral_prefect_settings")
    def test_basic_invocation(self, mock_ephemeral: Any, mock_flow: Any) -> None:
        mock_ephemeral.return_value.__enter__ = MagicMock(return_value=None)
        mock_ephemeral.return_value.__exit__ = MagicMock(return_value=False)

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
    @patch("genai_tk.extra.prefect.runtime.ephemeral_prefect_settings")
    def test_with_force_and_delete(self, mock_ephemeral: Any, mock_flow: Any) -> None:
        mock_ephemeral.return_value.__enter__ = MagicMock(return_value=None)
        mock_ephemeral.return_value.__exit__ = MagicMock(return_value=False)

        mock_result = MagicMock()
        mock_result.stats.total_processed = 5
        mock_result.stats.total_failed = 0
        mock_result.warnings = []
        mock_result.db_path = "/tmp/kg/test2"
        mock_flow.return_value = mock_result

        result = kg_create_step(
            config_name="stratnav_subset_rainbow_crm",
            delete_first=True,
            force_rebuild=True,
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
    @patch("genai_tk.extra.prefect.runtime.ephemeral_prefect_settings")
    def test_clears_factory_caches(self, mock_ephemeral: Any, mock_flow: Any) -> None:
        mock_ephemeral.return_value.__enter__ = MagicMock(return_value=None)
        mock_ephemeral.return_value.__exit__ = MagicMock(return_value=False)

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
