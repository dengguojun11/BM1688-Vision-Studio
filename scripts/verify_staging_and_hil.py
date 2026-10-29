#!/usr/bin/env python3
"""检查 staging/HIL 验收前置条件并生成证据模板。

该脚本是非破坏性的：不会启动服务、执行迁移、连接生产、SSH 设备或运行部署命令。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

STATUSES = {
    "NOT_STARTED",
    "IN_PROGRESS",
    "PASSED",
    "FAILED",
    "BLOCKED_EXTERNAL",
    "SKIPPED_UNAVAILABLE",
    "NOT_RUN_LOCAL_WINDOWS",
}
HIL_ENV = {
    "gpu": "LABELU_GPU_HIL_WORK_DIR",
    "bm1688": "LABELU_BM1688_HIL_WORK_DIR",
    "bm1684": "LABELU_BM1684_HIL_WORK_DIR",
    "rk1808": "LABELU_RK1808_HIL_WORK_DIR",
    "ota": "LABELU_OTA_HIL_WORK_DIR",
}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GIT_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("evidence-root 不能是文件系统根目录")
    if root == Path.cwd().resolve():
        raise ValueError(
            "evidence-root 不能直接使用工作区根目录，请指定 .tmp/evidence 等子目录"
        )
    return root


def _entry(
    evidence_id: str, gate: str, owner: str, environment: str, command: str
) -> dict:
    return {
        "evidence_id": evidence_id,
        "gate": gate,
        "owner": owner,
        "environment": environment,
        "command": command,
        "started_at": None,
        "finished_at": None,
        "result": "",
        "artifact_path": "",
        "artifact_sha256": "",
        "rollback_action": "只读门禁；修复前置条件后重跑，不执行数据回滚",
        "git_revision": "WORKTREE_UNVERIFIED",
        "worktree_fingerprint_sha256": "",
        "worktree_fingerprint_artifact_path": "",
        "worktree_fingerprint_artifact_sha256": "",
        "notes": "",
        "status": "NOT_STARTED",
    }


def _git_revision() -> str:
    """Return the local revision without requiring a clean worktree."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "WORKTREE_UNVERIFIED"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "WORKTREE_UNVERIFIED"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _run_git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr or b""
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="replace")
        raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
    stdout = completed.stdout or b""
    return stdout.encode("utf-8") if isinstance(stdout, str) else stdout


def _nul_paths(content: bytes) -> list[str]:
    return sorted(
        {
            raw.decode("utf-8", errors="surrogateescape")
            for raw in content.split(b"\0")
            if raw
        }
    )


def build_worktree_fingerprint(root: Path | None = None) -> dict:
    """Build a deterministic content fingerprint for the current Git worktree."""

    repo_root = (root or Path(__file__).resolve().parents[1]).resolve()
    try:
        revision = _run_git_bytes(repo_root, "rev-parse", "HEAD").decode().strip()
        status = _run_git_bytes(
            repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        changed = _run_git_bytes(repo_root, "diff", "--name-only", "-z", "HEAD", "--")
        untracked = _run_git_bytes(
            repo_root, "ls-files", "--others", "--exclude-standard", "-z"
        )
    except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError) as exc:
        return {
            "schema_version": 1,
            "git_revision": "WORKTREE_UNVERIFIED",
            "dirty": None,
            "files": [],
            "error": f"{type(exc).__name__}: {exc}",
            "fingerprint_sha256": "WORKTREE_UNVERIFIED",
        }

    files = []
    for relative in sorted(set(_nul_paths(changed)) | set(_nul_paths(untracked))):
        path = repo_root / relative
        record = {"path": relative.replace("\\", "/")}
        if path.is_symlink():
            target = os.readlink(path)
            encoded = target.encode("utf-8", errors="surrogateescape")
            record.update(
                kind="symlink", size=len(encoded), content_sha256=_sha256_bytes(encoded)
            )
        elif path.is_file():
            record.update(
                kind="file", size=path.stat().st_size, content_sha256=_sha256_file(path)
            )
        elif path.is_dir():
            record.update(kind="directory", size=None, content_sha256=None)
        else:
            record.update(kind="deleted", size=None, content_sha256=None)
        files.append(record)

    payload = {
        "schema_version": 1,
        "git_revision": revision or "WORKTREE_UNVERIFIED",
        "dirty": bool(status),
        "status_porcelain_sha256": _sha256_bytes(status),
        "files": files,
    }
    payload["fingerprint_sha256"] = _sha256_bytes(_canonical_json_bytes(payload))
    return payload


