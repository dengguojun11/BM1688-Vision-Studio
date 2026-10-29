#!/usr/bin/env python3
"""
算能盒板端 Agent：从公网 LabelU 拉取 BM1688 验证任务，跑 sophon-demo 全套检测并回传结果。

环境变量：
  LABELU_BASE_URL   默认 http://113.141.72.175:10000
  BOARD_AGENT_TOKEN 与服务器 .env 中 BOARD_AGENT_TOKEN 一致
  SOPHON_DEMO_ROOT  默认 ~/sophon-work/sophon-demo/sample/YOLOv8_plus_det
  POLL_INTERVAL_SEC 默认 30
  BOARD_AGENT_HEARTBEAT_INTERVAL_SECONDS 驻留任务心跳间隔，默认 30（需小于服务端 TTL）
  RUN_COCO_EVAL     默认 0（1 时跑 §5 COCO，很慢）
  LABELU_EDGE_ROOT  边端任务工作根目录，默认 /data/labelu_edge
  LABELU_EDGE_EXECUTION_STATE_FILE 可选，指定跨重启执行世代状态文件
  LABELU_EDGE_EXECUTION_STATE_DIR  可选，指定状态目录（默认 /var/lib/labelu-edge）

用法：
  export BOARD_AGENT_TOKEN='你的随机长串'
  python3 scripts/ops/labelu_board_agent.py
  python3 scripts/ops/labelu_board_agent.py --once

边端 / Docker 内 python3 可能为 3.6，勿使用 from __future__ import annotations。
"""
import argparse
import calendar
import json
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
import base64
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE = "http://113.141.72.175:10000"
API_PREFIX = "/api/v1"


def _log(msg: str, *, err: bool = False) -> None:
    """systemd/journal 下须 flush，否则长时间无日志。"""
    stream = sys.stderr if err else sys.stdout
    print(msg, file=stream, flush=True)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _sanitize_url(url: str) -> str:
    """去掉 .env 脏字符（如 \\r）避免下载 URL 非法。"""
    return re.sub(r"[\x00-\x1f\x7f]", "", (url or "")).strip()


ProgressFn = Callable[[str, str, Optional[Dict[str, Any]]], None]


def _post_progress(
    progress_url: str,
    token: str,
    stage: str,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
    task_token: Optional[str] = None,
) -> Optional[str]:
    if not progress_url:
        return None
    try:
        response = _http_json(
            "POST",
            progress_url,
            token,
            body={"stage": stage, "message": message, "detail": detail or {}},
            timeout=30,
            task_token=task_token,
        )
        # Unified progress responses renew the execution lease and return a
        # replacement task token.  Legacy responses do not include one, so
        # retain the caller's current token in that case.
        if isinstance(response, dict):
            data = response.get("data")
            candidate = data.get("task_token") if isinstance(data, dict) else None
            if not candidate:
                candidate = response.get("task_token")
            refreshed = str(candidate or "").strip()
            return refreshed or None
    except Exception as exc:
        _log(f"[board-agent] 进度上报失败: {exc}", err=True)
    return None


def _http_json(
    method: str,
    url: str,
    token: str,
    body: Optional[dict] = None,
    timeout: int = 120,
    task_token: Optional[str] = None,
) -> Any:
    headers = {"X-Board-Agent-Token": token, "Accept": "application/json"}
    # Newer servers bind heartbeats and unified jobs to the registered device.
    # Keep the header derived from the local environment so every existing
    # request (including legacy job endpoints) carries the same identity.
    agent_id = _env("BOARD_AGENT_ID") or _env("BM1688_DEVICE_ID")
    if agent_id:
        headers["X-Board-Agent-Id"] = agent_id
    if task_token:
        headers["X-Board-Agent-Task-Token"] = str(task_token).strip()
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body[:2000]}") from e
    except URLError as e:
        raise RuntimeError(f"网络错误 {url}: {e}") from e
    if not raw:
        return None
    return json.loads(raw)


def _post_heartbeat(base: str, token: str, demo_root: Path) -> None:
    """Publish liveness before polling so a new device becomes schedulable."""

    agent_id = _env("BOARD_AGENT_ID") or _env("BM1688_DEVICE_ID")
    if not agent_id:
        # Older manually installed agents have no registered identity and can
        # still use the legacy compatibility path; do not make them crash.
        return
    api = API_PREFIX
    url = f"{base.rstrip('/')}{api}/board-agent/heartbeat"
    ready = bool(demo_root.is_dir())
    capabilities = {
        "protocol_version": "labelu_board_agent_heartbeat_v1",
        "device_id": agent_id,
        "sophon_demo_root": str(demo_root),
        "python": sys.version.split()[0],
    }
    _http_json(
        "POST",
        url,
        token,
        body={
            "environment_ready": ready,
            "capabilities": capabilities,
            "message": (
                "BM1688 运行环境已就绪"
                if ready
                else f"SOPHON_DEMO_ROOT 不存在: {demo_root}"
            ),
        },
        timeout=30,
    )


class _HeartbeatKeeper:
    """Keep a registered Board Agent alive while one job is executing.

    The normal polling heartbeat runs only before claiming a job.  A board
    validation can take several minutes, so the device would otherwise exceed
    the server's heartbeat TTL while it is still legitimately working.  This
    small daemon thread uses an Event for prompt, race-free shutdown and keeps
    heartbeat failures isolated from the task execution path.
    """

    DEFAULT_INTERVAL_SECONDS = 30

    def __init__(
        self,
        base: str,
        token: str,
        demo_root: Path,
        interval_seconds: Optional[float] = None,
    ) -> None:
        self._base = base
        self._token = token
        self._demo_root = demo_root
        if interval_seconds is None:
            # ``..._SEC`` remains accepted for compatibility with the early
            # rollout; the project-wide spelling is ``..._SECONDS``.
            raw = _env("BOARD_AGENT_HEARTBEAT_INTERVAL_SECONDS") or _env(
                "BOARD_AGENT_HEARTBEAT_INTERVAL_SEC",
                str(self.DEFAULT_INTERVAL_SECONDS),
            )
            try:
                interval_seconds = float(raw)
            except (TypeError, ValueError):
                interval_seconds = float(self.DEFAULT_INTERVAL_SECONDS)
        # Keep the interval positive and bounded so a bad environment value
        # cannot create a busy loop or silently exceed the 120s server TTL.
        self._interval_seconds = max(1.0, min(float(interval_seconds), 60.0))
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]

    def start(self) -> "_HeartbeatKeeper":
        # Legacy agents without a registered identity intentionally skip the
        # heartbeat endpoint and continue to use their compatibility channel.
        if not (_env("BOARD_AGENT_ID") or _env("BM1688_DEVICE_ID")):
            return self
        if self._thread is not None:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="labelu-board-agent-heartbeat",
        )
        self._thread.daemon = True
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # The request timeout is 30s, so do not hold task cleanup forever
            # if the heartbeat endpoint is unreachable during shutdown.
            thread.join(timeout=min(self._interval_seconds + 30.0, 60.0))
            if thread.is_alive():
                # Keep the reference while a pathological transport call is
                # still unwinding; a later start must not create a duplicate
                # heartbeat worker for the same device.
                return
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                _post_heartbeat(self._base, self._token, self._demo_root)
            except Exception as exc:
                # Liveness is best effort while a task is running.  A failed
                # pulse must not abort or mark the local validation as failed.
                _log(f"[board-agent] 驻留心跳上报失败: {exc}", err=True)


