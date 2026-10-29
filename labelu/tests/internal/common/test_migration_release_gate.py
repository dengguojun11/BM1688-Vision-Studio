"""迁移发布闸门只读与 fail-closed 契约。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labelu.internal.common.migration_release_gate import (
    run_migration_release_gate,
    validate_sandbox_url,
)


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "alembic_labelu" / "alembic.ini"
VERSIONS = ROOT / "alembic_labelu" / "versions"


def _write_evidence(root: Path) -> tuple[Path, Path, Path]:
    provenance = {
        "evidence_id": "test-evidence-20260920",
        "generated_at": "2026-09-20T12:00:00+08:00",
        "script_version": "test-v1",
        "git_revision": "abcdef1234567",
        "database_name": "labelu_migration_sandbox_test",
        "mysql_version": "8.0.36",
    }
    backup = root / "backup.json"
    backup.write_text(
        json.dumps(
            {
                "status": "PASSED",
                "evidence_meta": provenance,
                "database_type": "mysql",
                "backup_path": "/evidence/backup.sql.gz",
                "backup_sha256": "a" * 64,
                "upgrade_result": "PASSED",
                "repeat_upgrade_result": "PASSED",
                "restore_target": "mysql://isolated-restore",
                "restore_result": "PASSED",
            }
        ),
        encoding="utf-8",
    )
    lock = root / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "status": "PASSED",
                "evidence_meta": provenance,
                "database_type": "mysql",
                "concurrent_workers": 20,
                "max_lock_wait_seconds": 0.8,
                "lock_wait_timeout_seconds": 5,
                "duplicate_claims": 0,
                "old_token_rejected": True,
            }
        ),
        encoding="utf-8",
    )
    matrix = root / "concurrency.json"
    matrix.write_text(
        json.dumps(
            {
                "status": "PASSED",
                "evidence_meta": provenance,
                "database_type": "mysql",
                "matrix": [
                    {
                        "status": "PASSED",
                        "concurrent_workers": workers,
                        "max_lock_wait_seconds": 0.8,
                        "duplicate_claims": 0,
                        "old_token_rejected": True,
                    }
                    for workers in (2, 10, 20, 50)
                ],
            }
        ),
        encoding="utf-8",
    )
    return backup, lock, matrix


def test_current_workspace_blocks_eval_candidate_until_formal_migration(tmp_path: Path):
    backup, lock, matrix = _write_evidence(tmp_path)
    report = run_migration_release_gate(
        config_path=CONFIG,
        versions_dir=VERSIONS,
        candidate_dir=ROOT / "docs" / "7gai_phase_one_migration_candidates",
        database_url="sqlite:///sandbox.sqlite",
        backup_evidence=backup,
        lock_wait_evidence=lock,
        concurrency_evidence=matrix,
    )
    assert report.valid is False
    assert report.checks["唯一 Alembic head"].passed is True
    assert report.checks["ORM字段索引闭包"].passed is True
    assert report.checks["调度租约候选迁移已合入正式 versions"].passed is True
    assert "z4a5b6c7d8e9" in report.checks["调度租约候选迁移已合入正式 versions"].detail
    assert report.checks["评估代次候选迁移已合入正式 versions"].passed is False


def test_gate_passes_only_with_empty_candidate_dir_and_complete_evidence(tmp_path: Path):
    backup, lock, matrix = _write_evidence(tmp_path)
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    report = run_migration_release_gate(
        config_path=CONFIG,
        versions_dir=VERSIONS,
        candidate_dir=candidate_dir,
        database_url="sqlite:///sandbox.sqlite",
        backup_evidence=backup,
        lock_wait_evidence=lock,
        concurrency_evidence=matrix,
    )
    assert report.valid is True
    assert all(check.passed for check in report.checks.values())


def test_gate_rejects_an_unmarked_candidate(tmp_path: Path):
    backup, lock, matrix = _write_evidence(tmp_path)
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    (candidate_dir / "gai_p1_08_add_dispatch_leases.py").write_text(
        "revision = 'gai_p1_08'\n", encoding="utf-8"
    )
    report = run_migration_release_gate(
        config_path=CONFIG,
        versions_dir=VERSIONS,
        candidate_dir=candidate_dir,
        database_url="sqlite:///sandbox.sqlite",
        backup_evidence=backup,
        lock_wait_evidence=lock,
        concurrency_evidence=matrix,
    )
    assert report.valid is False
    assert report.checks["调度租约候选迁移已合入正式 versions"].passed is False


def test_missing_backup_or_lock_evidence_fails_closed(tmp_path: Path):
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    report = run_migration_release_gate(
        config_path=CONFIG,
        versions_dir=VERSIONS,
        candidate_dir=candidate_dir,
        database_url="sqlite:///sandbox.sqlite",
    )
    assert report.valid is False
    assert report.checks["备份恢复证据"].passed is False
    assert report.checks["MySQL锁等待证据"].passed is False
    assert report.checks["2/10/20/50并发矩阵"].passed is False


def test_evidence_without_provenance_fails_closed(tmp_path: Path):
    evidence = tmp_path / "matrix.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "PASSED",
                "database_type": "mysql",
                "matrix": [
                    {
                        "status": "PASSED",
                        "concurrent_workers": workers,
                        "max_lock_wait_seconds": 0.8,
                        "duplicate_claims": 0,
                        "old_token_rejected": True,
                    }
                    for workers in (2, 10, 20, 50)
                ],
            }
        ),
        encoding="utf-8",
    )
    check = __import__(
        "labelu.internal.common.migration_release_gate",
        fromlist=["validate_concurrency_matrix_evidence"],
    ).validate_concurrency_matrix_evidence(evidence)
    assert check.passed is False
    assert "evidence_meta" in check.detail


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("database_name", "labelu", "隔离沙箱"),
        ("git_revision", "unknown", "可追溯提交摘要"),
        ("git_revision", "workspace", "可追溯提交摘要"),
    ],
)
def test_evidence_provenance_rejects_unsafe_source_metadata(
    tmp_path: Path, field: str, value: str, expected: str
):
    _, _, matrix = _write_evidence(tmp_path)
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    payload["evidence_meta"][field] = value
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    check = __import__(
        "labelu.internal.common.migration_release_gate",
        fromlist=["validate_concurrency_matrix_evidence"],
    ).validate_concurrency_matrix_evidence(matrix)
    assert check.passed is False
    assert expected in check.detail


def test_gate_rejects_incomplete_concurrency_matrix(tmp_path: Path):
    backup, lock, matrix = _write_evidence(tmp_path)
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    payload["matrix"] = payload["matrix"][:3]
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    report = run_migration_release_gate(
        config_path=CONFIG,
        versions_dir=VERSIONS,
        candidate_dir=tmp_path / "candidate",
        database_url="sqlite:///sandbox.sqlite",
        backup_evidence=backup,
        lock_wait_evidence=lock,
        concurrency_evidence=matrix,
    )
    assert report.valid is False
    assert report.checks["2/10/20/50并发矩阵"].passed is False
    assert "50" in report.checks["2/10/20/50并发矩阵"].detail


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://u:p@113.141.72.175/labelu",
        "mysql+pymysql://u:p@10.0.0.9/labelu_migration_sandbox_1",
        "mysql+pymysql://u:p@127.0.0.1/labelu",
        "postgresql://u:p@127.0.0.1/labelu_migration_sandbox_1",
    ],
)
def test_dangerous_or_unsupported_database_url_is_rejected(url: str):
    with pytest.raises(RuntimeError):
        validate_sandbox_url(url)


def test_local_mysql_sandbox_url_is_only_parsed_not_connected():
    parsed = validate_sandbox_url(
        "mysql+pymysql://u:p@127.0.0.1/labelu_migration_sandbox_20260920"
    )
    assert parsed.host == "127.0.0.1"
    assert parsed.database == "labelu_migration_sandbox_20260920"
