import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "verify_staging_and_hil.py"
sys.path.insert(0, str(SCRIPT.parent))
import verify_staging_and_hil as verify  # noqa: E402


def run_script(*args, env=None):
    merged = os.environ.copy()
    merged.pop("LABELU_STAGING_BASE_URL", None)
    merged.pop("LABELU_STAGING_USERNAME", None)
    merged.pop("LABELU_STAGING_PASSWORD", None)
    merged.pop("LABELU_PLATFORM_URL", None)
    merged.pop("LABELU_AXE_CONFIG", None)
    for name in (
        "LABELU_GPU_HIL_WORK_DIR",
        "LABELU_BM1688_HIL_WORK_DIR",
        "LABELU_BM1684_HIL_WORK_DIR",
        "LABELU_RK1808_HIL_WORK_DIR",
    ):
        merged.pop(name, None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=merged
    )


def test_windows_linux_release_is_explicitly_not_run(tmp_path):
    result = run_script(
        "--environment",
        "release",
        "--evidence-root",
        str(tmp_path / "evidence"),
        "--check",
    )
    if sys.platform.startswith("win"):
        assert result.returncode == 0
        assert "NOT_RUN_LOCAL_WINDOWS" in result.stdout
    else:
        assert result.returncode == 0
        assert "NOT_STARTED" in result.stdout


