from __future__ import annotations

import base64
import csv
import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from labelu.internal.common.pipeline_process_log import (
    yolo_train_log_path,
    yolo_train_work_root,
)
from labelu.internal.common.yolo_training_metrics import (
    build_class_metrics_detail,
    dataset_counts_from_metrics_summary,
    metrics_contract_from_summary,
    read_test_val_metrics_from_summary,
)
from labelu.internal.application.service.yolo_dataset import count_class_instances_in_dataset

SERIES_COLUMNS = {
    "train_loss": ("train/loss", "训练损失"),
    "train_box_loss": ("train/box_loss", "训练损失"),
    "val_box_loss": ("val/box_loss", "验证损失"),
    "train_cls_loss": ("train/cls_loss", "训练分类损失"),
    "val_cls_loss": ("val/cls_loss", "验证分类损失"),
    "train_dfl_loss": ("train/dfl_loss", "训练 DFL 损失"),
    "val_dfl_loss": ("val/dfl_loss", "验证 DFL 损失"),
    "map50": ("metrics/mAP50(B)", "mAP@0.5"),
    "map50_95": ("metrics/mAP50-95(B)", "mAP@0.5:0.95"),
    "precision": ("metrics/precision(B)", "Precision"),
    "recall": ("metrics/recall(B)", "Recall"),
    "learning_rate": ("lr/pg0", "学习率"),
    "gpu_temperature": ("gpu/temperature", "GPU 温度"),
    "gpu_memory": ("gpu/memory", "显存使用"),
}

