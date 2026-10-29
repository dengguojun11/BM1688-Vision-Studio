#!/usr/bin/env python3
"""
LabelU 远程 YOLO 训练 Agent（节点端，独立运行，不依赖 labelu 包）。

平台 SSH 启动示例（HTTP 拉取，推荐）：
  nohup /opt/labelu-train-venv/bin/python /opt/labelu-remote-train-agent/agent_worker.py \\
    --job-dir /data/labelu_remote_train/job-102 \\
    --payload-url 'http://平台:10000/api/v1/yolo-train/remote-jobs/102/payload?token=...' \\
    --payload-total-bytes 3500000000 > train.log 2>&1 &

作业目录结构（拉取解压后）：
  job-{id}/
    job_spec.json
    dataset/data.yaml
    weights/*.pt          # 可选，平台打包的初始权重
    train/                # Ultralytics 输出（本脚本生成）
    phase.json
    agent_result.json
"""
from __future__ import annotations

import argparse
import csv
import hmac
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - only used on Windows agents
    fcntl = None

try:
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - only used on POSIX agents
    msvcrt = None

ALLOWED_TRAIN_HYPER_KEYS = frozenset(
    {
        "batch",
        "workers",
        "patience",
        "lr0",
        "lrf",
        "momentum",
        "weight_decay",
        "warmup_epochs",
        "warmup_momentum",
        "warmup_bias_lr",
        "optimizer",
        "cos_lr",
        "close_mosaic",
        "label_smoothing",
        "dropout",
        "freeze",
        "seed",
        "deterministic",
        "single_cls",
        "rect",
        "cache",
        "amp",
        "fraction",
        "hsv_h",
        "hsv_s",
        "hsv_v",
        "degrees",
        "translate",
        "scale",
        "shear",
        "perspective",
        "flipud",
        "fliplr",
        "mosaic",
        "mixup",
        "copy_paste",
        "erasing",
        "crop_fraction",
        "max_det",
        "iou",
        "conf",
        "save_period",
        "cutmix",
        "multi_scale",
        "box",
        "cls",
        "dfl",
    }
)

_CURRENT_OPERATION_ID = ""
_CURRENT_SPEC_SHA256 = ""
_CURRENT_HMAC_SECRET = ""
_CURRENT_HMAC_KEY_ID = "v1"
_HMAC_REQUIRED = False

HMAC_SIGNATURE_FIELD = "hmac_sha256"
HMAC_KEY_ID_FIELD = "hmac_key_id"