def _download_file(
    url: str,
    token: str,
    dest: Path,
    timeout: int = 600,
    task_token: Optional[str] = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"X-Board-Agent-Token": token}
    agent_id = _env("BOARD_AGENT_ID") or _env("BM1688_DEVICE_ID")
    if agent_id:
        headers["X-Board-Agent-Id"] = agent_id
    if task_token:
        headers["X-Board-Agent-Task-Token"] = str(task_token).strip()
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _parse_bmrt_stdout(text: str) -> Dict[str, Any]:
    """从 bmrt_test 输出提取理论推理耗时。"""
    blob = text or ""
    out: Dict[str, Any] = {}
    for pat, unit in (
        (r"calculate[_\s]*time[^0-9]*([0-9.]+)\s*ms", "ms"),
        (r"calculate[_\s]*time[^0-9]*([0-9.]+)\s*s", "s"),
        (r"Calculate\s+time[^:]*:\s*([0-9.]+)", "s"),
        (r"calculate\s+time\s*=\s*([0-9.]+)", "s"),
    ):
        m = re.search(pat, blob, re.I)
        if m:
            v = float(m.group(1))
            if unit == "ms":
                out["calculate_time_ms"] = v
                out["calculate_time_s"] = round(v / 1000.0, 6)
            else:
                out["calculate_time_s"] = v
                out["calculate_time_ms"] = round(v * 1000.0, 3)
            break
    return out


def _parse_infer_stdout(text: str) -> Dict[str, Any]:
    """从 yolov8_bmcv 输出提取各阶段每图平均耗时（ms）。"""
    blob = text or ""
    stage_metrics: Dict[str, Any] = {}
    for metric_key, log_key in (
        ("decode_ms_avg", "decode"),
        ("preprocess_ms_avg", "preprocess"),
        ("infer_ms_avg", "inference"),
        ("postprocess_ms_avg", "postprocess"),
    ):
        matches = re.findall(
            r"\b{}_time\s*\(\s*ms\s*\)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)".format(log_key),
            blob,
            re.I,
        )
        if matches:
            stage_metrics[metric_key] = round(float(matches[-1]), 3)
    stage_keys = (
        "decode_ms_avg",
        "preprocess_ms_avg",
        "infer_ms_avg",
        "postprocess_ms_avg",
    )
    if all(key in stage_metrics for key in stage_keys):
        stage_metrics["pipeline_ms_sum"] = round(
            sum(float(stage_metrics[key]) for key in stage_keys),
            3,
        )
    if stage_metrics:
        stage_metrics["timing_source"] = "sophon_demo_stage_summary"
        stage_metrics["timing_scope"] = "per_image_stage_average"
        return stage_metrics

    # 兼容少量旧日志仅输出 "12.3 ms" 的格式；这类值没有分段语义。
    nums = [float(x) for x in re.findall(r"([\d.]+)\s*ms", blob, re.I)]
    if not nums:
        return {}
    tail = nums[-20:]
    return {
        "infer_ms_avg": round(sum(tail) / len(tail), 3),
        "infer_ms_min": round(min(tail), 3),
        "infer_ms_max": round(max(tail), 3),
        "infer_ms_samples": len(tail),
    }


def _load_board_manifest(export_work: Path) -> Dict[str, Any]:
    mf = export_work / "board_eval" / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _attach_step_metrics(step: Dict[str, Any]) -> None:
    blob = (step.get("stdout") or "") + "\n" + (step.get("stderr") or "")
    name = step.get("step") or ""
    if name == "bmrt_test":
        step["metrics"] = _parse_bmrt_stdout(blob)
    elif name.startswith("infer") or _is_bmcv_detection_step(name):
        step["metrics"] = _parse_infer_stdout(blob)


def _is_bmcv_detection_step(name: Any) -> bool:
    return bool(re.match(r"^bmcv_(?:fp32|fp16|int8)_detection$", str(name or ""), re.I))


def _ordered_bmcv_bmodels(bmodels: List[str]) -> List[Tuple[str, str]]:
    ordered: List[Tuple[str, str]] = []
    for precision in ("fp32", "fp16", "int8"):
        for name in bmodels:
            if precision in str(name).lower():
                ordered.append((precision, name))
    return ordered


def _build_report_metrics(report: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    bmrt_rows: List[Dict[str, Any]] = []
    infer_rows: List[Dict[str, Any]] = []
    for s in report.get("steps") or []:
        if not isinstance(s, dict):
            continue
        m = s.get("metrics") or {}
        if s.get("step") == "bmrt_test":
            bmrt_rows.append(
                {
                    "bmodel": s.get("bmodel"),
                    "ok": s.get("returncode") == 0,
                    **m,
                }
            )
        elif str(s.get("step") or "").startswith("infer") or _is_bmcv_detection_step(
            s.get("step")
        ):
            infer_rows.append(
                {
                    "tag": s.get("step"),
                    "bmodel": s.get("bmodel"),
                    "ok": s.get("returncode") == 0,
                    **m,
                }
            )
    eval_src = report.get("eval_image_source") or ""
    eval_count = 0
    for s in report.get("steps") or []:
        if s.get("step") == "board_eval_images" and s.get("returncode") == 0:
            txt = s.get("stdout") or ""
            m = re.search(r"(\d+)\s*张", txt)
            if m:
                eval_count = int(m.group(1))
    bmcv_det = {}
    bmcv_preview = {}
    bmcv_block = report.get("bmcv_infer") or {}
    if isinstance(bmcv_block, dict):
        ds = bmcv_block.get("detection_summary") or {}
        if isinstance(ds, dict):
            bmcv_det = {
                "image_count": ds.get("image_count"),
                "with_boxes": ds.get("with_boxes"),
                "total_boxes": ds.get("total_boxes"),
                "sample": ds.get("sample"),
                "result_json": ds.get("result_json"),
            }
        prev = bmcv_block.get("preview") or {}
        if isinstance(prev, dict):
            bmcv_preview = {
                "viz_image_count": prev.get("viz_image_count"),
                "preview_files": prev.get("preview_files") or [],
            }
    return {
        "eval_dataset": {
            "source": eval_src,
            "image_count": manifest.get("image_count") or eval_count,
            "imgsz": manifest.get("imgsz"),
            "class_names": manifest.get("class_names") or [],
            "yolo_train_job_id": manifest.get("yolo_train_job_id"),
            "task_id": manifest.get("task_id"),
            "same_as_training": eval_src in (
                "yolo_train_dataset",
                "yolo_train_dataset+task_samples",
                "eval_data_test",
                "eval_data_val",
            ),
        },
        "bmrt_tests": bmrt_rows,
        "infer_runs": infer_rows,
        "bmcv_detection": bmcv_det,
        "bmcv_preview": bmcv_preview,
        "validation_scope": [
            "bmrt_test：bmodel 能否在 BM1688 上加载并给出理论耗时",
            "yolov8_bmcv：用与训练同分布的验证图做真实推理（不测 mAP）",
        ],
        "compare_note": (
            "训练 mAP 在 GPU/训练集上计算；板端为 TPU 工程验证 + 同任务图片推理。"
            "数值不要求相等，用于确认导出模型可上板且推理链路正常。"
        ),
    }


def _run_cmd(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 3600) -> Dict[str, Any]:
    cwd_s = str(cwd) if cwd else None
    env = os.environ.copy()
    if sys.version_info >= (3, 7):
        proc = subprocess.run(
            cmd,
            cwd=cwd_s,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    else:
        proc = subprocess.run(
            cmd,
            cwd=cwd_s,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            env=env,
        )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-12000:],
        "stderr": (proc.stderr or "")[-8000:],
    }


