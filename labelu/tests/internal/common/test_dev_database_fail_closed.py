from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
import click

from labelu.internal.common.config import Settings, settings
from labelu.main import _enforce_dev_database_safety, app, lifespan


@pytest.fixture
def restore_dev_database_settings():
    snapshot = {
        "APP_ENV": settings.APP_ENV,
        "DATABASE_URL": settings.DATABASE_URL,
        "LABELU_ALLOW_DEV_MYSQL": settings.LABELU_ALLOW_DEV_MYSQL,
    }
    yield
    for key, value in snapshot.items():
        object.__setattr__(settings, key, value)


def test_development_rejects_inherited_mysql_by_default(restore_dev_database_settings):
    object.__setattr__(settings, "APP_ENV", "development")
    object.__setattr__(settings, "DATABASE_URL", "mysql+pymysql://localhost/labelu")
    object.__setattr__(settings, "LABELU_ALLOW_DEV_MYSQL", False)

    with pytest.raises(click.ClickException, match="LABELU_ALLOW_DEV_MYSQL"):
        _enforce_dev_database_safety()


def test_development_allows_mysql_only_with_explicit_switch(restore_dev_database_settings):
    object.__setattr__(settings, "APP_ENV", "development")
    object.__setattr__(settings, "DATABASE_URL", "mysql+pymysql://localhost/labelu")
    object.__setattr__(settings, "LABELU_ALLOW_DEV_MYSQL", True)

    _enforce_dev_database_safety()


def test_production_is_checked_by_production_security_not_dev_guard(restore_dev_database_settings):
    object.__setattr__(settings, "APP_ENV", "production")
    object.__setattr__(settings, "DATABASE_URL", "mysql+pymysql://localhost/labelu")
    object.__setattr__(settings, "LABELU_ALLOW_DEV_MYSQL", False)

    _enforce_dev_database_safety()


def test_asgi_lifespan_rejects_mysql_before_migration(monkeypatch, restore_dev_database_settings):
    object.__setattr__(settings, "APP_ENV", "development")
    object.__setattr__(settings, "DATABASE_URL", "mysql+pymysql://localhost/shared")
    object.__setattr__(settings, "LABELU_ALLOW_DEV_MYSQL", False)
    migration_called = False

    def migration_probe():
        nonlocal migration_called
        migration_called = True

    monkeypatch.setattr("labelu.main.run_db_migrations", migration_probe)

    async def start():
        async with lifespan(app):
            pass

    with pytest.raises(click.ClickException, match="LABELU_ALLOW_DEV_MYSQL"):
        asyncio.run(start())
    assert migration_called is False


def test_explicit_dev_database_url_overrides_inherited_url(tmp_path):
    local = Settings(
        APP_ENV="development",
        BASE_DATA_DIR=str(tmp_path),
        DATABASE_URL="mysql+pymysql://localhost/shared",
        LABELU_DEV_DATABASE_URL="sqlite:///./dev.sqlite",
    )

    assert local.DATABASE_URL == "sqlite:///./dev.sqlite"


def test_asgi_lifespan_schedules_agent_recovery_when_yolo_and_watchdogs_are_disabled(
    monkeypatch, restore_dev_database_settings
):
    """The resident Agent control plane must start independently of watchdogs."""

    main_module = importlib.import_module("labelu.main")
    production_security = importlib.import_module(
        "labelu.internal.common.production_security"
    )
    provider_bootstrap = importlib.import_module(
        "labelu.internal.application.service.provider_runtime_bootstrap"
    )
    edge_registry = importlib.import_module(
        "labelu.internal.application.service.edge_device_registry"
    )
    auto_training_watchdog = importlib.import_module(
        "labelu.internal.application.service.auto_training_watchdog"
    )
    auto_preannotate_watchdog = importlib.import_module(
        "labelu.internal.application.service.auto_sample_preannotate"
    )

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite:///./lifespan-agent.db")
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", False)
    monkeypatch.setenv("LABELU_DISABLE_BACKGROUND_WATCHDOGS", "true")
    calls = []

    class _Session:
        def close(self):
            calls.append("session_close")

    monkeypatch.setattr(main_module, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(main_module, "run_db_migrations", lambda: calls.append("migrate"))
    monkeypatch.setattr(main_module, "ensure_default_admin", lambda: calls.append("admin"))
    monkeypatch.setattr(
        main_module, "ensure_open_system_user", lambda _db: calls.append("system_user")
    )
    monkeypatch.setattr(
        production_security,
        "enforce_production_security",
        lambda **_kwargs: calls.append("security"),
    )
    monkeypatch.setattr(
        provider_bootstrap,
        "register_configured_provider_adapters",
        lambda: SimpleNamespace(enabled=False, registered=(), skipped=()),
    )
    monkeypatch.setattr(
        edge_registry,
        "schedule_system_environment_checks",
        lambda: calls.append("edge_recovery"),
    )
    monkeypatch.setattr(auto_training_watchdog, "schedule_auto_training_watchdog", lambda: None)
    monkeypatch.setattr(auto_preannotate_watchdog, "schedule_auto_preannotate_watchdog", lambda: None)

    async def start_and_stop():
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(start_and_stop())

    assert "edge_recovery" in calls
    assert "migrate" in calls
    assert "session_close" in calls
