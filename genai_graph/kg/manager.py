"""Central Knowledge Graph manager.

This module defines a singleton :class:`KgManager` responsible for
coordinating KG configuration, identity (profile + tag), filesystem
layout for artifacts, and high-level outcome/warning tracking.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

from genai_tk.utils.config_mngr import global_config
from genai_tk.utils.singleton import once
from loguru import logger
from pydantic import BaseModel, Field
from upath import UPath

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from genai_graph.kg.ingest.lineage import MarkdownLineage


class KgOutcome(BaseModel):
    """Record of a KG operation outcome."""

    timestamp: str
    operation: str
    status: str
    message: str
    details: dict[str, Any] | None = None


class KgGraphConfig(BaseModel):
    """Configuration for a single graph entry."""

    factory: str
    initial_load: list[str] = Field(default_factory=list)

    # Allow arbitrary extra keys (db_dsn, files, pull, trigger, ...)
    model_config = {
        "extra": "allow",
    }


class KgAgentConfig(BaseModel):
    """Agent-related configuration for a KG profile."""

    mcp_servers: list[str] = Field(default_factory=list)

    model_config = {
        "extra": "allow",
    }


class KgProfileConfig(BaseModel):
    """Configuration for a single KG profile (entry in ``kg_configs``)."""

    graphs: list[KgGraphConfig] = Field(default_factory=list)
    agent: KgAgentConfig | None = None
    imports: list[str] = Field(default_factory=list, alias="import")
    """List of KG config names to import before building this KG."""

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }


class KgConfig(BaseModel):
    """Top-level KG configuration loaded from ``config/ekg.yaml``."""

    kg_config: str
    kg_tag: str = "dev"
    schemas_root: str | None = None
    kg_configs: dict[str, KgProfileConfig] = Field(default_factory=dict)


class KgManager(BaseModel):
    """Singleton manager for KG configuration, identity and artifacts."""

    ekg_config: KgConfig
    profile: str
    tag: str
    warnings: list[str] = Field(default_factory=list)

    _base_path: UPath | None = None
    _db_path: UPath | None = None
    _html_path: UPath | None = None
    _schema_path: UPath | None = None
    _schema_json_path: UPath | None = None
    _schema_html_path: UPath | None = None
    _info_path: UPath | None = None
    _outcomes_file: UPath | None = None
    _warnings_file: UPath | None = None

    model_config = {
        "arbitrary_types_allowed": True,
    }

    # ------------------------------------------------------------------
    # Construction and activation
    # ------------------------------------------------------------------

    @classmethod
    def from_global_config(cls) -> "KgManager":
        """Build a manager instance from the current global configuration."""

        cfg = global_config()

        # Top-level KG config
        profile = cfg.get("kg_config", default="db_only")
        tag_env = os.environ.get("KG_CONFIG_TAG")
        tag = cfg.get("kg_tag", default=tag_env or "dev")

        try:
            kg_configs_dict = cfg.get_dict("kg_configs")
        except Exception:
            kg_configs_dict = {}

        schemas_root = cfg.get("schemas_root", default=None)

        ekg_config = KgConfig(
            kg_config=profile,
            kg_tag=tag,
            schemas_root=schemas_root,
            kg_configs={k: KgProfileConfig(**v) for k, v in kg_configs_dict.items()},
        )

        return cls(ekg_config=ekg_config, profile=profile, tag=tag)

    def reset_cached_paths(self) -> None:
        self._base_path = None
        self._db_path = None
        self._html_path = None
        self._schema_path = None
        self._schema_json_path = None
        self._schema_html_path = None
        self._info_path = None
        self._outcomes_file = None
        self._warnings_file = None

    def activate(self) -> tuple[str, str]:
        """Validate profile and return current profile and tag.

        Returns:
            Tuple of (profile, tag) in use.
        """
        if self.profile not in self.ekg_config.kg_configs:
            logger.warning(
                "Unknown KG_CONFIG= '%s'; available=%s",
                self.profile,
                sorted(self.ekg_config.kg_configs.keys()),
            )
        return (self.profile, self.tag)

    # ------------------------------------------------------------------
    # Configuration access
    # ------------------------------------------------------------------

    def get_profile_config(self) -> KgProfileConfig:
        """Return configuration for the active profile."""
        if self.profile not in self.ekg_config.kg_configs:
            raise KeyError(
                f"KG_CONFIG='{self.profile}' is not defined in ekg.yaml; "
                f"available: {sorted(self.ekg_config.kg_configs.keys())}"
            )
        return self.ekg_config.kg_configs[self.profile]

    def get_profile_dict(self) -> dict[str, Any]:
        """Return active profile configuration as a plain dictionary."""
        return self.get_profile_config().model_dump()

    # ------------------------------------------------------------------
    # Filesystem layout helpers - for any profile
    # ------------------------------------------------------------------

    def get_base_path_for(self, profile: str) -> UPath:
        """Return the base path for any given KG profile.

        Args:
            profile: KG configuration profile name

        Returns:
            Root directory for the specified KG profile
        """
        return global_config().get_dir_path("paths.kg_outputs") / profile

    def get_db_path_for(self, profile: str) -> UPath:
        """Return the database path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}.db"

    def get_html_path_for(self, profile: str) -> UPath:
        """Return the HTML export path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}.html"

    def get_schema_path_for(self, profile: str) -> UPath:
        """Return the schema text file path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}-schema.txt"

    def get_schema_json_path_for(self, profile: str) -> UPath:
        """Return the schema JSON file path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}-schema.json"

    def get_schema_html_path_for(self, profile: str) -> UPath:
        """Return the schema HTML file path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}-schema.html"

    def get_info_path_for(self, profile: str) -> UPath:
        """Return the info file path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}-info.md"

    def get_outcomes_file_for(self, profile: str) -> UPath:
        """Return the outcomes log file path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}-outcomes.jsonl"

    def get_warnings_file_for(self, profile: str) -> UPath:
        """Return the warnings log file path for any given KG profile."""
        return self.get_base_path_for(profile) / f"{profile}-{self.tag}-warnings.log"

    def ensure_directories_for(self, profile: str) -> None:
        """Create base directory for any given profile if it doesn't exist."""
        self.get_base_path_for(profile).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Filesystem layout helpers - for current profile (backward compatible)
    # ------------------------------------------------------------------

    @property
    def base_path(self) -> UPath:
        """Root directory for this KG profile."""
        if self._base_path is None:
            self._base_path = global_config().get_dir_path("paths.kg_outputs") / self.profile
        return self._base_path

    @property
    def db_path(self) -> UPath:
        """Path to the Kuzu database file for this KG."""
        if self._db_path is None:
            self._db_path = self.base_path / f"{self.profile}-{self.tag}.db"
        return self._db_path

    @property
    def html_path(self) -> UPath:
        """Path to the HTML export file for this KG."""
        if self._html_path is None:
            self._html_path = self.base_path / f"{self.profile}-{self.tag}.html"
        return self._html_path

    @property
    def schema_path(self) -> UPath:
        """Path to the schema text file for this KG."""
        if self._schema_path is None:
            self._schema_path = self.base_path / f"{self.profile}-{self.tag}-schema.txt"
        return self._schema_path

    @property
    def schema_json_path(self) -> UPath:
        """Path to the schema JSON file for this KG."""
        if self._schema_json_path is None:
            self._schema_json_path = self.base_path / f"{self.profile}-{self.tag}-schema.json"
        return self._schema_json_path

    @property
    def schema_html_path(self) -> UPath:
        """Path to the schema HTML visualization for this KG."""
        if self._schema_html_path is None:
            self._schema_html_path = self.base_path / f"{self.profile}-{self.tag}-schema.html"
        return self._schema_html_path

    @property
    def info_path(self) -> UPath:
        """Path to the info markdown file for this KG."""
        if self._info_path is None:
            self._info_path = self.base_path / f"{self.profile}-{self.tag}-info.md"
        return self._info_path

    @property
    def outcomes_file(self) -> UPath:
        """Path to the outcomes log file (JSONL)."""
        if self._outcomes_file is None:
            self._outcomes_file = self.base_path / f"{self.profile}-{self.tag}-outcomes.jsonl"
        return self._outcomes_file

    @property
    def warnings_file(self) -> UPath:
        """Path to the warnings log file (plain text)."""
        if self._warnings_file is None:
            self._warnings_file = self.base_path / f"{self.profile}-{self.tag}-warnings.log"
        return self._warnings_file

    def ensure_directories(self) -> None:
        """Create base directory if it doesn't exist."""
        self.base_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Outcome and warning management
    # ------------------------------------------------------------------

    def log_outcome(
        self,
        operation: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append a structured outcome entry to the JSONL outcomes file."""
        self.base_path.mkdir(parents=True, exist_ok=True)

        outcome = KgOutcome(
            timestamp=datetime.now().isoformat(),
            operation=operation,
            status=status,
            message=message,
            details=details,
        )

        with open(str(self.outcomes_file), "a") as f:
            f.write(outcome.model_dump_json() + "\n")

        logger.debug("[KG %s@%s] outcome: %s - %s", self.profile, self.tag, operation, status)

    def log_warnings(self, warnings: list[str]) -> None:
        """Append a block of warnings to the warnings log file."""
        if not warnings:
            return

        self.base_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().isoformat()
        with open(str(self.warnings_file), "a") as f:
            f.write(f"\n=== Warnings at {timestamp} ===\n")
            f.writelines(f"{warning}\n" for warning in warnings)

        logger.debug("[KG %s@%s] logged %d warnings", self.profile, self.tag, len(warnings))

    def get_recent_outcomes(self, limit: int = 10) -> list[KgOutcome]:
        """Return the most recent outcome entries (newest first)."""
        if not self.outcomes_file.exists():
            return []

        outcomes: list[KgOutcome] = []
        with open(str(self.outcomes_file)) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    outcomes.append(KgOutcome.model_validate_json(line))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to parse outcome line: %s", exc)

        return outcomes[-limit:][::-1]

    def get_recent_warnings(self, limit: int = 50) -> list[str]:
        """Return the most recent warning lines (newest first)."""
        if not self.warnings_file.exists():
            return []

        with open(str(self.warnings_file)) as f:
            lines = f.readlines()

        return [line.strip() for line in lines[-limit:][::-1]]

    def clear_all(self) -> None:
        """Remove all files and directories for this KG profile/tag."""
        import shutil

        if self.base_path.exists():
            shutil.rmtree(str(self.base_path))
            logger.info("Cleared all data for KG '%s@%s'", self.profile, self.tag)

    def get_info(self) -> dict[str, Any]:
        """Return information about this KG's artifacts and logs."""
        info: dict[str, Any] = {
            "profile": self.profile,
            "tag": self.tag,
            "base_path": str(self.base_path),
            "exists": self.base_path.exists(),
        }

        if not self.base_path.exists():
            return info

        # Database info
        if self.db_path.exists():
            info["database"] = {
                "path": str(self.db_path),
                "size_mb": self.db_path.stat().st_size / (1024 * 1024),
            }
        else:
            info["database"] = None

        # HTML export
        if self.html_path.exists():
            info["html_export"] = {
                "path": str(self.html_path),
                "size_mb": self.html_path.stat().st_size / (1024 * 1024),
            }
        else:
            info["html_export"] = None

        # Schema artifacts
        if self.schema_path.exists():
            info["schema"] = {
                "path": str(self.schema_path),
                "size_mb": self.schema_path.stat().st_size / (1024 * 1024),
            }
        else:
            info["schema"] = None

        if self.schema_json_path.exists():
            info["schema_json"] = {
                "path": str(self.schema_json_path),
                "size_mb": self.schema_json_path.stat().st_size / (1024 * 1024),
            }
        else:
            info["schema_json"] = None

        if self.schema_html_path.exists():
            info["schema_html"] = {
                "path": str(self.schema_html_path),
                "size_mb": self.schema_html_path.stat().st_size / (1024 * 1024),
            }
        else:
            info["schema_html"] = None

        # Outcomes
        if self.outcomes_file.exists():
            with open(str(self.outcomes_file)) as f:
                outcome_count = sum(1 for _ in f)
            info["outcomes"] = {
                "count": outcome_count,
                "file": str(self.outcomes_file),
            }
        else:
            info["outcomes"] = None

        # Warnings
        if self.warnings_file.exists():
            with open(str(self.warnings_file)) as f:
                warning_count = sum(1 for _ in f)
            info["warnings"] = {
                "count": warning_count,
                "file": str(self.warnings_file),
            }
        else:
            info["warnings"] = None

        return info

    def get_data_lineage(self) -> list["MarkdownLineage"]:
        """Return data lineage entries for JSON/Markdown/source artifacts.

        This delegates to :mod:`genai_graph.kg.ingest.lineage` so that
        lineage computation remains independent from this manager's other
        responsibilities while still exposing a convenient entry point for
        callers.

        Returns:
            List of MarkdownLineage objects describing source documents and
            associated JSON files for the active KG profile.
        """
        from genai_graph.kg.ingest.lineage import build_lineage_for_manager

        return build_lineage_for_manager(self)

    # ------------------------------------------------------------------
    # Warning collection helpers (for use as a context object)
    # ------------------------------------------------------------------

    def add_warning(self, message: str) -> None:
        """Record a warning message in memory (deduplicated on retrieval)."""
        if message and message not in self.warnings:
            self.warnings.append(message)

    def get_warnings(self) -> list[str]:
        """Return deduplicated warnings in order of first occurrence."""
        seen: set[str] = set()
        result: list[str] = []
        for warning in self.warnings:
            if warning not in seen:
                seen.add(warning)
                result.append(warning)
        return result

    def has_warnings(self) -> bool:
        """Return True if any warnings were collected in memory."""
        return bool(self.warnings)

    def clear_warnings(self) -> None:
        """Clear in-memory warnings (does not touch log files)."""
        self.warnings.clear()


@once
def get_kg_manager(activate: bool = True) -> KgManager:
    """Return the process-wide KgManager singleton.

    Args:
        activate: If True (default), validate profile and log warnings if needed.

    Returns:
        The singleton KgManager instance.
    """
    manager = KgManager.from_global_config()
    if activate:
        manager.activate()
    return manager


def reset_kg_manager() -> None:
    """Reset the singleton KgManager instance.

    Call this when configuration changes and a fresh instance is needed.
    """
    get_kg_manager.clear()  # type: ignore[attr-defined]
