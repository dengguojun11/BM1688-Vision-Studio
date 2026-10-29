from sqlalchemy import create_engine, inspect, text

from labelu.alembic_labelu import run_migrate


def test_run_db_migrations_bootstraps_empty_database(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh-labelu.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.delenv("LABELU_SKIP_MIGRATIONS", raising=False)
    monkeypatch.setattr(run_migrate.settings, "DATABASE_URL", database_url)

    run_migrate.run_db_migrations()

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()

    assert {"user", "task", "task_sample", "alembic_version"} <= tables
    assert revision


def test_production_cannot_skip_database_migrations(monkeypatch):
    monkeypatch.setenv("LABELU_SKIP_MIGRATIONS", "true")
    monkeypatch.setattr(run_migrate.settings, "APP_ENV", "production")

    try:
        run_migrate.run_db_migrations()
    except RuntimeError as exc:
        assert "生产环境禁止设置 LABELU_SKIP_MIGRATIONS" in str(exc)
    else:
        raise AssertionError("production migration skip must fail closed")


def test_run_db_migrations_rejects_revision_drift(tmp_path, monkeypatch):
    db_path = tmp_path / "drift-labelu.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.delenv("LABELU_SKIP_MIGRATIONS", raising=False)
    monkeypatch.setattr(run_migrate.settings, "DATABASE_URL", database_url)

    run_migrate.run_db_migrations()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = 'p2q3r4s5t6u7'")
            )
    finally:
        engine.dispose()

    monkeypatch.setattr(run_migrate, "upgrade", lambda *_args, **_kwargs: None)
    try:
        run_migrate.run_db_migrations()
    except RuntimeError as exc:
        assert "数据库 Alembic head 与当前代码不一致" in str(exc)
        assert "q3r4s5t6u7v8" in str(exc)
    else:
        raise AssertionError("revision drift must fail closed")