def _extract_models_zip(zip_path: Path, work_dir: Path) -> Path:
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    root = os.path.realpath(str(work_dir))

    def safe_target(member: zipfile.ZipInfo) -> Path:
        """Resolve one archive member without allowing traversal or links."""
        name = member.filename or ""
        if not name or "\x00" in name:
            raise RuntimeError("unsafe zip member path")
        # ZIP names are POSIX paths, but reject Windows separators and drive
        # prefixes as well because this archive can be inspected on either OS.
        normalized = name.replace("\\", "/")
        if (
            normalized.startswith("/")
            or normalized.startswith("//")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise RuntimeError("unsafe zip member path: {}".format(name))
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if any(re.match(r"^[A-Za-z]:", part) for part in parts):
            raise RuntimeError("unsafe zip member path: {}".format(name))
        if any(
            part == ".." or part.rstrip(" .") in ("", ".", "..")
            for part in parts
        ):
            raise RuntimeError("unsafe zip member path: {}".format(name))
        mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise RuntimeError("unsafe zip symlink member: {}".format(name))
        target = os.path.realpath(os.path.join(root, *parts))
        try:
            inside = os.path.commonpath([root, target]) == root
        except ValueError:
            inside = False
        if not inside:
            raise RuntimeError("unsafe zip member path: {}".format(name))
        current = root
        for part in parts[:-1]:
            current = os.path.join(current, part)
            if os.path.islink(current):
                raise RuntimeError("unsafe zip symlink path: {}".format(name))
        candidate = os.path.join(root, *parts)
        if os.path.islink(candidate):
            raise RuntimeError("unsafe zip symlink path: {}".format(name))
        return Path(target)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = [(member, safe_target(member)) for member in zf.infolist()]
        for member, target in members:
            if member.is_dir() or (member.filename or "").endswith(("/", "\\")):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(str(target), "wb") as dst:
                shutil.copyfileobj(src, dst)
    models = work_dir / "models"
    if not models.is_dir():
        for p in work_dir.rglob("models"):
            if p.is_dir() and (p / "BM1688").is_dir():
                return p.parent
        raise RuntimeError(f"zip 内未找到 models/: {work_dir}")
    return work_dir


def _load_run_config(work: Path) -> Dict[str, Any]:
    path = work / "run_config.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _edge_manifest_canonical_bytes(payload: Dict[str, Any]) -> bytes:
    excluded = {
        "manifest_ed25519_signature",
        "manifest_ed25519_key_id",
        "manifest_ed25519_algorithm",
        "manifest_hmac_sha256",
        "manifest_hmac_key_id",
        "manifest_signature_algorithm",
        "aicm_model_set_id",
        "aicm_published_at",
    }
    body = {str(k): v for k, v in payload.items() if str(k) not in excluded}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _edge_public_key(value: str):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as exc:
        raise RuntimeError("边端缺少 cryptography，无法验签") from exc
    raw = (value or "").encode("utf-8")
    text = (value or "").strip()
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
        raw = text.encode("utf-8")
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise RuntimeError("边端 Ed25519 公钥类型无效")
        return key
    candidates = [raw]
    try:
        candidates.append(base64.b64decode(text, validate=True))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(text))
    except Exception:
        pass
    for candidate in candidates:
        if len(candidate) == 32:
            return Ed25519PublicKey.from_public_bytes(candidate)
    raise RuntimeError("边端 Ed25519 公钥格式无效")


_EDGE_EXECUTION_STATE_SCHEMA = 1
_EDGE_EXECUTION_LOCK_TIMEOUT_SEC = 15.0
_EDGE_EXECUTION_LOCK_STALE_SEC = 120.0


def _edge_execution_state_path() -> Path:
    """Return the persistent device-local generation ledger path.

    The default is outside the temporary job directory so cleanup and process
    restarts cannot erase the replay fence.  Production should set the state
    directory to a persistent, device-owned mount (for example
    ``/var/lib/labelu-edge``); an explicit file path is useful for a read-only
    service account with a dedicated state mount.
    """
    explicit = _env(
        "LABELU_EDGE_EXECUTION_STATE_FILE",
        _env("EDGE_EXECUTION_STATE_FILE"),
    )
    if explicit:
        return Path(explicit).expanduser()
    state_dir = _env(
        "LABELU_EDGE_EXECUTION_STATE_DIR",
        _env("EDGE_EXECUTION_STATE_DIR", "/var/lib/labelu-edge"),
    )
    return Path(state_dir).expanduser() / "execution_generations.json"


def _edge_generation_scope(cfg: Dict[str, Any], export_id: int) -> str:
    explicit = str(cfg.get("execution_generation_scope") or "").strip()
    if explicit:
        return explicit
    identity = cfg.get("model_identity")
    if isinstance(identity, dict):
        external = str(identity.get("aicm_model_id") or "").strip()
        if external:
            return "aicm:" + external
    return "export:" + str(int(export_id))


def _generation_timestamp_value(value: Any) -> Optional[int]:
    """Parse the server's UTC ISO timestamp without Python 3.7 APIs."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$",
        text,
    )
    if not match:
        return None
    try:
        year, month, day, hour, minute, second = [int(item) for item in match.groups()[:6]]
        fraction = (match.group(7) or "").ljust(6, "0")
        micros = int(fraction) if fraction else 0
        stamp = datetime(year, month, day, hour, minute, second)
        return int(calendar.timegm(stamp.timetuple()) * 1000000 + micros)
    except (TypeError, ValueError, OverflowError):
        return None


def _edge_generation_order(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a comparable generation order, or None when it is unavailable.

    New producers may provide an integer sequence.  Current exports provide
    the immutable manifest creation time.  We deliberately do not compare the
    digest string itself: a SHA256 digest has no chronological meaning.
    """
    raw_sequence = cfg.get("execution_generation_sequence")
    if raw_sequence is not None and str(raw_sequence).strip() != "":
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("边端 execution_generation_sequence 无效") from exc
        if sequence < 0:
            raise RuntimeError("边端 execution_generation_sequence 不能为负数")
        return {"kind": "sequence", "value": sequence}

    issued_at = cfg.get("execution_generation_issued_at")
    if issued_at is None:
        manifest = cfg.get("artifact_manifest")
        if isinstance(manifest, dict):
            issued_at = manifest.get("created_at")
    value = _generation_timestamp_value(issued_at)
    if value is None and issued_at not in (None, ""):
        raise RuntimeError("边端 execution_generation_issued_at 无效")
    if value is not None:
        return {"kind": "timestamp_us", "value": value}
    return None


