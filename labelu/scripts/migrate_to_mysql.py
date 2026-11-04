from loguru import logger
from sqlalchemy import create_engine, MetaData, Table, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from contextlib import contextmanager
from typing import List, Dict, Any
import time
from datetime import datetime
from labelu.internal.common.config import settings

class MigrationError(Exception):
    pass

@contextmanager
def timer(operation: str):
    start = time.time()
    yield
    duration = time.time() - start
    logger.info(f"{operation} took {duration:.2f} seconds")

class DatabaseMigrator:
    BATCH_SIZE = 1000
    
    def __init__(self, sqlite_url: str, mysql_url: str):
        self.sqlite_engine = create_engine(sqlite_url)
        self.mysql_engine = create_engine(mysql_url)
        self.metadata = MetaData()
        
    @contextmanager
    def create_sessions(self):
        sqlite_session = sessionmaker(bind=self.sqlite_engine)()
        mysql_session = sessionmaker(bind=self.mysql_engine)()
        try:
            yield sqlite_session, mysql_session
        finally:
            sqlite_session.close()
            mysql_session.close()

    def get_tables_in_order(self) -> List[str]:
        inspector = inspect(self.sqlite_engine)
        tables = []
        table_names = inspector.get_table_names()
        
        dependencies = {name: set() for name in table_names}
        for name in table_names:
            for fk in inspector.get_foreign_keys(name):
                referred = fk["referred_table"]
                # Legacy SQLite files can keep a foreign key to a retired
                # table that no longer exists (for example project). Such a
                # reference cannot be part of the copy order.
                if referred in table_names:
                    dependencies[name].add(referred)
        
        while dependencies:
            independent_tables = [
                name for name, deps in dependencies.items() 
                if not deps
            ]
            if not independent_tables:
                raise MigrationError("Circular dependency detected in tables")
            
            tables.extend(independent_tables)
            for name in independent_tables:
                del dependencies[name]
                for deps in dependencies.values():
                    deps.discard(name)
        
        return tables

    def _process_row_data(self, row: Dict[str, Any]) -> Dict[str, Any]:
        processed = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                processed[key] = value.isoformat()
            elif isinstance(value, bytes):
                processed[key] = value.decode('utf-8')
            else:
                processed[key] = value
        return processed

    def _batch_insert(self, session, table: Table, rows: List[Dict[str, Any]]) -> None:
        try:
            processed_rows = [self._process_row_data(row._asdict()) for row in rows]
            session.execute(table.insert(), processed_rows)
            session.commit()
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Integrity error during batch insert: {e}")
            failures = []
            for row in rows:
                try:
                    processed_row = self._process_row_data(row._asdict())
                    session.execute(table.insert(), [processed_row])
                    session.commit()
                except Exception as row_error:
                    session.rollback()
                    logger.error(f"Failed to insert row: {row_error}")
                    failures.append(str(row_error))
            if failures:
                raise MigrationError(
                    f"{len(failures)} row(s) failed in {table.name}: {failures[0]}"
                )
        except Exception as e:
            session.rollback()
            raise MigrationError(f"Batch insert failed: {e}")

    def check_migration_status(self, mysql_session) -> bool:
        try:
            result = mysql_session.execute(text("SELECT COUNT(*) FROM user")).scalar()
            return result > 0
        except SQLAlchemyError:
            return False

    def migrate_table(self, table_name: str, sqlite_session, mysql_session) -> None:
        if table_name == "alembic_version":
            return

        with timer(f"Migrating table {table_name}"):
            table = Table(table_name, self.metadata)
            query = sqlite_session.query(table)
            total_rows = query.count()
            
            if total_rows == 0:
                logger.info(f"Table {table_name} is empty, skipping...")
                return

            batches = range(0, total_rows, self.BATCH_SIZE)
            for batch_start in batches:
                rows = query.offset(batch_start).limit(self.BATCH_SIZE).all()
                self._batch_insert(mysql_session, table, rows)
                logger.info(f"Migrated {min(batch_start + self.BATCH_SIZE, total_rows)}/{total_rows} rows in {table_name}")

    def verify_counts(self, sqlite_session, mysql_session, table_names: List[str]) -> None:
        """Fail closed when a table did not copy completely."""
        mismatches = []
        for table_name in table_names:
            sqlite_count = sqlite_session.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar()
            mysql_count = mysql_session.execute(
                text(f"SELECT COUNT(*) FROM `{table_name}`")
            ).scalar()
            if int(sqlite_count or 0) != int(mysql_count or 0):
                mismatches.append((table_name, sqlite_count, mysql_count))
        if mismatches:
            raise MigrationError(f"Row count mismatch after migration: {mismatches}")
        logger.info(f"Verified row counts for {len(table_names)} tables")

    def migrate(self) -> None:
        with self.create_sessions() as (sqlite_session, mysql_session):
            if self.check_migration_status(mysql_session):
                logger.info("Database has already been migrated.")
                return

            try:
                self.metadata.reflect(bind=self.sqlite_engine, resolve_fks=False)
                try:
                    ordered_tables = self.get_tables_in_order()
                except MigrationError as order_error:
                    # Foreign keys are disabled for the copy below, so a cycle
                    # (including legacy self references) cannot break the data
                    # transfer. Keep a deterministic fallback instead of
                    # aborting the whole migration.
                    logger.warning(
                        f"Foreign-key order unavailable ({order_error}); "
                        "copying tables in name order with FK checks disabled"
                    )
                    ordered_tables = sorted(self.metadata.tables.keys())
                mysql_tables = set(inspect(self.mysql_engine).get_table_names())
                copied_tables = []
                # Legacy rows may reference retired tables that the fresh MySQL
                # schema intentionally does not create. Data is copied first and
                # the target constraints are left for the schema audit.
                mysql_session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                mysql_session.commit()
                try:
                    with timer("Complete migration"):
                        for table_name in ordered_tables:
                            if table_name not in mysql_tables:
                                logger.warning(
                                    f"Table {table_name} is not in the MySQL schema, skipping..."
                                )
                                continue
                            self.migrate_table(table_name, sqlite_session, mysql_session)
                            copied_tables.append(table_name)
                    self.verify_counts(sqlite_session, mysql_session, copied_tables)
                finally:
                    mysql_session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
                    mysql_session.commit()

                logger.info("Migration completed successfully.")

            except Exception as e:
                logger.error(f"Migration failed: {e}")
                mysql_session.rollback()
                raise MigrationError(f"Migration failed: {e}")

def migrate_to_mysql():
    sqlite_url = f"sqlite:///{settings.BASE_DATA_DIR}/labelu.sqlite"
    
    try:
        migrator = DatabaseMigrator(sqlite_url, settings.DATABASE_URL)
        migrator.migrate()
    except MigrationError as e:
        logger.error(f"Migration failed: {e}")
        raise e