def _canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    body = dict(payload)
    body.pop(HMAC_SIGNATURE_FIELD, None)
    body.pop(HMAC_KEY_ID_FIELD, None)
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _json_signature(payload: Dict[str, Any]) -> str:
    return hmac.new(
        _CURRENT_HMAC_SECRET.encode("utf-8"),
        _canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def _signed_json_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    signed = dict(payload)
    if _CURRENT_HMAC_SECRET:
        signed[HMAC_KEY_ID_FIELD] = _CURRENT_HMAC_KEY_ID
        signed[HMAC_SIGNATURE_FIELD] = _json_signature(signed)
    elif _HMAC_REQUIRED:
        raise RuntimeError("远程训练 HMAC 密钥缺失，拒绝写入未签名制品")
    return signed


def _verify_json_payload(payload: Dict[str, Any], *, required: bool = False) -> bool:
    signature = str(payload.get(HMAC_SIGNATURE_FIELD) or "").strip()
    if not signature:
        return not required and not _CURRENT_HMAC_SECRET
    if not _CURRENT_HMAC_SECRET:
        return False
    if str(payload.get(HMAC_KEY_ID_FIELD) or "").strip() != _CURRENT_HMAC_KEY_ID:
        return False
    return hmac.compare_digest(signature, _json_signature(payload))


def _bytes_signature(content: bytes) -> str:
    return hmac.new(_CURRENT_HMAC_SECRET.encode("utf-8"), content, hashlib.sha256).hexdigest()


def _write_best_signature(best_path: Path) -> None:
    sidecar = best_path.with_name(best_path.name + ".hmac.json")
    if not _CURRENT_HMAC_SECRET:
        if _HMAC_REQUIRED:
            raise RuntimeError("远程训练 HMAC 密钥缺失，拒绝写入未签名 best.pt")
        sidecar.unlink(missing_ok=True)
        return
    sidecar.write_text(
        json.dumps(
            {
                HMAC_KEY_ID_FIELD: _CURRENT_HMAC_KEY_ID,
                HMAC_SIGNATURE_FIELD: _bytes_signature(best_path.read_bytes()),
                "operation_id": _CURRENT_OPERATION_ID or None,
                "spec_sha256": _CURRENT_SPEC_SHA256 or None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_hmac_key(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("远程训练 HMAC 密钥文件不可读") from exc


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_hyper_value(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip()
        low = s.lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            if "." in s or "e" in low:
                return float(s)
            return int(s)
        except ValueError:
            return s
    return v


def normalize_hyperparams(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw:
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in ALLOWED_TRAIN_HYPER_KEYS or v is None:
            continue
        if isinstance(v, str) and not str(v).strip():
            continue
        out[k] = _coerce_hyper_value(v)
    return out


def write_phase(
    job_dir: Path,
    phase: str,
    *,
    detail: str = "",
    operation_id: str = "",
    **extra: Any,
) -> None:
    operation = str(operation_id or _CURRENT_OPERATION_ID or "").strip()
    if not operation:
        try:
            spec_path = job_dir / "job_spec.json"
            if spec_path.is_file():
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                operation = str(spec.get("operation_id") or "").strip()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            operation = ""
    payload: Dict[str, Any] = {
        "phase": phase,
        "detail": detail,
        "updated_at": _iso_now(),
        "runtime_pid": os.getpid(),
    }
    if operation:
        payload["operation_id"] = operation
    if _CURRENT_SPEC_SHA256:
        payload["spec_sha256"] = _CURRENT_SPEC_SHA256
    if extra:
        payload.update(extra)
    (job_dir / "phase.json").write_text(
        json.dumps(_signed_json_payload(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_log(log_fp: TextIO, line: str) -> None:
    log_fp.write(line.rstrip() + "\n")
    log_fp.flush()


def resolve_weights_path(job_dir: Path, spec: Dict[str, Any]) -> str:
    weights_dir = job_dir / "weights"
    if weights_dir.is_dir():
        for pattern in ("*.pt",):
            for p in sorted(weights_dir.glob(pattern)):
                if p.is_file() and p.stat().st_size > 1024:
                    return str(p.resolve())

    token = str(spec.get("model_weights") or "yolo11n.pt").strip()
    if token.startswith(("imported:", "trained:")):
        base = "yolo11n.pt"
    else:
        base = Path(token).name or "yolo11n.pt"

    cache_dir = str(spec.get("weights_cache_dir") or "").strip()
    if cache_dir:
        cached = Path(cache_dir) / base
        if cached.is_file():
            return str(cached.resolve())

    local = job_dir / base
    if local.is_file():
        return str(local.resolve())
    return base


def parse_results_csv_metrics(train_dir: Path) -> Dict[str, Any]:
    path = train_dir / "results.csv"
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return {}
    if not rows:
        return {}
    last = rows[-1]
    out: Dict[str, Any] = {"epoch": last.get("epoch")}
    for src, dst in (
        ("metrics/mAP50(B)", "map50"),
        ("metrics/mAP50-95(B)", "map50_95"),
        ("val/box_loss", "val_box_loss"),
        ("val/cls_loss", "val_cls_loss"),
    ):
        if src in last and last[src] not in (None, ""):
            try:
                out[dst] = float(last[src])
            except (TypeError, ValueError):
                out[dst] = last[src]
    return out


def build_metrics_summary(
    job_dir: Path,
    spec: Dict[str, Any],
    *,
    final_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    dataset_root = job_dir / "dataset"
    counts = {"train": 0, "val": 0, "test": 0}
    for split in ("train", "val", "test"):
        img_dir = dataset_root / "images" / split
        if img_dir.is_dir():
            counts[split] = sum(
                1
                for p in img_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
            )
    classes: List[str] = []
    data_yaml = dataset_root / "data.yaml"
    if data_yaml.is_file():
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
            names = data.get("names") if isinstance(data, dict) else None
            if isinstance(names, dict):
                classes = [str(names[k]) for k in sorted(names, key=lambda x: int(x))]
            elif isinstance(names, list):
                classes = [str(x) for x in names]
        except Exception:
            pass
    return {
        "results_dir": str(job_dir / "train"),
        "dataset": counts,
        "classes": classes,
        "final_metrics": final_metrics,
        "remote_agent": True,
        "job_id": spec.get("job_id"),
    }


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_class_contract(job_dir: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    contract = spec.get("class_contract")
    if not isinstance(contract, dict) or not str(
        contract.get("class_order_signature") or ""
    ).strip():
        return {}

    data_yaml = job_dir / "dataset" / "data.yaml"
    import yaml  # type: ignore

    payload = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    names = payload.get("names") if isinstance(payload, dict) else None
    if isinstance(names, dict):
        actual_names = [str(names[key]) for key in sorted(names, key=lambda x: int(x))]
    elif isinstance(names, list):
        actual_names = [str(value) for value in names]
    else:
        actual_names = []
    expected_names = [str(value) for value in contract.get("class_names") or []]
    if actual_names != expected_names:
        raise RuntimeError(
            "remote class contract mismatch: data.yaml names != frozen class_names"
        )

    actual_yaml_sha256 = _file_sha256(data_yaml)
    if actual_yaml_sha256 != str(contract.get("data_yaml_sha256") or ""):
        raise RuntimeError("remote class contract mismatch: data.yaml sha256")
    actual_order_signature = _canonical_sha256(
        [str(value).strip() for value in actual_names]
    )
    if actual_order_signature != str(contract.get("class_order_signature") or ""):
        raise RuntimeError("remote class contract mismatch: class order signature")

    manifest_path = job_dir / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(manifest.get("dataset_content_signature") or "") != str(
        contract.get("dataset_content_signature") or ""
    ):
        raise RuntimeError("remote class contract mismatch: dataset signature")
    dataset_root = job_dir / "dataset"
    for item in manifest.get("files") or []:
        relative = str((item or {}).get("relative_path") or "")
        if not relative:
            continue
        candidate = (dataset_root / relative).resolve()
        candidate.relative_to(dataset_root.resolve())
        if not candidate.is_file() or _file_sha256(candidate) != str(
            (item or {}).get("sha256") or ""
        ):
            raise RuntimeError(
                "remote class contract mismatch: dataset file " + relative
            )
    return {
        "class_names": actual_names,
        "label_set_signature": contract.get("label_set_signature"),
        "class_order_signature": actual_order_signature,
        "data_yaml_sha256": actual_yaml_sha256,
        "dataset_content_signature": manifest.get("dataset_content_signature"),
    }


def run_train(job_dir: Path, spec: Dict[str, Any], log_fp: TextIO) -> int:
    global _CURRENT_SPEC_SHA256
    _CURRENT_SPEC_SHA256 = _file_sha256(job_dir / "job_spec.json")
    os.environ.setdefault("YOLO_OFFLINE", "True")
    dataset_root = job_dir / "dataset"
    data_yaml = dataset_root / "data.yaml"
    if not data_yaml.is_file():
        raise FileNotFoundError(f"缺少 dataset/data.yaml: {data_yaml}")

    verified_class_contract = validate_class_contract(job_dir, spec)

    epochs = int(spec.get("epochs") or 50)
    imgsz = int(spec.get("imgsz") or 640)
    device = str(spec.get("cuda_devices") or "0").strip() or "0"
    hp = normalize_hyperparams(spec.get("hyperparams") if isinstance(spec.get("hyperparams"), dict) else {})

    weights_path = resolve_weights_path(job_dir, spec)
    write_phase(job_dir, "loading_model", detail=weights_path)
    append_log(log_fp, f"loading weights: {weights_path}")

    from ultralytics import YOLO

    model = YOLO(weights_path)
    target_epochs = epochs

    def on_epoch_end(trainer) -> None:
        ep = getattr(trainer, "epoch", None)
        if ep is not None:
            write_phase(
                job_dir,
                "running",
                detail=f"epoch {int(ep) + 1}/{target_epochs} device={device}",
            )

    model.add_callback("on_fit_epoch_end", on_epoch_end)

    train_kw: Dict[str, Any] = {
        "data": str(data_yaml.resolve()),
        "epochs": epochs,
        "imgsz": imgsz,
        "project": str(job_dir.resolve()),
        "name": "train",
        "exist_ok": True,
        "pretrained": True,
        "verbose": True,
        "patience": min(100, max(15, epochs // 5)),
        "save": True,
        "plots": True,
        "device": device,
        "workers": int(hp.pop("workers", 4)),
        "amp": bool(hp.pop("amp", True)),
    }
    train_kw.update(hp)

    write_phase(
        job_dir,
        "running",
        detail=f"epochs={epochs} imgsz={imgsz} device={device}",
    )
    append_log(log_fp, f"model.train kwargs={json.dumps(train_kw, ensure_ascii=False, default=str)}")

    results = model.train(**train_kw)
    append_log(log_fp, f"train finished: {type(results).__name__}")

    best_path = job_dir / "train" / "weights" / "best.pt"
    if not best_path.is_file():
        raise FileNotFoundError(f"训练结束但未找到 best.pt: {best_path}")

    write_phase(job_dir, "finalizing", detail="writing agent_result.json")
    final_metrics = parse_results_csv_metrics(job_dir / "train")
    summary = build_metrics_summary(job_dir, spec, final_metrics=final_metrics)
    if results is not None and hasattr(results, "speed"):
        summary["speed"] = getattr(results, "speed", None)

    _write_best_signature(best_path)

    agent_result = {
        "status": "completed",
        "finished_at": _iso_now(),
        "operation_id": str(spec.get("operation_id") or _CURRENT_OPERATION_ID or "") or None,
        "spec_sha256": _CURRENT_SPEC_SHA256,
        "weights_path": str(best_path.resolve()),
        "metrics_summary": summary,
        "final_metrics": final_metrics,
        "class_contract": verified_class_contract,
    }
    (job_dir / "agent_result.json").write_text(
        json.dumps(_signed_json_payload(agent_result), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_phase(job_dir, "completed", detail=f"best.pt ready ({best_path.stat().st_size} bytes)")
    append_log(log_fp, json.dumps(agent_result, ensure_ascii=False, default=str))
    return 0


def _probe_payload_total_bytes(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as resp:
            cl = resp.headers.get("Content-Length")
            if cl:
                return int(cl)
    except Exception:
        pass
    return 0


_PHASE_THROTTLE = {"t": 0.0, "pct": -1}


def _report_download_progress(
    job_dir: Path,
    offset: int,
    total: int,
    *,
    force: bool = False,
) -> None:
    pct = min(100, int(offset * 100 / total)) if total > 0 else 0
    now = time.monotonic()
    if (
        not force
        and pct < 100
        and pct < _PHASE_THROTTLE["pct"] + 1
        and now - _PHASE_THROTTLE["t"] < 2.0
    ):
        return
    _PHASE_THROTTLE["t"] = now
    _PHASE_THROTTLE["pct"] = pct
    write_phase(
        job_dir,
        "downloading",
        detail=f"{offset}/{total or '?'} bytes",
        download_bytes=offset,
        download_total_bytes=total or None,
        download_pct=pct,
    )


def _curl_available() -> bool:
    # The agent normally runs on Linux. On Windows, the inbox curl wrapper
    # can be present but behaves differently (and is not needed for the
    # urllib fallback), so prefer the portable Python downloader there.
    return os.name != "nt" and shutil.which("curl") is not None


def _curl_base_args() -> List[str]:
    return [
        "curl",
        "-sS",
        "-f",
        "--connect-timeout",
        "30",
        "--keepalive-time",
        "30",
        "--retry",
        "8",
        "--retry-all-errors",
        "--retry-delay",
        "2",
        "--retry-max-time",
        "0",
    ]


def _acquire_job_lock(job_dir: Path) -> TextIO:
    """同一 job 目录只允许一个 Agent（避免重复拉取）。"""
    lock_path = job_dir / ".agent.lock"
    lock_fp = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            # msvcrt.locking starts at the current cursor and requires a byte
            # to exist. Keep a one-byte marker in the lock file.
            lock_fp.seek(0)
            lock_fp.write("0")
            lock_fp.flush()
            lock_fp.seek(0)
            msvcrt.locking(lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
    except BlockingIOError as exc:
        lock_fp.close()
        raise RuntimeError(
            f"job 目录已有 agent_worker 在运行: {job_dir}"
        ) from exc
    except OSError as exc:
        # Windows reports a held msvcrt lock as EACCES/EDEADLK instead of
        # BlockingIOError. Normalize it to the same agent-facing error.
        if msvcrt is not None and fcntl is None:
            lock_fp.close()
            raise RuntimeError(
                f"job 鐩綍宸叉湁 agent_worker 鍦ㄨ繍琛? {job_dir}"
            ) from exc
        lock_fp.close()
        raise
    lock_fp.seek(0)
    lock_fp.truncate()
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()
    return lock_fp


def _release_job_lock(lock_fp: TextIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            lock_fp.seek(0)
            msvcrt.locking(lock_fp.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        lock_fp.close()


def _download_range_curl(url: str, dest_part: Path, start: int, end: int) -> int:
    """curl Range 下载（流式写盘，适合大分片）。"""
    have = int(dest_part.stat().st_size) if dest_part.is_file() else 0
    cur = start + have
    if cur > end:
        return 0
    dest_part.parent.mkdir(parents=True, exist_ok=True)
    chunk_path = dest_part.with_suffix(f".{cur}.chunk")
    if chunk_path.is_file():
        chunk_path.unlink()
    proc = subprocess.run(
        [
            *_curl_base_args(),
            "-r",
            f"{cur}-{end}",
            "-o",
            str(chunk_path),
            url,
        ],
        capture_output=True,
        timeout=6 * 3600,
    )
    if proc.returncode != 0:
        chunk_path.unlink(missing_ok=True)
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise IOError(f"curl range failed rc={proc.returncode}: {err}")
    added = int(chunk_path.stat().st_size) if chunk_path.is_file() else 0
    if added <= 0:
        chunk_path.unlink(missing_ok=True)
        return 0
    mode = "ab" if have > 0 else "wb"
    with dest_part.open(mode) as out_fp, chunk_path.open("rb") as in_fp:
        shutil.copyfileobj(in_fp, out_fp, length=8 * 1024 * 1024)
    chunk_path.unlink(missing_ok=True)
    return added


def _download_range_to_file(
    url: str,
    dest_part: Path,
    start: int,
    end: int,
    *,
    chunk_size: int = 8 * 1024 * 1024,
) -> int:
    """下载 [start,end] 闭区间到 dest_part（支持 part 内续传）。返回新增字节数。"""
    have = int(dest_part.stat().st_size) if dest_part.is_file() else 0
    cur = start + have
    if cur > end:
        return 0
    headers = {"Range": f"bytes={cur}-{end}"}
    req = urllib.request.Request(url, headers=headers)
    added = 0
    with urllib.request.urlopen(req, timeout=300) as resp:
        code = int(getattr(resp, "status", None) or resp.getcode() or 200)
        if code == 200 and cur > start:
            dest_part.unlink(missing_ok=True)
            have = 0
            cur = start
            headers = {"Range": f"bytes={cur}-{end}"}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp2:
                resp = resp2
        mode = "ab" if dest_part.is_file() and dest_part.stat().st_size > 0 else "wb"
        with dest_part.open(mode) as out_fp:
            while True:
                buf = resp.read(chunk_size)
                if not buf:
                    break
                out_fp.write(buf)
                added += len(buf)
    return added


def download_payload_parallel(
    url: str,
    dest: Path,
    job_dir: Path,
    log_fp: TextIO,
    *,
    total_hint: int = 0,
    workers: int = 8,
) -> None:
    workers = max(2, min(32, int(workers or 16)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = int(total_hint or 0)
    if total <= 0:
        total = _probe_payload_total_bytes(url)
    if total <= 0:
        raise IOError("无法确定 payload 总大小，无法并行下载")

    parts_dir = job_dir / ".payload_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_size = (total + workers - 1) // workers
    progress_lock = threading.Lock()
    state = {"done": 0}
    stop_poll = threading.Event()
    use_curl = _curl_available()

    def _poll_progress() -> None:
        while not stop_poll.is_set():
            done = 0
            for fp in parts_dir.glob("part-*"):
                if fp.is_file():
                    done += fp.stat().st_size
            _report_download_progress(job_dir, done, total)
            stop_poll.wait(2.0)

    _PHASE_THROTTLE["t"] = 0.0
    _PHASE_THROTTLE["pct"] = -1
    write_phase(
        job_dir,
        "downloading",
        detail=f"parallel x{workers} starting ({'curl' if use_curl else 'urllib'})",
        download_bytes=0,
        download_total_bytes=total,
        download_pct=0,
    )
    append_log(
        log_fp,
        f"download payload parallel workers={workers} backend={'curl' if use_curl else 'urllib'} total={total}",
    )

    poller = threading.Thread(target=_poll_progress, name="payload-progress", daemon=True)
    poller.start()

    def _worker(part_idx: int) -> None:
        start = part_idx * part_size
        end = min(total - 1, start + part_size - 1)
        if start > end:
            return
        part_path = parts_dir / f"part-{part_idx:03d}.bin"
        last_err: Optional[Exception] = None
        for attempt in range(5):
            try:
                if use_curl:
                    _download_range_curl(url, part_path, start, end)
                else:
                    added = _download_range_to_file(url, part_path, start, end)
                    if added:
                        with progress_lock:
                            state["done"] += added
                            _report_download_progress(job_dir, state["done"], total)
                return
            except Exception as exc:
                last_err = exc
                if attempt >= 4:
                    break
                time.sleep(min(30, 2 ** attempt))
        raise last_err or IOError(f"分片 {part_idx} 下载失败")

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker, i) for i in range(workers)]
            for fut in as_completed(futures):
                fut.result()
    finally:
        stop_poll.set()
        poller.join(timeout=3)

    expected_parts = workers
    for i in range(expected_parts):
        start = i * part_size
        end = min(total - 1, start + part_size - 1)
        if start > end:
            continue
        part_path = parts_dir / f"part-{i:03d}.bin"
        need = end - start + 1
        have = part_path.stat().st_size if part_path.is_file() else 0
        if have < need:
            raise IOError(f"分片 {i} 不完整: {have}/{need}")

    with dest.open("wb") as out_fp:
        for i in range(expected_parts):
            part_path = parts_dir / f"part-{i:03d}.bin"
            if not part_path.is_file():
                continue
            with part_path.open("rb") as inp:
                shutil.copyfileobj(inp, out_fp, length=8 * 1024 * 1024)

    shutil.rmtree(parts_dir, ignore_errors=True)
    final_size = dest.stat().st_size
    if final_size < total:
        raise IOError(f"下载未完成: {final_size}/{total} bytes")
    _report_download_progress(job_dir, final_size, total, force=True)
    append_log(log_fp, f"download complete parallel size={final_size}")


def download_payload_with_resume(
    url: str,
    dest: Path,
    job_dir: Path,
    log_fp: TextIO,
    *,
    total_hint: int = 0,
) -> None:
    chunk_size = 8 * 1024 * 1024
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = int(total_hint or 0)
    if total <= 0:
        total = _probe_payload_total_bytes(url)

    _PHASE_THROTTLE["t"] = 0.0
    _PHASE_THROTTLE["pct"] = -1
    write_phase(
        job_dir,
        "downloading",
        detail="starting download",
        download_bytes=0,
        download_total_bytes=total or None,
        download_pct=0,
    )
    append_log(log_fp, f"download payload url={url.split('?', 1)[0]} total_hint={total}")

    if _curl_available():
        dest.parent.mkdir(parents=True, exist_ok=True)
        offset = int(dest.stat().st_size) if dest.is_file() else 0
        curl_cmd = [
            *_curl_base_args(),
            "-C",
            "-",
            "-o",
            str(dest),
            url,
        ]
        append_log(log_fp, "download backend=curl resume")
        proc = subprocess.run(curl_cmd, capture_output=True, timeout=6 * 3600)
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise IOError(f"curl download failed rc={proc.returncode}: {err}")
        final_size = int(dest.stat().st_size) if dest.is_file() else 0
        _report_download_progress(job_dir, final_size, total or final_size, force=True)
        append_log(log_fp, f"download complete size={final_size}")
        return

    offset = int(dest.stat().st_size) if dest.is_file() else 0
    while True:
        headers: Dict[str, str] = {}
        mode = "ab" if offset > 0 else "wb"
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                code = int(getattr(resp, "status", None) or resp.getcode() or 200)
                if code == 200 and offset > 0:
                    dest.unlink(missing_ok=True)
                    offset = 0
                    continue
                if total <= 0:
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        add = int(cl)
                        total = add + offset if code == 206 else add
                    cr = resp.headers.get("Content-Range") or ""
                    if "/" in cr:
                        try:
                            total = int(cr.rsplit("/", 1)[-1])
                        except ValueError:
                            pass
                with dest.open(mode) as out_fp:
                    while True:
                        buf = resp.read(chunk_size)
                        if not buf:
                            break
                        out_fp.write(buf)
                        offset += len(buf)
                        _report_download_progress(job_dir, offset, total)
                break
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                break
            raise

    final_size = int(dest.stat().st_size) if dest.is_file() else 0
    if total > 0 and final_size < total:
        raise IOError(f"下载未完成: {final_size}/{total} bytes")
    _report_download_progress(job_dir, final_size, total or final_size, force=True)
    append_log(log_fp, f"download complete size={final_size}")


class UnsafeArchiveMember(RuntimeError):
    """A downloaded payload member would escape the job directory."""


def _safe_payload_target(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise UnsafeArchiveMember("unsafe payload member path: {!r}".format(name))
    normalized = name.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise UnsafeArchiveMember("unsafe payload member path: {!r}".format(name))
    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if any(re.match(r"^[A-Za-z]:", part) for part in parts):
        raise UnsafeArchiveMember("unsafe payload member path: {!r}".format(name))
    if any(
        part == ".." or part.rstrip(" .") in ("", ".", "..") for part in parts
    ):
        raise UnsafeArchiveMember("unsafe payload member path: {!r}".format(name))

    root_real = os.path.realpath(str(root))
    target = root.joinpath(*parts)
    target_real = os.path.realpath(str(target))
    try:
        inside = os.path.commonpath((root_real, target_real)) == root_real
    except ValueError:
        inside = False
    if not inside:
        raise UnsafeArchiveMember("unsafe payload member path: {!r}".format(name))

    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise UnsafeArchiveMember(
                "payload member traverses a symbolic link: {!r}".format(name)
            )
    if target.is_symlink():
        raise UnsafeArchiveMember(
            "payload member targets a symbolic link: {!r}".format(name)
        )
    return Path(target_real)


def _safe_extract_payload_tar(tar_path: Path, job_dir: Path) -> None:
    """Extract only regular files/directories below ``job_dir``.

    The remote agent is deployed as a standalone script, so this intentionally
    mirrors the server-side archive guard instead of importing LabelU modules.
    """
    root = Path(job_dir)
    raw_root = Path(os.path.abspath(str(root)))
    current = raw_root
    while True:
        if current.is_symlink():
            raise UnsafeArchiveMember(
                "payload destination must not traverse a symbolic link"
            )
        parent = current.parent
        if parent == current:
            break
        current = parent
    root.mkdir(parents=True, exist_ok=True)
    root = Path(os.path.realpath(str(root)))
    mode = "r:gz" if tar_path.name.endswith(".gz") else "r"
    with tarfile.open(tar_path, mode) as tar:
        validated = []
        for member in tar.getmembers():
            target = _safe_payload_target(root, member.name or "")
            if not (member.isdir() or member.isfile()):
                raise UnsafeArchiveMember(
                    "unsafe payload link or special member: {!r}".format(member.name)
                )
            if target == root and not member.isdir():
                raise UnsafeArchiveMember(
                    "payload file member resolves to job root: {!r}".format(
                        member.name
                    )
                )
            validated.append((member, target))

        for member, target in validated:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise UnsafeArchiveMember(
                    "payload member has no file payload: {!r}".format(member.name)
                )
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def extract_payload_archive(tar_path: Path, job_dir: Path, log_fp: TextIO) -> None:
    append_log(log_fp, f"extract {tar_path.name}")
    _safe_extract_payload_tar(tar_path, job_dir)
    tar_path.unlink(missing_ok=True)


def main() -> int:
    global _CURRENT_OPERATION_ID, _CURRENT_SPEC_SHA256
    global _CURRENT_HMAC_SECRET, _CURRENT_HMAC_KEY_ID, _HMAC_REQUIRED
    parser = argparse.ArgumentParser(description="LabelU remote YOLO train agent")
    parser.add_argument("--job-dir", required=True, help="远程作业目录")
    parser.add_argument(
        "--payload-url",
        default="",
        help="平台 HTTP 拉取地址（含 token query），下载后解压再训练",
    )
    parser.add_argument(
        "--payload-total-bytes",
        type=int,
        default=0,
        help="payload 总字节数（用于进度条，可选）",
    )
    parser.add_argument(
        "--payload-filename",
        default="job_payload.tar.gz",
        help="本地保存的文件名（与平台打包格式一致）",
    )
    parser.add_argument(
        "--payload-parallel",
        type=int,
        default=8,
        help="并行 Range 下载线程数（1=单连接）",
    )
    parser.add_argument(
        "--operation-id",
        default="",
        help="本次远程训练操作世代标识",
    )
    parser.add_argument(
        "--spec-sha256",
        default="",
        help="平台生成的 job_spec.json 内容摘要",
    )
    parser.add_argument(
        "--hmac-key-file",
        default="",
        help="平台通过 SSH 临时下发的 HMAC 密钥文件路径",
    )
    parser.add_argument(
        "--hmac-key-id",
        default="v1",
        help="HMAC 密钥版本标识",
    )
    parser.add_argument(
        "--hmac-required",
        action="store_true",
        help="要求所有阶段、结果和权重制品必须带 HMAC",
    )
    args = parser.parse_args()
    _CURRENT_OPERATION_ID = str(args.operation_id or "").strip()
    _CURRENT_SPEC_SHA256 = str(args.spec_sha256 or "").strip()
    _CURRENT_HMAC_KEY_ID = str(args.hmac_key_id or "v1").strip() or "v1"
    _HMAC_REQUIRED = bool(args.hmac_required)
    job_dir = Path(args.job_dir).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    _CURRENT_HMAC_SECRET = _read_hmac_key(str(args.hmac_key_file or "").strip())
    if _HMAC_REQUIRED and not _CURRENT_HMAC_SECRET:
        if args.hmac_key_file:
            try:
                Path(args.hmac_key_file).unlink(missing_ok=True)
            except OSError:
                pass
        raise RuntimeError("远程训练 HMAC 密钥缺失，拒绝启动 Agent")
    log_path = job_dir / "train.log"
    write_phase(job_dir, "starting", detail=f"agent pid={os.getpid()}")

    lock_fp: Optional[TextIO] = None
    with log_path.open("a", encoding="utf-8") as log_fp:
        append_log(log_fp, f"===== agent_worker started job_dir={job_dir} =====")
        try:
            lock_fp = _acquire_job_lock(job_dir)
            append_log(log_fp, f"agent lock acquired pid={os.getpid()}")
            payload_url = str(args.payload_url or "").strip()
            if payload_url:
                parallel = max(1, int(args.payload_parallel or 1))
                total_hint = int(args.payload_total_bytes or 0)
                fname = str(args.payload_filename or "job_payload.tar.gz").strip()
                if not fname.endswith((".tar", ".tar.gz")):
                    fname = "job_payload.tar.gz"
                dest = job_dir / Path(fname).name
                parts_dir = job_dir / ".payload_parts"
                if parallel > 1 and dest.exists() and dest.stat().st_size > 0 and not any(parts_dir.glob("part-*.bin")):
                    append_log(log_fp, "resume sequential partial (legacy) before parallel")
                    download_payload_with_resume(
                        payload_url, dest, job_dir, log_fp, total_hint=total_hint
                    )
                elif parallel > 1:
                    download_payload_parallel(
                        payload_url,
                        dest,
                        job_dir,
                        log_fp,
                        total_hint=total_hint,
                        workers=parallel,
                    )
                else:
                    download_payload_with_resume(
                        payload_url,
                        dest,
                        job_dir,
                        log_fp,
                        total_hint=total_hint,
                    )
                extract_payload_archive(dest, job_dir, log_fp)

            spec_path = job_dir / "job_spec.json"
            if not spec_path.is_file():
                append_log(log_fp, "job_spec.json not found after payload setup")
                print("job_spec.json not found", file=sys.stderr)
                return 2

            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            if not isinstance(spec, dict):
                raise RuntimeError("job_spec.json 不是 JSON 对象")
            if not _verify_json_payload(spec, required=_HMAC_REQUIRED or bool(_CURRENT_HMAC_SECRET)):
                raise RuntimeError("远程训练 job_spec.json 签名校验失败")
            spec_operation_id = str(spec.get("operation_id") or "").strip()
            if _CURRENT_OPERATION_ID and spec_operation_id and _CURRENT_OPERATION_ID != spec_operation_id:
                raise RuntimeError("远程训练操作世代与作业清单不一致")
            if not _CURRENT_OPERATION_ID:
                _CURRENT_OPERATION_ID = spec_operation_id
            _CURRENT_SPEC_SHA256 = _file_sha256(spec_path)
            if args.spec_sha256 and _CURRENT_SPEC_SHA256 != str(args.spec_sha256).strip():
                raise RuntimeError("远程训练 job_spec.json 内容摘要不一致")
            return run_train(job_dir, spec, log_fp)
        except Exception as exc:
            tb = traceback.format_exc()
            append_log(log_fp, tb)
            err = str(exc)[:500]
            write_phase(job_dir, "failed", detail=err)
            (job_dir / "agent_result.json").write_text(
                json.dumps(
                    _signed_json_payload(
                        {
                        "status": "failed",
                        "finished_at": _iso_now(),
                        "error_message": err,
                        "operation_id": _CURRENT_OPERATION_ID or None,
                        "spec_sha256": _CURRENT_SPEC_SHA256 or None,
                        }
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(tb, file=sys.stderr)
            return 1
        finally:
            if lock_fp is not None:
                try:
                    _release_job_lock(lock_fp)
                except OSError:
                    pass
            if args.hmac_key_file:
                try:
                    Path(args.hmac_key_file).unlink(missing_ok=True)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