PREVIEW_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PREVIEW_NAME_HINTS = (
    "pred",
    "val_batch",
    "results",
    "confusion_matrix",
    "labels",
    "curve",
)
ANALYSIS_SOURCE_ROOTS = {
    "train": Path("train"),
    "plate_detection_pose_final": Path("plate_detection") / "pose_final_fit" / "train",
    "plate_detection_pose_train": Path("plate_detection") / "pose_train" / "train",
    "plate_pose_final": Path("plate_components") / "pose_final_fit" / "train",
    "plate_pose_train": Path("plate_components") / "pose_train" / "train",
    "plate_ocr": Path("plate_components") / "evaluation" / "ocr" / "analysis",
    "plate_color": Path("plate_components") / "evaluation" / "color" / "analysis",
    "plate_type": Path("plate_components") / "evaluation" / "plate_type" / "analysis",
}
PLATE_COMPONENT_META = {
    "pose": {
        "label": "车牌定位与四角",
        "model_filename": "plate_pose_best.pt",
        "source_key": "plate_pose_train",
    },
    "ocr": {
        "label": "车牌号码 OCR",
        "model_filename": "plate_ocr_best.pt",
        "source_key": "plate_ocr",
    },
    "color": {
        "label": "车牌颜色",
        "model_filename": "plate_color_best.pt",
        "source_key": "plate_color",
    },
    "plate_type": {
        "label": "车牌类型",
        "model_filename": "plate_type_best.pt",
        "source_key": "plate_type",
    },
}
LOG_TIME_RE = re.compile(r"^\[(?P<time>[^\]]+)\]\s*(?P<message>.*)$")
KWARGS_RE = re.compile(r"model\.train kwargs=(?P<json>\{.*\})")
EPOCH_RE = re.compile(r"\bepoch=(?P<epoch>\d+)(?:/(?P<total>\d+))?")
IPS_RE = re.compile(
    r"(?:ips|img/s|images/s|每秒图像数)\s*[=:]\s*(?P<value>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
BATCH_RE = re.compile(
    r"(?:当前 Batch|current_batch|batch_progress)\s*[=:]\s*(?P<current>\d+)(?:/(?P<total>\d+))?",
    re.IGNORECASE,
)


def _job_root(job_id: int) -> Path:
    return yolo_train_work_root(job_id).resolve()


def _analysis_train_source(
    job_id: int,
    job_payload: Dict[str, Any],
) -> Tuple[str, Path]:
    root = _job_root(job_id)
    job_type = str(job_payload.get("job_type") or "").strip()
    if job_type in {"plate_detection", "plate_recognition"}:
        source_keys = (
            ("plate_detection_pose_train", "plate_detection_pose_final")
            if job_type == "plate_detection"
            else ("plate_pose_train", "plate_pose_final")
        )
        # Prefer the independent train/val/test evaluation stage. The final fit
        # intentionally trains on all confirmed samples and is not held out.
        for source_key in source_keys:
            source_dir = root / ANALYSIS_SOURCE_ROOTS[source_key]
            if (source_dir / "results.csv").is_file():
                return source_key, source_dir
        for source_key in source_keys:
            source_dir = root / ANALYSIS_SOURCE_ROOTS[source_key]
            if source_dir.is_dir():
                return source_key, source_dir
    return "train", root / ANALYSIS_SOURCE_ROOTS["train"]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _float_or_none(raw: Any) -> Optional[float]:
    if raw in (None, "", "nan", "NaN"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int_or_none(raw: Any) -> Optional[int]:
    val = _float_or_none(raw)
    return int(val) if val is not None else None


def _read_results_series(train_dir: Path) -> Dict[str, List[Dict[str, float]]]:
    out = {key: [] for key in SERIES_COLUMNS}
    csv_path = train_dir / "results.csv"
    if not csv_path.is_file():
        return out
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return out

    for idx, row in enumerate(rows, start=1):
        epoch = _int_or_none(row.get("epoch")) or idx
        for key, (column, _label) in SERIES_COLUMNS.items():
            value = _float_or_none(row.get(column))
            if value is None:
                continue
            out[key].append({"epoch": epoch, "value": value})
    return out


def _read_results_rows(train_dir: Path) -> List[Dict[str, str]]:
    csv_path = train_dir / "results.csv"
    if not csv_path.is_file():
        return []
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _read_metrics_summary(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _fill_series_from_metric_summary(
    series: Dict[str, List[Dict[str, float]]],
    job_payload: Dict[str, Any],
) -> None:
    summary = _read_metrics_summary(job_payload.get("metrics_summary"))
    final_metrics = summary.get("final_metrics")
    if not isinstance(final_metrics, dict):
        return
    epoch = (
        _int_or_none(final_metrics.get("epoch"))
        or _int_or_none(job_payload.get("epochs"))
        or 1
    )
    field_map = {
        "map50": "map50",
        "map50_95": "map50_95",
        "precision": "precision",
        "recall": "recall",
    }
    for series_key, summary_key in field_map.items():
        if series.get(series_key):
            continue
        value = _float_or_none(final_metrics.get(summary_key))
        if value is not None:
            series[series_key].append({"epoch": epoch, "value": value})


def _last_epoch_seconds_from_results(rows: List[Dict[str, str]]) -> Optional[float]:
    timed_rows = [
        (_int_or_none(row.get("epoch")), _float_or_none(row.get("time")))
        for row in rows
    ]
    timed_rows = [(ep, t) for ep, t in timed_rows if ep is not None and t is not None]
    if len(timed_rows) < 2:
        return None
    prev_epoch, prev_time = timed_rows[-2]
    last_epoch, last_time = timed_rows[-1]
    epoch_delta = max(1, int(last_epoch) - int(prev_epoch))
    seconds = (float(last_time) - float(prev_time)) / epoch_delta
    return seconds if seconds > 0 else None


def _file_mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _parse_log_line(line: str) -> Dict[str, str]:
    text = line.rstrip("\n")
    if text.startswith("[") and "] " in text:
        ts, msg = text.split("] ", 1)
        return {"time": ts[1:], "message": msg}
    return {"time": "", "message": text}


def _read_tail_logs(job_id: int, limit: int = 10) -> List[Dict[str, str]]:
    path = yolo_train_log_path(job_id)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [_parse_log_line(x) for x in lines[-limit:]]


def _read_process_log_lines(job_id: int) -> List[str]:
    path = yolo_train_log_path(job_id)
    if not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _line_time(line: str) -> Optional[datetime]:
    m = LOG_TIME_RE.match(line)
    if not m:
        return None
    raw = m.group("time").strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _read_train_kwargs(lines: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for line in lines:
        m = KWARGS_RE.search(line)
        if not m:
            continue
        try:
            data = json.loads(m.group("json"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.update(data)
    return out


def _epoch_times(
    lines: List[str], *, completed_only: bool = False
) -> List[Tuple[int, datetime]]:
    out: List[Tuple[int, datetime]] = []
    seen = set()
    for line in lines:
        if completed_only and ("train_started" in line or "mAP" not in line):
            continue
        m = EPOCH_RE.search(line)
        ts = _line_time(line)
        if not m or ts is None:
            continue
        epoch = int(m.group("epoch"))
        key = (epoch, ts)
        if key in seen:
            continue
        seen.add(key)
        out.append((epoch, ts))
    return out


def _seconds_label(seconds: Optional[float]) -> Optional[str]:
    if seconds is None or seconds < 0 or not math.isfinite(seconds):
        return None
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"


def _estimate_runtime_cards(
    job_payload: Dict[str, Any],
    progress: Dict[str, Any],
    lines: List[str],
    train_dir: Path,
    result_rows: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:
    kwargs = _read_train_kwargs(lines)
    batch_size = (
        _int_or_none(kwargs.get("batch"))
        or _int_or_none((job_payload.get("hyperparams") or {}).get("batch"))
        or 1
    )
    train_samples = _int_or_none(job_payload.get("train_sample_count")) or 0
    batch_total = (
        int(math.ceil(train_samples / max(batch_size, 1)))
        if train_samples > 0
        else None
    )
    current_epoch = int(progress.get("current_epoch") or 0)
    current_batch = None

    ips = None
    log_batch_current = None
    log_batch_total = None
    for line in reversed(lines[-200:]):
        if ips is None:
            m_ips = IPS_RE.search(line)
            if m_ips:
                ips = _float_or_none(m_ips.group("value"))
        if log_batch_current is None:
            m_batch = BATCH_RE.search(line)
            if m_batch:
                log_batch_current = _int_or_none(m_batch.group("current"))
                log_batch_total = _int_or_none(m_batch.group("total"))
        if ips is not None and log_batch_current is not None:
            break
    if log_batch_current is not None:
        current_batch = log_batch_current
    if log_batch_total is not None:
        batch_total = log_batch_total

    epoch_seconds = _last_epoch_seconds_from_results(result_rows)
    if epoch_seconds is None:
        epoch_times = _epoch_times(lines, completed_only=True)
    else:
        epoch_times = []
    if epoch_seconds is None and len(epoch_times) >= 2:
        pairs = list(zip(epoch_times, epoch_times[1:]))[-5:]
        durations = [
            (b_time - a_time).total_seconds() / max(1, b_epoch - a_epoch)
            for (a_epoch, a_time), (b_epoch, b_time) in pairs
            if b_epoch > a_epoch and (b_time - a_time).total_seconds() > 0
        ]
        if durations:
            epoch_seconds = sum(durations) / len(durations)
            if ips is None and train_samples > 0:
                ips = round(train_samples / epoch_seconds, 1)
    if epoch_seconds is not None and ips is None and train_samples > 0:
        ips = round(train_samples / epoch_seconds, 1)

    eta_seconds = None
    target_epochs = int(progress.get("target_epochs") or 0)
    if target_epochs > current_epoch and epoch_seconds:
        eta_seconds = (target_epochs - current_epoch) * epoch_seconds

    return {
        "current_batch": {
            "label": "当前 Batch",
            "value": current_batch,
            "total": batch_total,
        },
        "ips": {
            "label": "每秒图像数",
            "value": round(float(ips), 1) if ips else None,
            "unit": "img/s",
        },
        "eta": {
            "label": "预计完成",
            "value": _seconds_label(eta_seconds),
            "seconds": int(eta_seconds) if eta_seconds else None,
        },
    }


def _encode_asset_id(asset_id: str) -> str:
    return (
        base64.urlsafe_b64encode(asset_id.encode("utf-8")).decode("ascii").rstrip("=")
    )


def decode_asset_id(raw: str) -> str:
    text = str(raw or "")
    if ":" in text:
        return text
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode((text + padding).encode("ascii")).decode(
            "utf-8"
        )
    except Exception:
        return ""


def _preview_asset_id(path: Path, root: Path, source_key: str = "train") -> str:
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    return source_key + ":" + rel


def _preview_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "results.png":
        return "training_summary"
    if "confusion_matrix" in name:
        return "confusion_matrix"
    if "curve" in name:
        return "metric_curve"
    if "val_batch" in name:
        return "validation_preview"
    if name.startswith("labels"):
        return "label_distribution"
    if "pred" in name:
        return "prediction"
    return "other"


def _read_preview_images(
    job_id: int,
    job_payload: Dict[str, Any],
) -> List[Dict[str, str]]:
    source_key, train_dir = _analysis_train_source(job_id, job_payload)
    return _read_preview_images_from_source(job_id, source_key, train_dir)


def _read_preview_images_from_source(
    job_id: int,
    source_key: str,
    source_dir: Path,
) -> List[Dict[str, str]]:
    if not source_dir.is_dir():
        return []
    candidates: List[Path] = []
    for path in source_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in PREVIEW_EXTENSIONS:
            continue
        name = path.name.lower()
        if any(h in name for h in PREVIEW_NAME_HINTS):
            candidates.append(path)
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return [
        {
            "asset_id": _preview_asset_id(path, source_dir, source_key),
            "filename": path.name,
            "kind": _preview_kind(path),
            "url": f"/api/v1/yolo-train-jobs/{int(job_id)}/analysis/assets/{_encode_asset_id(_preview_asset_id(path, source_dir, source_key))}",
        }
        for path in candidates
    ]


def resolve_analysis_asset_path(job_id: int, asset_id: str) -> Optional[Path]:
    decoded = decode_asset_id(asset_id)
    if ":" not in decoded:
        return None
    source_key, rel = decoded.split(":", 1)
    source_rel = ANALYSIS_SOURCE_ROOTS.get(source_key)
    if source_rel is None:
        return None
    if not rel or Path(rel).is_absolute():
        return None
    train_dir = _job_root(job_id) / source_rel
    path = (train_dir / rel).resolve()
    if not _is_under(path, train_dir):
        return None
    if not path.is_file() or path.suffix.lower() not in PREVIEW_EXTENSIONS:
        return None
    return path


def _latest_series_value(points: List[Dict[str, float]]) -> Optional[float]:
    return points[-1]["value"] if points else None


def _read_class_performance(job_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    detail = build_class_metrics_detail(job_payload.get("metrics_summary"))
    if not detail:
        return []
    out: List[Dict[str, Any]] = []
    for row in detail.get("rows") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "index": row.get("index"),
                "label": row.get("label"),
                "map50": row.get("map50"),
                "map50_95": row.get("map50_95"),
                "precision": row.get("precision"),
                "recall": row.get("recall"),
                "test_map50": row.get("test_map50"),
                "test_map50_95": row.get("test_map50_95"),
                "test_precision": row.get("test_precision"),
                "test_recall": row.get("test_recall"),
                "train_instances": row.get("train_instances"),
                "val_instances": row.get("val_instances"),
                "test_instances": row.get("test_instances"),
                "train_images": row.get("train_images"),
                "val_images": row.get("val_images"),
                "test_images": row.get("test_images"),
            }
        )
    return out


def _build_health_diagnosis(
    metric_contract: Dict[str, Any], class_performance: List[Dict[str, Any]]
) -> Dict[str, Any]:
    notices: List[Dict[str, str]] = []
    last = metric_contract.get("last_epoch_metrics") or {}
    best = metric_contract.get("best_epoch_metrics") or {}
    last_map = _float_or_none(last.get("map50_95"))
    best_map = _float_or_none(best.get("map50_95"))
    post_best_regression = (
        round(best_map - last_map, 4)
        if last_map is not None and best_map is not None and best_map > last_map
        else None
    )
    if post_best_regression is not None and post_best_regression >= 0.01:
        notices.append(
            {
                "code": "post_best_regression",
                "severity": "warning",
                "title": "最佳轮次后指标回落",
                "detail": f"最佳 epoch {best.get('epoch')} 的 mAP50-95={best_map:.4f}，末轮为 {last_map:.4f}。",
                "action": "优先评估 best.pt，不要仅因末轮较低而继续增加训练轮数。",
            }
        )

    populated = [
        row
        for row in class_performance
        if _float_or_none(row.get("recall")) is not None
    ]
    weak = (
        min(populated, key=lambda row: float(row.get("recall"))) if populated else None
    )
    if weak and float(weak.get("recall")) < 0.5:
        notices.append(
            {
                "code": "weak_class_recall",
                "severity": "critical",
                "title": f"类别 {weak.get('label')} 召回率偏低",
                "detail": f"Val Recall={float(weak.get('recall')):.4f}，容易产生漏检。",
                "action": "先检查该类别漏检图片、标注质量和目标尺寸分布。",
            }
        )

    instance_rows = [
        row for row in class_performance if int(row.get("train_instances") or 0) > 0
    ]
    imbalance_ratio = None
    if len(instance_rows) >= 2:
        largest = max(
            instance_rows, key=lambda row: int(row.get("train_instances") or 0)
        )
        smallest = min(
            instance_rows, key=lambda row: int(row.get("train_instances") or 0)
        )
        small_count = int(smallest.get("train_instances") or 0)
        large_count = int(largest.get("train_instances") or 0)
        imbalance_ratio = round(large_count / small_count, 1) if small_count else None
        if imbalance_ratio is not None and imbalance_ratio >= 5:
            notices.append(
                {
                    "code": "class_imbalance",
                    "severity": "critical",
                    "title": "训练类别数量失衡",
                    "detail": f"{largest.get('label')} {large_count} 框，{smallest.get('label')} {small_count} 框，约 {imbalance_ratio}:1。",
                    "action": f"优先补充 {smallest.get('label')} 的有效样本，而不是单纯增加 epochs。",
                }
            )

    critical = sum(1 for row in notices if row["severity"] == "critical")
    warning = sum(1 for row in notices if row["severity"] == "warning")
    return {
        "status": "critical" if critical else ("warning" if warning else "healthy"),
        "summary": notices[0]["title"] if notices else "当前未发现显著训练异常",
        "notices": notices,
        "weak_class": weak,
        "class_imbalance_ratio": imbalance_ratio,
        "post_best_regression": post_best_regression,
    }


def _read_runtime_gpu_memory() -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"label": "显存占用", "value": None}
    if proc.returncode != 0:
        return {"label": "显存占用", "value": None}
    used_total = 0.0
    total_total = 0.0
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        used = _float_or_none(parts[0])
        total = _float_or_none(parts[1])
        if used is None or total is None or total <= 0:
            continue
        used_total += used
        total_total += total
    if total_total <= 0:
        return {"label": "显存占用", "value": None}
    return {
        "label": "显存占用",
        "value": round((used_total / total_total) * 100, 1),
        "used_mb": int(round(used_total)),
        "total_mb": int(round(total_total)),
    }


def _progress(
    job_payload: Dict[str, Any], series: Dict[str, List[Dict[str, float]]]
) -> Dict[str, Any]:
    target = int(job_payload.get("epochs") or 0)
    current = job_payload.get("current_epoch")
    if current is None:
        all_epochs = [p["epoch"] for rows in series.values() for p in rows]
        current = int(max(all_epochs)) if all_epochs else 0
    current = int(current or 0)
    percent = round((current / target) * 100) if target > 0 else 0
    return {
        "current_epoch": current,
        "target_epochs": target,
        "percent": max(0, min(100, percent)),
        "snapshot": job_payload.get("progress_snapshot") or "",
    }


def _dict_value(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _plate_component_analyses(
    job_id: int,
    job_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    job_type = str(job_payload.get("job_type") or "").strip()
    if job_type not in {"plate_detection", "plate_recognition"}:
        return []
    root = _job_root(job_id)
    component_root = (
        "plate_detection" if job_type == "plate_detection" else "plate_components"
    )
    component_meta = PLATE_COMPONENT_META
    if job_type == "plate_detection":
        component_meta = {
            "pose": {
                "label": "车牌检测与四角定位",
                "model_filename": "plate_detection_best.pt",
                "source_key": "plate_detection_pose_train",
            }
        }
    artifacts = _dict_value(job_payload.get("component_artifacts"))
    statuses = _dict_value(job_payload.get("component_status"))
    summary_metrics = _dict_value(job_payload.get("component_metrics"))
    result: List[Dict[str, Any]] = []
    for component, meta in component_meta.items():
        artifact = artifacts.get(component) or {}
        evaluation_artifact = (
            artifact.get("evaluation_stage")
            if isinstance(artifact.get("evaluation_stage"), dict)
            else artifact
        )
        metric_row = summary_metrics.get(component) or {}
        source_key = str(meta["source_key"])
        source_dir = root / ANALYSIS_SOURCE_ROOTS[source_key]
        component_series = _read_results_series(source_dir)
        analysis_metrics = metric_row.get("metrics") or {}
        if not analysis_metrics and component != "pose":
            analysis_metrics = evaluation_artifact.get("evaluation_metrics") or {}
        model_filename = str(meta["model_filename"])
        artifact_pt = str(artifact.get("pt") or "").strip()
        if artifact_pt:
            model_filename = Path(artifact_pt).name
        epoch_values = [
            int(point["epoch"])
            for points in component_series.values()
            for point in points
            if point.get("epoch") is not None
        ]
        live_progress: Dict[str, Any] = {}
        progress_path = root / component_root / "progress" / f"{component}.json"
        if progress_path.is_file():
            try:
                raw_progress = json.loads(progress_path.read_text(encoding="utf-8"))
                if isinstance(raw_progress, dict):
                    live_progress = raw_progress
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                live_progress = {}
        current_epoch = int(live_progress.get("current_epoch") or 0)
        if not current_epoch and epoch_values:
            current_epoch = max(epoch_values)
        target_epochs = int(
            live_progress.get("target_epochs") or job_payload.get("epochs") or 0
        )
        progress_snapshot = ""
        if live_progress.get("loss") is not None:
            try:
                progress_snapshot = f"loss={float(live_progress['loss']):.4f}"
            except (TypeError, ValueError):
                progress_snapshot = ""
        result.append(
            {
                "key": component,
                "label": metric_row.get("label") or meta["label"],
                "status": (
                    (statuses.get(component) or {}).get("status")
                    or metric_row.get("status")
                    or "PENDING"
                ),
                "model_filename": model_filename,
                "model_available": (root / component_root / model_filename).is_file(),
                "train_sample_count": (
                    metric_row.get("train_sample_count")
                    if metric_row.get("train_sample_count") is not None
                    else evaluation_artifact.get("train_sample_count")
                ),
                "final_loss": evaluation_artifact.get("final_loss"),
                "current_epoch": current_epoch,
                "target_epochs": target_epochs,
                "percent": (
                    min(100, round((current_epoch / target_epochs) * 100))
                    if target_epochs
                    else 0
                ),
                "progress_snapshot": progress_snapshot,
                "phase": str(live_progress.get("phase") or ""),
                "metrics": analysis_metrics,
                "series": component_series,
                "preview_images": _read_preview_images_from_source(
                    job_id, source_key, source_dir
                ),
                "analysis_source": {
                    "key": source_key,
                    "relative_path": ANALYSIS_SOURCE_ROOTS[source_key].as_posix(),
                },
            }
        )
    return result


def build_train_analysis(job_id: int, job_payload: Dict[str, Any]) -> Dict[str, Any]:
    from labelu.internal.application.service import yolo_train as yolo_train_service
    from labelu.internal.common.yolo_train_hyperparams import (
        RUNTIME_EDITABLE_TRAIN_HYPER_KEYS,
        hyperparam_field_meta,
    )

    source_key, train_dir = _analysis_train_source(job_id, job_payload)
    series = _read_results_series(train_dir)
    _fill_series_from_metric_summary(series, job_payload)
    result_rows = _read_results_rows(train_dir)
    progress = _progress(job_payload, series)
    log_lines = _read_process_log_lines(job_id)
    runtime_cards = _estimate_runtime_cards(
        job_payload, progress, log_lines, train_dir, result_rows
    )
    summary_counts = dataset_counts_from_metrics_summary(
        job_payload.get("metrics_summary")
    )
    train_samples = job_payload.get("train_sample_count")
    val_samples = job_payload.get("val_sample_count")
    test_samples = job_payload.get("test_sample_count")
    if train_samples in (None, 0):
        train_samples = summary_counts.get("train")
    if val_samples in (None, 0):
        val_samples = summary_counts.get("val")
    if test_samples in (None, 0):
        test_samples = summary_counts.get("test")
    total_samples = job_payload.get("total_sample_count")
    if total_samples is None:
        total_samples = sum(
            int(x or 0) for x in (train_samples, val_samples, test_samples)
        )
    gpu_memory = {
        "label": "显存占用",
        "value": _latest_series_value(series.get("gpu_memory") or []),
    }
    if gpu_memory["value"] is None and str(job_payload.get("status") or "").upper() in {
        "PROCESSING",
        "PENDING",
    }:
        gpu_memory = _read_runtime_gpu_memory()
    test_val_metrics = (
        read_test_val_metrics_from_summary(job_payload.get("metrics_summary")) or {}
    )
    metric_contract = metrics_contract_from_summary(
        job_payload.get("metrics_summary"), train_dir=train_dir
    )
    class_performance = _read_class_performance(job_payload)
    # Older jobs only persisted box counts. Re-read the immutable training
    # dataset when available so the detail page can show distinct images per
    # label without confusing image counts with box instance counts.
    dataset_root = _job_root(job_id) / "dataset"
    if class_performance and dataset_root.is_dir():
        class_names = [str(item.get("label") or "") for item in class_performance]
        image_counts = count_class_instances_in_dataset(dataset_root, class_names)
        image_counts_by_index = {
            int(item.get("index")): item
            for item in image_counts
            if item.get("index") is not None
        }
        for item in class_performance:
            count = image_counts_by_index.get(int(item.get("index", -1)))
            if count:
                item["train_images"] = count.get("train_images")
                item["val_images"] = count.get("val_images")
                item["test_images"] = count.get("test_images")
    health_diagnosis = _build_health_diagnosis(metric_contract, class_performance)
    authoritative = metric_contract.get("authoritative_val_metrics") or {}
    pending_runtime_hyperparams = yolo_train_service.read_pending_runtime_hyperparams(
        int(job_id)
    )
    return {
        "job": job_payload,
        "progress": progress,
        "series": series,
        "status_cards": {
            "total_samples": {"label": "总样本数", "value": total_samples or 0},
            "sample_split": {
                "label": "样本划分",
                "train": int(train_samples or 0),
                "val": int(val_samples or 0),
                "test": int(test_samples or 0),
            },
            "current_batch": runtime_cards["current_batch"],
            "ips": runtime_cards["ips"],
            "eta": runtime_cards["eta"],
            "map50": {
                "label": "mAP@0.5",
                "value": (
                    authoritative.get("map50")
                    if authoritative.get("map50") is not None
                    else _latest_series_value(series.get("map50") or [])
                ),
            },
            "map50_95": {
                "label": "mAP@0.5:0.95",
                "value": (
                    authoritative.get("map50_95")
                    if authoritative.get("map50_95") is not None
                    else _latest_series_value(series.get("map50_95") or [])
                ),
            },
            "precision": {
                "label": "Precision",
                "value": (
                    authoritative.get("precision")
                    if authoritative.get("precision") is not None
                    else _latest_series_value(series.get("precision") or [])
                ),
            },
            "recall": {
                "label": "Recall",
                "value": (
                    authoritative.get("recall")
                    if authoritative.get("recall") is not None
                    else _latest_series_value(series.get("recall") or [])
                ),
            },
            "test_map50": {
                "label": "测试集 mAP@0.5",
                "value": test_val_metrics.get("map50"),
            },
            "test_map50_95": {
                "label": "测试集 mAP@0.5:0.95",
                "value": test_val_metrics.get("map50_95"),
            },
            "gpu_memory": gpu_memory,
        },
        "metric_contract": metric_contract,
        "health_diagnosis": health_diagnosis,
        "class_performance": class_performance,
        "component_analyses": _plate_component_analyses(job_id, job_payload),
        "logs": _read_tail_logs(job_id),
        "preview_images": _read_preview_images(job_id, job_payload),
        "analysis_source": {
            "key": source_key,
            "relative_path": ANALYSIS_SOURCE_ROOTS[source_key].as_posix(),
        },
        "hyperparam_fields": hyperparam_field_meta(),
        "runtime_editable_hyperparam_keys": list(RUNTIME_EDITABLE_TRAIN_HYPER_KEYS),
        "pending_runtime_hyperparams": pending_runtime_hyperparams,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
