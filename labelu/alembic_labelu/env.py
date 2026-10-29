from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import inspect, text

from alembic import context

from labelu.internal.common.db import Base


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata


def _bootstrap_empty_database(connection) -> bool:
    """Create the current ORM baseline for a truly empty database.

    The original ``init_db`` revision predates Alembic adoption and is an
    intentionally empty marker.  Running the historical chain against a
    brand-new database therefore fails as soon as ``e76c...`` tries to alter
    the missing ``task`` table.  Empty databases have no data to migrate, so
    creating the current metadata and stamping the single graph head is both
    deterministic and equivalent to the application's existing bootstrap
    path.  Databases with an Alembic version row are never treated as empty.
    """
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    version_table = context.config.get_main_option("version_table") or "alembic_version"
    application_tables = tables - {version_table}
    if application_tables:
        return False

    if version_table in tables:
        version_rows = connection.execute(
            text(f"SELECT version_num FROM {version_table}")
        ).fetchall()
        if version_rows:
            return False

    # Importing the package registers every declarative model on Base.metadata.
    import labelu.internal.domain.models  # noqa: F401

    from labelu.internal.common.schema_governance import apply_schema_comment_defaults
    from labelu.internal.common.phase_one_schema import create_phase_one_tables

    apply_schema_comment_defaults(Base.metadata, include_governance=True)
    create_phase_one_tables(connection, Base.metadata)

    # Resolve the graph head through Alembic instead of hard-coding a revision.
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(context.config).get_current_head()
    if not head:
        raise RuntimeError("Alembic migration graph has no single head")
    if version_table not in inspect(connection).get_table_names():
        connection.execute(
            text(
                f"CREATE TABLE {version_table} "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
    connection.execute(
        text(f"INSERT INTO {version_table} (version_num) VALUES (:revision)"),
        {"revision": head},
    )
    return True

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if not url:
        from labelu.internal.common.config import settings

        url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    from labelu.internal.common.config import settings

    section = dict(config.get_section(config.config_ini_section, {}) or {})
    ini_url = (section.get("sqlalchemy.url") or "").strip()
    if not ini_url:
        section["sqlalchemy.url"] = settings.DATABASE_URL

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Keep the connection in an explicit transaction.  SQLAlchemy 2.x no
    # longer autocommits the ``stamp``/version-row writes made by Alembic.
    with connectable.begin() as connection:
        if _bootstrap_empty_database(connection):
            return
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=not str(connection.engine.url).startswith('mysql'))

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
