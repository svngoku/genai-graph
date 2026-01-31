"""Factory classes for creating graphs.

A GraphFactory loads data and provides a GraphSchema for extraction.
The actual graph is built via extract_graph_data() → merge_nodes_batch().

Classes:
    GraphFactory: Abstract base class for all graph factory implementations.
    JsonFileBackedGraphFactory: Factory for loading data from JSON files.
    TableBackedGraphFactory: Factory for loading data from SQL database tables.
    Neo4jGraphFactory: Factory for loading data from Neo4j JSONL exports.
"""

import hashlib
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Type

import pandas as pd
from loguru import logger
from pydantic import BaseModel
from rich.console import Console
from sqlalchemy import Engine, text
from upath import UPath

from genai_graph.core.graph_schema import GraphSchema

console = Console()


class GraphFactory(ABC, BaseModel):
    """Abstract base class for graph factory implementations.

    A GraphFactory provides:
    - A GraphSchema defining node types and relationships
    - A method to load structured data by key for graph extraction
    """

    # Optional class constant - set for factories with a single root model type.
    TOP_CLASS: Type[BaseModel] | None = None

    @property
    def name(self) -> str:
        """Name of this graph factory.

        Derived from TOP_CLASS if set, otherwise from build_schema().root_model_class.
        """
        if self.TOP_CLASS is not None:
            return self.TOP_CLASS.__name__
        # Fallback to root_model_class from schema
        schema = self.build_schema()
        if schema.root_model_class is not None:
            return schema.root_model_class.__name__
        return self.__class__.__name__

    @abstractmethod
    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load structured data for the given key.

        The returned Pydantic model is then processed by extract_graph_data()
        according to the schema from build_schema().
        """
        ...

    @abstractmethod
    def build_schema(self) -> GraphSchema:
        """Build and return the graph schema configuration.

        The schema defines node types, relationships, and how to extract data
        from Pydantic models.
        """
        ...

    def get_node_labels(self) -> dict[str, str]:
        """Get mapping of node types to human-readable descriptions from schema."""
        schema = self.build_schema()
        return {node.node_class.__name__: node.description for node in schema.nodes}

    def get_relationship_labels(self) -> dict[str, tuple[str, str]]:
        """Get mapping of relationship types to (direction, meaning) tuples from schema."""
        schema = self.build_schema()
        result = {}
        for relation in schema.relations:
            direction = f"{relation.from_node.__name__} → {relation.to_node.__name__}"
            result[relation.name] = (direction, relation.description)
        return result

    def get_sample_queries(self) -> list[str]:
        """Get list of sample Cypher queries for this graph."""
        return []

    def register(self, registry: Any = None) -> None:
        """Register this graph factory.

        If ``registry`` is not provided, the global :class:`GraphRegistry`
        instance is used.
        """
        from genai_graph.core.graph_registry import register_subgraph

        register_subgraph(self.name, self, registry=registry)


class JsonFileBackedGraphFactory(GraphFactory):
    """Graph factory that reads structured data from JSON files.

    This factory works with the output of 'baml extract' command, which stores
    extracted structured data as JSON files in a directory structure with model subdirectory.

    Note: TOP_CLASS must be set for this factory type.
    """

    data_root: str
    include: list[str] | None = None
    exclude: list[str] | None = None
    recursive: bool = True
    case_sensitive: bool = False

    _files_cache: list[UPath] | None = None

    # Class-level cache to track which (data_root, model) pairs have been initialized
    _initialized_roots: ClassVar[set[tuple[str, str]]] = set()

    def model_post_init(self, _context: object) -> None:
        """Initialize and discover JSON files matching the patterns.

        Uses class-level cache to avoid redundant file discovery when the same
        factory is instantiated multiple times.
        """
        from genai_tk.utils.file_patterns import resolve_config_path, resolve_files

        if self.TOP_CLASS is None:
            raise ValueError(f"{self.__class__.__name__} requires TOP_CLASS to be set")

        model_name = self.TOP_CLASS.__name__

        # Check if this root + model combination has already been initialized
        root_key = (self.data_root, model_name)
        if root_key in JsonFileBackedGraphFactory._initialized_roots:
            logger.debug(
                f"Skipping duplicate file discovery for {model_name} in {self.data_root} "
                f"(already discovered in this session)"
            )
            # Set empty cache - the actual files were already processed
            # This instance won't be used for actual data loading
            self._files_cache = []
            return

        resolved_root = resolve_config_path(self.data_root)
        root_path = UPath(resolved_root)

        if not root_path.exists():
            logger.warning(f"Data root directory not found: {root_path}")
            self._files_cache = []
            return

        # Build include patterns to find files in model subdirectory
        # Pattern format: {model_name}/{user_pattern} or **/{model_name}/{user_pattern}
        user_patterns = self.include or ["*.json"]
        include_patterns = []
        for pattern in user_patterns:
            if self.recursive:
                # Search recursively: **/ReviewedOpportunity/*.json
                include_patterns.append(f"**/{model_name}/{pattern}")
            else:
                # Direct subdirectory only: ReviewedOpportunity/*.json
                include_patterns.append(f"{model_name}/{pattern}")

        # Always exclude manifest.json files (metadata files from baml extract)
        exclude_patterns = self.exclude or []
        if not isinstance(exclude_patterns, list):
            exclude_patterns = [exclude_patterns]
        else:
            exclude_patterns = list(exclude_patterns)  # Copy to avoid modifying the original

        # Add manifest.json exclusions if not already present
        manifest_patterns = ["manifest.json", "**/manifest.json"]
        for manifest_pattern in manifest_patterns:
            if manifest_pattern not in exclude_patterns:
                exclude_patterns.append(manifest_pattern)

        # Use resolve_files to find all matching files
        # The exclude patterns will automatically filter out unwanted paths
        files = resolve_files(
            str(root_path),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            recursive=self.recursive,
            case_sensitive=self.case_sensitive,
        )

        self._files_cache = [UPath(f) for f in files]
        logger.debug(f"Discovered {len(self._files_cache)} files for model {model_name} in {root_path}")

        # Mark this root + model as initialized
        JsonFileBackedGraphFactory._initialized_roots.add(root_key)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level initialization cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh file discovery, especially when file contents may have changed.
        """
        cls._initialized_roots.clear()
        logger.debug(f"Cleared JsonFileBackedGraphFactory cache ({cls.__name__})")

    def get_all_file_paths(self) -> list[UPath]:
        """Get all discovered JSON file paths."""
        if self._files_cache is None:
            return []
        return self._files_cache

    def get_struct_data_by_file_path(self, file_path: UPath) -> BaseModel | None:
        """Load structured data from a JSON file."""
        if self.TOP_CLASS is None:
            raise ValueError(f"{self.__class__.__name__} requires TOP_CLASS to be set")
        try:
            json_text = file_path.read_text(encoding="utf-8")
            data = json.loads(json_text)
            return self.TOP_CLASS.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to load JSON file {file_path}: {e}")
            return None

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load structured data by key (interprets key as file path)."""
        file_path = UPath(key)
        return self.get_struct_data_by_file_path(file_path)


class TableBackedGraphFactory(GraphFactory):
    db_dsn: str
    files: list[UPath]
    pd_read_parameters: dict[str, Any] = {}

    _db_engine: Engine | None = None

    # Class-level cache to track which (db_dsn, table_name) pairs have been initialized
    # This prevents duplicate initialization across multiple instances
    _initialized_databases: ClassVar[set[tuple[str, str]]] = set()

    # Track warnings to avoid repetition
    _shown_warnings: ClassVar[set[str]] = set()
    db_dsn: str
    files: list[UPath]

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level initialization cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh data loading from database files.
        """
        cls._initialized_databases.clear()
        logger.debug(f"Cleared TableBackedGraphFactory cache ({cls.__name__})")

    @property
    def table_name(self) -> str:
        """Derive table name from TOP_CLASS name in snake_case.

        Subclasses should override this if TOP_CLASS is not set.
        """
        if self.TOP_CLASS is None:
            raise ValueError(
                f"{self.__class__.__name__} requires TOP_CLASS to be set, or override the table_name property"
            )
        # Convert PascalCase to snake_case
        name = self.TOP_CLASS.__name__
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        return snake

    def _normalize_column_name(self, name: str) -> str:
        """Normalise a column name to aid fuzzy matching.

        This helps when the `db_field` value comes from an Excel header that
        may have been renamed to be SQL-compliant.
        """
        return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")

    def resolve_db_field_name(self, db_field: str) -> str:
        """Resolve a configured DB field name to an actual table column name."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        table_name = self.table_name
        with self._db_engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()

        cols = [str(r[1]) for r in rows]  # PRAGMA: (cid, name, type, ...)
        if not cols:
            raise ValueError(f"No columns found for table '{table_name}'")

        # Exact match
        if db_field in cols:
            return db_field

        # Case-insensitive match
        for col in cols:
            if col.lower() == db_field.lower():
                return col

        # Normalised match
        target = self._normalize_column_name(db_field)
        for col in cols:
            if self._normalize_column_name(col) == target:
                return col

        available = ", ".join(cols)
        raise ValueError(f"Unknown db_field '{db_field}' for table '{table_name}'. Available: {available}")

    def get_struct_data_by_field(self, field_name: str, value: str) -> BaseModel | None:
        """Load data by an arbitrary DB column name instead of the key field."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        resolved = self.resolve_db_field_name(field_name)
        table_name = self.table_name

        query = text(f'SELECT * FROM {table_name} WHERE "{resolved}" = :value')
        with self._db_engine.connect() as conn:
            result = conn.execute(query, {"value": value}).fetchone()

        if result is None:
            logger.debug(f"No data found for {table_name}.{resolved}={value}")
            return None

        row_dict = dict(result._mapping)
        return self.mapper_function(row_dict)

    @abstractmethod
    def mapper_function(self, row: dict[str, Any]) -> BaseModel | None:
        """Map database row to model instance.

        Subclasses must implement this to convert a database row dictionary
        to an instance of their top_class model.
        """

    @abstractmethod
    def get_key_field(self) -> str:
        """Return the field name used as the unique key for data retrieval.
        Must implement by subclass.
        """

    def model_post_init(self, _context: Any) -> None:
        """Initialize the database engine and load data from files.

        This method uses a class-level cache to ensure that each (db_dsn, table_name)
        pair is only initialized once, even if the factory is instantiated multiple
        times (e.g., during GraphRegistry initialization).
        """
        from sqlalchemy import create_engine

        # Check if this database + table combination has already been initialized
        db_key = (self.db_dsn, self.table_name)
        if db_key in TableBackedGraphFactory._initialized_databases:
            logger.debug(
                f"Skipping duplicate initialization for {self.table_name} "
                f"(database already initialized in this session)"
            )
            # Still need to create engine for this instance to support queries
            self._db_engine = create_engine(self.db_dsn)
            return

        logger.debug(f"Initializing TableBackedGraphFactory with db_dsn: {self.db_dsn}")

        # Ensure parent directory exists for SQLite databases
        if self.db_dsn.startswith("sqlite:///"):
            # Extract file path from DSN (format: sqlite:///path/to/db.db)
            db_file_path = self.db_dsn.replace("sqlite:///", "")
            db_path = UPath(db_file_path)
            if db_path.parent and not db_path.parent.exists():
                logger.info(f"Creating database directory: {db_path.parent}")
                db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db_engine = create_engine(self.db_dsn)
        self._create_import_tracking_table()
        for file_path in self.files:
            self._process_file(file_path)

        # Mark this database + table as initialized
        TableBackedGraphFactory._initialized_databases.add(db_key)

    def _create_import_tracking_table(self) -> None:
        """Create a table to track imported files with checksums and timestamps."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS imported_files (
            file_path TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            import_date TIMESTAMP NOT NULL,
            row_count INTEGER NOT NULL
        )
        """
        with self._db_engine.connect() as conn:
            conn.execute(text(create_table_sql))
            conn.commit()
        logger.debug("Import tracking table created or verified")

    def _calculate_file_checksum(self, file_path: UPath) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        with open(str(file_path), "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _check_file_changed(self, file_path: UPath, checksum: str) -> bool | None:
        """Check if file has changed since last import.

        Returns:
            None: File never imported before
            False: File unchanged (same checksum)
            True: File changed (different checksum)
        """
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        query = text("SELECT checksum FROM imported_files WHERE file_path = :file_path")
        with self._db_engine.connect() as conn:
            result = conn.execute(query, {"file_path": str(file_path)}).fetchone()
            if result:
                existing_checksum = result[0]
                if existing_checksum == checksum:
                    return False  # Unchanged
                else:
                    return True  # Changed
            return None  # New file

    def _delete_file_data(self, file_path: UPath) -> None:
        """Delete all data previously imported from this file."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        table_name = self.table_name

        # Get the row count that will be deleted
        query = text("SELECT row_count FROM imported_files WHERE file_path = :file_path")
        with self._db_engine.connect() as conn:
            result = conn.execute(query, {"file_path": str(file_path)}).fetchone()
            if result:
                old_row_count = result[0]
                logger.debug(f"Deleting {old_row_count} rows from previous import of {file_path}")

        # Delete all data from the table (simple approach: delete everything since we only have one file typically)
        # In multi-file scenarios, you'd want to track file_source column for selective deletion
        delete_sql = text(f"DELETE FROM {table_name}")
        delete_tracking_sql = text("DELETE FROM imported_files WHERE file_path = :file_path")

        with self._db_engine.connect() as conn:
            result = conn.execute(delete_sql)
            conn.execute(delete_tracking_sql, {"file_path": str(file_path)})
            conn.commit()
            logger.debug(f"Deleting {result.rowcount} rows from table '{table_name}' for reimport")

    def _record_import(self, file_path: UPath, checksum: str, row_count: int) -> None:
        """Record file import in tracking table."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        delete_sql = text("DELETE FROM imported_files WHERE file_path = :file_path")
        insert_sql = text("""
            INSERT INTO imported_files (file_path, checksum, import_date, row_count)
            VALUES (:file_path, :checksum, :import_date, :row_count)
        """)
        with self._db_engine.connect() as conn:
            conn.execute(delete_sql, {"file_path": str(file_path)})
            conn.execute(
                insert_sql,
                {
                    "file_path": str(file_path),
                    "checksum": checksum,
                    "import_date": datetime.now(),
                    "row_count": row_count,
                },
            )
            conn.commit()
        logger.debug(f"Recorded import of {file_path} with {row_count} rows")

    def _process_file(self, file_path: UPath) -> None:
        """Process a single file: check existence, checksum, and import if needed."""
        # Check file exists
        if not file_path.exists():
            error_msg = f"File not found: {file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        logger.debug(f"Processing file: {file_path}")

        checksum = self._calculate_file_checksum(file_path)
        logger.debug(f"File checksum: {checksum}")

        # Check if file was previously imported
        file_changed = self._check_file_changed(file_path, checksum)

        if file_changed is False:
            logger.info(f"Skipping already imported file: {file_path}")
            return
        elif file_changed is True:
            # File changed - delete existing data before reimport
            warning_msg = f"File {file_path} has changed (checksum differs) - will delete old data and reimport"
            if warning_msg not in TableBackedGraphFactory._shown_warnings:
                logger.warning(warning_msg)
                TableBackedGraphFactory._shown_warnings.add(warning_msg)
            self._delete_file_data(file_path)

        try:
            df = self._load_dataframe(file_path)
            logger.debug(f"Loaded {len(df)} rows from {file_path}")
            self._import_dataframe(df)
            self._record_import(file_path, checksum, len(df))

        except Exception as e:
            error_msg = f"Failed to process file {file_path}: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _load_dataframe(self, file_path: UPath) -> pd.DataFrame:
        """Load data from Excel or CSV file using pandas."""
        file_suffix = file_path.suffix.lower()

        try:
            if file_suffix in [".xlsx", ".xls"]:
                logger.debug(f"Reading Excel file with parameters: {self.pd_read_parameters}")
                df = pd.read_excel(str(file_path), **self.pd_read_parameters)  # type: ignore[arg-type]
            elif file_suffix == ".csv":
                logger.debug(f"Reading CSV file with parameters: {self.pd_read_parameters}")
                df = pd.read_csv(str(file_path), **self.pd_read_parameters)  # type: ignore[arg-type]
            else:
                raise ValueError(f"Unsupported file format: {file_suffix}. Use .xlsx, .xls, or .csv")

            return df

        except Exception as e:
            logger.error(f"Failed to load file {file_path}: {str(e)}")
            raise

    def _import_dataframe(self, df: pd.DataFrame) -> None:
        """Import dataframe to SQL database with unique index on key field."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        table_name = self.table_name
        key_field = self.get_key_field()

        # Validate key field exists, check for null keys
        if key_field not in df.columns:
            raise ValueError(f"Key field '{key_field}' not found in dataframe columns: {df.columns.tolist()}")
        null_keys = df[key_field].isna().sum()
        if null_keys > 0:
            warning_msg = f"Found {null_keys} rows with null key field '{key_field}' - these will be skipped"
            if warning_msg not in TableBackedGraphFactory._shown_warnings:
                logger.warning(warning_msg)
                TableBackedGraphFactory._shown_warnings.add(warning_msg)
            df = df[df[key_field].notna()]

        # Remove duplicates based on key field
        initial_rows = len(df)
        df = df.drop_duplicates(subset=[key_field], keep="last")
        if len(df) < initial_rows:
            duplicate_count = initial_rows - len(df)
            if duplicate_count > 0:
                warning_msg = f"Removed {duplicate_count} duplicate rows based on key field '{key_field}'"
                if warning_msg not in TableBackedGraphFactory._shown_warnings:
                    logger.warning(warning_msg)
                    TableBackedGraphFactory._shown_warnings.add(warning_msg)

        try:
            # Check if table exists to decide on index creation
            from sqlalchemy import inspect
            from sqlalchemy.exc import IntegrityError

            inspector = inspect(self._db_engine)
            table_exists = table_name in inspector.get_table_names()

            # Try to import data - pandas will create the table on first call
            try:
                df.to_sql(name=table_name, con=self._db_engine, if_exists="append", index=False, method="multi")
                logger.debug(f"Successfully imported {len(df)} rows to table '{table_name}'")
            except IntegrityError as ie:
                # Handle duplicate key errors gracefully
                logger.warning(f"Integrity constraint violation during import: {str(ie)}")
                logger.warning("Attempting row-by-row upsert for conflicting records...")

                # Fall back to row-by-row upsert
                inserted = 0
                updated = 0
                skipped = 0

                for _idx, row in df.iterrows():
                    key_value = row[key_field]
                    try:
                        # Try insert
                        row.to_frame().T.to_sql(name=table_name, con=self._db_engine, if_exists="append", index=False)
                        inserted += 1
                    except IntegrityError:
                        # Key exists - update instead
                        try:
                            # Build UPDATE query with proper parameter names
                            # Replace spaces and special chars in column names for bind parameters
                            param_map = {}
                            set_parts = []
                            for col in df.columns:
                                if col != key_field:
                                    param_name = (
                                        col.replace(" ", "_")
                                        .replace("(", "")
                                        .replace(")", "")
                                        .replace(":", "_")
                                        .replace("-", "_")
                                    )
                                    set_parts.append(f'"{col}" = :{param_name}')
                                    param_map[param_name] = row[col]

                            # Add key field to params
                            key_param_name = (
                                key_field.replace(" ", "_")
                                .replace("(", "")
                                .replace(")", "")
                                .replace(":", "_")
                                .replace("-", "_")
                            )
                            param_map[key_param_name] = key_value

                            set_clause = ", ".join(set_parts)
                            update_sql = text(
                                f'UPDATE {table_name} SET {set_clause} WHERE "{key_field}" = :{key_param_name}'
                            )

                            with self._db_engine.connect() as conn:
                                conn.execute(update_sql, param_map)
                                conn.commit()
                            updated += 1
                        except Exception as update_err:
                            logger.warning(f"Failed to update row with key {key_value}: {update_err}")
                            skipped += 1

                logger.debug(f"Upsert complete: {inserted} inserted, {updated} updated, {skipped} skipped")

            # Create unique index only if table was just created (first import)
            if not table_exists:
                with self._db_engine.connect() as conn:
                    index_sql = f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{key_field.replace(" ", "_")} ON {table_name} ("{key_field}")'
                    conn.execute(text(index_sql))
                    conn.commit()
                    logger.debug(f"Created unique index on '{key_field}'")

        except Exception as e:
            logger.error(f"Failed to import dataframe to database: {str(e)}")
            raise

    def get_all_keys(self) -> list[str]:
        """Get all unique keys available in the database table.

        Returns:
            List of all unique key values from the table
        """
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        table_name = self.table_name
        key_field = self.get_key_field()

        logger.debug("Retrieving all unique keys from database")

        try:
            query = text(f'SELECT DISTINCT "{key_field}" FROM {table_name} ORDER BY "{key_field}"')

            with self._db_engine.connect() as conn:
                results = conn.execute(query).fetchall()

            keys = [str(row[0]) for row in results]
            logger.debug(f"Found {len(keys)} unique keys in database")
            return keys

        except Exception as e:
            logger.error(f"Failed to retrieve keys from database: {str(e)}")
            raise

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load data for the given key from the SQL database."""
        if self._db_engine is None:
            raise RuntimeError("Database engine not initialized")

        table_name = self.table_name
        key_field = self.get_key_field()

        logger.debug(f"Querying database for key: {key}")

        try:
            query = text(f'SELECT * FROM {table_name} WHERE "{key_field}" = :key')

            with self._db_engine.connect() as conn:
                result = conn.execute(query, {"key": key}).fetchone()

            if result is None:
                logger.warning(f"No data found for key: {key}")
                return None

            # Convert result to dict
            row_dict = dict(result._mapping)
            logger.debug(f"Found row with {len(row_dict)} columns")

            # Use mapper function to convert to model instance
            model_instance = self.mapper_function(row_dict)

            if model_instance is None:
                logger.warning(f"Mapper function returned None for key: {key}")
                return None

            logger.debug(f"Successfully retrieved and mapped data for key: {key}")
            return model_instance

        except Exception as e:
            logger.error(f"Failed to retrieve data for key {key}: {str(e)}")
            raise


class Neo4jGraphFactory(GraphFactory):
    """Graph factory that reads structured data from Neo4j JSONL exports.

    This factory analyzes and processes Neo4j JSONL export files, transforming
    the nodes and relationships according to a mapping specification. It handles
    large JSONL files efficiently with streaming processing.

    Configuration attributes:
        neo4j_export_file: Path to the Neo4j JSONL export file.
    """

    neo4j_export_file: str

    # Caches for processed data
    _schema_info: Any = None
    _node_data: dict[str, list[dict[str, Any]]] = {}
    _rel_data: dict[str, list[dict[str, Any]]] = {}
    _initialized: bool = False

    # Class-level cache to track which export files have been initialized
    _initialized_files: ClassVar[set[str]] = set()

    model_config = {
        "arbitrary_types_allowed": True,
    }

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level initialization cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh file discovery.
        """
        cls._initialized_files.clear()
        logger.debug(f"Cleared Neo4jGraphFactory cache ({cls.__name__})")

    def model_post_init(self, _context: object) -> None:
        """Initialize and analyze the Neo4j JSONL file.

        Uses class-level cache to avoid redundant processing when the same
        factory is instantiated multiple times.
        """
        from genai_tk.utils.file_patterns import resolve_config_path

        resolved_path = resolve_config_path(self.neo4j_export_file)
        export_path = UPath(resolved_path)

        if not export_path.exists():
            logger.warning(f"Neo4j export file not found: {export_path}")
            self._initialized = False
            return

        # Check if this file has already been processed
        file_key = str(export_path)
        if file_key in Neo4jGraphFactory._initialized_files:
            logger.debug(f"Skipping duplicate Neo4j JSONL analysis for {export_path}")
            self._initialized = True
            return

        logger.info(f"Analyzing Neo4j JSONL export: {export_path}")
        self._analyze_and_load(export_path)
        Neo4jGraphFactory._initialized_files.add(file_key)
        self._initialized = True

    def _analyze_and_load(self, export_path: UPath) -> None:
        """Analyze and load data from Neo4j JSONL file.

        This method performs streaming processing of the JSONL file,
        building node and relationship data structures.

        Args:
            export_path: Path to the JSONL file
        """
        from genai_graph.neo4j_import.schema_analyzer import SchemaAnalyzer

        # First pass: analyze schema
        analyzer = SchemaAnalyzer(str(export_path))
        self._schema_info = analyzer.analyze()

        # Second pass: collect transformed data
        self._node_data = {}
        self._rel_data = {}

        with export_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if line_num % 10000 == 0:
                    logger.debug(f"Processing line {line_num}...")

                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(f"Line {line_num}: Invalid JSON - {e}")
                    continue

                record_type = record.get("type")

                if record_type == "node":
                    self._process_node_record(record)
                elif record_type == "relationship":
                    self._process_rel_record(record)

        logger.info(
            f"Loaded {sum(len(v) for v in self._node_data.values())} nodes "
            f"and {sum(len(v) for v in self._rel_data.values())} relationships"
        )

    def _process_node_record(self, record: dict[str, Any]) -> None:
        """Process a single node record from JSONL.

        Args:
            record: The JSON record for a node
        """
        node_id = str(record.get("id", ""))
        labels = record.get("labels", [])
        properties = record.get("properties", {})

        for label in labels:
            if label not in self._node_data:
                self._node_data[label] = []

            node_record = {
                "_neo4j_id": node_id,
                **properties,
            }
            self._node_data[label].append(node_record)

    def _process_rel_record(self, record: dict[str, Any]) -> None:
        """Process a single relationship record from JSONL.

        Args:
            record: The JSON record for a relationship
        """
        rel_type = record.get("label", "UNKNOWN")
        start_node = record.get("start", {})
        end_node = record.get("end", {})
        properties = record.get("properties", {})

        start_id = str(start_node.get("id", ""))
        end_id = str(end_node.get("id", ""))
        start_labels = start_node.get("labels", [])
        end_labels = end_node.get("labels", [])

        # Create a key for this relationship type
        from_label = start_labels[0] if start_labels else "Unknown"
        to_label = end_labels[0] if end_labels else "Unknown"
        rel_key = f"{rel_type}__{from_label}__{to_label}"

        if rel_key not in self._rel_data:
            self._rel_data[rel_key] = []

        rel_record = {
            "_from_id": start_id,
            "_to_id": end_id,
            "_from_label": from_label,
            "_to_label": to_label,
            **properties,
        }
        self._rel_data[rel_key].append(rel_record)

    def get_schema_info(self) -> Any:
        """Return the analyzed schema information."""
        return self._schema_info

    def get_node_labels(self) -> list[str]:
        """Return all discovered node labels."""
        return list(self._node_data.keys())

    def get_relationship_types(self) -> list[str]:
        """Return all discovered relationship types."""
        return list(self._rel_data.keys())

    def get_nodes_by_label(self, label: str) -> list[dict[str, Any]]:
        """Get all nodes with a specific label.

        Args:
            label: The node label to filter by

        Returns:
            List of node data dictionaries
        """
        return self._node_data.get(label, [])

    def get_relationships_by_type(self, rel_type: str) -> list[dict[str, Any]]:
        """Get all relationships of a specific type.

        Args:
            rel_type: The relationship type key (format: TYPE__FromLabel__ToLabel)

        Returns:
            List of relationship data dictionaries
        """
        return self._rel_data.get(rel_type, [])

    def get_all_node_ids(self) -> list[str]:
        """Get all unique node IDs from the export.

        Returns:
            List of neo4j node IDs
        """
        ids = set()
        for nodes in self._node_data.values():
            for node in nodes:
                ids.add(node.get("_neo4j_id", ""))
        return sorted(ids)

    def get_struct_data_by_key(self, key: str) -> BaseModel | None:
        """Load structured data by key (neo4j node ID).

        This method is called during document ingestion. The key is expected
        to be a neo4j node ID, and this returns transformed data according
        to the schema mapping defined in build_schema.

        Args:
            key: The neo4j node ID

        Returns:
            Pydantic model instance or None if not found
        """
        # Subclasses must implement the mapping logic
        # Default implementation returns None - override in subclass
        return self._map_node_to_model(key)

    def _map_node_to_model(self, node_id: str) -> BaseModel | None:
        """Map a Neo4j node to a Pydantic model instance.

        Override this in subclasses to implement custom mapping logic.

        Args:
            node_id: The neo4j node ID

        Returns:
            Pydantic model instance or None
        """
        # Default: return None - subclasses must override
        return None