def test_staging_missing_prerequisites_is_not_passed(tmp_path):
    result = run_script(
        "--environment",
        "staging",
        "--evidence-root",
        str(tmp_path / "evidence"),
        "--check",
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["blocking"] == 4
    assert all(item["status"] == "BLOCKED_EXTERNAL" for item in payload["entries"])


def test_hil_missing_workspace_has_explicit_unavailable_status(tmp_path):
    result = run_script(
        "--environment", "hil", "--evidence-root", str(tmp_path / "evidence"), "--check"
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    statuses = {item["gate"]: item["status"] for item in payload["entries"]}
    assert statuses["hil_bm1688"] == "SKIPPED_UNAVAILABLE"
    assert statuses["hil_bm1684"] == "SKIPPED_UNAVAILABLE"
    assert statuses["hil_rk1808"] == "SKIPPED_UNAVAILABLE"
    assert statuses["hil_gpu"] == "BLOCKED_EXTERNAL"
    assert statuses["hil_ota"] == "BLOCKED_EXTERNAL"


def test_emit_template_contains_required_fields_and_safe_root(tmp_path):
    evidence_root = tmp_path / "evidence"
    result = run_script(
        "--environment", "all", "--evidence-root", str(evidence_root), "--emit-template"
    )
    assert result.returncode == 0
    output = next(evidence_root.glob("acceptance-template-*.json"))
    payload = json.loads(output.read_text(encoding="utf-8"))
    required = {
        "evidence_id",
        "gate",
        "owner",
        "environment",
        "command",
        "started_at",
        "finished_at",
        "result",
        "artifact_path",
        "artifact_sha256",
        "rollback_action",
        "git_revision",
        "worktree_fingerprint_sha256",
        "worktree_fingerprint_artifact_path",
        "worktree_fingerprint_artifact_sha256",
        "notes",
        "status",
    }
    assert required <= set(payload["entries"][0])
    assert set(payload["status_values"]) >= {
        "PASSED",
        "NOT_RUN_LOCAL_WINDOWS",
        "SKIPPED_UNAVAILABLE",
    }


def test_workspace_root_is_rejected(tmp_path):
    result = run_script("--environment", "staging", "--evidence-root", str(Path.cwd()))
    assert result.returncode != 0
    assert "evidence-root" in result.stderr


def test_staging_requires_playwright_tool_when_credentials_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("LABELU_STAGING_BASE_URL", "https://staging.invalid")
    monkeypatch.setenv("LABELU_STAGING_USERNAME", "qa")
    monkeypatch.setenv("LABELU_STAGING_PASSWORD", "secret")
    monkeypatch.setattr(
        verify.shutil,
        "which",
        lambda name: None if name == "npx" else "/usr/bin/" + name,
    )
    template = verify.build_template("staging", tmp_path / "evidence")
    failures = verify.check_template(template, tmp_path / "evidence")
    assert len(failures) == 3
    assert all(item["status"] == "BLOCKED_EXTERNAL" for item in failures)
    api = next(item for item in template["entries"] if item["gate"] == "api_docs")
    assert api["status"] == "NOT_STARTED"


def test_staging_requires_explicit_platform_and_axe_configuration(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LABELU_STAGING_BASE_URL", "https://staging.invalid")
    monkeypatch.setenv("LABELU_STAGING_USERNAME", "qa")
    monkeypatch.setenv("LABELU_STAGING_PASSWORD", "secret")
    monkeypatch.delenv("LABELU_PLATFORM_URL", raising=False)
    monkeypatch.delenv("LABELU_AXE_CONFIG", raising=False)
    monkeypatch.setattr(verify.shutil, "which", lambda name: "/usr/bin/" + name)
    template = verify.build_template("staging", tmp_path / "evidence")
    failures = verify.check_template(template, tmp_path / "evidence")
    statuses = {item["gate"]: item["status"] for item in template["entries"]}
    assert statuses == {
        "api_docs": "NOT_STARTED",
        "real_backend_playwright": "BLOCKED_EXTERNAL",
        "accessibility_axe": "BLOCKED_EXTERNAL",
        "workspace_api_performance": "BLOCKED_EXTERNAL",
    }
    assert len(failures) == 3


def test_local_gates_are_not_run_by_default(monkeypatch, tmp_path):
    called = []

    def unexpected(*args, **kwargs):
        called.append(args)
        raise AssertionError("local gate was unexpectedly executed")

    monkeypatch.setattr(verify, "run_local_gates", unexpected)
    assert (
        verify.main(
            ["--environment", "local", "--evidence-root", str(tmp_path / "evidence")]
        )
        == 0
    )
    assert called == []


def test_run_local_reports_failure_and_never_runs_external_gates(monkeypatch, tmp_path):
    calls = []

    class Completed:
        returncode = 7
        stdout = "local gate failed\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["git", "rev-parse"]:

            class Revision:
                returncode = 0
                stdout = "abc123\n"
                stderr = ""

            return Revision()
        return Completed()

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    evidence_root = tmp_path / "evidence"
    template = verify.build_template("all", evidence_root)
    failures = verify.check_template(template, evidence_root)
    executed, path = verify.run_local_gates(template, evidence_root)
    failures.extend(item for item in executed if item["status"] != "PASSED")

    assert len(executed) == 8
    assert all(item["status"] == "FAILED" for item in executed)
    assert all(item["git_revision"] == "abc123" for item in executed)
    assert all("staging_acceptance" not in " ".join(call) for call in calls)
    assert path.is_file()
    payload = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert payload["git_revision"] == "abc123"
    assert all(item["result"].startswith("exit_code=7") for item in payload["entries"])
    assert all(Path(item["artifact_path"]).is_file() for item in payload["entries"])
    assert all(len(item["artifact_sha256"]) == 64 for item in payload["entries"])
    assert Path(payload["worktree_fingerprint_artifact_path"]).is_file()
    assert len(payload["worktree_fingerprint_artifact_sha256"]) == 64
    assert any(item["status"] == "BLOCKED_EXTERNAL" for item in failures)


def test_backup_manifest_requires_restore_and_recovery_fields():
    failures = verify.validate_backup_manifest(
        {
            "git_revision": "abc1234",
            "alembic_heads": ["head"],
            "database_type": "sqlite",
            "backup_sha256": "0" * 64,
            "upgrade_result": "passed",
            "repeat_upgrade_result": "passed",
            "restore_result": "passed",
            "restore_duration_seconds": 12.4,
            "rpo_seconds": 60,
            "rto_seconds": 120,
            "ownership_audit": {"status": "isolated"},
        }
    )
    assert failures == []


def test_staging_probe_artifact_is_validated_by_the_release_gate():
    payload = {
        "product": "7GAI Evolution Platform",
        "passed": True,
        "probes": [
            {
                "name": name,
                "status": 200,
                "passed": True,
                "required": True,
                "detail": "",
                "response_kind": "json",
            }
            for name in (
                "liveness",
                "readiness",
                "version",
                "swagger docs",
                "openapi json",
                "canonical OpenAPI paths",
                "unauthenticated API rejected",
                "一期已禁用边端作业写入",
            )
        ],
    }
    assert verify.validate_probe_evidence(payload) == []


def test_staging_probe_artifact_accepts_default_retired_write_skip():
    payload = {
        "product": "7GAI Evolution Platform",
        "passed": True,
        "probes": [
            {
                "name": name,
                "status": 200,
                "passed": True,
                "required": True,
                "detail": "",
                "response_kind": "json",
            }
            for name in (
                "liveness",
                "readiness",
                "version",
                "swagger docs",
                "openapi json",
                "canonical OpenAPI paths",
                "unauthenticated API rejected",
            )
        ]
        + [
            {
                "name": "退休写接口探针",
                "status": None,
                "passed": True,
                "required": False,
                "detail": "默认跳过",
                "response_kind": "",
            }
        ],
    }
    assert verify.validate_probe_evidence(payload) == []


def test_staging_probe_artifact_rejects_failed_required_probe():
    payload = {
        "product": "7GAI Evolution Platform",
        "passed": False,
        "probes": [
            {
                "name": "liveness",
                "status": 503,
                "passed": False,
                "required": True,
                "detail": "service unavailable",
            }
        ],
    }
    failures = verify.validate_probe_evidence(payload)
    assert any(item["evidence_id"] == "liveness" for item in failures)
    assert any(item["evidence_id"] == "PROBE-ARTIFACT" for item in failures)


def test_staging_probe_artifact_rejects_missing_json_kind_and_retired_write_probe():
    payload = {
        "product": "7GAI Evolution Platform",
        "passed": True,
        "probes": [
            {
                "name": name,
                "status": 200,
                "passed": True,
                "required": True,
                "detail": "",
                "response_kind": "json" if name != "liveness" else "text",
            }
            for name in (
                "liveness",
                "readiness",
                "version",
                "swagger docs",
                "openapi json",
                "canonical OpenAPI paths",
                "unauthenticated API rejected",
            )
        ],
    }
    failures = verify.validate_probe_evidence(payload)
    assert any(item["evidence_id"] == "liveness" for item in failures)
    assert any(item["evidence_id"] == "PROBE-ARTIFACT" for item in failures)


def test_backup_manifest_rejects_placeholder_digest_and_missing_restore(tmp_path):
    manifest = tmp_path / "backup.json"
    manifest.write_text(
        '{"git_revision":"abc1234","backup_sha256":"placeholder"}',
        encoding="utf-8",
    )
    result = run_script(
        "--evidence-root",
        str(tmp_path),
        "--validate-backup",
        manifest.name,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["validation"] == "FAILED"
    assert any(item["field"] == "backup_sha256" for item in payload["failures"])