def _write_json_artifact(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_gate_log(
    path: Path, item: dict, argv: list[str], return_code: int, output: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            f"evidence_id={item['evidence_id']}",
            f"gate={item['gate']}",
            f"command={shlex.join(argv)}",
            f"exit_code={return_code}",
            "--- output ---",
            output.rstrip(),
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")


def run_local_gates(template: dict, evidence_root: Path) -> tuple[list[dict], Path]:
    """Run only entries explicitly scoped to the local environment.

    The commands are fixed by ``build_template`` and executed with the current
    Python interpreter. No staging, migration target, SSH, deployment or
    production command is accepted through this path.
    """

    root = Path(__file__).resolve().parents[1]
    revision = _git_revision()
    run_id = _timestamp()
    worktree_fingerprint = build_worktree_fingerprint(root)
    fingerprint_error = ""
    if not SHA256_RE.fullmatch(str(worktree_fingerprint.get("fingerprint_sha256", ""))):
        fingerprint_error = "工作树指纹不可验证"
    elif str(worktree_fingerprint.get("git_revision", "")) != revision:
        fingerprint_error = "工作树指纹 revision 与 gate revision 不一致"
    fingerprint_path = evidence_root / "local" / f"worktree-{run_id}.json"
    _write_json_artifact(fingerprint_path, worktree_fingerprint)
    fingerprint_artifact_sha256 = _sha256_file(fingerprint_path)
    executed: list[dict] = []
    for item in template["entries"]:
        if item.get("environment") != "local_windows":
            continue
        argv = shlex.split(item["command"], posix=os.name != "nt")
        if argv and argv[0].lower() == "python":
            argv[0] = sys.executable
        started = datetime.now(timezone.utc)
        item["started_at"] = started.isoformat()
        item["git_revision"] = revision
        item["worktree_fingerprint_sha256"] = worktree_fingerprint["fingerprint_sha256"]
        item["worktree_fingerprint_artifact_path"] = str(fingerprint_path)
        item["worktree_fingerprint_artifact_sha256"] = fingerprint_artifact_sha256
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            return_code = int(completed.returncode)
            output = (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired as exc:
            return_code = 124
            output = f"TimeoutExpired: {exc}"
        except (OSError, subprocess.SubprocessError) as exc:
            return_code = 1
            output = f"{type(exc).__name__}: {exc}"
        finished = datetime.now(timezone.utc)
        lines = [line for line in output.splitlines() if line.strip()]
        item["finished_at"] = finished.isoformat()
        item["result"] = f"exit_code={return_code}"
        item["notes"] = "\n".join(lines[-20:])[-4000:]
        item["status"] = (
            "PASSED" if return_code == 0 and not fingerprint_error else "FAILED"
        )
        if fingerprint_error:
            item["result"] += "; evidence_integrity=failed"
            item["notes"] = "\n".join(
                part for part in (item["notes"], fingerprint_error) if part
            )
        item["exit_code"] = return_code
        artifact_path = evidence_root / "local" / item["evidence_id"] / f"{run_id}.log"
        _write_gate_log(artifact_path, item, argv, return_code, output)
        item["artifact_path"] = str(artifact_path)
        item["artifact_sha256"] = _sha256_file(artifact_path)
        executed.append(dict(item))

    evidence_root.mkdir(parents=True, exist_ok=True)
    output_path = evidence_root / f"local-gates-{run_id}.json"
    output_path.write_text(
        json.dumps(
            {
                "product": "7GAI Evolution Platform",
                "environment": "local_windows",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "git_revision": revision,
                "worktree_fingerprint_sha256": worktree_fingerprint[
                    "fingerprint_sha256"
                ],
                "worktree_fingerprint_artifact_path": str(fingerprint_path),
                "worktree_fingerprint_artifact_sha256": fingerprint_artifact_sha256,
                "read_only": True,
                "entries": executed,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return executed, output_path


def _resolve_artifact_path(raw_path: str, evidence_root: Path) -> Path:
    root = evidence_root.resolve()
    raw = Path(raw_path).expanduser()
    candidates = (
        [raw.resolve()]
        if raw.is_absolute()
        else [
            (root / raw).resolve(),
            raw.resolve(),
        ]
    )
    for candidate in candidates:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise ValueError("artifact 必须是 evidence-root 内存在的普通文件")


def _validate_fingerprint_artifact(
    path: Path, expected_fingerprint: str, expected_revision: str
) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"工作树指纹 artifact 无法读取: {type(exc).__name__}"
    recorded = payload.pop("fingerprint_sha256", "")
    calculated = _sha256_bytes(_canonical_json_bytes(payload))
    if not SHA256_RE.fullmatch(recorded) or recorded.lower() != calculated:
        return "工作树指纹 artifact 自校验失败"
    if recorded.lower() != expected_fingerprint.lower():
        return "工作树指纹与证据条目不一致"
    if str(payload.get("git_revision", "")).lower() != expected_revision.lower():
        return "工作树指纹 revision 与证据条目不一致"
    return None


def validate_probe_evidence(payload: dict) -> list[dict]:
    """Validate the probe artifact emitted by ``staging_acceptance.py``.

    The staging probe intentionally has a different shape from the gate
    template (``probes`` instead of ``entries``).  Keep its validation strict
    enough to prove every required read-only check ran and passed, while
    remaining independent of the target environment.
    """

    failures: list[dict] = []

    def fail(evidence_id: str, reason: str) -> None:
        failures.append({"evidence_id": evidence_id, "reason": reason})

    if not isinstance(payload, dict):
        return [{"evidence_id": "UNKNOWN", "reason": "probe 证据必须是 JSON 对象"}]
    if payload.get("product") != "7GAI Evolution Platform":
        fail("PROBE-ARTIFACT", "product 不是 7GAI Evolution Platform")
    if payload.get("passed") is not True:
        fail("PROBE-ARTIFACT", "probe 证据总体状态不是 PASSED")
    probes = payload.get("probes")
    if not isinstance(probes, list) or not probes:
        return failures + [{"evidence_id": "PROBE-ARTIFACT", "reason": "probes 必须是非空数组"}]

    required_names = {
        "liveness",
        "readiness",
        "version",
        "swagger docs",
        "openapi json",
        "canonical OpenAPI paths",
        "unauthenticated API rejected",
    }
    json_probe_names = {
        "liveness",
        "readiness",
        "version",
        "openapi json",
        "canonical OpenAPI paths",
        "一期已禁用边端作业写入",
    }
    seen_names: set[str] = set()
    for probe in probes:
        if not isinstance(probe, dict):
            fail("PROBE-ARTIFACT", "probe 条目必须是对象")
            continue
        name = str(probe.get("name", "")).strip()
        if not name:
            fail("PROBE-ARTIFACT", "probe 条目缺少 name")
            continue
        seen_names.add(name)
        if probe.get("required") is True and probe.get("passed") is not True:
            fail(name, probe.get("detail") or "required probe 未通过")
        status = probe.get("status")
        if probe.get("passed") is True and not isinstance(status, int):
            if name != "退休写接口探针":
                fail(name, "通过的 probe 缺少 HTTP status")
        if name in json_probe_names and probe.get("response_kind") != "json":
            fail(name, "必须记录 response_kind=json；不能用 SPA HTML 或其他非 JSON 响应放行")
        if name == "退休写接口探针" and probe.get("passed") is True:
            if probe.get("required") is not False or probe.get("status") is not None:
                fail(name, "默认只读验收的退休写探针必须是 required=false 且 status=null")
    missing = sorted(required_names - seen_names)
    if missing:
        fail("PROBE-ARTIFACT", "缺少 required probes: " + ", ".join(missing))
    if (
        "退休写接口探针" not in seen_names
        and "一期已禁用边端作业写入" not in seen_names
    ):
        fail("PROBE-ARTIFACT", "缺少退休写接口探针或其跳过记录")
    return failures


def validate_external_evidence(payload: dict, evidence_root: Path) -> list[dict]:
    """Fail closed when staging/HIL evidence is incomplete or tampered."""

    if isinstance(payload, dict) and "probes" in payload:
        return validate_probe_evidence(payload)

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return [{"evidence_id": "UNKNOWN", "reason": "entries 必须是数组"}]

    failures = []
    external_entries = 0
    for item in entries:
        if not isinstance(item, dict):
            failures.append({"evidence_id": "UNKNOWN", "reason": "证据条目必须是对象"})
            continue
        environment = str(item.get("environment", ""))
        if environment != "staging_linux" and not environment.startswith("hil_"):
            continue
        external_entries += 1
        evidence_id = str(item.get("evidence_id", "UNKNOWN"))

        def fail(reason: str) -> None:
            failures.append({"evidence_id": evidence_id, "reason": reason})

        if item.get("status") != "PASSED":
            fail(f"外部证据状态为 {item.get('status') or 'EMPTY'}，未达到 PASSED")
            continue
        if not str(item.get("evidence_id", "")).strip():
            fail("PASSED 条目缺少 evidence_id")
        for field in (
            "gate",
            "owner",
            "started_at",
            "finished_at",
            "result",
            "command",
            "rollback_action",
        ):
            if not str(item.get(field, "")).strip():
                fail(f"PASSED 条目缺少 {field}")
        revision = str(item.get("git_revision", "")).strip()
        if not GIT_REVISION_RE.fullmatch(revision):
            fail("PASSED 条目缺少可验证 git_revision")

        timestamps = {}
        for field in ("started_at", "finished_at"):
            raw_timestamp = str(item.get(field, "")).strip()
            if not raw_timestamp:
                continue
            try:
                timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            except ValueError:
                fail(f"{field} 不是合法 ISO-8601 时间")
                continue
            if timestamp.tzinfo is None:
                fail(f"{field} 必须包含时区")
                continue
            timestamps[field] = timestamp
        if (
            timestamps.get("started_at")
            and timestamps.get("finished_at")
            and timestamps["finished_at"] < timestamps["started_at"]
        ):
            fail("finished_at 早于 started_at")

        artifact_sha256 = str(item.get("artifact_sha256", "")).strip()
        if not SHA256_RE.fullmatch(artifact_sha256):
            fail("PASSED 条目缺少合法 artifact_sha256")
        else:
            try:
                artifact_path = _resolve_artifact_path(
                    str(item.get("artifact_path", "")), evidence_root
                )
            except ValueError as exc:
                fail(str(exc))
            else:
                if _sha256_file(artifact_path) != artifact_sha256.lower():
                    fail("artifact_sha256 与文件内容不一致")

        fingerprint = str(item.get("worktree_fingerprint_sha256", "")).strip()
        fingerprint_artifact_sha256 = str(
            item.get("worktree_fingerprint_artifact_sha256", "")
        ).strip()
        if not SHA256_RE.fullmatch(fingerprint):
            fail("PASSED 条目缺少合法 worktree_fingerprint_sha256")
        if not SHA256_RE.fullmatch(fingerprint_artifact_sha256):
            fail("PASSED 条目缺少合法 worktree_fingerprint_artifact_sha256")
            continue
        try:
            fingerprint_path = _resolve_artifact_path(
                str(item.get("worktree_fingerprint_artifact_path", "")),
                evidence_root,
            )
        except ValueError as exc:
            fail(str(exc))
            continue
        if _sha256_file(fingerprint_path) != fingerprint_artifact_sha256.lower():
            fail("工作树指纹 artifact SHA-256 与文件内容不一致")
            continue
        if SHA256_RE.fullmatch(fingerprint):
            fingerprint_error = _validate_fingerprint_artifact(
                fingerprint_path, fingerprint, revision
            )
            if fingerprint_error:
                fail(fingerprint_error)
    if external_entries == 0:
        failures.append(
            {
                "evidence_id": "UNKNOWN",
                "reason": "证据文件不包含 staging_linux 或 hil_* 条目",
            }
        )
    return failures


def validate_backup_manifest(payload: dict) -> list[dict]:
    """Validate the non-secret fields required for a restore rehearsal.

    This only inspects a supplied JSON artifact.  It never connects to a
    database or attempts a restore, so a valid manifest remains evidence of
    documentation, not proof that production recovery succeeded.
    """

    failures: list[dict] = []

    def fail(field: str, reason: str) -> None:
        failures.append({"field": field, "reason": reason})

    if not isinstance(payload, dict):
        return [{"field": "manifest", "reason": "备份恢复证据必须是 JSON 对象"}]
    required = (
        "git_revision",
        "alembic_heads",
        "database_type",
        "backup_sha256",
        "upgrade_result",
        "repeat_upgrade_result",
        "restore_result",
        "restore_duration_seconds",
        "rpo_seconds",
        "rto_seconds",
        "ownership_audit",
    )
    for field in required:
        value = payload.get(field)
        if value is None or value == "" or value == [] or value == {}:
            fail(field, "缺少必填恢复演练字段")
    digest = str(payload.get("backup_sha256", ""))
    if digest and not SHA256_RE.fullmatch(digest):
        fail("backup_sha256", "必须是 64 位十六进制 SHA-256")
    for field in ("restore_duration_seconds", "rpo_seconds", "rto_seconds"):
        value = payload.get(field)
        if value is None:
            continue
        try:
            if float(value) < 0:
                raise ValueError
        except (TypeError, ValueError):
            fail(field, "必须是非负数字")
    if isinstance(payload.get("ownership_audit"), dict):
        audit = payload["ownership_audit"]
        if not str(audit.get("status", "")).strip():
            fail("ownership_audit.status", "恢复后的归属审计必须记录 status")
    return failures


def build_template(environment: str, evidence_root: Path) -> dict:
    entries = []
    if environment in {"local", "all"}:
        entries.extend(
            [
                _entry(
                    "QA-07-MIGRATION-HEAD-001",
                    "migration_single_head",
                    "QA/ARCH",
                    "local_windows",
                    "python -m pytest labelu/tests/internal/common/test_architecture_contracts.py -q",
                ),
                _entry(
                    "QA-07-MIGRATION-EMPTY-001",
                    "migration_empty_repeat_upgrade",
                    "QA/MLOps",
                    "local_windows",
                    "python -m pytest labelu/tests/alembic/test_empty_database_upgrade.py -q",
                ),
                _entry(
                    "QA-08-E2E-SKIP-001",
                    "frontend_external_gate_semantics",
                    "QA/FE/UX",
                    "local_windows",
                    "python -m pytest scripts/tests/test_local_gate_inventory.py scripts/tests/test_deploy_nginx_backend_routes.py -q",
                ),
                _entry(
                    "ARCH-12-OWNERSHIP-DRYRUN-001",
                    "project_ownership_dry_run",
                    "ARCH/BE-A/QA",
                    "local_windows",
                    "python -m pytest labelu/tests/internal/application/service/test_project_ownership_audit.py scripts/tests/test_audit_project_ownership_cli.py -q",
                ),
                _entry(
                    "QA-09-EVIDENCE-INTEGRITY-001",
                    "release_evidence_integrity",
                    "QA/MLOps/ARCH",
                    "local_windows",
                    "python -m pytest scripts/tests/test_release_evidence_integrity.py -q",
                ),
                _entry(
                    "QA-09-PROBE-EVIDENCE-001",
                    "staging_probe_evidence_validation",
                    "QA/MLOps/ARCH",
                    "local_windows",
                    "python -m pytest scripts/tests/test_verify_staging_and_hil.py -q",
                ),
                _entry(
                    "MLOPS-LOCAL-PROVIDER-CONTRACT-001",
                    "provider_contract_local",
                    "MLOps/BE-B/QA",
                    "local_windows",
                    "python -m pytest labelu/tests/internal/application/service/test_provider_adapter.py labelu/tests/internal/application/service/test_provider_worker.py labelu/tests/internal/application/service/test_provider_worker_runtime.py -q",
                ),
                _entry(
                    "MLOPS-LOCAL-OTA-CONTRACT-001",
                    "ota_contract_local",
                    "MLOps/BE-C/QA",
                    "local_windows",
                    "python -m pytest scripts/tests/test_seven_gai_agent_simulator.py labelu/tests/test_edge_agent_protocol.py -q",
                ),
            ]
        )
    if environment in {"staging", "all"}:
        entries.extend(
            [
                _entry(
                    "QA-06-API-001",
                    "api_docs",
                    "QA/MLOps",
                    "staging_linux",
                    "python scripts/staging_acceptance.py --base-url $LABELU_STAGING_BASE_URL --username $LABELU_STAGING_USERNAME --password $LABELU_STAGING_PASSWORD --evidence <evidence-path>",
                ),
                _entry(
                    "QA-06-E2E-001",
                    "real_backend_playwright",
                    "QA/FE",
                    "staging_linux",
                    "python -m pytest labelu/tests/e2e/test_7gai_platform_shell_frontend.py -q",
                ),
                _entry(
                    "QA-08-AXE-001",
                    "accessibility_axe",
                    "QA/FE/UX",
                    "staging_linux",
                    "npx playwright test --config=$LABELU_AXE_CONFIG",
                ),
                _entry(
                    "QA-10-PERFORMANCE-001",
                    "workspace_api_performance",
                    "QA/MLOps/BE-A",
                    "staging_linux",
                    "python scripts/benchmark_workspace_api.py --base-url $LABELU_STAGING_BASE_URL --token $LABELU_BENCHMARK_TOKEN --iterations 25 --warmups 3 --format json",
                ),
            ]
        )
    if environment in {"hil", "all"}:
        for board, variable in HIL_ENV.items():
            entries.append(
                _entry(
                    f"MLOPS-02-{board.upper()}-001",
                    f"hil_{board}",
                    "MLOps/QA",
                    f"hil_{board}",
                    f"真实 {board} HIL runner（见运行手册）",
                )
            )
            entries[-1]["notes"] = f"就绪变量：{variable}"
    if environment in {"release", "all"}:
        entries.append(
            _entry(
                "MLOPS-05-LINUX-001",
                "linux_release",
                "MLOps",
                "production_linux",
                "LABELU_GIT_BRANCH=<branch> bash deploy.sh update",
            )
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "evidence_root": str(evidence_root),
        "status_values": sorted(STATUSES),
        "entries": entries,
    }


def check_template(template: dict, evidence_root: Path) -> list[dict]:
    failures: list[dict] = []
    is_windows = platform.system().lower().startswith("win")
    base_url = os.environ.get("LABELU_STAGING_BASE_URL", "").strip()
    username = os.environ.get("LABELU_STAGING_USERNAME", "").strip()
    password = os.environ.get("LABELU_STAGING_PASSWORD", "").strip()
    benchmark_token = os.environ.get("LABELU_BENCHMARK_TOKEN", "").strip()
    for item in template["entries"]:
        gate = item["gate"]
        if gate == "linux_release":
            item["status"] = "NOT_RUN_LOCAL_WINDOWS" if is_windows else "NOT_STARTED"
            item["notes"] = (
                "Linux-only deploy/systemd/Nginx/SSH 命令未在 Windows 本地执行"
                if is_windows
                else "需在受控 Linux 发布主机执行"
            )
            if is_windows:
                continue
            if shutil.which("bash") is None:
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = "Linux 发布环境未找到 bash；未执行 deploy.sh"
                failures.append(item)
                continue
        if item["environment"] == "staging_linux":
            if not base_url or not username or not password:
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = (
                    "缺少 LABELU_STAGING_BASE_URL/USERNAME/PASSWORD；脚本不读取明文值"
                )
                failures.append(item)
            elif (
                gate in {"real_backend_playwright", "accessibility_axe"}
                and shutil.which("npx") is None
            ):
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = (
                    "已配置 staging 环境变量，但本机未找到 npx；不能执行真实 Playwright"
                )
                failures.append(item)
            elif (
                gate == "real_backend_playwright"
                and not os.environ.get("LABELU_PLATFORM_URL", "").strip()
            ):
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = (
                    "未设置 LABELU_PLATFORM_URL；pytest skip 不能作为真实后端通过证据"
                )
                failures.append(item)
            elif (
                gate == "accessibility_axe"
                and not os.environ.get("LABELU_AXE_CONFIG", "").strip()
            ):
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = (
                    "未设置 LABELU_AXE_CONFIG；axe 未执行，不能标记为 PASSED"
                )
                failures.append(item)
            elif gate == "workspace_api_performance" and not benchmark_token:
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = (
                    "未设置 LABELU_BENCHMARK_TOKEN；不能执行带鉴权的只读性能预算门禁"
                )
                failures.append(item)
            else:
                item["status"] = "NOT_STARTED"
        if gate.startswith("hil_"):
            board = gate.removeprefix("hil_")
            variable = HIL_ENV[board]
            work_dir = os.environ.get(variable, "").strip()
            if not work_dir:
                item["status"] = (
                    "BLOCKED_EXTERNAL"
                    if board in {"gpu", "ota"}
                    else "SKIPPED_UNAVAILABLE"
                )
                item["notes"] = f"未设置 {variable}，没有真实 {board} 工作目录"
                failures.append(item)
            elif not Path(work_dir).expanduser().is_dir():
                item["status"] = "BLOCKED_EXTERNAL"
                item["notes"] = f"{variable} 指向的工作目录不存在"
                failures.append(item)
            else:
                item["status"] = "NOT_STARTED"
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment",
        choices=("local", "staging", "hil", "release", "all"),
        default="all",
    )
    parser.add_argument("--evidence-root", default=".tmp/evidence")
    parser.add_argument("--emit-template", action="store_true")
    parser.add_argument(
        "--run-local",
        action="store_true",
        help="显式执行 local_windows 门禁；不会执行 staging/HIL/生产命令",
    )
    parser.add_argument(
        "--check", action="store_true", help="检查前置条件；缺失依赖时返回非零"
    )
    parser.add_argument(
        "--validate-evidence",
        help="校验 evidence-root 内既有 staging/HIL JSON；缺失或篡改时始终返回非零",
    )
    parser.add_argument(
        "--validate-backup",
        help="只读校验 evidence-root 内备份恢复演练 manifest JSON",
    )
    args = parser.parse_args(argv)
    try:
        root = _safe_root(args.evidence_root)
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_evidence:
        try:
            evidence_path = _resolve_artifact_path(args.validate_evidence, root)
            evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(
                json.dumps(
                    {
                        "validation": "FAILED",
                        "blocking": 1,
                        "failures": [{"evidence_id": "UNKNOWN", "reason": str(exc)}],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        validation_failures = validate_external_evidence(evidence_payload, root)
        print(
            json.dumps(
                {
                    "validation": "FAILED" if validation_failures else "PASSED",
                    "blocking": len(validation_failures),
                    "failures": validation_failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if validation_failures else 0
    if args.validate_backup:
        try:
            backup_path = _resolve_artifact_path(args.validate_backup, root)
            backup_payload = json.loads(backup_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(
                json.dumps(
                    {
                        "validation": "FAILED",
                        "blocking": 1,
                        "failures": [{"field": "manifest", "reason": str(exc)}],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        backup_failures = validate_backup_manifest(backup_payload)
        print(
            json.dumps(
                {
                    "validation": "FAILED" if backup_failures else "PASSED",
                    "blocking": len(backup_failures),
                    "failures": backup_failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if backup_failures else 0
    template = build_template(args.environment, root)
    failures = check_template(template, root)
    local_evidence_path = None
    if args.run_local:
        if args.environment not in {"local", "all"}:
            parser.error("--run-local 只能与 --environment local 或 all 一起使用")
        executed, local_evidence_path = run_local_gates(template, root)
        failures.extend(item for item in executed if item["status"] != "PASSED")
    if args.emit_template:
        root.mkdir(parents=True, exist_ok=True)
        output = root / f"acceptance-template-{_timestamp()}.json"
        output.write_text(
            json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"template={output}")
    payload = {
        "environment": args.environment,
        "entries": template["entries"],
        "blocking": len(failures),
    }
    if local_evidence_path is not None:
        payload["local_evidence"] = str(local_evidence_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.check and failures:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
