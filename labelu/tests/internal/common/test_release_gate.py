from pathlib import Path

from labelu.internal.common.release_gate import (
    backup_restore_simulation,
    migration_heads,
    run_release_gate,
)


def test_migration_head_is_single():
    config_path = Path(__file__).resolve().parents[3] / "alembic_labelu" / "alembic.ini"
    heads = migration_heads(config_path)
    assert len(heads) == 1


def test_backup_restore_simulation_round_trip():
    assert backup_restore_simulation()


def test_release_gate_passes_for_development_defaults(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "development")
    report = run_release_gate(
        config_path=Path(__file__).resolve().parents[3] / "alembic_labelu" / "alembic.ini",
        jwt_from_env=False,
    )
    assert report.valid
    assert report.backup_restore_ok
    assert len(report.migration_heads) == 1
