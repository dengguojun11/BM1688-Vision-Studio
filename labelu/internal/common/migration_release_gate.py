"""只读/沙箱迁移发布闸门。

该模块只检查本地代码、迁移源码和外部证据文件，不打开数据库连接。
真实 MySQL 演练由 ``rehearse_dispatch_lease_migration.py`` 在明确指定的
本机 ``labelu_migration_sandbox*`` 库中执行；本闸门只验证演练证据是否完整。

生产数据库、AICM、S3 和设备均不属于本模块的访问范围。缺少备份恢复、锁
等待或候选迁移正式合入证据时，结果必须为 ``valid=False``。
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url

from labelu.scripts.audit_execution_lease_migration import (
    TARGET_TABLES,
    audit_migration_drift,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALEMBIC_INI = ROOT / "alembic_labelu" / "alembic.ini"
DEFAULT_VERSIONS = ROOT / "alembic_labelu" / "versions"
DEFAULT_CANDIDATE_DIR = ROOT / "docs" / "7gai_phase_one_migration_candidates"


@dataclass(frozen=True)
class GateCheck:
    """One named, serialisable release-gate check."""

    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class MigrationReleaseGateReport:
    """只读迁移发布检查结果。"""

    valid: bool
    checks: dict[str, GateCheck]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checks": {name: item.as_dict() for name, item in self.checks.items()},
            "errors": list(self.errors),
        }


def validate_sandbox_url(database_url: str | URL) -> URL:
    """校验演练 URL，拒绝生产或任意远程 MySQL。

    该函数只解析 URL，不建立连接。允许 SQLite（测试临时库）以及本机
    MySQL 且库名以 ``labelu_migration_sandbox`` 开头的隔离库。
    """

    parsed = make_url(database_url) if isinstance(database_url, str) else database_url
    backend = parsed.get_backend_name().lower()
    if backend in {"sqlite", "sqlite+pysqlite"}:
        return parsed
    if backend != "mysql":
        raise RuntimeError("迁移演练只允许 SQLite 或本机 MySQL 沙箱")
    host = str(parsed.host or "").strip().lower()
    database = str(parsed.database or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("拒绝连接远程或生产 MySQL 主机")
    if not database.startswith("labelu_migration_sandbox"):
        raise RuntimeError("MySQL 演练库名必须以 labelu_migration_sandbox 开头")
    return parsed


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _load_json_evidence(path: str | Path | None, name: str) -> tuple[bool, str]:
    if path is None:
        return False, f"未提供{name}证据文件"
    evidence_path = Path(path)
    if not evidence_path.is_file():
        return False, f"{name}证据文件不存在: {evidence_path}"
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, f"{name}证据不可读取: {exc}"
    if not isinstance(payload, dict):
        return False, f"{name}证据必须是 JSON 对象"
    if str(payload.get("status", "")).upper() not in {"PASS", "PASSED", "通过"}:
        return False, f"{name}证据状态不是通过"
    return True, "证据状态为通过"


def _validate_evidence_provenance(payload: dict[str, Any], name: str) -> GateCheck:
    """Require enough provenance to distinguish real sandbox evidence from a hand-written JSON file."""
    metadata = payload.get("evidence_meta")
    if not isinstance(metadata, dict):
        return GateCheck(False, f"{name}缺少 evidence_meta 来源信息")
    required = (
        "evidence_id",
        "generated_at",
        "script_version",
        "git_revision",
        "database_name",
        "mysql_version",
    )
    missing = [field for field in required if not str(metadata.get(field, "")).strip()]
    if missing:
        return GateCheck(False, f"{name}来源信息缺少: " + ", ".join(missing))
    database_name = str(metadata["database_name"]).strip().lower()
    if not database_name.startswith("labelu_migration_sandbox"):
        return GateCheck(False, f"{name}来源库不是隔离沙箱: {database_name}")
    git_revision = str(metadata["git_revision"]).strip().lower()
    if git_revision in {"unknown", "workspace", "dirty"} or not re.fullmatch(
        r"[0-9a-f]{7,64}", git_revision
    ):
        return GateCheck(False, f"{name} git_revision 不是可追溯提交摘要")
    return GateCheck(True, f"{name}来源包含沙箱库、MySQL版本、脚本版本、代码提交和生成时间")


def validate_backup_restore_evidence(path: str | Path | None) -> GateCheck:
    """验证备份恢复证据的最小字段，不执行恢复。"""

    passed, detail = _load_json_evidence(path, "备份恢复")
    if not passed:
        return GateCheck(False, detail)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    provenance = _validate_evidence_provenance(payload, "备份恢复")
    if not provenance.passed:
        return provenance
    required = (
        "database_type",
        "backup_path",
        "backup_sha256",
        "upgrade_result",
        "repeat_upgrade_result",
        "restore_target",
        "restore_result",
    )
    missing = [field for field in required if not str(payload.get(field, "")).strip()]
    digest = str(payload.get("backup_sha256", ""))
    if missing:
        return GateCheck(False, "备份恢复证据缺少: " + ", ".join(missing))
    if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        return GateCheck(False, "backup_sha256 必须是 64 位十六进制")
    if str(payload.get("restore_result", "")).upper() not in {"PASS", "PASSED", "通过"}:
        return GateCheck(False, "restore_result 未明确通过")
    if str(payload.get("database_type", "")).lower() != "mysql":
        return GateCheck(False, "备份恢复证据 database_type 必须为 mysql")
    return GateCheck(True, "备份来源、重复升级和独立恢复证据字段完整")


def validate_lock_wait_evidence(path: str | Path | None) -> GateCheck:
    """验证 MySQL 并发/锁等待证据的最小字段，不执行并发测试。"""

    passed, detail = _load_json_evidence(path, "MySQL 锁等待")
    if not passed:
        return GateCheck(False, detail)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    provenance = _validate_evidence_provenance(payload, "MySQL 锁等待")
    if not provenance.passed:
        return provenance
    if str(payload.get("database_type", "")).lower() != "mysql":
        return GateCheck(False, "锁等待证据 database_type 必须为 mysql")
    try:
        workers = int(payload.get("concurrent_workers", 0))
        max_wait = float(payload.get("max_lock_wait_seconds", -1))
        timeout = float(payload.get("lock_wait_timeout_seconds", -1))
    except (TypeError, ValueError):
        return GateCheck(False, "锁等待数值字段格式错误")
    if workers < 2:
        return GateCheck(False, "锁等待证据必须包含至少 2 个并发执行者")
    if max_wait < 0 or timeout <= 0:
        return GateCheck(False, "锁等待超时和最大等待时间必须为有效非负值")
    if int(payload.get("duplicate_claims", 1)) != 0:
        return GateCheck(False, "并发领取存在重复副作用")
    if payload.get("old_token_rejected") is not True:
        return GateCheck(False, "缺少旧 fencing token 被拒绝证据")
    return GateCheck(True, "MySQL 并发、锁等待和旧 token 证据完整")


def validate_concurrency_matrix_evidence(path: str | Path | None) -> GateCheck:
    """Require the production-preflight 2/10/20/50 concurrency matrix.

    This is an evidence-only check. It deliberately does not execute workers or
    open a database connection; the matrix must be produced by a separately
    controlled MySQL sandbox rehearsal.
    """

    passed, detail = _load_json_evidence(path, "并发矩阵")
    if not passed:
        return GateCheck(False, detail)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    provenance = _validate_evidence_provenance(payload, "并发矩阵")
    if not provenance.passed:
        return provenance
    if str(payload.get("database_type", "")).lower() != "mysql":
        return GateCheck(False, "并发矩阵 database_type 必须为 mysql")
    matrix = payload.get("matrix")
    if not isinstance(matrix, list):
        return GateCheck(False, "并发矩阵必须是 matrix 数组")
    expected = (2, 10, 20, 50)
    rows: dict[int, dict[str, Any]] = {}
    for item in matrix:
        if not isinstance(item, dict):
            return GateCheck(False, "并发矩阵包含非对象记录")
        try:
            workers = int(item.get("concurrent_workers", 0))
        except (TypeError, ValueError):
            return GateCheck(False, "并发矩阵 concurrent_workers 格式错误")
        if workers in rows:
            return GateCheck(False, f"并发矩阵重复记录: {workers}")
        rows[workers] = item
    missing = [str(value) for value in expected if value not in rows]
    if missing:
        return GateCheck(False, "并发矩阵缺少档位: " + ", ".join(missing))
    for workers in expected:
        item = rows[workers]
        if str(item.get("status", "")).upper() not in {"PASS", "PASSED", "通过"}:
            return GateCheck(False, f"并发矩阵 {workers} 并发未通过")
        try:
            duplicate_claims = int(item.get("duplicate_claims", 1))
            max_wait = float(item.get("max_lock_wait_seconds", -1))
        except (TypeError, ValueError):
            return GateCheck(False, f"并发矩阵 {workers} 数值字段格式错误")
        if duplicate_claims != 0:
            return GateCheck(False, f"并发矩阵 {workers} 存在重复领取副作用")
        if max_wait < 0:
            return GateCheck(False, f"并发矩阵 {workers} 锁等待时间无效")
        if item.get("old_token_rejected") is not True:
            return GateCheck(False, f"并发矩阵 {workers} 缺少旧 token 拒绝证据")
    return GateCheck(True, "2/10/20/50 并发、唯一领取和旧 token 证据完整")


def _orm_closure_check(audit: dict[str, Any]) -> GateCheck:
    """检查 ORM 声明的字段/索引均能在正式迁移源码中找到来源。"""

    orm = audit.get("ORM租约契约") or {}
    formal_columns = audit.get("正式迁移字段来源") or {}
    formal_indexes = audit.get("正式迁移索引来源") or {}
    formal_aux_columns = audit.get("正式迁移辅助字段来源") or {}
    formal_aux_indexes = audit.get("正式迁移辅助索引来源") or {}
    missing: list[str] = []
    for table_name in TARGET_TABLES:
        block = orm.get(table_name) or {}
        for column in block.get("columns", []):
            if not formal_columns.get(column):
                missing.append(f"{table_name}.{column}")
        for index in block.get("indexes", []):
            if not formal_indexes.get(index):
                missing.append(index)
        for column in block.get("辅助字段", []):
            if not formal_aux_columns.get(column):
                missing.append(f"{table_name}.{column}")
        for index in block.get("辅助索引", []):
            if not formal_aux_indexes.get(index):
                missing.append(index)
    if missing:
        return GateCheck(False, "ORM字段/索引没有正式迁移来源: " + ", ".join(sorted(missing)))
    return GateCheck(True, "ORM字段、扫描索引和 worker_action 均有正式迁移来源")


def _candidate_status(candidate_path: Path, formal_revision: str) -> GateCheck:
    """Only accept a historical draft when it names its formal replacement."""

    if not candidate_path.is_file():
        return GateCheck(True, "未发现待处理候选迁移")
    spec = importlib.util.spec_from_file_location(
        "labelu_phase_one_candidate_gate", candidate_path
    )
    if spec is None or spec.loader is None:
        return GateCheck(False, f"候选迁移无法读取: {candidate_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - defensive gate boundary
        return GateCheck(False, f"候选迁移加载失败: {exc}")
    superseded_by = str(getattr(module, "SUPERSEDED_BY", "")).strip()
    if superseded_by == formal_revision:
        return GateCheck(True, f"候选迁移已由正式 {formal_revision} 覆盖，保留仅用于审计")
    return GateCheck(False, f"候选迁移仍待正式收口: {candidate_path.name}")


def run_migration_release_gate(
    *,
    config_path: str | Path = DEFAULT_ALEMBIC_INI,
    versions_dir: str | Path = DEFAULT_VERSIONS,
    candidate_dir: str | Path = DEFAULT_CANDIDATE_DIR,
    database_url: str | URL | None = None,
    backup_evidence: str | Path | None = None,
    lock_wait_evidence: str | Path | None = None,
    concurrency_evidence: str | Path | None = None,
) -> MigrationReleaseGateReport:
    """运行不连接数据库的迁移发布门禁。"""

    checks: dict[str, GateCheck] = {}
    errors: list[str] = []

    config = Config(str(config_path))
    config.set_main_option("script_location", str(Path(config_path).parent))
    scripts = ScriptDirectory.from_config(config)
    heads = tuple(scripts.get_heads())
    checks["唯一 Alembic head"] = GateCheck(
        len(heads) == 1,
        f"当前 head={','.join(heads) if heads else '无'}" if len(heads) == 1 else f"发现 {len(heads)} 个 head",
    )

    audit = audit_migration_drift(
        config_path,
        versions_dir=versions_dir,
        candidate_path=Path(candidate_dir) / "gai_p1_08_add_dispatch_leases.py",
    )
    checks["ORM字段索引闭包"] = _orm_closure_check(audit)
    if not checks["ORM字段索引闭包"].passed:
        errors.append(checks["ORM字段索引闭包"].detail)

    candidate_root = Path(candidate_dir).resolve()
    candidate_path = candidate_root / "gai_p1_08_add_dispatch_leases.py"
    checks["调度租约候选迁移已合入正式 versions"] = _candidate_status(
        candidate_path, "z4a5b6c7d8e9"
    )
    if not checks["调度租约候选迁移已合入正式 versions"].passed:
        errors.append(checks["调度租约候选迁移已合入正式 versions"].detail)

    eval_candidate_path = candidate_root / "gai_p1_09_add_yolo_eval_generations.py"
    checks["评估代次候选迁移已合入正式 versions"] = _candidate_status(
        eval_candidate_path, "待正式迁移"
    )
    if not checks["评估代次候选迁移已合入正式 versions"].passed:
        errors.append(checks["评估代次候选迁移已合入正式 versions"].detail)

    if database_url is None:
        checks["演练 URL 安全"] = GateCheck(False, "未提供沙箱 URL；发布闸门不接受默认生产 URL")
    else:
        try:
            validate_sandbox_url(database_url)
        except (TypeError, ValueError, RuntimeError) as exc:
            checks["演练 URL 安全"] = GateCheck(False, str(exc))
        else:
            checks["演练 URL 安全"] = GateCheck(True, "URL 仅允许 SQLite 或本机 labelu_migration_sandbox* MySQL")
    if not checks["演练 URL 安全"].passed:
        errors.append(checks["演练 URL 安全"].detail)

    checks["备份恢复证据"] = validate_backup_restore_evidence(backup_evidence)
    checks["MySQL锁等待证据"] = validate_lock_wait_evidence(lock_wait_evidence)
    checks["2/10/20/50并发矩阵"] = validate_concurrency_matrix_evidence(
        concurrency_evidence
    )
    for name in ("备份恢复证据", "MySQL锁等待证据", "2/10/20/50并发矩阵"):
        if not checks[name].passed:
            errors.append(checks[name].detail)

    valid = not errors and all(check.passed for check in checks.values())
    return MigrationReleaseGateReport(valid, checks, tuple(errors))


__all__ = [
    "GateCheck",
    "MigrationReleaseGateReport",
    "run_migration_release_gate",
    "validate_backup_restore_evidence",
    "validate_lock_wait_evidence",
    "validate_concurrency_matrix_evidence",
    "validate_sandbox_url",
]
