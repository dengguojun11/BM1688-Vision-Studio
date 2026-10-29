"""Release-evidence integrity tests without staging, HIL or service access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
import verify_staging_and_hil as verify  # noqa: E402


def _write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> tuple[str, str]:
    payload = {
        "schema_version": 1,
        "git_revision": "a" * 40,
        "dirty": False,
        "status_porcelain_sha256": hashlib.sha256(b"").hexdigest(),
        "files": [],
    }
    fingerprint = hashlib.sha256(verify._canonical_json_bytes(payload)).hexdigest()
    payload["fingerprint_sha256"] = fingerprint
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return fingerprint, hashlib.sha256(path.read_bytes()).hexdigest()


def _passed_entry(root: Path, environment: str = "staging_linux") -> dict:
    artifact = root / "staging" / "api" / "result.log"
    artifact_sha256 = _write(artifact, "real external evidence\n")
    fingerprint_path = root / "staging" / "worktree.json"
    fingerprint, fingerprint_artifact_sha256 = _fingerprint(fingerprint_path)
    return {
        "evidence_id": "QA-06-API-001",
        "gate": "api_docs",
        "owner": "QA/MLOps",
        "environment": environment,
        "command": "python scripts/staging_acceptance.py",
        "started_at": "2026-09-03T01:00:00+00:00",
        "finished_at": "2026-09-03T01:01:00+00:00",
        "result": "all probes and schema checks passed",
        "artifact_path": str(artifact),
        "artifact_sha256": artifact_sha256,
        "rollback_action": "stop rollout",
        "git_revision": "a" * 40,
        "worktree_fingerprint_sha256": fingerprint,
        "worktree_fingerprint_artifact_path": str(fingerprint_path),
        "worktree_fingerprint_artifact_sha256": fingerprint_artifact_sha256,
        "notes": "",
        "status": "PASSED",
    }


def test_worktree_fingerprint_is_deterministic_and_content_sensitive(
    monkeypatch, tmp_path: Path
):
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    git_outputs = {
        ("rev-parse", "HEAD"): b"b" * 40 + b"\n",
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ): b" M tracked.txt\0",
        ("diff", "--name-only", "-z", "HEAD", "--"): b"tracked.txt\0",
        ("ls-files", "--others", "--exclude-standard", "-z"): b"",
    }
    monkeypatch.setattr(
        verify, "_run_git_bytes", lambda root, *args: git_outputs[tuple(args)]
    )

    first = verify.build_worktree_fingerprint(tmp_path)
    second = verify.build_worktree_fingerprint(tmp_path)
    assert first == second
    assert len(first["fingerprint_sha256"]) == 64

    tracked.write_text("v2\n", encoding="utf-8")
    third = verify.build_worktree_fingerprint(tmp_path)
    assert third["fingerprint_sha256"] != first["fingerprint_sha256"]


def test_local_gate_fails_closed_when_worktree_fingerprint_is_unavailable(
    monkeypatch, tmp_path: Path
):
    class Completed:
        returncode = 0
        stdout = "passed\n"
        stderr = ""

    monkeypatch.setattr(verify.subprocess, "run", lambda *args, **kwargs: Completed())
    monkeypatch.setattr(verify, "_git_revision", lambda: "a" * 40)
    monkeypatch.setattr(
        verify,
        "build_worktree_fingerprint",
        lambda root: {
            "schema_version": 1,
            "git_revision": "WORKTREE_UNVERIFIED",
            "dirty": None,
            "files": [],
            "fingerprint_sha256": "WORKTREE_UNVERIFIED",
        },
    )

    template = verify.build_template("local", tmp_path)
    executed, _ = verify.run_local_gates(template, tmp_path)
    assert all(item["status"] == "FAILED" for item in executed)
    assert all("evidence_integrity=failed" in item["result"] for item in executed)


def test_external_evidence_validation_accepts_complete_untampered_bundle(
    tmp_path: Path,
):
    entry = _passed_entry(tmp_path)
    assert verify.validate_external_evidence({"entries": [entry]}, tmp_path) == []


def test_external_evidence_validation_rejects_blocked_or_incomplete_claims(
    tmp_path: Path,
):
    blocked = _passed_entry(tmp_path)
    blocked["status"] = "BLOCKED_EXTERNAL"
    incomplete = _passed_entry(tmp_path, environment="hil_bm1688")
    incomplete["artifact_sha256"] = ""
    incomplete["rollback_action"] = ""

    failures = verify.validate_external_evidence(
        {"entries": [blocked, incomplete]}, tmp_path
    )
    reasons = "\n".join(item["reason"] for item in failures)
    assert "未达到 PASSED" in reasons
    assert "artifact_sha256" in reasons
    assert "rollback_action" in reasons


def test_external_evidence_validation_rejects_tampered_artifact(tmp_path: Path):
    entry = _passed_entry(tmp_path)
    Path(entry["artifact_path"]).write_text("tampered\n", encoding="utf-8")

    failures = verify.validate_external_evidence({"entries": [entry]}, tmp_path)
    assert any(
        "artifact_sha256 与文件内容不一致" in item["reason"] for item in failures
    )


def test_external_evidence_validation_rejects_revision_mismatch(tmp_path: Path):
    entry = _passed_entry(tmp_path)
    entry["git_revision"] = "c" * 40

    failures = verify.validate_external_evidence({"entries": [entry]}, tmp_path)
    assert any("指纹 revision" in item["reason"] for item in failures)


def test_external_evidence_validation_rejects_bundle_without_external_entries(
    tmp_path: Path,
):
    failures = verify.validate_external_evidence(
        {"entries": [{"environment": "local_windows", "status": "PASSED"}]},
        tmp_path,
    )
    assert failures == [
        {
            "evidence_id": "UNKNOWN",
            "reason": "证据文件不包含 staging_linux 或 hil_* 条目",
        }
    ]


def test_validate_evidence_cli_fails_closed_for_template(tmp_path: Path, capsys):
    evidence = tmp_path / "acceptance-template.json"
    template = verify.build_template("staging", tmp_path)
    evidence.write_text(json.dumps(template), encoding="utf-8")

    result = verify.main(
        [
            "--evidence-root",
            str(tmp_path),
            "--validate-evidence",
            str(evidence),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 2
    assert output["validation"] == "FAILED"
    assert output["blocking"] == 4


def test_validate_evidence_cli_accepts_complete_bundle(tmp_path: Path, capsys):
    evidence = tmp_path / "staging" / "bundle.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps({"entries": [_passed_entry(tmp_path)]}), encoding="utf-8"
    )

    result = verify.main(
        [
            "--evidence-root",
            str(tmp_path),
            "--validate-evidence",
            str(evidence),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output == {"validation": "PASSED", "blocking": 0, "failures": []}
