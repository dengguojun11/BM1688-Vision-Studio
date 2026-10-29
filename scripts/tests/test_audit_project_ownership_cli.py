from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import audit_project_ownership as cli


class _FakeSession:
    closed = False

    def close(self):
        self.closed = True


def test_dry_run_defaults_to_stdout(monkeypatch, capsys):
    session = _FakeSession()
    report = {
        "schema_version": "7gai.project_ownership_backfill_dry_run.v1",
        "read_only": True,
        "write_count": 0,
        "entities": [],
    }
    monkeypatch.setattr(cli, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        cli, "project_ownership_backfill_dry_run", lambda db, row_limit: report
    )

    assert cli.main(["--dry-run-backfill"]) == 0

    assert json.loads(capsys.readouterr().out) == report
    assert session.closed is True


def test_output_path_must_be_json_below_explicit_tmp_subdirectory(tmp_path: Path):
    allowed = cli._safe_output_path(".tmp/project-ownership/report.json")
    assert allowed.parts[-3:] == (".tmp", "project-ownership", "report.json")

    with pytest.raises(ValueError, match=".tmp"):
        cli._safe_output_path(str(tmp_path / "outside.json"))
    with pytest.raises(ValueError, match="子目录"):
        cli._safe_output_path(".tmp/report.json")
    with pytest.raises(ValueError, match="json"):
        cli._safe_output_path(".tmp/project-ownership/report.txt")
