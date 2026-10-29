"""远程 YOLO 训练：平台侧数据集准备、打包上传与调度（节点 Agent 另装）。"""
from __future__ import annotations

import ipaddress
import hashlib
import tempfile
import json
import os
import shutil
import socket
import subprocess
import tarfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from loguru import logger

from labelu.internal.adapter.persistence import crud_user, crud_yolo_train_job
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.service.remote_train_nodes import resolve_node_for_train
from labelu.internal.application.service.yolo_train_merge import (
    load_merged_training_context,
    parse_source_task_ids,
    source_task_ids_json,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.remote_train_nodes_config import get_node_by_id
from labelu.internal.common.remote_train_hmac import (
    HMAC_KEY_ID_FIELD,
    HMAC_SIGNATURE_FIELD,
    sign_json_file,
    verify_bytes_signature,
    verify_json_payload,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus
from labelu.internal.common.execution_fencing import (
    ExecutionFencingLost,
    ExecutionFencingUnavailable,
    ExecutionLeaseToken,
    claim_execution_lease,
    conditional_execution_update,
    execution_lease_is_current,
    release_execution_lease,
    renew_execution_lease,
)

_REMOTE_LOCK = threading.Lock()
_ACTIVE_REMOTE_JOBS: set[int] = set()
_REMOTE_EXECUTION_TOKENS: Dict[int, ExecutionLeaseToken] = {}
_REMOTE_TOKEN_LOCK = threading.Lock()
_REMOTE_ACTIVE_PHASES = frozenset(
    {"preparing", "uploading", "downloading", "uploaded", "remote_running"}
)
REMOTE_TRAIN_ANALYSIS_ARTIFACTS = (
    "train/args.yaml",
    "train/results.png",
    "train/confusion_matrix.png",
    "train/confusion_matrix_normalized.png",
    "train/labels.jpg",
    "train/labels_correlate.jpg",
    "train/BoxPR_curve.png",
    "train/BoxF1_curve.png",
    "train/BoxP_curve.png",
    "train/BoxR_curve.png",
    "train/PR_curve.png",
    "train/F1_curve.png",
    "train/P_curve.png",
    "train/R_curve.png",
    "train/val_batch0_labels.jpg",
    "train/val_batch0_pred.jpg",
    "train/val_batch1_labels.jpg",
    "train/val_batch1_pred.jpg",
    "train/val_batch2_labels.jpg",
    "train/val_batch2_pred.jpg",
)


def is_remote_train_job_dispatching(job_id: int) -> bool:
    with _REMOTE_LOCK:
        return int(job_id) in _ACTIVE_REMOTE_JOBS


def remote_job_runtime_status(job: YoloTrainJob) -> Optional[str]:
    """远程作业运行态：dispatch/上传/远端训练中视为 RUNNING，避免误报 STOPPED。"""
    if str(getattr(job, "execution_mode", None) or "local").strip() != "remote":
        return None
    if is_remote_train_job_dispatching(int(job.id)):
        return "RUNNING"
    remote = _read_remote_status(job)
    phase = str(remote.get("phase") or remote.get("remote_phase") or "").strip().lower()
    if phase in _REMOTE_ACTIVE_PHASES:
        return "RUNNING"
    if phase == "failed":
        return "FAILED"
    from labelu.internal.application.service.yolo_train import _read_train_phase

    train_phase, _, _, _ = _read_train_phase(int(job.id))
    status_value = str(job.status or "")
    if train_phase == "running" and status_value == YoloTrainStatus.PROCESSING.value:
        return "RUNNING"
    if status_value in {
        YoloTrainStatus.PROCESSING.value,
        YoloTrainStatus.PENDING.value,
    } and train_phase in {"preparing_dataset", "dataset_ready"}:
        if phase in {"preparing", "uploading", ""}:
            return "RUNNING"
    return "STOPPED"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _job_work_root(job_id: int) -> Path:
    from labelu.internal.application.service.yolo_train import _job_work_root

    return _job_work_root(job_id)


def _shell_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def _media_listen_port() -> int:
    base = str(settings.MEDIA_HOST or "").strip()
    if base:
        try:
            parsed = urlparse(base if "://" in base else f"http://{base}")
            if parsed.port:
                return int(parsed.port)
        except (TypeError, ValueError):
            pass
    return 10000


def _linux_global_ipv4s() -> List[str]:
    """平台机非 loopback IPv4（Linux ip 命令）。"""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ips: List[str] = []
    for line in out.splitlines():
        parts = line.split()
        for idx, token in enumerate(parts):
            if token == "inet" and idx + 1 < len(parts):
                ips.append(parts[idx + 1].split("/", 1)[0])
                break
    return list(dict.fromkeys(ips))


def _resolve_node_host_ip(node: Dict[str, Any]) -> Optional[str]:
    from labelu.internal.common.edge_ssh_client import _lookup_openssh_config

    host_alias = str(node.get("host") or "").strip()
    if not host_alias:
        return None
    openssh_cfg = _lookup_openssh_config(host_alias)
    resolved = str(openssh_cfg.get("hostname") or host_alias).strip()
    try:
        ipaddress.ip_address(resolved)
        return resolved
    except ValueError:
        pass
    try:
        return socket.gethostbyname(resolved)
    except OSError:
        return None


def infer_payload_download_host_for_node(node: Dict[str, Any]) -> Optional[str]:
    """若训练节点与平台在同一 /24 子网，自动选用内网下载地址（显著快于公网）。"""
    node_ip = _resolve_node_host_ip(node)
    if not node_ip:
        return None
    try:
        nip = ipaddress.ip_address(node_ip)
    except ValueError:
        return None
    if nip.version != 4:
        return None
    port = _media_listen_port()
    node_net = ipaddress.ip_network(f"{nip}/24", strict=False)
    for pip in _linux_global_ipv4s():
        try:
            paddr = ipaddress.ip_address(pip)
        except ValueError:
            continue
        if paddr in node_net:
            return f"http://{pip}:{port}"
    return None


def _remote_agent_running_check_cmd(remote_job_dir: str, agent_path: str) -> str:
    job_q = _shell_quote(remote_job_dir)
    agent_q = _shell_quote(agent_path)
    return (
        f"pgrep -f {agent_q} >/dev/null 2>&1 && "
        f"pgrep -af {agent_q} | grep -F {job_q} >/dev/null 2>&1 && echo RUNNING || echo IDLE"
    )


def build_remote_agent_start_command(
    *,
    remote_job_dir: str,
    python_path: str,
    agent_path: str,
    extra_args: str = "",
) -> str:
    """setsid + stdin 脱离 SSH channel，避免 nohup 阻塞导致 60s 超时误报失败。"""
    agent_cmd = (
        f"setsid {_shell_quote(python_path)} {_shell_quote(agent_path)} "
        f"{extra_args.strip()} "
        f">> train.log 2>&1 < /dev/null & echo $! > train.pid"
    )
    return f"cd {_shell_quote(remote_job_dir)} && {agent_cmd}"


def _start_remote_agent_worker(
    client,
    *,
    remote_job_dir: str,
    python_path: str,
    agent_path: str,
    extra_args: str,
) -> Tuple[bool, str]:
    from labelu.internal.common.edge_ssh_client import EdgeSshError, ssh_run

    check_cmd = _remote_agent_running_check_cmd(remote_job_dir, agent_path)
    _, out, _ = ssh_run(client, check_cmd, timeout=15)
    if "RUNNING" in (out or ""):
        return True, "Agent 已在运行"

    start_cmd = build_remote_agent_start_command(
        remote_job_dir=remote_job_dir,
        python_path=python_path,
        agent_path=agent_path,
        extra_args=extra_args,
    )
    try:
        code, out, err = ssh_run(client, start_cmd, timeout=20)
        if code == 0:
            return True, "已启动 Agent"
    except EdgeSshError:
        pass

    _, out2, err2 = ssh_run(client, check_cmd, timeout=15)
    if "RUNNING" in (out2 or ""):
        return True, "Agent 已启动（SSH 会话已返回）"
    detail = (err2 or out2 or err or out or "未知错误")[:300]
    return False, f"启动 Agent 失败: {detail}"


def _read_remote_status(job: YoloTrainJob) -> Dict[str, Any]:
    raw = getattr(job, "remote_status_json", None) or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_remote_status(db, job: YoloTrainJob, patch: Dict[str, Any]) -> None:
    cur = _read_remote_status(job)
    cur.update(patch)
    job.remote_status_json = json.dumps(cur, ensure_ascii=False)
    db.flush()


def _remote_owner(job_id: int) -> str:
    return f"remote-yolo-{os.getpid()}-{int(job_id)}-{uuid.uuid4().hex[:12]}"


def _remote_operation_id(token: ExecutionLeaseToken | None) -> str:
    if token is None:
        return ""
    return f"{token.owner}:{token.fencing_token}"


def _remote_spec_sha256(job_id: int) -> str:
    path = _job_work_root(job_id) / "remote_staging" / "job_spec.json"
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _remote_hmac_secret() -> str:
    return str(getattr(settings, "YOLO_REMOTE_TRAIN_HMAC_SECRET", "") or "").strip()


def _remote_hmac_required() -> bool:
    env = str(getattr(settings, "APP_ENV", "development") or "").strip().lower()
    # 生产安全模块同时接受 prod/production；远程训练必须使用同一判定，
    # 避免线上以 APP_ENV=prod 启动时绕过无密钥 fail-closed。
    return bool(getattr(settings, "YOLO_REMOTE_TRAIN_HMAC_REQUIRED", False)) or env in {
        "prod",
        "production",
    }


def _assert_remote_hmac_ready() -> None:
    if _remote_hmac_required() and not _remote_hmac_secret():
        raise RuntimeError(
            "生产远程训练未配置 YOLO_REMOTE_TRAIN_HMAC_SECRET，已拒绝派发"
        )


def _sign_remote_job_spec(path: Path) -> None:
    secret = _remote_hmac_secret()
    if secret:
        sign_json_file(
            path,
            secret=secret,
            key_id=str(getattr(settings, "YOLO_REMOTE_TRAIN_HMAC_KEY_ID", "v1") or "v1"),
        )
    elif _remote_hmac_required():
        raise RuntimeError("远程训练 HMAC 密钥缺失，已拒绝生成作业清单")


def _remote_hmac_metadata_matches(payload: Dict[str, Any]) -> bool:
    secret = _remote_hmac_secret()
    required = _remote_hmac_required()
    return verify_json_payload(
        payload,
        secret=secret,
        expected_key_id=(
            str(getattr(settings, "YOLO_REMOTE_TRAIN_HMAC_KEY_ID", "v1") or "v1")
            if secret
            else ""
        ),
        required=required or bool(secret),
    )


def _remote_best_signature_matches(
    best_path: Path,
    sidecar_path: Path,
    *,
    expected_operation_id: str = "",
    expected_spec_sha256: str = "",
) -> bool:
    secret = _remote_hmac_secret()
    required = _remote_hmac_required()
    if not sidecar_path.is_file():
        return not required and not secret
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(sidecar, dict):
        return False
    if expected_operation_id and str(sidecar.get("operation_id") or "").strip() != expected_operation_id:
        return False
    if expected_spec_sha256 and str(sidecar.get("spec_sha256") or "").strip() != expected_spec_sha256:
        return False
    key_id = str(getattr(settings, "YOLO_REMOTE_TRAIN_HMAC_KEY_ID", "v1") or "v1")
    if secret and str(sidecar.get(HMAC_KEY_ID_FIELD) or "").strip() != key_id:
        return False
    try:
        content = best_path.read_bytes()
    except OSError:
        return False
    return verify_bytes_signature(
        content,
        str(sidecar.get(HMAC_SIGNATURE_FIELD) or ""),
        secret=secret,
        required=required or bool(secret),
    )


def _remote_expected_artifact_metadata(job_id: int) -> Dict[str, str]:
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        status = _read_remote_status(job) if job is not None else {}
    return {
        "operation_id": str(status.get("operation_id") or "").strip(),
        "spec_sha256": str(status.get("spec_sha256") or "").strip(),
    }


def _remote_artifact_matches(
    job_id: int,
    token: ExecutionLeaseToken | None,
    artifact: Dict[str, Any],
) -> bool:
    if not _remote_hmac_metadata_matches(artifact):
        return False
    if token is None:
        return True
    expected = _remote_expected_artifact_metadata(job_id)
    expected_operation_id = expected["operation_id"] or _remote_operation_id(token)
    reported_operation_id = str(artifact.get("operation_id") or "").strip()
    if not reported_operation_id or reported_operation_id != expected_operation_id:
        return False
    expected_spec_sha256 = expected["spec_sha256"]
    if expected_spec_sha256 and str(artifact.get("spec_sha256") or "").strip() != expected_spec_sha256:
        return False
    return True


def _remote_lease_seconds() -> int:
    return max(
        60,
        int(
            getattr(
                settings,
                "YOLO_TRAIN_DEDICATED_WORKER_LEASE_SECONDS",
                900,
            )
            or 900
        ),
    )


def _claim_remote_execution(job_id: int) -> ExecutionLeaseToken:
    """远程训练进入派发前领取执行租约；字段不可用时必须失败关闭。"""
    owner = _remote_owner(job_id)
    with session_scope() as db:
        try:
            token = claim_execution_lease(
                db,
                YoloTrainJob,
                int(job_id),
                owner=owner,
                lease_seconds=_remote_lease_seconds(),
            )
        except ExecutionFencingUnavailable as exc:
            raise RuntimeError("远程训练执行租约字段不可用，已拒绝派发") from exc
        if token is None:
            raise RuntimeError("远程训练作业已被其他执行器占用")
    with _REMOTE_TOKEN_LOCK:
        _REMOTE_EXECUTION_TOKENS[int(job_id)] = token
    return token


def _remember_remote_execution(job_id: int, token: ExecutionLeaseToken | None) -> None:
    if token is None:
        return
    with _REMOTE_TOKEN_LOCK:
        _REMOTE_EXECUTION_TOKENS[int(job_id)] = token


def _forget_remote_execution(job_id: int, token: ExecutionLeaseToken | None = None) -> None:
    with _REMOTE_TOKEN_LOCK:
        current = _REMOTE_EXECUTION_TOKENS.get(int(job_id))
        if token is None or current is token or (
            current is not None
            and token is not None
            and current.fencing_token == token.fencing_token
            and current.owner == token.owner
        ):
            _REMOTE_EXECUTION_TOKENS.pop(int(job_id), None)


def _release_remote_execution(job_id: int, token: ExecutionLeaseToken | None) -> None:
    if token is None:
        return
    try:
        with session_scope() as db:
            release_execution_lease(db, token)
    finally:
        _forget_remote_execution(job_id, token)


def _renew_remote_execution(job_id: int, token: ExecutionLeaseToken) -> ExecutionLeaseToken:
    with session_scope() as db:
        renewed = renew_execution_lease(
            db, token, lease_seconds=_remote_lease_seconds()
        )
    if renewed is None:
        raise ExecutionFencingLost(f"远程训练作业 {job_id} 执行租约已失效")
    _remember_remote_execution(job_id, renewed)
    return renewed


def _assert_remote_execution_current(job_id: int, token: ExecutionLeaseToken | None) -> None:
    if token is None:
        return
    with session_scope() as db:
        if not execution_lease_is_current(db, token):
            raise ExecutionFencingLost(f"远程训练作业 {job_id} 执行租约已失效")


def _conditional_remote_update(
    job_id: int,
    token: ExecutionLeaseToken | None,
    values: Dict[str, Any],
) -> bool:
    if token is None:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                return False
            with begin_transaction(db):
                for key, value in values.items():
                    setattr(job, key, value)
                db.flush()
            return True
    with session_scope() as db:
        return conditional_execution_update(db, token, values)


def _persist_remote_status_patch(
    job_id: int,
    patch: Dict[str, Any],
    execution_token: ExecutionLeaseToken | None = None,
) -> bool:
    """后台线程条件更新 remote_status_json，拒绝旧 owner/token。"""
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job:
            return False
        cur = _read_remote_status(job)
        cur.update(patch)
        payload = json.dumps(cur, ensure_ascii=False)
        if execution_token is None:
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db=db, job_id=job_id)
                if not job:
                    return False
                _write_remote_status(db, job, patch)
            return True
        return conditional_execution_update(
            db,
            execution_token,
            {YoloTrainJob.remote_status_json.key: payload},
        )


def _make_upload_progress_reporter(
    job_id: int,
    total_bytes: int,
    execution_token: ExecutionLeaseToken | None = None,
):
    import time

    from labelu.internal.application.service.yolo_train import _append_train_process_log

    state = {"last_t": 0.0, "last_pct": -1, "last_log_pct": -1}

    def _report(sent: int, total: int) -> None:
        nonlocal execution_token
        tot = int(total or total_bytes or 0)
        if tot <= 0:
            return
        sent_i = min(int(sent), tot)
        pct = min(100, int(sent_i * 100 / tot))
        now = time.monotonic()
        if pct < 100 and pct < state["last_pct"] + 1 and now - state["last_t"] < 2.0:
            return
        state["last_t"] = now
        state["last_pct"] = pct
        if execution_token is not None:
            try:
                execution_token = _renew_remote_execution(job_id, execution_token)
            except ExecutionFencingLost:
                return
        _persist_remote_status_patch(
            job_id,
            {
                "phase": "uploading",
                "upload_bytes": sent_i,
                "upload_total_bytes": tot,
                "upload_pct": pct,
                "updated_at": _iso_now(),
            },
            execution_token,
        )
        if pct >= state["last_log_pct"] + 5 or pct == 100:
            state["last_log_pct"] = pct
            _append_train_process_log(
                job_id,
                f"remote_upload {pct}% ({sent_i}/{tot} bytes)",
            )

    return _report


def prepare_local_dataset_for_job(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    execution_token: ExecutionLeaseToken | None = None,
) -> Dict[str, Any]:
    """在平台导出 YOLO 数据集（与本地训练相同逻辑）。"""
    from labelu.internal.application.service.yolo_dataset import (
        DATASET_VALIDATION_STRICT,
        RUN_DATASET_VALIDATION_AFTER_EXPORT,
        prepare_yolo_dataset,
        yolo_job_reports_dir,
    )
    from labelu.internal.application.service.yolo_dataset_validation import (
        validate_exported_yolo_dataset,
    )
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _job_work_root,
        _merge_tasks_summary_payload,
        _write_train_phase,
    )

    dataset_root = _job_work_root(job_id) / "dataset"
    _assert_remote_execution_current(job_id, execution_token)
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job:
            raise RuntimeError(f"训练作业不存在: {job_id}")
        owner = crud_user.get(db, job.created_by) if job.created_by else None
        if not owner:
            raise RuntimeError("训练作业缺少创建者")

        source_ids = parse_source_task_ids(job)
        _write_train_phase(job_id, "preparing_dataset", detail="remote: platform export")
        _task, task_config, samples, per_task_counts = load_merged_training_context(
            db,
            primary_task_id=task_id,
            source_task_ids=source_ids,
            current_user=owner,
        )
        train_n, val_n, test_n, classes, per_task_exported = prepare_yolo_dataset(
            task_config=task_config,
            samples=samples,
            media_root=Path(settings.MEDIA_ROOT),
            dataset_root=dataset_root,
            val_ratio=settings.YOLO_TRAIN_VAL_RATIO,
            test_ratio=float(settings.YOLO_TRAIN_TEST_RATIO),
            seed=settings.YOLO_TRAIN_SPLIT_SEED,
        )
        if RUN_DATASET_VALIDATION_AFTER_EXPORT:
            validate_exported_yolo_dataset(
                dataset_root,
                strict=DATASET_VALIDATION_STRICT,
                write_reports=True,
                reports_dir=yolo_job_reports_dir(dataset_root),
            )
        partial_summary = json.dumps(
            {
                "dataset": {"train": train_n, "val": val_n, "test": test_n},
                "merge_tasks": _merge_tasks_summary_payload(
                    source_ids,
                    per_task_counts,
                    per_task_exported,
                ),
            },
            ensure_ascii=False,
        )
        if execution_token is None:
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db=db, job_id=job_id)
                if job:
                    crud_yolo_train_job.update_status(
                        db,
                        job,
                        job.status,
                        train_sample_count=train_n,
                        val_sample_count=val_n,
                        metrics_summary=partial_summary,
                    )
        elif not conditional_execution_update(
            db,
            execution_token,
            {
                YoloTrainJob.train_sample_count.key: train_n,
                YoloTrainJob.val_sample_count.key: val_n,
                YoloTrainJob.metrics_summary.key: partial_summary,
            },
        ):
            raise ExecutionFencingLost(f"远程训练作业 {job_id} 数据集准备结果已失租")
        _assert_remote_execution_current(job_id, execution_token)
        _write_train_phase(
            job_id,
            "dataset_ready",
            detail=f"remote train={train_n} val={val_n} test={test_n}",
        )
        _append_train_process_log(
            job_id,
            f"remote_dataset_ready train={train_n} val={val_n} test={test_n}",
        )
        return {
            "train_n": train_n,
            "val_n": val_n,
            "test_n": test_n,
            "classes": classes,
            "source_ids": source_ids,
        }


