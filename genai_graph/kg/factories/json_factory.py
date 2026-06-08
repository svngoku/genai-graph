"""JSON file-backed factory for Knowledge Graph construction.

This factory reads structured data from JSON files, typically the output
of 'baml extract' command which stores extracted structured data as JSON
files in a directory structure with model subdirectory.
"""

import json
from pathlib import Path
from typing import ClassVar

from loguru import logger
from pydantic import BaseModel

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.factories.document_mixin import DocumentMixin
from genai_graph.kg.schema.core import GraphSchema


class JsonFileBackedFactory(DocumentMixin, KgFactory):
    """KG factory that reads structured data from JSON files.

    This factory works with the output of 'baml extract' command, which stores
    extracted structured data as JSON files in a directory structure with model subdirectory.

    Note: build_schema() must return a schema with root_model_class set.
    """

    data_root: str
    include: list[str] | None = None
    exclude: list[str] | None = None
    recursive: bool = True
    case_sensitive: bool = False

    _files_cache: list[Path] | None = None

    # Class-level cache to track which (data_root, model) pairs have been initialized
    _initialized_roots: ClassVar[set[tuple[str, str]]] = set()

    def model_post_init(self, _context: object) -> None:
        """Initialize and discover JSON files matching the patterns.

        Uses class-level cache to avoid redundant file discovery when the same
        factory is instantiated multiple times.
        """
        from genai_tk.config_mgmt.file_patterns import resolve_config_path, resolve_files

        schema = self.build_schema()
        root_model_class = schema.root_model_class
        if root_model_class is None:
            raise ValueError(
                f"{self.__class__.__name__} requires build_schema() to return a schema with root_model_class set"
            )

        model_name = root_model_class.__name__

        # Check if this root + model combination has already been initialized
        root_key = (self.data_root, model_name)
        if root_key in JsonFileBackedFactory._initialized_roots:
            logger.debug(
                f"Skipping duplicate file discovery for {model_name} in {self.data_root} "
                f"(already discovered in this session)"
            )
            # Set empty cache - the actual files were already processed
            # This instance won't be used for actual data loading
            self._files_cache = []
            return

        resolved_root = resolve_config_path(self.data_root)
        root_path = Path(resolved_root)

        if not root_path.exists():
            logger.warning(f"Data root directory not found: {root_path}")
            self._files_cache = []
            return

        # Build pathspecs to find files in model subdirectory
        # Pattern format: {model_name}/{user_pattern} or **/{model_name}/{user_pattern}
        user_patterns = self.include or ["*.json"]
        pathspecs = []
        for pattern in user_patterns:
            if self.recursive:
                pathspecs.append(f"**/{model_name}/{pattern}")
            else:
                pathspecs.append(f"{model_name}/{pattern}")

        # Exclude manifest.json files (metadata files from baml extract)
        for manifest_pattern in ["manifest.json", "**/manifest.json"]:
            pathspecs.append(f"!{manifest_pattern}")

        # Add user-specified exclusions
        for excl in self.exclude or []:
            pathspecs.append(f"!{excl}")

        # Use resolve_files to find all matching files
        files = resolve_files(
            str(root_path),
            pathspecs=pathspecs,
        )

        self._files_cache = list(files)
        logger.debug(f"Discovered {len(self._files_cache)} files for model {model_name} in {root_path}")

        # Mark this root + model as initialized
        JsonFileBackedFactory._initialized_roots.add(root_key)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level initialization cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh file discovery, especially when file contents may have changed.
        """
        cls._initialized_roots.clear()
        logger.debug(f"Cleared JsonFileBackedFactory cache ({cls.__name__})")

    def get_all_file_paths(self) -> list[Path]:
        """Get all discovered JSON file paths."""
        if self._files_cache is None:
            return []
        return self._files_cache

    def get_struct_data_by_file_path(self, file_path: Path) -> BaseModel | None:
        """Load structured data from a JSON file."""
        schema = self.build_schema()
        root_model_class = schema.root_model_class
        if root_model_class is None:
            raise ValueError(
                f"{self.__class__.__name__} requires build_schema() to return a schema with root_model_class set"
            )
        try:
            json_text = file_path.read_text(encoding="utf-8")
            data = json.loads(json_text)
            return root_model_class.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to load JSON file {file_path}: {e}")
            return None

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load structured data by key (interprets key as file path)."""
        file_path = Path(key)
        return self.get_struct_data_by_file_path(file_path)

    def build_schema(self) -> GraphSchema:
        """Build and return the graph schema configuration.

        Subclasses must implement this to provide their specific schema.
        """
        raise NotImplementedError("Subclasses must implement build_schema()")
