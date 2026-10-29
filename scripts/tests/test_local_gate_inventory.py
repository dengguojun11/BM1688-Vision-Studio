"""Regression checks for the local/external acceptance gate inventory.

These tests deliberately assert that unavailable staging, accessibility and
hardware prerequisites remain non-passing. They do not start a server or
invoke Playwright, Alembic migrations, SSH or deployment commands.
"""

from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
import verify_staging_and_hil as verify


def _clear_gate_environment(monkeypatch) -> None:
    for name in (
        "LABELU_STAGING_BASE_URL",
        "LABELU_STAGING_USERNAME",
        "LABELU_STAGING_PASSWORD",
        "LABELU_PLATFORM_URL",
        "LABELU_AXE_CONFIG",
        "LABELU_BENCHMARK_TOKEN",
        "LABELU_GPU_HIL_WORK_DIR",
        "LABELU_BM1688_HIL_WORK_DIR",
        "LABELU_BM1684_HIL_WORK_DIR",
        "LABELU_RK1808_HIL_WORK_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_all_gate_template_keeps_external_gates_explicit(tmp_path: Path):
    template = verify.build_template("all", tmp_path / "evidence")
    gates = {entry["gate"] for entry in template["entries"]}

    assert {
        "migration_single_head",
        "migration_empty_repeat_upgrade",
        "frontend_external_gate_semantics",
        "project_ownership_dry_run",
        "release_evidence_integrity",
        "api_docs",
        "real_backend_playwright",
        "accessibility_axe",
        "workspace_api_performance",
        "provider_contract_local",
        "ota_contract_local",
        "hil_gpu",
        "hil_ota",
        "linux_release",
    } <= gates
    assert {entry["status"] for entry in template["entries"]} == {"NOT_STARTED"}
    assert all(entry["rollback_action"] for entry in template["entries"])


def test_missing_staging_credentials_or_tools_never_becomes_passed(
    monkeypatch, tmp_path: Path
):
    _clear_gate_environment(monkeypatch)
    monkeypatch.setattr(verify.shutil, "which", lambda name: None)
    template = verify.build_template("staging", tmp_path / "evidence")
    failures = verify.check_template(template, tmp_path / "evidence")

    assert len(failures) == 4
    assert {entry["gate"] for entry in failures} == {
        "api_docs",
        "real_backend_playwright",
        "accessibility_axe",
        "workspace_api_performance",
    }
    assert all(entry["status"] == "BLOCKED_EXTERNAL" for entry in failures)
    assert all(entry["status"] != "PASSED" for entry in template["entries"])


def test_frontend_playwright_and_accessibility_are_opt_in():
    e2e_path = (
        Path(__file__).resolve().parents[2]
        / "labelu"
        / "tests"
        / "e2e"
        / "test_7gai_platform_shell_frontend.py"
    )
    source = e2e_path.read_text(encoding="utf-8")

    # The suite must remain skipped without an explicitly configured URL;
    # source inspection avoids importing Playwright or launching a browser.
    assert 'PLATFORM_URL = os.getenv("LABELU_PLATFORM_URL")' in source
    assert "pytest.mark.skipif(" in source
    assert "not PLATFORM_URL" in source

    runbook = (
        e2e_path.parents[3] / "docs" / "系统方案文档" / "7GAI staging与HIL验收运行手册.md"
    )
    runbook_text = runbook.read_text(encoding="utf-8")
    assert "axe/键盘" in runbook_text
    assert "不能把 mock 前端或 HTTP 200 当成业务通过" in runbook_text
