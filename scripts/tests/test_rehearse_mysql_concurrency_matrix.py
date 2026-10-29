from __future__ import annotations

import json

import pytest

from scripts import rehearse_mysql_concurrency_matrix as matrix


def _report(workers: int) -> dict:
    return {
        "锁等待": [
            {"worker": f"worker-{i}", "wait_seconds": i / 10}
            for i in range(workers)
        ],
        "最终状态": {"status": "COMPLETED", "lease_owner": "worker-2"},
        "旧 token=1 写入": "拒绝（重启接管后的迟到写入）",
    }


def test_matrix_runs_required_levels_and_shapes_gate_evidence(monkeypatch):
    calls = []

    def fake_run(url, *, hold_seconds, concurrency):
        calls.append((url, hold_seconds, concurrency))
        return _report(concurrency)

    monkeypatch.setattr(matrix, "run_rehearsal", fake_run)
    monkeypatch.setattr(
        matrix,
        "validate_mysql_sandbox_url",
        lambda url, allow_write: {"database": "labelu_migration_sandbox_test"},
    )
    monkeypatch.setattr(
        matrix,
        "probe_mysql",
        lambda _url: {
            "数据库": {"database": "labelu_migration_sandbox_test"},
            "版本": "8.0.36",
        },
    )

    payload = matrix.run_matrix("mysql+pymysql://local/sandbox", hold_seconds=0.2)

    assert payload["status"] == "PASSED"
    assert [row["concurrent_workers"] for row in payload["matrix"]] == [2, 10, 20, 50]
    assert all(row["duplicate_claims"] == 0 for row in payload["matrix"])
    assert all(row["old_token_rejected"] is True for row in payload["matrix"])
    assert [call[2] for call in calls] == [2, 10, 20, 50]


def test_matrix_preserves_failed_level_and_fails_closed(monkeypatch):
    monkeypatch.setattr(
        matrix,
        "validate_mysql_sandbox_url",
        lambda url, allow_write: {"database": "labelu_migration_sandbox_test"},
    )
    monkeypatch.setattr(
        matrix,
        "probe_mysql",
        lambda _url: {
            "数据库": {"database": "labelu_migration_sandbox_test"},
            "版本": "8.0.36",
        },
    )

    def fake_run(_url, *, hold_seconds, concurrency):
        if concurrency == 20:
            raise RuntimeError("锁等待超时")
        return _report(concurrency)

    monkeypatch.setattr(matrix, "run_rehearsal", fake_run)
    payload = matrix.run_matrix("mysql+pymysql://local/sandbox")

    assert payload["status"] == "FAILED"
    failed = next(row for row in payload["matrix"] if row["concurrent_workers"] == 20)
    assert failed["status"] == "FAILED"
    assert "锁等待超时" in failed["error"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--url", "mysql+pymysql://local/sandbox"],
        ["--url", "mysql+pymysql://local/sandbox", "--模式", "演练"],
    ],
)
def test_cli_defaults_fail_closed_without_explicit_write_confirmation(argv, capsys):
    assert matrix.main(argv) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BLOCKED"
