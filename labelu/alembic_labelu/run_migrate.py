import os
from alembic.command import stamp, upgrade

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from labelu.internal.common.config import settings


def _bootstrap_empty_database(config: Config, db_url: str) -> bool:
    engine = create_engine(db_url)
    try:
        existing_tables = set(inspect(engine).get_table_names())
        application_tables = existing_tables - {"alembic_version"}
        if application_tables:
            return False

        import labelu.internal.domain.models  # noqa: F401
        from labelu.internal.common.db import Base
        from labelu.internal.common.schema_governance import (
            apply_schema_comment_defaults,
        )
        from labelu.internal.common.phase_one_schema import create_phase_one_tables

        apply_schema_comment_defaults(Base.metadata, include_governance=True)
        create_phase_one_tables(engine, Base.metadata)
    finally:
        engine.dispose()

    stamp(config, "head")
    _assert_database_matches_code_head(config, db_url)
    return True


def _assert_database_matches_code_head(config: Config, db_url: str) -> None:
    """Fail closed when the database revision does not match the code graph."""

    script_heads = tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            current_heads = tuple(
                sorted(MigrationContext.configure(connection).get_current_heads())
            )
    finally:
        engine.dispose()

    if current_heads != script_heads:
        revision_history = tuple(
            revision.revision
            for revision in ScriptDirectory.from_config(config).walk_revisions()
        )
        raise RuntimeError(
            "数据库 Alembic head 与当前代码不一致："
            f"current={current_heads or ('<none>',)} "
            f"code={script_heads or ('<none>',)} "
            f"history={revision_history}；"
            "禁止继续启动，请先完成迁移或回滚到匹配版本。"
        )


def run_db_migrations():
    skip_migrations = (
        os.environ.get("LABELU_SKIP_MIGRATIONS") or ""
    ).strip().lower() in {"1", "true", "yes"}
    if skip_migrations:
        environment = (
            (getattr(settings, "APP_ENV", "development") or "development")
            .strip()
            .lower()
        )
        if environment in {"prod", "production"}:
            raise RuntimeError(
                "生产环境禁止设置 LABELU_SKIP_MIGRATIONS；"
                "必须先执行数据库迁移并验证 Alembic head 对齐。"
            )
        return
    migrations_dir = os.path.dirname(os.path.realpath(__file__))
    # assumes the alembic.ini is also contained in the current directory of this file
    config_file = os.path.join(migrations_dir, "alembic.ini")

    config = Config(file_=config_file)
    config.set_main_option("script_location", migrations_dir)
    # configparser 会把 % 当插值；密码中的 %40 需写成 %%40
    db_url = (settings.DATABASE_URL or "").replace("%", "%%")
    config.set_main_option("sqlalchemy.url", db_url)

    from labelu.internal.common.annotation_taxonomy_seed import (
        seed_default_global_tag_dimensions,
    )

    if _bootstrap_empty_database(config, settings.DATABASE_URL):
        seed_default_global_tag_dimensions(settings.DATABASE_URL)
        return

    upgrade(config, "head")
    _assert_database_matches_code_head(config, settings.DATABASE_URL)
    seed_default_global_tag_dimensions(settings.DATABASE_URL)


if __name__ == "__main__":
    run_db_migrations()
