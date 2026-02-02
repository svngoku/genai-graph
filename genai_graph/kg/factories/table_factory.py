"""Table-backed (SQL database) factory for Knowledge Graph construction.

This factory loads data from SQL database tables (SQLite, etc.) populated
from Excel/CSV files.
"""

import re
from abc import abstractmethod
from datetime import datetime
from typing import Any, ClassVar

import pandas as pd
from genai_tk.utils.hashing import file_digest
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import Engine, text
from upath import UPath

from genai_graph.kg.factories.base import KgFactory
from genai_graph.kg.schema.core import GraphSchema


class TableBackedFactory(KgFactory):
    """KG factory that loads data from SQL database tables.

    Data is loaded from Excel/CSV files into a SQLite database for efficient
    querying during graph construction.
    """

    db_dsn: str
    files: list[UPath]
    pd_read_parameters: dict[str, Any] = {}

    _db_engine: Engine | None = None

    # Class-level cache to track which (db_dsn, table_name) pairs have been initialized
    # This prevents duplicate initialization across multiple instances
    _initialized_databases: ClassVar[set[tuple[str, str]]] = set()

    # Track warnings to avoid repetition
    _shown_warnings: ClassVar[set[str]] = set()

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the class-level initialization cache.

        Call this at the start of a new KG creation workflow to ensure
        fresh data loading from database files.
        """
        cls._initialized_databases.clear()
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
        if db_key in TableBackedFactory._initialized_databases:
            logger.debug(
                f"Skipping duplicate initialization for {self.table_name} "
                f"(database already initialized in this session)"
            )
            # Still need to create engine for this instance to support queries
            self._db_engine = create_engine(self.db_dsn)
            return

        logger.debug(f"Initializing TableBackedFactory with db_dsn: {self.db_dsn}")

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
        TableBackedFactory._initialized_databases.add(db_key)

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
        return file_digest(file_path, algorithm="sha256")

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
            if warning_msg not in TableBackedFactory._shown_warnings:
                logger.warning(warning_msg)
                TableBackedFactory._shown_warnings.add(warning_msg)
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
            if warning_msg not in TableBackedFactory._shown_warnings:
                logger.warning(warning_msg)
                TableBackedFactory._shown_warnings.add(warning_msg)
            df = df[df[key_field].notna()]

        # Remove duplicates based on key field
        initial_rows = len(df)
        df = df.drop_duplicates(subset=[key_field], keep="last")
        if len(df) < initial_rows:
            duplicate_count = initial_rows - len(df)
            if duplicate_count > 0:
                warning_msg = f"Removed {duplicate_count} duplicate rows based on key field '{key_field}'"
                if warning_msg not in TableBackedFactory._shown_warnings:
                    logger.warning(warning_msg)
                    TableBackedFactory._shown_warnings.add(warning_msg)

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
                self._upsert_rows(df, table_name, key_field)

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

    def _upsert_rows(self, df: pd.DataFrame, table_name: str, key_field: str) -> None:
        """Upsert rows one by one when bulk import fails."""
        from sqlalchemy.exc import IntegrityError

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
                    update_sql = text(f'UPDATE {table_name} SET {set_clause} WHERE "{key_field}" = :{key_param_name}')

                    with self._db_engine.connect() as conn:
                        conn.execute(update_sql, param_map)
                        conn.commit()
                    updated += 1
                except Exception as update_err:
                    logger.warning(f"Failed to update row with key {key_value}: {update_err}")
                    skipped += 1

        logger.debug(f"Upsert complete: {inserted} inserted, {updated} updated, {skipped} skipped")

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

    def build_schema(self) -> GraphSchema:
        """Build and return the graph schema configuration.

        Subclasses must implement this to provide their specific schema.
        """
        raise NotImplementedError("Subclasses must implement build_schema()")
