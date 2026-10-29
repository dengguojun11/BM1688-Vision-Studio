"""迁移部署脚本必须先通过只读发布闸门。"""

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "ops" / "migrate_deploy.sh"
).read_text(encoding="utf-8")


def test_migration_script_invokes_read_only_gate_before_alembic():
    gate = SCRIPT.index("labelu_migrate_release_gate")
    alembic = SCRIPT.index('alembic upgrade head')
    assert gate < alembic
    assert "LABELU_MIGRATION_SANDBOX_URL" in SCRIPT
    assert "LABELU_MIGRATION_BACKUP_EVIDENCE" in SCRIPT
    assert "LABELU_MIGRATION_LOCK_WAIT_EVIDENCE" in SCRIPT
    assert "禁止连接 DATABASE_URL 或执行 Alembic DDL" in SCRIPT


def test_migration_script_does_not_offer_an_environment_bypass():
    assert "LABELU_SKIP_MIGRATION_RELEASE_GATE" not in SCRIPT
    assert "LABELU_MIGRATION_RELEASE_GATE=0" not in SCRIPT