def _read_edge_execution_state(path: Path) -> Dict[str, Any]:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("边端世代状态文件不是安全普通文件")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("边端世代状态文件损坏") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != _EDGE_EXECUTION_STATE_SCHEMA:
            raise RuntimeError("边端世代状态文件版本无效")
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            raise RuntimeError("边端世代状态 entries 无效")
        for scope, item in entries.items():
            if not isinstance(scope, str) or not scope or not isinstance(item, dict):
                raise RuntimeError("边端世代状态条目无效")
            generation = str(item.get("generation") or "").strip()
            if not generation:
                raise RuntimeError("边端世代状态缺少 generation")
            order = item.get("order")
            if order is not None:
                if (
                    not isinstance(order, dict)
                    or order.get("kind") not in ("sequence", "timestamp_us")
                    or not isinstance(order.get("value"), int)
                    or order.get("value") < 0
                ):
                    raise RuntimeError("边端世代状态 order 无效")
        return payload
    return {"schema_version": _EDGE_EXECUTION_STATE_SCHEMA, "entries": {}}


def _write_edge_execution_state(path: Path, payload: Dict[str, Any]) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("边端世代状态文件是符号链接")
    tmp = parent / (
        "." + path.name + ".{}.{}.tmp".format(
            os.getpid(), int(time.time() * 1000000000)
        )
    )
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(tmp), flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(str(tmp), str(path))
        try:
            directory_fd = os.open(str(parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Directory fsync is unavailable on some filesystems/Windows; the
            # file itself has already been flushed and atomically replaced.
            pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _edge_execution_lock(path: Path):
    """Acquire an O_EXCL lock; return a cleanup callback."""
    lock = Path(str(path) + ".lock")
    deadline = time.time() + _EDGE_EXECUTION_LOCK_TIMEOUT_SEC
    while True:
        try:
            fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, ("pid={} time={}\n".format(os.getpid(), time.time())).encode("ascii"))
            finally:
                os.close(fd)
            return lambda: _release_edge_execution_lock(lock)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > _EDGE_EXECUTION_LOCK_STALE_SEC:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise RuntimeError("边端世代状态锁等待超时")
            time.sleep(0.05)
        except OSError as exc:
            raise RuntimeError("边端世代状态锁创建失败") from exc


def _release_edge_execution_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # The state was already atomically committed; leaving a lock behind is
        # safer than deleting an unknown replacement.
        pass


def consume_edge_execution_generation(
    cfg: Dict[str, Any],
    *,
    expected_export_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist and fence a verified generation across process restarts.

    A repeated generation is idempotent.  A lower generation is rejected.  If
    two different generations have no comparable signed order, the function
    rejects both the ambiguity and any possible replay instead of guessing.
    """
    generation = str(cfg.get("execution_generation") or "").strip()
    if not generation:
        raise RuntimeError("边端清单 execution_generation 缺失")
    try:
        export_id = int(cfg.get("export_id"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("边端清单 export_id 缺失") from exc
    if expected_export_id is not None and export_id != int(expected_export_id):
        raise RuntimeError("边端清单 export_id 与任务不一致")
    scope = _edge_generation_scope(cfg, export_id)
    order = _edge_generation_order(cfg)
    path = _edge_execution_state_path()
    try:
        if path.parent.is_symlink():
            raise RuntimeError("边端世代状态目录是符号链接")
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("边端世代状态目录不可用") from exc
    release = _edge_execution_lock(path)
    try:
        state = _read_edge_execution_state(path)
        entries = state["entries"]
        previous = entries.get(scope)
        if previous is not None:
            old_generation = str(previous.get("generation") or "").strip()
            if old_generation == generation:
                return {
                    "status": "idempotent",
                    "scope": scope,
                    "execution_generation": generation,
                    "order": previous.get("order"),
                    "state_file": str(path),
                }
            old_order = previous.get("order")
            if (
                order is None
                or old_order is None
                or order.get("kind") != old_order.get("kind")
            ):
                raise RuntimeError("边端无法比较不同 execution_generation，已拒绝")
            if order["value"] <= old_order["value"]:
                raise RuntimeError("边端 execution_generation 已过期，拒绝重放")

        entries[scope] = {
            "export_id": export_id,
            "generation": generation,
            "order": order,
            "consumed_at": datetime.utcnow().isoformat() + "Z",
        }
        _write_edge_execution_state(path, state)
        return {
            "status": "accepted_new",
            "scope": scope,
            "execution_generation": generation,
            "order": order,
            "state_file": str(path),
        }
    finally:
        release()


def verify_edge_run_config(
    work: Path,
    cfg: Dict[str, Any],
    *,
    expected_export_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Verify the signed server envelope before loading any BModel.

    Historical development bundles remain readable when the envelope is not
    marked required. Production-generated bundles set that flag and fail
    closed if the public key, signature, identity, or file hashes are wrong.
    """
    required = bool(cfg.get("edge_signature_required"))
    signature = str(cfg.get("manifest_ed25519_signature") or "").strip()
    if not signature:
        if required:
            raise RuntimeError("边端清单签名缺失")
        return {"status": "unsigned_compat", "required": False}
    key_id = str(cfg.get("manifest_ed25519_key_id") or cfg.get("edge_manifest_key_id") or "").strip()
    expected_key_id = _env("EDGE_MANIFEST_ED25519_KEY_ID", _env("LABELU_EDGE_MANIFEST_ED25519_KEY_ID", key_id))
    if not expected_key_id or key_id != expected_key_id:
        raise RuntimeError("边端清单 key_id 不匹配")
    public_key = _env("EDGE_MANIFEST_ED25519_PUBLIC_KEY", _env("LABELU_EDGE_MANIFEST_ED25519_PUBLIC_KEY"))
    if not public_key:
        raise RuntimeError("边端未配置 Ed25519 公钥")
    try:
        _edge_public_key(public_key).verify(bytes.fromhex(signature), _edge_manifest_canonical_bytes(cfg))
    except Exception as exc:
        raise RuntimeError("边端清单 Ed25519 验签失败") from exc
    try:
        export_id = int(cfg.get("export_id"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("边端清单 export_id 缺失") from exc
    if expected_export_id is not None and export_id != int(expected_export_id):
        raise RuntimeError("边端清单 export_id 与任务不一致")
    if not str(cfg.get("execution_generation") or "").strip():
        raise RuntimeError("边端清单 execution_generation 缺失")
    profile_digest = str(cfg.get("profile_digest") or "").strip()
    if not profile_digest:
        raise RuntimeError("边端清单 profile_digest 缺失")
    manifest = cfg.get("artifact_manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("边端清单 artifact_manifest 缺失")
    expected_generation = str(
        manifest.get("execution_signature") or manifest.get("manifest_id") or ""
    ).strip()
    if expected_generation and str(cfg.get("execution_generation") or "").strip() != expected_generation:
        raise RuntimeError("边端清单执行世代与制品清单不一致")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise RuntimeError("边端清单制品列表缺失")
    precision = set()
    for item in artifacts:
        if not isinstance(item, dict) or not item.get("relative_path"):
            continue
        if item.get("role") == "model_bmodel":
            declared = str(item.get("precision") or "").upper()
            if declared in ("FP32", "FP16", "INT8"):
                precision.add(declared)
        relative = str(item.get("relative_path") or "").replace("\\", "/")
        candidate = (work / relative).resolve()
        try:
            candidate.relative_to(work.resolve())
        except ValueError as exc:
            raise RuntimeError("边端制品路径越界") from exc
        if not candidate.is_file():
            raise RuntimeError("边端制品文件缺失: {}".format(relative))
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != str(item.get("sha256") or ""):
            raise RuntimeError("边端制品 SHA256 不匹配: {}".format(relative))
    if set(("FP32", "FP16", "INT8")) - precision:
        raise RuntimeError("边端缺少 FP32/FP16/INT8 完整制品")
    generation_state = consume_edge_execution_generation(
        cfg,
        expected_export_id=expected_export_id,
    )
    return {
        "status": "verified",
        "required": required,
        "export_id": export_id,
        "execution_generation": cfg.get("execution_generation"),
        "execution_generation_state": generation_state,
    }


def _validate_threshold_unit(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "ratio_0_1"
    if not isinstance(value, str) or value.strip() != "ratio_0_1":
        raise RuntimeError("unsupported edge threshold unit: {}".format(value))
    return "ratio_0_1"


def _validate_threshold_value(value: Any, key: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("edge threshold {} is not numeric".format(key)) from exc
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise RuntimeError("edge threshold {} is outside 0..1".format(key))
    return numeric


def _load_threshold_contract(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read the platform validation contract, with legacy defaults."""
    cfg = cfg if isinstance(cfg, dict) else {}
    nested = cfg.get("threshold_contract")
    raw = nested if isinstance(nested, dict) else cfg

    # Validate both the current field and the legacy alias in every layer.
    # Falling back to the default for an explicit ``unit=percent`` would make
    # the same numeric value mean 35% on one side and 0.35 on the other.
    for label, source in (("run_config", cfg), ("threshold_contract", nested)):
        if not isinstance(source, dict):
            continue
        for key in ("threshold_unit", "unit"):
            if key not in source:
                continue
            value = source.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if not isinstance(value, str) or value.strip() != "ratio_0_1":
                raise RuntimeError(
                    "unsupported edge threshold unit {}.{}: {}".format(
                        label, key, value
                    )
                )

    unit = _validate_threshold_unit(
        raw.get("threshold_unit")
        if "threshold_unit" in raw
        else cfg.get("threshold_unit")
    )

    values: Dict[str, float] = {}
    for key, default in (
        ("conf_thres", 0.35),
        ("nms_thres", 0.7),
        ("match_iou_thres", 0.5),
    ):
        values[key] = _validate_threshold_value(
            raw.get(key, cfg.get(key, default)), key
        )
    return {
        "schema_version": raw.get("schema_version") or cfg.get("schema_version"),
        "threshold_unit": unit,
        "source": raw.get("source") or cfg.get("threshold_source") or "system_default",
        "defaulted_fields": list(
            raw.get("defaulted_fields") or cfg.get("defaulted_fields") or []
        ),
        **values,
    }


def _resolve_eval_input_dir(export_work: Path, demo_root: Path) -> Tuple[Path, str]:
    """优先 edge SSH 下发的 eval_data/test，其次 board_eval/images。"""
    for split, note in (("test", "eval_data_test"), ("val", "eval_data_val")):
        p = export_work / "eval_data" / "images" / split
        if p.is_dir() and any(p.iterdir()):
            return p, note
    for sub in ("board_eval/images", "board_eval_images"):
        p = export_work / sub
        if p.is_dir() and any(p.iterdir()):
            manifest = export_work / "board_eval" / "manifest.json"
            note = "board_eval"
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    note = str(data.get("source") or note)
                except json.JSONDecodeError:
                    pass
            return p, note
    return demo_root / "datasets" / "test", "sophon_demo_fallback"


def _pick_int8_bmodel(bm1688_dir: Path) -> Optional[str]:
    if not bm1688_dir.is_dir():
        return None
    names = sorted(p.name for p in bm1688_dir.glob("*int8*1b.bmodel"))
    if names:
        return names[0]
    names = sorted(p.name for p in bm1688_dir.glob("*int8*.bmodel"))
    return names[0] if names else None


def _pick_fp32_bmodel(bm1688_dir: Path) -> Optional[str]:
    if not bm1688_dir.is_dir():
        return None
    for pat in ("*fp32*1b.bmodel", "*fp32*.bmodel"):
        names = sorted(p.name for p in bm1688_dir.glob(pat))
        if names:
            return names[0]
    for name in sorted(p.name for p in bm1688_dir.glob("*.bmodel")):
        if "fp16" in name:
            fp32_name = name.replace("fp16", "fp32")
            if (bm1688_dir / fp32_name).is_file():
                return fp32_name
    return None


def summarize_bmcv_result_json(json_path: Path) -> Dict[str, Any]:
    """解析 yolov8_bmcv 输出的 result json（检测框统计）。"""
    out: Dict[str, Any] = {
        "image_count": 0,
        "with_boxes": 0,
        "total_boxes": 0,
        "sample": None,
    }
    if not json_path.is_file():
        out["error"] = "result_json_missing"
        return out
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        out["error"] = str(exc)
        return out
    rows = raw if isinstance(raw, list) else []
    out["image_count"] = len(rows)
    for row in rows:
        if not isinstance(row, dict):
            continue
        boxes = row.get("bboxes") or []
        if boxes:
            out["with_boxes"] += 1
            out["total_boxes"] += len(boxes)
    if rows and isinstance(rows[0], dict):
        r0 = rows[0]
        name = r0.get("image_name") or r0.get("filename") or r0.get("image_path")
        out["sample"] = {
            "image": str(name or ""),
            "boxes": len(r0.get("bboxes") or []),
        }
    out["result_json"] = str(json_path)
    return out


def _find_latest_bmcv_result_json(demo_root: Path, bmodel_name: str) -> Optional[Path]:
    results_dir = demo_root / "results"
    if not results_dir.is_dir():
        return None
    stem = Path(bmodel_name).stem
    patterns = (
        f"{bmodel_name}_test_bmcv_python_result.json",
        f"{stem}_test_bmcv_python_result.json",
        f"*{stem}*bmcv*result*.json",
    )
    for pat in patterns:
        hits = sorted(results_dir.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    hits = sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


class _BmcvResultsGuard:
    def __init__(self, demo_root: Path, bmodel_name: str):
        self.demo_root = demo_root.resolve()
        self.bmodel_name = bmodel_name
        self._lock_fh = None
        self.started_at = 0.0

    def __enter__(self) -> float:
        results_dir = self.demo_root / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        lock_path = results_dir / ".labelu_bmcv.lock"
        self._lock_fh = open(lock_path, "a+", encoding="utf-8")
        try:
            import fcntl  # type: ignore

            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        _cleanup_stale_bmcv_outputs(self.demo_root, self.bmodel_name)
        self.started_at = time.time()
        return self.started_at

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._lock_fh is None:
            return
        try:
            import fcntl  # type: ignore

            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._lock_fh.close()
        except Exception:
            pass
        self._lock_fh = None


def _cleanup_stale_bmcv_outputs(demo_root: Path, bmodel_name: str) -> None:
    results_dir = demo_root / "results"
    if not results_dir.is_dir():
        return
    stem = Path(bmodel_name).stem
    for pat in (
        f"{bmodel_name}_*_bmcv_python_result.json",
        f"{stem}_*_bmcv_python_result.json",
        f"*{stem}*bmcv*result*.json",
    ):
        for fp in results_dir.glob(pat):
            try:
                fp.unlink()
            except OSError:
                pass
    images_dir = results_dir / "images"
    if images_dir.is_dir():
        shutil.rmtree(images_dir, ignore_errors=True)


def _find_fresh_bmcv_result_json(
    demo_root: Path,
    bmodel_name: str,
    *,
    started_at: float,
) -> Optional[Path]:
    results_dir = demo_root / "results"
    if not results_dir.is_dir():
        return None
    stem = Path(bmodel_name).stem
    cutoff = max(0.0, float(started_at) - 1.0)
    candidates: List[Path] = []
    for pat in (
        f"{bmodel_name}_*_bmcv_python_result.json",
        f"{stem}_*_bmcv_python_result.json",
        f"*{stem}*bmcv*result*.json",
    ):
        candidates.extend(results_dir.glob(pat))
    fresh = []
    seen = set()
    for fp in candidates:
        try:
            resolved = fp.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if fp.stat().st_mtime >= cutoff:
                fresh.append(fp)
        except OSError:
            continue
    fresh.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return fresh[0] if fresh else None


def package_bmcv_preview_images(
    demo_root: Path, dest_dir: Path, *, max_images: int = 12
) -> Dict[str, Any]:
    """复制 sophon-demo results/images 中带框可视化图到 output/bmcv_preview。"""
    src = demo_root / "results" / "images"
    info: Dict[str, Any] = {"viz_image_count": 0, "preview_files": []}
    if not src.is_dir():
        info["error"] = "results_images_missing"
        return info
    files = sorted(
        [p for p in src.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    info["viz_image_count"] = len(files)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    picked = files[: max(1, int(max_images))]
    for fp in picked:
        dst = dest_dir / fp.name
        shutil.copy2(fp, dst)
        info["preview_files"].append(fp.name)
    info["preview_dir"] = str(dest_dir)
    return info


def run_bmcv_detection(
    demo_root: Path,
    *,
    input_dir: Path,
    bmodel_path: Path,
    precision: str,
    conf_thresh: float = 0.35,
    nms_thresh: float = 0.7,
    preview_dir: Optional[Path] = None,
    max_preview: int = 12,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """
    边端 bmodel 图片推理（yolov8_bmcv），并汇总检测框与可视化图。
    供 edge_board_validate_runner / 板端 Agent 在验证结束后写回平台展示。
    """
    precision = str(precision or "").strip().lower()
    if precision not in ("fp32", "fp16", "int8"):
        raise ValueError("unsupported bmodel precision: {}".format(precision))
    demo_root = demo_root.resolve()
    bmodel_path = bmodel_path.resolve()
    input_dir = input_dir.resolve()
    out: Dict[str, Any] = {
        "step": "bmcv_{}_detection".format(precision),
        "precision": precision,
        "bmodel": bmodel_path.name,
        "input_dir": str(input_dir),
        "conf_thresh": float(conf_thresh),
        "nms_thresh": float(nms_thresh),
    }
    if not bmodel_path.is_file():
        out["returncode"] = 2
        out["error"] = "bmodel_missing"
        return out
    if not input_dir.is_dir():
        out["returncode"] = 2
        out["error"] = "input_dir_missing"
        return out
    with _BmcvResultsGuard(demo_root, bmodel_path.name) as started_at:
        r = _run_cmd(
            [
                "python3",
                "python/yolov8_bmcv.py",
                "--input",
                str(input_dir),
                "--bmodel",
                str(bmodel_path),
                "--dev_id",
                "0",
                "--conf_thresh",
                str(conf_thresh),
                "--nms_thresh",
                str(nms_thresh),
            ],
            cwd=demo_root,
            timeout=timeout,
        )
        out.update(r)
        result_json = _find_fresh_bmcv_result_json(
            demo_root, bmodel_path.name, started_at=started_at
        )
        detection = summarize_bmcv_result_json(result_json) if result_json else {}
    out["detection_summary"] = detection
    if preview_dir is not None:
        out["preview"] = package_bmcv_preview_images(
            demo_root, preview_dir, max_images=max_preview
        )
    else:
        viz_dir = demo_root / "results" / "images"
        n_viz = 0
        if viz_dir.is_dir():
            n_viz = sum(
                1
                for p in viz_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")
            )
        out["preview"] = {"viz_image_count": n_viz, "preview_files": []}
    _attach_step_metrics(out)
    return out


def run_bmcv_int8_detection(
    demo_root: Path,
    *,
    input_dir: Path,
    bmodel_path: Path,
    conf_thresh: float = 0.35,
    nms_thresh: float = 0.7,
    preview_dir: Optional[Path] = None,
    max_preview: int = 12,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """兼容旧调用方的 INT8 BMCV 入口。"""
    result = run_bmcv_detection(
        demo_root,
        input_dir=input_dir,
        bmodel_path=bmodel_path,
        precision="int8",
        conf_thresh=conf_thresh,
        nms_thresh=nms_thresh,
        preview_dir=preview_dir,
        max_preview=max_preview,
        timeout=timeout,
    )
    result["step"] = "bmcv_int8_detection"
    return result


def run_validation(
    demo_root: Path,
    export_work: Path,
    run_coco: bool,
    on_progress: Optional[ProgressFn] = None,
    conf_thresh: float = 0.35,
    nms_thresh: float = 0.7,
    match_iou_thres: float = 0.5,
    threshold_unit: str = "ratio_0_1",
    threshold_source: str = "system_default",
) -> Dict[str, Any]:
    def prog(stage: str, message: str, **detail: Any) -> None:
        if on_progress:
            on_progress(stage, message, detail or None)

    demo_root = demo_root.resolve()
    models_src = export_work / "models"
    models_dst = demo_root / "models"
    prog("deploy_models", "正在部署 bmodel 到 sophon-demo …")
    if models_dst.exists():
        shutil.rmtree(models_dst)
    shutil.copytree(models_src, models_dst)

    bm1688 = models_dst / "BM1688"
    bmodels = sorted(p.name for p in bm1688.glob("*.bmodel")) if bm1688.is_dir() else []
    test_img, eval_source = _resolve_eval_input_dir(export_work, demo_root)
    threshold_unit = _validate_threshold_unit(threshold_unit)
    conf_thresh = _validate_threshold_value(conf_thresh, "conf_thres")
    nms_thresh = _validate_threshold_value(nms_thresh, "nms_thres")
    match_iou_thres = _validate_threshold_value(match_iou_thres, "match_iou_thres")
    applied_thresholds = {
        "threshold_unit": threshold_unit,
        "source": str(threshold_source or "system_default"),
        "conf_thres": conf_thresh,
        "nms_thres": nms_thresh,
        "match_iou_thres": match_iou_thres,
    }
    report: Dict[str, Any] = {
        "steps": [],
        "bmodels": bmodels,
        "passed": True,
        "summary": "",
        "eval_input_dir": str(test_img),
        "eval_image_source": eval_source,
        "thresholds_applied": dict(applied_thresholds),
        # ``thresholds`` is the historical report key consumed by the UI.
        "thresholds": dict(applied_thresholds),
    }
    report["inference_applied"] = {
        "threshold_unit": applied_thresholds["threshold_unit"],
        "source": applied_thresholds["source"],
        "conf_thres": applied_thresholds["conf_thres"],
        "nms_thres": applied_thresholds["nms_thres"],
    }
    report["evaluation_match_config"] = {
        "threshold_unit": applied_thresholds["threshold_unit"],
        "source": applied_thresholds["source"],
        "match_iou_thres": applied_thresholds["match_iou_thres"],
        "status": "configured_for_formal_evaluator",
    }

    for name in bmodels:
        prog("bmrt_test", f"理论性能测试 {name}", bmodel=name)
        bpath = bm1688 / name
        r = _run_cmd(
            ["bmrt_test", "--bmodel", str(bpath), "--devid", "0"],
            timeout=600,
        )
        step = {"step": "bmrt_test", "bmodel": name, **r}
        _attach_step_metrics(step)
        report["steps"].append(step)
        if r["returncode"] != 0:
            report["passed"] = False

    if eval_source == "sophon_demo_fallback" and not test_img.is_dir():
        r = _run_cmd(["bash", "scripts/download.sh"], cwd=demo_root, timeout=1800)
        report["steps"].append({"step": "download_datasets", **r})
    elif eval_source != "sophon_demo_fallback":
        n_img = sum(1 for _ in test_img.glob("*") if _.is_file())
        report["steps"].append(
            {
                "step": "board_eval_images",
                "returncode": 0 if n_img > 0 else 1,
                "cmd": ["pack", str(test_img)],
                "stdout": f"使用任务验证图 {n_img} 张 ({eval_source})",
                "stderr": "",
            }
        )
        if n_img <= 0:
            report["passed"] = False

    bmcv_runs: List[Dict[str, Any]] = []
    if test_img.is_dir():
        for precision, name in _ordered_bmcv_bmodels(bmodels):
            step_name = "bmcv_{}_detection".format(precision)
            prog(
                "infer",
                "{} 图片推理 · {}".format(precision.upper(), name),
                bmodel=name,
                input=str(test_img),
            )
            bmcv = run_bmcv_detection(
                demo_root,
                input_dir=test_img,
                bmodel_path=bm1688 / name,
                precision=precision,
                conf_thresh=float(conf_thresh),
                nms_thresh=float(nms_thresh),
                preview_dir=None,
            )
            bmcv["step"] = step_name
            bmcv["precision"] = precision
            bmcv_runs.append(bmcv)
            step = {
                "step": step_name,
                "bmodel": name,
                "returncode": bmcv.get("returncode"),
                "metrics": bmcv.get("metrics") or {},
            }
            ds = bmcv.get("detection_summary") or {}
            if ds.get("image_count") is not None:
                step["stdout"] = (
                    "图片数: {0} 有框图数: {1} 总框数: {2}".format(
                        ds.get("image_count"),
                        ds.get("with_boxes"),
                        ds.get("total_boxes"),
                    )
                )
            report["steps"].append(step)
            if bmcv.get("returncode") != 0:
                report["passed"] = False
    report["bmcv_infer_runs"] = bmcv_runs
    if bmcv_runs:
        report["bmcv_infer"] = next(
            (row for row in bmcv_runs if row.get("precision") == "int8"),
            bmcv_runs[0],
        )

    if run_coco:
        val_dir = demo_root / "datasets" / "coco" / "val2017_1000"
        gt = demo_root / "datasets" / "coco" / "instances_val2017_1000.json"
        fp32_name = _pick_fp32_bmodel(bm1688)
        fp32 = bm1688 / fp32_name if fp32_name else None
        if fp32 and fp32.is_file() and val_dir.is_dir() and gt.is_file():
            r = _run_cmd(
                [
                    "python3",
                    "python/yolov8_bmcv.py",
                    "--input",
                    str(val_dir),
                    "--bmodel",
                    str(fp32),
                    "--dev_id",
                    "0",
                    "--conf_thresh",
                    str(float(conf_thresh)),
                    "--nms_thresh",
                    str(float(nms_thresh)),
                ],
                cwd=demo_root,
                timeout=7200,
            )
            report["steps"].append(
                {
                    "step": "infer_coco_val",
                    "thresholds": dict(report["inference_applied"]),
                    **r,
                }
            )
            if r["returncode"] == 0:
                results = list((demo_root / "results").glob("*.json"))
                if results:
                    r2 = _run_cmd(
                        [
                            "python3",
                            "tools/eval_coco.py",
                            "--gt_path",
                            str(gt),
                            "--result_json",
                            str(results[-1]),
                        ],
                        cwd=demo_root,
                        timeout=600,
                    )
                    report["steps"].append({"step": "eval_coco", **r2})
                    if r2["returncode"] != 0:
                        report["passed"] = False
            else:
                report["passed"] = False

    ok_n = sum(1 for s in report["steps"] if s.get("returncode") == 0)
    report["summary"] = (
        f"板端验证 {'通过' if report['passed'] else '失败'}；"
        f"{len(bmodels)} 个 bmodel，{ok_n}/{len(report['steps'])} 步成功"
    )
    return report


def finalize_board_report(report: Dict[str, Any], export_work: Path) -> Dict[str, Any]:
    manifest = _load_board_manifest(export_work)
    report["board_eval_manifest"] = manifest
    report["metrics"] = _build_report_metrics(report, manifest)
    return report


EDGE_ROOT = Path(_env("LABELU_EDGE_ROOT", "/data/labelu_edge"))
BOARD_JOBS_ROOT = EDGE_ROOT / "jobs" / "board-agent"


def _require_edge_work_path(path: Path) -> Path:
    root = EDGE_ROOT.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise RuntimeError("edge work path escapes {}: {}".format(root, path))
    return path


def _prepare_board_jobs_root() -> None:
    if EDGE_ROOT.is_symlink():
        raise RuntimeError("{} must not be a symbolic link".format(EDGE_ROOT))
    EDGE_ROOT.mkdir(parents=True, exist_ok=True)
    _require_edge_work_path(BOARD_JOBS_ROOT)
    BOARD_JOBS_ROOT.mkdir(parents=True, exist_ok=True)


def _cleanup_job_workdir(work: Path) -> None:
    """任务结束后删除本地下载/解压目录，避免磁盘堆积。"""
    _require_edge_work_path(work)
    if work.is_dir():
        shutil.rmtree(work, ignore_errors=True)


def _prune_stale_board_job_dirs(
    keep_validation_id: Optional[int] = None,
    max_age_hours: float = 48,
) -> None:
    """清理过期的 labelu_board_jobs 子目录（保留当前任务目录）。"""
    if not BOARD_JOBS_ROOT.is_dir():
        return
    cutoff = time.time() - max(1.0, max_age_hours) * 3600
    for child in BOARD_JOBS_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            vid = int(child.name)
        except ValueError:
            continue
        if keep_validation_id is not None and vid == keep_validation_id:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


def _submit_final_report(
    report_url: str,
    token: str,
    status_value: str,
    report: Dict[str, Any],
    err: Optional[str],
    task_token: Optional[str] = None,
) -> None:
    _http_json(
        "POST",
        report_url,
        token,
        body={"status": status_value, "report": report, "error_message": err},
        timeout=120,
        task_token=task_token,
    )


def poll_once(base: str, token: str, demo_root: Path, run_coco: bool) -> bool:
    try:
        _post_heartbeat(base, token, demo_root)
    except Exception as exc:
        # A transient heartbeat failure must not stop the agent from retrying
        # the task channel; the scheduler will keep the device non-ready until
        # a fresh heartbeat is accepted.
        _log(f"[board-agent] 心跳上报失败: {exc}", err=True)
    _prepare_board_jobs_root()
    url = f"{base.rstrip('/')}{API_PREFIX}/board-agent/jobs/next"
    payload = _http_json("GET", url, token)
    if not payload or not payload.get("data"):
        _log("[board-agent] 无待办任务")
        return False

    job = payload["data"]
    vid = job["validation_id"]
    task_token = str(job.get("task_token") or "").strip() or None
    report_url = _sanitize_url(
        job.get("report_url") or f"{base.rstrip('/')}{API_PREFIX}/board-agent/jobs/{vid}/report"
    )
    progress_url = _sanitize_url(
        job.get("progress_url") or report_url.replace("/report", "/progress")
    )
    zip_url = _sanitize_url(job.get("models_zip_url") or "")

    def prog(stage: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        nonlocal task_token
        _log(f"[board-agent] {message}")
        refreshed_token = _post_progress(
            progress_url,
            token,
            stage,
            message,
            detail,
            task_token=task_token,
        )
        if refreshed_token:
            task_token = refreshed_token

    work = BOARD_JOBS_ROOT / str(vid)
    _prune_stale_board_job_dirs(keep_validation_id=vid)
    if work.exists():
        _cleanup_job_workdir(work)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / "models.zip"
    report: Dict[str, Any] = {"steps": [], "passed": False, "summary": ""}
    status_value = "FAILED"
    err: Optional[str] = None
    heartbeat_keeper = _HeartbeatKeeper(base, token, demo_root)
    heartbeat_keeper.start()

    try:
        try:
            prog(
                "claimed",
                f"边端已领取任务 #{vid}（export {job.get('export_id')}）",
                {"validation_id": vid, "export_id": job.get("export_id")},
            )
            if not zip_url:
                raise RuntimeError(
                    "models_zip_url 为空，请检查公网 BOARD_AGENT_PUBLIC_BASE_URL"
                )
            prog("downloading", "正在从公网下载 models.zip …", {"url": zip_url[:120]})
            _download_file(zip_url, token, zip_path, task_token=task_token)
            size_mb = zip_path.stat().st_size / (1024 * 1024)
            prog(
                "download_ok",
                f"下载完成（{size_mb:.1f} MB）",
                {"zip_path": str(zip_path), "size_mb": round(size_mb, 2)},
            )
            prog("extracting", "正在解压 models.zip …")
            export_work = _extract_models_zip(zip_path, work / "export")
            prog(
                "extract_ok",
                "解压完成，准备跑 bmrt_test / 推理",
                {"dir": str(export_work)},
            )
            run_cfg = _load_run_config(export_work)
            integrity = verify_edge_run_config(
                export_work,
                run_cfg,
                expected_export_id=job.get("export_id"),
            )
            prog("manifest_verified", "边端清单验签和制品完整性校验通过", integrity)
            report["execution_generation_state"] = integrity.get(
                "execution_generation_state"
            )
            threshold_contract = _load_threshold_contract(run_cfg)
            report = run_validation(
                demo_root,
                export_work,
                run_coco=run_coco,
                on_progress=prog,
                conf_thresh=threshold_contract["conf_thres"],
                nms_thresh=threshold_contract["nms_thres"],
                match_iou_thres=threshold_contract["match_iou_thres"],
                threshold_unit=threshold_contract["threshold_unit"],
                threshold_source=threshold_contract["source"],
            )
            # 将服务器生成的模型/制品绑定带入回传报告；设备端只加载并
            # 推理已有 BModel，不在板端导出 ONNX 或执行 TPU-MLIR 编译。
            report["model_identity"] = run_cfg.get("model_identity") or {
                "export_id": job.get("export_id"),
                "model_id": job.get("export_id"),
                "model_id_source": "export_id_pre_publish",
            }
            report["artifact_execution_signature"] = run_cfg.get(
                "artifact_execution_signature"
            )
            report["profile_digest"] = run_cfg.get("profile_digest")
            report["bm1688_profile"] = run_cfg.get("bm1688_profile")
            report["edge_execution_contract"] = run_cfg.get(
                "edge_execution_contract"
            ) or {
                "compile_location": "server",
                "device_action": "load_and_infer",
                "device_must_not_compile": True,
            }
            report["required_bmodel_precisions"] = run_cfg.get(
                "required_bmodel_precisions"
            ) or ["fp32", "fp16", "int8"]
            report["execution_device"] = {
                "device_id": _env("BM1688_DEVICE_ID") or socket.gethostname(),
                "host": _env("BM1688_DEVICE_HOST") or socket.gethostname(),
                "serial": _env("BM1688_DEVICE_SERIAL"),
            }
            report["threshold_contract"] = threshold_contract
            report = finalize_board_report(report, export_work)
            status_value = "COMPLETED" if report.get("passed") else "FAILED"
            err = None if report.get("passed") else report.get("summary")
            prog("reporting", "正在回传验证结果到平台 …")
        except Exception as exc:
            report = {
                "summary": str(exc),
                "passed": False,
                "steps": report.get("steps") or [],
            }
            status_value = "FAILED"
            err = str(exc)[:8000]
            prog("failed", f"验证失败: {err}", {"error": err})

        _submit_final_report(
            report_url,
            token,
            status_value,
            report,
            err,
            task_token=task_token,
        )
        _log(f"[board-agent] 已回传 {status_value}: {report.get('summary', '')}")
    finally:
        heartbeat_keeper.stop()
        _cleanup_job_workdir(work)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="LabelU BM1688 板端 Agent")
    parser.add_argument("--once", action="store_true", help="只拉取一次任务后退出")
    parser.add_argument("--base-url", default=_env("LABELU_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--token", default=_env("BOARD_AGENT_TOKEN"))
    parser.add_argument(
        "--demo-root",
        default=_env(
            "SOPHON_DEMO_ROOT",
            str(EDGE_ROOT / "runtime" / "sophon-demo" / "sample" / "YOLOv8_plus_det"),
        ),
    )
    args = parser.parse_args()
    if not args.token:
        _log("请设置环境变量 BOARD_AGENT_TOKEN", err=True)
        return 2

    demo_root = _require_edge_work_path(Path(args.demo_root))
    if not demo_root.is_dir():
        _log(f"[board-agent] 警告: SOPHON_DEMO_ROOT 不存在: {demo_root}", err=True)
    run_coco = _env("RUN_COCO_EVAL", "0") in ("1", "true", "yes")
    interval = max(10, int(_env("POLL_INTERVAL_SEC", "30") or "30"))

    os.environ.setdefault("PATH", "/opt/sophon/libsophon-current/bin:" + os.environ.get("PATH", ""))
    if args.once:
        poll_once(args.base_url, args.token, demo_root, run_coco)
        return 0

    _log(f"[board-agent] 已启动，轮询 {args.base_url} 每 {interval}s")
    while True:
        try:
            poll_once(args.base_url, args.token, demo_root, run_coco)
        except Exception as exc:
            _log(f"[board-agent] 错误: {exc}", err=True)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
