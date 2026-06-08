"""Table-backed (Parquet cache) factory for Knowledge Graph construction.

This factory loads data from Excel/CSV files, caches them as Parquet files
for efficient reuse, and uses SHA-256 checksums to detect changes.

Cache layout: kg_outputs/{config_name}/table_cache/{table_name}/
  {stem}.parquet      — cached DataFrame
  {stem}.meta.json    — TableFileCacheMeta (checksum, import_date, row_count)
"""

import re
from abc import abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from genai_tk.utils.hashing import file_digest
from loguru import logger
from pydantic import BaseModel

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.schema.core import GraphSchema


class TableFileCacheMeta(BaseModel):
    """Metadata sidecar stored alongside each Parquet cache file."""

    source_path: str
    checksum: str
    import_date: str
    row_count: int


class TableBackedFactory(KgFactory):
    """KG factory that loads data from Excel/CSV files, caching them as Parquet.

    Each source file is processed into a Parquet file plus a `.meta.json`
    sidecar under ``kg_outputs/{config_name}/table_cache/{table_name}/``.
    SHA-256 checksums are used to skip re-processing unchanged files.
    All per-file DataFrames are merged in memory for fast key lookups.
    """

    files: list[Path]
    pd_read_parameters: dict[str, Any] = {}

    # Class-level cache: table_name -> merged DataFrame
    _cached_dataframes: ClassVar[dict[str, pd.DataFrame]] = {}

    # Deduplicate repeated warning messages across instances
    _shown_warnings: ClassVar[set[str]] = set()

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh data loading from source files.
        """
        cls._cached_dataframes.clear()
        cls._shown_warnings.clear()
        logger.debug(f"Cleared TableBackedFactory cache ({cls.__name__})")

    @property
    def table_name(self) -> str:
        """Derive table name from TOP_CLASS name in snake_case.

        Subclasses should override this if TOP_CLASS is not set.
        """
        if self.TOP_CLASS is None:
            raise ValueError(
                f"{self.__class__.__name__} requires TOP_CLASS to be set, or override the table_name property"
            )
        name = self.TOP_CLASS.__name__
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        return snake

    def _normalize_column_name(self, name: str) -> str:
        """Normalise a column name to aid fuzzy matching."""
        return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")

    def _get_cache_dir(self) -> Path:
        """Return (and create) the Parquet cache directory for this factory.

        Cache is stored under ``kg_outputs/_table_cache/{table_name}/``,
        shared across all KG configs that use the same source data.
        Checksums ensure that stale entries are automatically invalidated.
        """
        from genai_tk.config_mgmt.config_mngr import global_config

        cache_dir = (
            global_config().get_dir_path("paths.kg_outputs", create_if_not_exists=True)
            / "_table_cache"
            / self.table_name
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _get_df(self) -> pd.DataFrame:
        """Return the merged DataFrame for this factory's table."""
        tname = self.table_name
        if tname not in TableBackedFactory._cached_dataframes:
            raise RuntimeError(
                f"TableBackedFactory '{tname}' is not initialized. Ensure model_post_init completed successfully."
            )
        return TableBackedFactory._cached_dataframes[tname]

    def resolve_field_name(self, field: str) -> str:
        """Resolve a field name against actual DataFrame columns.

        Tries exact match, case-insensitive match, then normalised match.
        """
        df = self._get_df()
        cols = list(df.columns)

        if field in cols:
            return field
        for col in cols:
            if col.lower() == field.lower():
                return col
        target = self._normalize_column_name(field)
        for col in cols:
            if self._normalize_column_name(col) == target:
                return col

        available = ", ".join(cols)
        raise ValueError(f"Unknown field '{field}' for table '{self.table_name}'. Available: {available}")

    # Keep backward-compatible alias used by subclasses
    def resolve_db_field_name(self, db_field: str) -> str:
        """Alias for resolve_field_name (backward compatibility)."""
        return self.resolve_field_name(db_field)

    def get_struct_data_by_field(self, field_name: str, value: str) -> BaseModel | None:
        """Load data by an arbitrary column name instead of the key field."""
        resolved = self.resolve_field_name(field_name)
        df = self._get_df()
        rows = df[df[resolved] == value]
        if rows.empty:
            logger.debug(f"No data found for {self.table_name}.{resolved}={value}")
            return None
        return self.mapper_function(rows.iloc[0].to_dict())

    @abstractmethod
    def mapper_function(self, row: dict[str, Any]) -> BaseModel | None:
        """Map a row dict to a model instance. Must be implemented by subclasses."""

    @abstractmethod
    def get_key_field(self) -> str:
        """Return the column name used as the unique key."""

    def _warn(self, msg: str) -> None:
        """Log a warning once per unique message."""
        if msg not in TableBackedFactory._shown_warnings:
            logger.warning(msg)
            TableBackedFactory._shown_warnings.add(msg)

    def _load_dataframe(self, file_path: Path) -> pd.DataFrame:
        """Load an Excel or CSV file into a DataFrame."""
        suffix = file_path.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            logger.debug(f"Reading Excel file: {file_path.name}")
            return pd.read_excel(str(file_path), **self.pd_read_parameters)  # type: ignore[arg-type]
        if suffix == ".csv":
            logger.debug(f"Reading CSV file: {file_path.name}")
            return pd.read_csv(str(file_path), **self.pd_read_parameters)  # type: ignore[arg-type]
        raise ValueError(f"Unsupported file format: {suffix}. Use .xlsx, .xls, or .csv")

    def model_post_init(self, _context: Any) -> None:
        """Load source files into a merged in-memory DataFrame, using Parquet cache.

        For each source file:
        - If a Parquet + matching .meta.json (same SHA-256 checksum) exist → load from cache.
        - Otherwise → read from source, write Parquet + .meta.json.

        All per-file DataFrames are concatenated and deduplicated on the key field.
        """
        tname = self.table_name
        if tname in TableBackedFactory._cached_dataframes:
            logger.debug(f"Skipping duplicate initialization for '{tname}' (already loaded this session)")
            return

        logger.debug(f"Initializing TableBackedFactory '{tname}' from {len(self.files)} file(s)")
        cache_dir = self._get_cache_dir()
        key_field = self.get_key_field()
        per_file_dfs: list[pd.DataFrame] = []

        for file_path in self.files:
            if not file_path.exists():
                raise FileNotFoundError(f"Source file not found: {file_path}")

            checksum = file_digest(file_path, algorithm="sha256")
            stem = file_path.stem
            parquet_path = cache_dir / f"{stem}.parquet"
            meta_path = cache_dir / f"{stem}.meta.json"

            # Try cache hit
            cached_df: pd.DataFrame | None = None
            if parquet_path.exists() and meta_path.exists():
                try:
                    meta = TableFileCacheMeta.model_validate_json(meta_path.read_text())
                    if meta.checksum == checksum:
                        logger.info(f"Cache hit for '{file_path.name}' — loading from Parquet")
                        cached_df = pd.read_parquet(str(parquet_path))
                    else:
                        logger.info(f"Cache stale for '{file_path.name}' (checksum changed) — reimporting")
                except Exception as exc:
                    logger.warning(f"Could not read cache for '{file_path.name}': {exc} — reimporting")

            if cached_df is not None:
                per_file_dfs.append(cached_df)
                continue

            # Full import from source
            df = self._load_dataframe(file_path)
            logger.debug(f"Loaded {len(df)} rows from {file_path.name}")

            if key_field not in df.columns:
                raise ValueError(f"Key field '{key_field}' not found in columns: {df.columns.tolist()}")

            null_count = int(df[key_field].isna().sum())
            if null_count > 0:
                self._warn(f"Dropping {null_count} rows with null key '{key_field}' in {file_path.name}")
                df = df[df[key_field].notna()]

            dup_count = int(df.duplicated(subset=[key_field]).sum())
            if dup_count > 0:
                self._warn(f"Removing {dup_count} duplicate rows on key '{key_field}' in {file_path.name}")
                df = df.drop_duplicates(subset=[key_field], keep="last")

            # Write Parquet + sidecar
            df.to_parquet(str(parquet_path), index=False)
            meta = TableFileCacheMeta(
                source_path=str(file_path),
                checksum=checksum,
                import_date=datetime.now().isoformat(),
                row_count=len(df),
            )
            meta_path.write_text(meta.model_dump_json(indent=2))
            logger.info(f"Cached {len(df)} rows from '{file_path.name}' → {parquet_path.name}")
            per_file_dfs.append(df)

        if not per_file_dfs:
            logger.warning(f"No data loaded for '{tname}'")
            TableBackedFactory._cached_dataframes[tname] = pd.DataFrame()
            return

        merged = pd.concat(per_file_dfs, ignore_index=True)
        dup_after_merge = int(merged.duplicated(subset=[key_field]).sum())
        if dup_after_merge > 0:
            self._warn(f"Removing {dup_after_merge} cross-file duplicate rows on key '{key_field}'")
            merged = merged.drop_duplicates(subset=[key_field], keep="last")

        TableBackedFactory._cached_dataframes[tname] = merged
        logger.debug(f"TableBackedFactory '{tname}' ready: {len(merged)} rows")

    def get_all_keys(self) -> list[str]:
        """Get all unique key values from the in-memory DataFrame."""
        df = self._get_df()
        key_field = self.get_key_field()
        return [str(k) for k in df[key_field].dropna().unique()]

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load data for the given key from the in-memory DataFrame."""
        df = self._get_df()
        key_field = self.get_key_field()
        logger.debug(f"Looking up key '{key}' in '{self.table_name}'")

        rows = df[df[key_field].astype(str) == str(key)]
        if rows.empty:
            logger.warning(f"No data found for key: {key}")
            return None

        result = self.mapper_function(rows.iloc[0].to_dict())
        if result is None:
            logger.warning(f"Mapper returned None for key: {key}")
        return result

    def build_schema(self) -> GraphSchema:
        """Build and return the graph schema. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement build_schema()")