def _resolve_weights_for_payload(job_id: int, cmd: CreateYoloTrainJobCommand) -> Optional[Path]:
    from labelu.internal.application.service.yolo_train import resolve_initial_weights_for_training

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job or not job.created_by:
            return None
        try:
            path = resolve_initial_weights_for_training(
                db,
                int(job.created_by),
                cmd.model_weights or job.model_weights or "yolo11n.pt",
            )
            p = Path(path)
            return p if p.is_file() else None
        except Exception:
            return None


def _build_job_payload_archive(
    job_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    node: Dict[str, Any],
    execution_token: ExecutionLeaseToken | None = None,
) -> Path:
    work = _job_work_root(job_id)
    staging = work / "remote_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    dataset_src = work / "dataset"
    if not dataset_src.is_dir():
        raise FileNotFoundError("dataset 未就绪")
    shutil.copytree(dataset_src, staging / "dataset")

    from labelu.internal.application.service.yolo_train import _job_hyperparams_dict

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        hp = _job_hyperparams_dict(job) if job else {}

    spec: Dict[str, Any] = {
        "version": 1,
        "job_id": job_id,
        "model_weights": cmd.model_weights,
        "epochs": int(cmd.epochs),
        "imgsz": int(cmd.imgsz),
        "hyperparams": hp,
        "cuda_devices": str(node.get("cuda_devices") or "0"),
        "weights_cache_dir": str(node.get("weights_cache_dir") or ""),
        "created_at": _iso_now(),
        "operation_id": (
            f"{execution_token.owner}:{execution_token.fencing_token}"
            if execution_token is not None
            else ""
        ),
    }
    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if job:
            spec["task_id"] = int(job.task_id)
            spec["class_contract"] = {
                "class_names": json.loads(job.class_names_json or "[]"),
                "label_set_signature": job.label_set_signature,
                "class_order_signature": job.class_order_signature,
                "data_yaml_sha256": job.data_yaml_sha256,
                "dataset_content_signature": job.dataset_content_signature,
                "training_recipe": {
                    "key": job.training_recipe_key,
                    "version": job.training_recipe_version,
                },
                "runtime_profile": {
                    "key": job.runtime_profile,
                    "interface_version": job.runtime_interface_version,
                },
            }
            if job.dataset_manifest_json:
                (staging / "dataset_manifest.json").write_text(
                    job.dataset_manifest_json,
                    encoding="utf-8",
                )
            (staging / "class_contract.json").write_text(
                json.dumps(spec["class_contract"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    (staging / "job_spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 签名必须在最终清单写完后生成，随后 SHA256 才代表已签名内容。
    _sign_remote_job_spec(staging / "job_spec.json")

    weights = _resolve_weights_for_payload(job_id, cmd)
    if weights and weights.is_file():
        wdir = staging / "weights"
        wdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(weights, wdir / weights.name)

    bundle = work / (
        "remote_job_payload.tar.gz"
        if settings.YOLO_REMOTE_TRAIN_PAYLOAD_COMPRESS
        else "remote_job_payload.tar"
    )
    if bundle.is_file():
        bundle.unlink()
    tar_mode = "w:gz" if settings.YOLO_REMOTE_TRAIN_PAYLOAD_COMPRESS else "w"
    with tarfile.open(bundle, tar_mode) as tar:
        for fp in staging.rglob("*"):
            if fp.is_file():
                tar.add(fp, arcname=fp.relative_to(staging).as_posix())
    return bundle


def resolve_remote_payload_archive(job_id: int) -> Optional[Path]:
    """平台侧 remote payload（供节点 HTTP 拉取）。"""
    root = _job_work_root(job_id)
    for name in ("remote_job_payload.tar", "remote_job_payload.tar.gz"):
        path = root / name
        if path.is_file():
            return path
    return None


def payload_download_base_url(*, node: Optional[Dict[str, Any]] = None) -> str:
    if node and str(node.get("payload_download_host") or "").strip():
        return str(node["payload_download_host"]).strip().rstrip("/")
    if node:
        inferred = infer_payload_download_host_for_node(node)
        if inferred:
            return inferred.rstrip("/")
    override = str(settings.YOLO_REMOTE_TRAIN_PAYLOAD_DOWNLOAD_HOST or "").strip()
    if override:
        return override.rstrip("/")
    base = str(settings.MEDIA_HOST or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("MEDIA_HOST 未配置，远程节点无法拉取训练数据")
    return base


def should_use_http_pull_for_node(node: Dict[str, Any]) -> bool:
    """
    有内网/显式 payload 下载地址时用 HTTP 拉取；否则 SFTP 经 SSH 推送。
    gpu-54-99 等无法访问平台内网 :10000 的节点会自动走 SFTP，避免公网 25KB/s 拉取。
    """
    if node.get("pull_payload") is False:
        return False
    if node.get("pull_payload") is True:
        return True
    if str(node.get("payload_download_host") or "").strip():
        return True
    if infer_payload_download_host_for_node(node):
        return True
    if not settings.YOLO_REMOTE_TRAIN_PULL_PAYLOAD:
        return False
    return False


def build_remote_payload_download_url(
    job_id: int,
    token: str,
    *,
    node: Optional[Dict[str, Any]] = None,
) -> str:
    base = payload_download_base_url(node=node)
    return (
        f"{base}/api/v1/yolo-train/remote-jobs/{int(job_id)}/payload"
        f"?token={quote(token, safe='')}"
    )


def nginx_accel_redirect_path(job_id: int, archive_path: Path) -> str:
    return (
        f"/internal/yolo-remote-payload/job-{int(job_id)}/"
        f"{archive_path.name}"
    )


def _platform_agent_worker_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "remote_train"
        / "agent_worker.py"
    )


def _sync_agent_worker(client, node: Dict[str, Any]) -> None:
    """同步节点 Agent 脚本（体积小，走 SFTP）。"""
    from labelu.internal.common.edge_ssh_client import sftp_upload

    local_agent = _platform_agent_worker_path()
    remote_agent = str(node.get("agent_path") or "").strip()
    if local_agent.is_file() and remote_agent:
        sftp_upload(client, local_agent, remote_agent)


def _prepare_remote_hmac_key(client, remote_job_dir: str) -> str:
    """把共享密钥以 600 权限短暂放到节点作业目录，不进入 payload 或启动参数。"""
    secret = _remote_hmac_secret()
    if not secret:
        _assert_remote_hmac_ready()
        return ""
    from labelu.internal.common.edge_ssh_client import sftp_upload, ssh_run

    remote_path = f"{remote_job_dir}/.hmac-key"
    fd, temp_name = tempfile.mkstemp(prefix="labelu-remote-hmac-")
    os.close(fd)
    local_path = Path(temp_name)
    try:
        local_path.write_text(secret, encoding="utf-8")
        sftp_upload(client, local_path, remote_path)
    finally:
        try:
            local_path.unlink(missing_ok=True)
        except OSError:
            pass
    code, out, err = ssh_run(client, f"chmod 600 {_shell_quote(remote_path)}", timeout=15)
    if code != 0:
        raise RuntimeError(f"远程 HMAC 密钥文件权限设置失败: {(err or out)[:300]}")
    return remote_path


def _pull_and_start_remote_job(
    job_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    node: Dict[str, Any],
    execution_token: ExecutionLeaseToken | None = None,
) -> Dict[str, Any]:
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _write_train_phase,
    )
    from labelu.internal.common.edge_ssh_client import (
        ssh_connect_for_remote_train_node,
        ssh_mkdir_p,
    )
    from labelu.internal.common.remote_train_payload_token import (
        create_remote_payload_token,
    )

    node_id = str(node.get("id") or "")
    _assert_remote_execution_current(job_id, execution_token)
    remote_job_dir = _remote_job_dir(node, job_id)
    if execution_token is None:
        bundle = _build_job_payload_archive(job_id, cmd, node=node)
    else:
        bundle = _build_job_payload_archive(
            job_id, cmd, node=node, execution_token=execution_token
        )
    total_bytes = int(bundle.stat().st_size)
    spec_sha256 = _remote_spec_sha256(job_id)
    token = create_remote_payload_token(job_id)
    payload_url = build_remote_payload_download_url(job_id, token, node=node)
    parallel = max(0, int(settings.YOLO_REMOTE_TRAIN_PAYLOAD_PARALLEL_WORKERS or 0))
    payload_filename = bundle.name

    _persist_remote_status_patch(
        job_id,
        {
            "phase": "downloading",
            "node_id": node_id,
            "host": str(node.get("host") or ""),
            "upload_bytes": 0,
            "upload_total_bytes": total_bytes,
            "upload_pct": 0,
            "transfer_mode": "pull",
            "spec_sha256": spec_sha256,
            "updated_at": _iso_now(),
        },
        execution_token,
    )
    download_host = payload_download_base_url(node=node)
    _append_train_process_log(
        job_id,
        f"remote_pull_start total_bytes={total_bytes} download_host={download_host} parallel={parallel}",
    )

    client = None
    try:
        client = ssh_connect_for_remote_train_node(node, timeout=30)
        ssh_mkdir_p(client, remote_job_dir)
        _sync_agent_worker(client, node)
        hmac_key_path = _prepare_remote_hmac_key(client, remote_job_dir)

        agent_path = str(node.get("agent_path") or "").strip()
        python_path = str(node.get("python_path") or "").strip()
        remote_phase = "downloading"
        remote_message = "已通知远程节点拉取训练数据包"
        if agent_path and python_path:
            extra_args = (
                f"--job-dir {_shell_quote(remote_job_dir)} "
                f"--payload-url {_shell_quote(payload_url)} "
                f"--payload-filename {_shell_quote(payload_filename)} "
                f"--operation-id {_shell_quote(_remote_operation_id(execution_token))} "
                f"--spec-sha256 {_shell_quote(spec_sha256)} "
                f"--payload-total-bytes {total_bytes} "
                f"--payload-parallel {parallel}"
            )
            if hmac_key_path:
                extra_args += (
                    f" --hmac-key-file {_shell_quote(hmac_key_path)}"
                    f" --hmac-key-id {_shell_quote(str(getattr(settings, 'YOLO_REMOTE_TRAIN_HMAC_KEY_ID', 'v1') or 'v1'))}"
                )
            if _remote_hmac_required():
                extra_args += " --hmac-required"
            started, start_msg = _start_remote_agent_worker(
                client,
                remote_job_dir=remote_job_dir,
                python_path=python_path,
                agent_path=agent_path,
                extra_args=extra_args,
            )
            if started:
                remote_phase = "downloading"
                remote_message = start_msg or "远程节点正在拉取训练数据包"
                _write_train_phase(job_id, "running", detail=f"remote pull node={node_id}")
                schedule_remote_train_poll(job_id, node_id, execution_token)
            else:
                remote_phase = "start_failed"
                remote_message = start_msg
        else:
            remote_message = "数据包已就绪；待节点安装 agent_worker 后手动启动拉取"

        _append_train_process_log(
            job_id, f"remote_{remote_phase} node={node_id} {remote_message}"
        )
        return {
            "phase": remote_phase,
            "message": remote_message,
            "remote_job_dir": remote_job_dir,
            "node_id": node_id,
            "host": str(node.get("host") or ""),
            "transfer_mode": "pull",
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _upload_and_start_remote_job(
    job_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    node: Dict[str, Any],
    execution_token: ExecutionLeaseToken | None = None,
) -> Dict[str, Any]:
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _write_train_phase,
    )
    from labelu.internal.common.edge_ssh_client import (
        EdgeSshError,
        ssh_connect_for_remote_train_node,
        ssh_mkdir_p,
        ssh_run,
        sftp_upload,
    )

    node_id = str(node.get("id") or "")
    _assert_remote_execution_current(job_id, execution_token)
    remote_base = str(node.get("remote_base") or "").rstrip("/")
    remote_job_dir = f"{remote_base}/job-{job_id}"
    if execution_token is None:
        bundle = _build_job_payload_archive(job_id, cmd, node=node)
    else:
        bundle = _build_job_payload_archive(
            job_id, cmd, node=node, execution_token=execution_token
        )
    total_bytes = int(bundle.stat().st_size)
    spec_sha256 = _remote_spec_sha256(job_id)
    _persist_remote_status_patch(
        job_id,
        {
            "phase": "uploading",
            "node_id": node_id,
            "host": str(node.get("host") or ""),
            "upload_bytes": 0,
            "upload_total_bytes": total_bytes,
            "upload_pct": 0,
            "spec_sha256": spec_sha256,
            "updated_at": _iso_now(),
        },
        execution_token,
    )
    _append_train_process_log(
        job_id,
        f"remote_upload_start total_bytes={total_bytes} remote={remote_job_dir}",
    )

    client = None
    try:
        client = ssh_connect_for_remote_train_node(node, timeout=30)
        ssh_mkdir_p(client, remote_job_dir)
        hmac_key_path = _prepare_remote_hmac_key(client, remote_job_dir)
        remote_tar = f"{remote_job_dir}/job_payload.tar.gz"
        sftp_upload(
            client,
            bundle,
            remote_tar,
            progress_callback=_make_upload_progress_reporter(
                job_id, total_bytes, execution_token
            ),
        )
        _persist_remote_status_patch(
            job_id,
            {
                "phase": "uploading",
                "upload_bytes": total_bytes,
                "upload_total_bytes": total_bytes,
                "upload_pct": 100,
                "updated_at": _iso_now(),
            },
            execution_token,
        )
        _assert_remote_execution_current(job_id, execution_token)
        _append_train_process_log(job_id, "remote_upload 100% extract_on_remote")
        code, out, err = ssh_run(
            client,
            f"cd {_shell_quote(remote_job_dir)} && tar -xzf job_payload.tar.gz && rm -f job_payload.tar.gz",
            timeout=600,
        )
        if code != 0:
            raise EdgeSshError(f"远端解压失败: {(err or out)[:500]}")

        agent_path = str(node.get("agent_path") or "").strip()
        python_path = str(node.get("python_path") or "").strip()
        remote_phase = "uploaded"
        remote_message = "数据已上传至远程节点"
        if agent_path and python_path:
            agent_args = (
                f"--job-dir {_shell_quote(remote_job_dir)} "
                f"--operation-id {_shell_quote(_remote_operation_id(execution_token))} "
                f"--spec-sha256 {_shell_quote(spec_sha256)}"
            )
            if hmac_key_path:
                agent_args += (
                    f" --hmac-key-file {_shell_quote(hmac_key_path)}"
                    f" --hmac-key-id {_shell_quote(str(getattr(settings, 'YOLO_REMOTE_TRAIN_HMAC_KEY_ID', 'v1') or 'v1'))}"
                )
            if _remote_hmac_required():
                agent_args += " --hmac-required"
            started, start_msg = _start_remote_agent_worker(
                client,
                remote_job_dir=remote_job_dir,
                python_path=python_path,
                agent_path=agent_path,
                extra_args=agent_args,
            )
            if started:
                remote_phase = "remote_running"
                remote_message = start_msg or "已在远程节点启动训练 Agent"
                _write_train_phase(job_id, "running", detail=f"remote node={node_id}")
            else:
                remote_phase = "start_failed"
                remote_message = start_msg
        else:
            remote_message = "数据已上传；待节点安装 agent_worker 后手动或自动启动"

        _append_train_process_log(job_id, f"remote_{remote_phase} node={node_id} {remote_message}")
        return {
            "phase": remote_phase,
            "message": remote_message,
            "remote_job_dir": remote_job_dir,
            "node_id": node_id,
            "host": str(node.get("host") or ""),
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _dispatch_remote_train_job(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
) -> None:
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _write_train_phase,
    )

    node_id = (cmd.train_node_id or "").strip()
    execution_token: ExecutionLeaseToken | None = None
    lease_handed_to_poll = False
    try:
        _assert_remote_hmac_ready()
        execution_token = _claim_remote_execution(job_id)
        node = resolve_node_for_train(node_id)
        if not _conditional_remote_update(
            job_id,
            execution_token,
            {
                YoloTrainJob.status.key: YoloTrainStatus.PROCESSING.value,
                YoloTrainJob.error_message.key: None,
            },
        ):
            raise ExecutionFencingLost(f"远程训练作业 {job_id} 启动时令牌已失效")
        _persist_remote_status_patch(
            job_id,
            {
                "phase": "preparing",
                "node_id": node_id,
                "host": node.get("host"),
                "operation_id": _remote_operation_id(execution_token),
                "updated_at": _iso_now(),
            },
            execution_token,
        )

        prepare_local_dataset_for_job(job_id, task_id, cmd, execution_token)

        use_pull = should_use_http_pull_for_node(node)
        transfer_phase = "downloading" if use_pull else "uploading"
        _persist_remote_status_patch(
            job_id,
            {
                "phase": transfer_phase,
                "transfer_mode": "pull" if use_pull else "push",
                "updated_at": _iso_now(),
            },
            execution_token,
        )

        if use_pull:
            result = _pull_and_start_remote_job(
                job_id, cmd, node=node, execution_token=execution_token
            )
        else:
            result = _upload_and_start_remote_job(
                job_id, cmd, node=node, execution_token=execution_token
            )

        status_val = YoloTrainStatus.PROCESSING.value
        values: Dict[str, Any] = {}
        if result.get("phase") == "start_failed":
            status_val = YoloTrainStatus.FAILED.value
            values[YoloTrainJob.error_message.key] = str(result.get("message") or "")[:500]
        elif result.get("phase") == "uploaded":
            status_val = YoloTrainStatus.PENDING.value
        values[YoloTrainJob.status.key] = status_val
        if not _conditional_remote_update(job_id, execution_token, values):
            raise ExecutionFencingLost(f"远程训练作业 {job_id} 派发结果已被接管")
        _persist_remote_status_patch(
            job_id,
            {**result, "updated_at": _iso_now()},
            execution_token,
        )

        if result.get("phase") in ("remote_running", "downloading"):
            schedule_remote_train_poll(job_id, node_id, execution_token)
            lease_handed_to_poll = True

    except Exception as exc:
        logger.exception("remote train dispatch failed job={}", job_id)
        fail_msg = str(exc)[:500]
        _append_train_process_log(job_id, f"remote_failed {fail_msg}")
        _write_train_phase(job_id, "failed", detail=fail_msg[:200])
        if execution_token is None:
            # 密钥缺失发生在领取租约前，仍需把用户可见作业明确置为失败，
            # 避免生产缺钥时作业永久停留在待处理状态。
            _conditional_remote_update(
                job_id,
                None,
                {
                    YoloTrainJob.status.key: YoloTrainStatus.FAILED.value,
                    YoloTrainJob.error_message.key: fail_msg,
                },
            )
        else:
            _conditional_remote_update(
                job_id,
                execution_token,
                {
                    YoloTrainJob.status.key: YoloTrainStatus.FAILED.value,
                    YoloTrainJob.error_message.key: fail_msg,
                },
            )
            _persist_remote_status_patch(
                job_id,
                {
                    "phase": "failed",
                    "message": fail_msg,
                    "updated_at": _iso_now(),
                },
                execution_token,
            )
    finally:
        if execution_token is not None and not lease_handed_to_poll:
            try:
                _release_remote_execution(job_id, execution_token)
            except Exception:
                logger.exception("release remote execution lease failed job={}", job_id)
        with _REMOTE_LOCK:
            _ACTIVE_REMOTE_JOBS.discard(int(job_id))


def request_remote_train_start(
    job_id: int,
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    resume: bool = False,
) -> None:
    if resume:
        logger.warning("remote train resume not implemented job={}", job_id)
    jid = int(job_id)
    with _REMOTE_LOCK:
        if jid in _ACTIVE_REMOTE_JOBS:
            logger.info("remote train job {} already dispatching", jid)
            return
        _ACTIVE_REMOTE_JOBS.add(jid)

    def _run() -> None:
        _dispatch_remote_train_job(job_id, task_id, cmd)

    threading.Thread(
        target=_run,
        name=f"remote-yolo-train-{job_id}",
        daemon=True,
    ).start()


_POLL_TIMERS: Dict[int, threading.Timer] = {}
_POLL_TOKENS: Dict[int, ExecutionLeaseToken] = {}


def schedule_remote_train_poll(
    job_id: int,
    node_id: str,
    execution_token: ExecutionLeaseToken | None = None,
) -> None:
    """轮询远程 phase.json / 日志并同步到平台（节点 Agent 就绪后生效）。"""
    stop_remote_train_poll(job_id)
    if execution_token is not None:
        _POLL_TOKENS[int(job_id)] = execution_token

    def _tick() -> None:
        try:
            _poll_remote_train_once(job_id, node_id, _POLL_TOKENS.get(int(job_id)))
        except ExecutionFencingLost:
            logger.warning("remote train execution lease lost; stop polling job={}", job_id)
            stop_remote_train_poll(job_id)
            return
        except Exception:
            logger.exception("remote train poll failed job={}", job_id)
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                stop_remote_train_poll(job_id)
                return
            if job.status in {
                YoloTrainStatus.COMPLETED.value,
                YoloTrainStatus.FAILED.value,
                YoloTrainStatus.CANCELLED.value,
            }:
                stop_remote_train_poll(job_id)
                return
        t = threading.Timer(5.0, _tick)
        _POLL_TIMERS[int(job_id)] = t
        t.daemon = True
        t.start()

    t = threading.Timer(3.0, _tick)
    _POLL_TIMERS[int(job_id)] = t
    t.daemon = True
    t.start()


def stop_remote_train_poll(job_id: int) -> None:
    t = _POLL_TIMERS.pop(int(job_id), None)
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass
    _POLL_TOKENS.pop(int(job_id), None)


def _remote_job_dir(node: Dict[str, Any], job_id: int) -> str:
    remote_base = str(node.get("remote_base") or "").rstrip("/")
    return f"{remote_base}/job-{job_id}"


def _ssh_client_for_node(node: Dict[str, Any]):
    from labelu.internal.common.edge_ssh_client import ssh_connect_for_remote_train_node

    return ssh_connect_for_remote_train_node(node, timeout=30)


def _sync_remote_train_artifacts(
    client,
    *,
    remote_job_dir: str,
    work: Path,
) -> None:
    from labelu.internal.common.edge_ssh_client import sftp_download, sftp_download_tree

    work.mkdir(parents=True, exist_ok=True)
    for rel in (
        "phase.json",
        "train.log",
        "agent_result.json",
        "train/results.csv",
        "train/weights/best.pt",
        "train/weights/best.pt.hmac.json",
        "train/weights/last.pt",
    ):
        sftp_download(client, f"{remote_job_dir}/{rel}", work / rel)
    for rel in REMOTE_TRAIN_ANALYSIS_ARTIFACTS:
        sftp_download(client, f"{remote_job_dir}/{rel}", work / rel)
    weights_dir = work / "train" / "weights"
    if not (weights_dir / "best.pt").is_file():
        sftp_download_tree(client, f"{remote_job_dir}/train/weights", weights_dir)


def _try_finalize_remote_train(
    job_id: int,
    node_id: str,
    execution_token: ExecutionLeaseToken | None = None,
) -> bool:
    """远端训练完成后拉回权重并在平台侧收尾（val/test、对比等）。"""
    from labelu.internal.application.service.yolo_train import (
        _append_train_process_log,
        _finalize_yolo_train_job_sync,
        _job_work_root,
        _merge_done_counts_from_job,
        _merge_exported_counts_from_job,
        _run_post_train_side_effects,
        _sample_counts_for_job,
        parse_source_task_ids,
    )
    from labelu.internal.application.service.yolo_train_queue import command_from_job

    node = get_node_by_id(node_id)
    if not node:
        return False
    _assert_remote_execution_current(job_id, execution_token)
    work = _job_work_root(job_id)
    remote_job_dir = _remote_job_dir(node, job_id)

    client = _ssh_client_for_node(node)
    try:
        _sync_remote_train_artifacts(client, remote_job_dir=remote_job_dir, work=work)
    finally:
        client.close()

    best_path = work / "train" / "weights" / "best.pt"
    agent_path = work / "agent_result.json"
    if not best_path.is_file():
        return False

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job or job.status == YoloTrainStatus.COMPLETED.value:
            return job is not None and job.status == YoloTrainStatus.COMPLETED.value

    agent_data: Dict[str, Any] = {}
    if agent_path.is_file():
        try:
            agent_data = json.loads(agent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            agent_data = {}
    if execution_token is not None:
        if not _remote_artifact_matches(job_id, execution_token, agent_data):
            _append_train_process_log(
                job_id,
                "remote_finalize: ignore stale, unsigned, or tampered agent_result.json",
            )
            return False
    if not _remote_best_signature_matches(
        best_path,
        work / "train" / "weights" / "best.pt.hmac.json",
        expected_operation_id=str(agent_data.get("operation_id") or "").strip(),
        expected_spec_sha256=str(agent_data.get("spec_sha256") or "").strip(),
    ):
        _append_train_process_log(
            job_id,
            "remote_finalize: ignore unsigned or tampered best.pt",
        )
        return False
    if str(agent_data.get("status") or "").lower() == "failed":
        err = str(agent_data.get("error_message") or "远程训练失败")[:500]
        _conditional_remote_update(
            job_id,
            execution_token,
            {
                YoloTrainJob.status.key: YoloTrainStatus.FAILED.value,
                YoloTrainJob.error_message.key: err,
            },
        )
        return True

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job:
            return False
        cmd = command_from_job(job)
        sc = _sample_counts_for_job(job)
        source_ids = parse_source_task_ids(job)
        per_task_counts = _merge_done_counts_from_job(job)
        per_task_exported = _merge_exported_counts_from_job(job)
        dataset_root = work / "dataset"
        classes: list = []
        if agent_data.get("metrics_summary") and isinstance(
            agent_data["metrics_summary"].get("classes"), list
        ):
            classes = list(agent_data["metrics_summary"]["classes"])

    _append_train_process_log(job_id, "remote_finalize: ingesting best.pt from remote node")
    try:
        with session_scope() as db:
            contract_job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if contract_job:
                _assert_remote_result_class_contract(
                    contract_job,
                    agent_data.get("class_contract"),
                )
        _finalize_yolo_train_job_sync(
            job_id=job_id,
            work_root=work,
            dataset_root=dataset_root,
            cmd=cmd,
            train_n=int(sc.get("train") or 0),
            val_n=int(sc.get("val") or 0),
            test_n=int(sc.get("test") or 0),
            classes=classes,
            source_ids=source_ids,
            per_task_counts=per_task_counts,
            per_task_exported=per_task_exported,
            results=None,
            execution_fencing_token=(
                execution_token.fencing_token if execution_token is not None else None
            ),
            execution_owner=(execution_token.owner if execution_token is not None else None),
        )
        _run_post_train_side_effects(job_id)
        return True
    except Exception as exc:
        logger.exception("remote finalize failed job={}", job_id)
        _conditional_remote_update(
            job_id,
            execution_token,
            {
                YoloTrainJob.status.key: YoloTrainStatus.FAILED.value,
                YoloTrainJob.error_message.key: str(exc)[:500],
            },
        )
        return True


def _assert_remote_result_class_contract(job, reported: Any) -> None:
    expected_order = str(getattr(job, "class_order_signature", None) or "").strip()
    if not expected_order:
        return
    try:
        expected_names = json.loads(getattr(job, "class_names_json", None) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        expected_names = []
    expected = {
        "class_names": expected_names,
        "label_set_signature": getattr(job, "label_set_signature", None),
        "class_order_signature": expected_order,
        "data_yaml_sha256": getattr(job, "data_yaml_sha256", None),
        "dataset_content_signature": getattr(job, "dataset_content_signature", None),
    }
    reported = reported if isinstance(reported, dict) else {}
    mismatches = [key for key, value in expected.items() if reported.get(key) != value]
    if mismatches:
        raise RuntimeError(
            "远程训练回传类别契约与平台冻结契约不一致: " + ", ".join(mismatches)
        )


def _poll_remote_train_once(
    job_id: int,
    node_id: str,
    execution_token: ExecutionLeaseToken | None = None,
) -> None:
    from labelu.internal.common.edge_ssh_client import sftp_download
    from labelu.internal.application.service.yolo_train import _append_train_process_log

    node = get_node_by_id(node_id)
    if not node:
        return
    if execution_token is not None:
        execution_token = _renew_remote_execution(job_id, execution_token)
        _POLL_TOKENS[int(job_id)] = execution_token
    _assert_remote_execution_current(job_id, execution_token)
    remote_job_dir = _remote_job_dir(node, job_id)
    work = _job_work_root(job_id)
    work.mkdir(parents=True, exist_ok=True)

    client = _ssh_client_for_node(node)
    phase: Optional[Dict[str, Any]] = None
    try:
        remote_phase_path = work / ".remote_phase.json.tmp"
        if sftp_download(client, f"{remote_job_dir}/phase.json", remote_phase_path):
            try:
                downloaded_phase = json.loads(remote_phase_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                downloaded_phase = None
            if isinstance(downloaded_phase, dict):
                if execution_token is not None:
                    if not _remote_artifact_matches(
                        job_id, execution_token, downloaded_phase
                    ):
                        _append_train_process_log(
                            job_id,
                            "remote_poll: ignore stale, unsigned, or tampered phase.json",
                        )
                        downloaded_phase = None
                if downloaded_phase is None:
                    try:
                        remote_phase_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return
                phase = downloaded_phase
                local_phase_path = work / "remote_phase.json"
                replace_tmp = work / ".remote_phase.replace.tmp"
                replace_tmp.write_text(
                    json.dumps(downloaded_phase, ensure_ascii=False),
                    encoding="utf-8",
                )
                replace_tmp.replace(local_phase_path)
            try:
                remote_phase_path.unlink(missing_ok=True)
            except OSError:
                pass
        sftp_download(client, f"{remote_job_dir}/train.log", work / "remote_train.log")
        sftp_download(client, f"{remote_job_dir}/train/results.csv", work / "train" / "results.csv")
        if isinstance(phase, dict):
            patch: Dict[str, Any] = {
                "remote_phase": phase.get("phase"),
                "remote_detail": phase.get("detail"),
                "updated_at": _iso_now(),
            }
            phase_name = str(phase.get("phase") or "").strip().lower()
            if phase_name == "downloading":
                patch["phase"] = "downloading"
                for src, dst in (
                    ("download_bytes", "upload_bytes"),
                    ("download_total_bytes", "upload_total_bytes"),
                    ("download_pct", "upload_pct"),
                ):
                    if phase.get(src) is not None:
                        patch[dst] = phase.get(src)
            _persist_remote_status_patch(job_id, patch, execution_token)
    finally:
        client.close()

    phase_name = str(phase.get("phase") or "").strip().lower() if isinstance(phase, dict) else ""
    if phase_name in ("completed", "finalizing"):
        finalized = _try_finalize_remote_train(job_id, node_id, execution_token)
        if finalized:
            _release_remote_execution(job_id, execution_token)
            stop_remote_train_poll(job_id)
    elif phase_name == "failed":
        err = str(phase.get("detail") or "远程训练失败")[:500]
        _conditional_remote_update(
            job_id,
            execution_token,
            {
                YoloTrainJob.status.key: YoloTrainStatus.FAILED.value,
                YoloTrainJob.error_message.key: err,
            },
        )
        _release_remote_execution(job_id, execution_token)
        stop_remote_train_poll(job_id)
