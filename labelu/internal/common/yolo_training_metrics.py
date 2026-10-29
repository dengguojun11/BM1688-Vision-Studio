"""Ultralytics 训练指标解析（results.csv / metrics_summary）与 AI 评估正文中的精度摘录。"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from labelu.internal.common.config import settings
from labelu.internal.common.yolo_train_hyperparams import default_train_patience

# 与 yolo_train_evaluation._PROVIDER_TITLE 保持一致（避免 service 层循环引用）
YOLO_EVAL_PROVIDER_LABELS: Dict[str, str] = {
    "volcengine": "火山方舟",
    "qwen_plus": "通义千问 Plus",
    "qwen_235b": "通义千问 235B",
}

DEFAULT_EVAL_PROVIDER_ORDER = ("volcengine",)


def resolve_train_dir_from_weights(stored: str | None) -> Optional[Path]:
    """由 weights_path（best.pt）解析训练目录 …/train。"""
    if not stored:
        return None
    p = Path(stored)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / p
    try:
        resolved = p.resolve()
    except OSError:
        return None
    root = Path(settings.MEDIA_ROOT).resolve()
    if not str(resolved).startswith(str(root)):
        return None
    train_dir = resolved.parent.parent
    if train_dir.name != "train":
        return None
    return train_dir


def _parse_results_metric_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one Ultralytics ``results.csv`` row into the public metric shape."""

    def pick_float(*candidates: str) -> Optional[float]:
        for key in candidates:
            if key not in row:
                continue
            raw = row.get(key)
            if raw is None or raw == "" or str(raw).lower() == "nan":
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    epoch: Optional[int] = None
    raw_ep = row.get("epoch")
    if raw_ep not in (None, "", "nan"):
        try:
            epoch = int(float(raw_ep))
        except (TypeError, ValueError):
            epoch = None

    out = {
        "epoch": epoch,
        "time_seconds": pick_float("time", "time_seconds"),
        "train_box_loss": pick_float("train/box_loss", "train_box_loss"),
        "train_cls_loss": pick_float("train/cls_loss", "train_cls_loss"),
        "train_dfl_loss": pick_float("train/dfl_loss", "train_dfl_loss"),
        "val_box_loss": pick_float("val/box_loss", "val_box_loss"),
        "val_cls_loss": pick_float("val/cls_loss", "val_cls_loss"),
        "val_dfl_loss": pick_float("val/dfl_loss", "val_dfl_loss"),
        "map50_95": pick_float(
            "metrics/mAP50-95(B)",
            "metrics/mAP50-95",
            "mAP50-95(B)",
            "mAP50-95",
        ),
        "map50": pick_float(
            "metrics/mAP50(B)",
            "metrics/mAP50",
            "mAP50(B)",
            "mAP50",
        ),
        "precision": pick_float(
            "metrics/precision(B)",
            "metrics/precision",
            "precision(B)",
            "precision",
        ),
        "recall": pick_float(
            "metrics/recall(B)",
            "metrics/recall",
            "recall(B)",
            "recall",
        ),
        "fitness": pick_float("fitness"),
        "learning_rate": pick_float("lr/pg0", "learning_rate"),
    }
    if not any(
        out.get(k) is not None
        for k in (
            "train_box_loss",
            "train_cls_loss",
            "train_dfl_loss",
            "val_box_loss",
            "val_cls_loss",
            "val_dfl_loss",
            "map50_95",
            "map50",
            "precision",
            "recall",
            "fitness",
        )
    ):
        return None
    return out


def _read_results_metric_rows(train_dir: Path) -> List[Dict[str, Any]]:
    csv_path = train_dir / "results.csv"
    if not csv_path.is_file():
        return []
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    return rows


def parse_results_csv_final_metrics(train_dir: Path) -> Optional[Dict[str, Any]]:
    """Read the last Ultralytics validation row (the last epoch, not ``best.pt``)."""
    rows = _read_results_metric_rows(train_dir)
    return _parse_results_metric_row(rows[-1]) if rows else None


def parse_results_csv_history(
    train_dir: Path, *, max_rows: int = 5000
) -> List[Dict[str, Any]]:
    """Read the per-epoch training history from ``results.csv``.

    This is intentionally separate from the final/best metric helpers. The
    detail endpoint is the only consumer and can therefore read a bounded
    history without adding a large payload to the training list polling API.
    """
    if max_rows <= 0:
        return []
    rows = _read_results_metric_rows(train_dir)
    parsed = [
        metric
        for row in rows[-max_rows:]
        if (metric := _parse_results_metric_row(row)) is not None
    ]
    # A partially written last CSV line can have no epoch. It is not useful as
    # a table row and should not make the displayed order ambiguous.
    history = [item for item in parsed if item.get("epoch") is not None]
    for item in history:
        # Ultralytics uses this weighted fitness for best-epoch selection,
        # while older results.csv files often omit the derived column.
        if item.get("fitness") is None and item.get("map50") is not None and item.get("map50_95") is not None:
            item["fitness"] = 0.1 * item["map50"] + 0.9 * item["map50_95"]
    return history


def parse_results_csv_best_metrics(train_dir: Path) -> Optional[Dict[str, Any]]:
    """Read the strongest epoch from ``results.csv`` using Ultralytics fitness semantics."""
    parsed = [
        metric
        for row in _read_results_metric_rows(train_dir)
        if (metric := _parse_results_metric_row(row))
    ]
    if not parsed:
        return None

    def score(metric: Dict[str, Any]) -> tuple[float, float, int]:
        fitness = metric.get("fitness")
        map50_95 = metric.get("map50_95")
        return (
            float(fitness) if fitness is not None else float(map50_95 or -1.0),
            float(map50_95 or -1.0),
            int(metric.get("epoch") or -1),
        )

    best = dict(max(parsed, key=score))
    best["selection_metric"] = (
        "fitness" if best.get("fitness") is not None else "map50_95"
    )
    return best


def dataset_counts_from_metrics_summary(raw: Any) -> Dict[str, Optional[int]]:
    """从 metrics_summary.dataset 读取 train/val/test 样本数。"""
    out: Dict[str, Optional[int]] = {"train": None, "val": None, "test": None}
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return out
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return out
    if not isinstance(obj, dict):
        return out
    ds = obj.get("dataset")
    if not isinstance(ds, dict):
        return out
    for key in ("train", "val", "test"):
        v = ds.get(key)
        if v is not None:
            try:
                out[key] = int(v)
            except (TypeError, ValueError):
                pass
    return out


def count_dataset_split_images(job_work_root: Path, split: str) -> Optional[int]:
    img_dir = job_work_root / "dataset" / "images" / split
    if not img_dir.is_dir():
        return None
    n = sum(1 for p in img_dir.iterdir() if p.is_file())
    return n if n > 0 else 0


def resolve_job_sample_counts(
    *,
    job_id: int,
    metrics_summary: Optional[str],
    train_sample_count: Optional[int],
    val_sample_count: Optional[int],
) -> Dict[str, Optional[int]]:
    """
    解析训练作业样本数：DB 列 → metrics_summary.dataset → 磁盘 dataset 目录。
    返回 train / val / test / total。
    """
    ds = dataset_counts_from_metrics_summary(metrics_summary)
    train = train_sample_count if (train_sample_count or 0) > 0 else ds.get("train")
    val = val_sample_count if (val_sample_count or 0) > 0 else ds.get("val")
    test = ds.get("test")

    root = Path(settings.MEDIA_ROOT) / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    if train is None or train == 0:
        train = count_dataset_split_images(root, "train")
    if val is None or val == 0:
        val = count_dataset_split_images(root, "val")
    if test is None or test == 0:
        test = count_dataset_split_images(root, "test")

    parts = [x for x in (train, val, test) if isinstance(x, int) and x >= 0]
    total = sum(parts) if parts else None
    return {"train": train, "val": val, "test": test, "total": total}


def read_test_val_metrics_from_summary(raw: Any) -> Optional[Dict[str, Any]]:
    """读取 metrics_summary.test_val_metrics（model.val split=test）。"""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    tvm = obj.get("test_val_metrics")
    return tvm if isinstance(tvm, dict) else None


def metrics_dict_to_training_val(
    fm: Dict[str, Any],
    *,
    source: str,
    split: str = "val",
) -> Dict[str, Any]:
    line = format_training_metrics_line(fm)
    ep_raw = fm.get("epoch")
    epoch_i: Optional[int] = None
    if ep_raw is not None:
        try:
            epoch_i = int(float(ep_raw))
        except (TypeError, ValueError):
            epoch_i = None
    return {
        "map50_95": fm.get("map50_95"),
        "map50": fm.get("map50"),
        "precision": fm.get("precision"),
        "recall": fm.get("recall"),
        "fitness": fm.get("fitness"),
        "epoch": epoch_i,
        "summary_line": line,
        "source": source,
        "split": split,
    }


def build_val_test_comparison(
    val_m: Optional[Dict[str, Any]],
    test_m: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """val 与 test 指标并排对比（用于前端表格）。"""
    if not val_m and not test_m:
        return None

    def fget(d: Optional[Dict[str, Any]], k: str) -> Optional[float]:
        if not d:
            return None
        v = d.get(k)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    v50 = fget(val_m, "map50")
    t50 = fget(test_m, "map50")
    v95 = fget(val_m, "map50_95")
    t95 = fget(test_m, "map50_95")

    def delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        return round(b - a, 4)

    val_speed = (
        (val_m or {}).get("speed")
        if isinstance((val_m or {}).get("speed"), dict)
        else {}
    )
    test_speed = (
        (test_m or {}).get("speed")
        if isinstance((test_m or {}).get("speed"), dict)
        else {}
    )
    val_inf = val_speed.get("inference_ms") if isinstance(val_speed, dict) else None
    test_inf = test_speed.get("inference_ms") if isinstance(test_speed, dict) else None

    parts: List[str] = []
    if v50 is not None and t50 is not None:
        d = delta(v50, t50)
        parts.append(
            f"mAP50 val={v50:.4f} test={t50:.4f}"
            + (f" (Δ{d:+.4f})" if d is not None else "")
        )
    if v95 is not None and t95 is not None:
        d = delta(v95, t95)
        parts.append(
            f"mAP50-95 val={v95:.4f} test={t95:.4f}"
            + (f" (Δ{d:+.4f})" if d is not None else "")
        )

    return {
        "val_map50": v50,
        "test_map50": t50,
        "delta_map50": delta(v50, t50),
        "val_map50_95": v95,
        "test_map50_95": t95,
        "delta_map50_95": delta(v95, t95),
        "val_inference_ms": val_inf,
        "test_inference_ms": test_inf,
        "summary_line": "；".join(parts) if parts else None,
    }


def _epoch_int_from_metrics(fm: Optional[Dict[str, Any]]) -> Optional[int]:
    if not fm:
        return None
    raw_ep = fm.get("epoch")
    if raw_ep in (None, "", "nan"):
        return None
    try:
        return int(float(raw_ep))
    except (TypeError, ValueError):
        return None


def train_job_work_train_dir(job_id: int) -> Path:
    return (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "train"
    )


def format_results_csv_epoch_log_line(
    train_dir: Path, *, target_epochs: Optional[int] = None
) -> Optional[str]:
    """将 results.csv 末行格式化为过程日志一行（供每 epoch 追加到 process.log）。"""
    ep = read_last_epoch_from_results_csv(train_dir)
    if ep is None:
        return None
    ep_label = f"epoch={ep}/{target_epochs}" if target_epochs else f"epoch={ep}"
    fm = parse_results_csv_final_metrics(train_dir) or {}
    parts = [ep_label]
    for key, label in (
        ("map50", "mAP50"),
        ("map50_95", "mAP50-95"),
        ("precision", "P"),
        ("recall", "R"),
        ("fitness", "fitness"),
    ):
        val = fm.get(key)
        if val is None:
            continue
        try:
            parts.append(f"{label}={float(val):.4f}")
        except (TypeError, ValueError):
            parts.append(f"{label}={val}")
    return " ".join(parts)


def read_last_epoch_from_results_csv(train_dir: Path) -> Optional[int]:
    """仅读取 results.csv 最后一行的 epoch 列（不依赖 mAP 等指标是否存在）。"""
    csv_path = train_dir / "results.csv"
    if not csv_path.is_file():
        return None
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return None
    if not rows:
        return None
    return _epoch_int_from_metrics({"epoch": rows[-1].get("epoch")})


def actual_epochs_for_train_job(
    *,
    job_id: int,
    weights_path: str | None,
    metrics_summary: str | None,
) -> Optional[int]:
    """从 metrics_summary.final_metrics 或 train/results.csv 末行读取实际已跑 epoch 数。"""
    ep = _epoch_int_from_metrics(final_metrics_from_metrics_summary(metrics_summary))
    if ep is not None:
        return ep
    train_dir = resolve_train_dir_from_weights(weights_path)
    if train_dir is None:
        cand = train_job_work_train_dir(job_id)
        train_dir = cand if cand.is_dir() else None
    if train_dir is None:
        return None
    ep = _epoch_int_from_metrics(parse_results_csv_final_metrics(train_dir))
    if ep is not None:
        return ep
    return read_last_epoch_from_results_csv(train_dir)


def read_train_patience_from_args_yaml(train_dir: Optional[Path]) -> Optional[int]:
    """Read the effective Ultralytics patience for historical jobs."""
    if train_dir is None:
        return None
    args_path = train_dir / "args.yaml"
    if not args_path.is_file():
        return None
    try:
        import yaml

        payload = yaml.safe_load(args_path.read_text(encoding="utf-8"))
    except (ImportError, OSError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return max(0, int(float(payload.get("patience"))))
    except (TypeError, ValueError):
        return None


def training_stop_info(
    *,
    job_id: int,
    status: str | None,
    configured_epochs: Any,
    hyperparams: Optional[Dict[str, Any]],
    weights_path: str | None,
    metrics_summary: str | None,
) -> Dict[str, Any]:
    """Describe whether a completed Ultralytics run ended via early stopping."""
    try:
        target = max(1, int(float(configured_epochs)))
    except (TypeError, ValueError):
        target = None

    actual = actual_epochs_for_train_job(
        job_id=job_id,
        weights_path=weights_path,
        metrics_summary=metrics_summary,
    )
    train_dir = resolve_train_dir_from_weights(weights_path)
    if train_dir is None:
        candidate = train_job_work_train_dir(job_id)
        train_dir = candidate if candidate.is_dir() else None
    hp = hyperparams if isinstance(hyperparams, dict) else {}
    try:
        patience = int(float(hp.get("patience")))
    except (TypeError, ValueError):
        patience = read_train_patience_from_args_yaml(train_dir)
        if patience is None:
            patience = default_train_patience(target)

    best_metrics = best_epoch_metrics_from_summary(metrics_summary)
    if best_metrics is None and train_dir is not None:
        best_metrics = parse_results_csv_best_metrics(train_dir)
    best_epoch = _epoch_int_from_metrics(best_metrics)

    completed = str(status or "").upper() == "COMPLETED"
    early_stopped = bool(
        completed
        and target is not None
        and actual is not None
        and best_epoch is not None
        and patience > 0
        and actual < target
        and actual - best_epoch >= patience
    )
    reason_code = "early_stopping" if early_stopped else None
    reason_summary = None
    if early_stopped:
        reason_summary = (
            f"正常早停：实际 {actual}/{target} 轮，最佳第 {best_epoch} 轮，"
            f"连续 {patience} 轮未提升"
        )
    return {
        "configured_epochs": target,
        "actual_epochs": actual,
        "best_epoch": best_epoch,
        "patience": patience,
        "early_stopped": early_stopped,
        "stop_reason": reason_code,
        "stop_reason_summary": reason_summary,
    }


def final_metrics_from_metrics_summary(raw: Any) -> Optional[Dict[str, Any]]:
    """从作业 metrics_summary JSON 中读取训练结束时写入的 final_metrics。"""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    fm = obj.get("final_metrics")
    if not isinstance(fm, dict) or not fm:
        return None
    if not any(
        fm.get(k) is not None
        for k in ("map50_95", "map50", "precision", "recall", "fitness")
    ):
        return None
    return fm


def auto_training_evaluation_metrics_from_summary(
    raw: Any,
) -> Optional[Dict[str, Any]]:
    """Read trusted automatic-evaluation quality evidence from ``metrics_summary``.

    Auto-training evaluation is intentionally kept separate from Ultralytics
    ``val`` metrics.  Some local fixtures and older auto-training jobs only
    persist ``auto_training_evaluation.overall_metrics.overall_recall``; that
    evidence is still sufficient for the auto-stop quality gate, but must not
    be presented as mAP.  Invalid, non-finite, or out-of-range values are
    ignored so a malformed fixture cannot turn into a quality decision.
    """
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None

    evidence = obj.get("auto_training_evaluation")
    if not isinstance(evidence, dict):
        return None
    overall = evidence.get("overall_metrics")
    if not isinstance(overall, dict):
        # A few early local fixtures put the metric directly in the evidence
        # object.  Keep this read-only compatibility path bounded as well.
        overall = evidence
    value = overall.get("overall_recall")
    if isinstance(value, bool):
        return None
    try:
        recall = float(value)
    except (TypeError, ValueError):
        return None
    if recall != recall or recall in (float("inf"), float("-inf")):
        return None
    if not 0.0 <= recall <= 1.0:
        return None

    threshold = overall.get("overall_recall_threshold")
    if threshold is None:
        threshold = evidence.get("overall_recall_threshold")
    if threshold is None:
        preview = evidence.get("decision_preview")
        if isinstance(preview, dict):
            threshold = preview.get("overall_recall_threshold")
    try:
        threshold_value = float(threshold) if threshold is not None else None
    except (TypeError, ValueError):
        threshold_value = None
    if (
        threshold_value is not None
        and (
            threshold_value != threshold_value
            or threshold_value in (float("inf"), float("-inf"))
            or not 0.0 <= threshold_value <= 1.0
        )
    ):
        threshold_value = None

    summary = f"overall_recall={recall:.4f}"
    return {
        "overall_recall": recall,
        "overall_recall_threshold": threshold_value,
        "summary_line": summary,
        "source": "auto_training_evaluation",
        "split": "evaluation",
        "metric_basis": "overall_recall",
    }


def _metric_block_from_summary(raw: Any, key: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    block = obj.get(key)
    if not isinstance(block, dict) or not block:
        return None
    if block.get("status") not in (None, "ok"):
        return None
    if not any(
        block.get(metric) is not None
        for metric in ("map50_95", "map50", "precision", "recall", "fitness")
    ):
        return None
    return block


def last_epoch_metrics_from_summary(raw: Any) -> Optional[Dict[str, Any]]:
    """Return explicit last-epoch metrics, falling back to the legacy final block."""
    return _metric_block_from_summary(
        raw, "last_epoch_metrics"
    ) or final_metrics_from_metrics_summary(raw)


def best_epoch_metrics_from_summary(raw: Any) -> Optional[Dict[str, Any]]:
    return _metric_block_from_summary(raw, "best_epoch_metrics")


def authoritative_val_metrics_from_summary(raw: Any) -> Optional[Dict[str, Any]]:
    """Return the metrics for the delivered artifact, with legacy compatibility."""
    return _metric_block_from_summary(
        raw, "best_pt_val_metrics"
    ) or final_metrics_from_metrics_summary(raw) or auto_training_evaluation_metrics_from_summary(raw)


def metrics_contract_from_summary(
    raw: Any, *, train_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Expose metric semantics with a read-only fallback for historical jobs."""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        obj: Dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        obj = parsed if isinstance(parsed, dict) else {}

    last = last_epoch_metrics_from_summary(raw)
    best = best_epoch_metrics_from_summary(raw)
    if train_dir is not None:
        last = last or parse_results_csv_final_metrics(train_dir)
        best = best or parse_results_csv_best_metrics(train_dir)
    best_pt = _metric_block_from_summary(raw, "best_pt_val_metrics")
    legacy_final = final_metrics_from_metrics_summary(raw)
    auto_evaluation = auto_training_evaluation_metrics_from_summary(raw)
    # A trusted auto-training evaluation may be the only persisted quality
    # evidence for locally simulated or historical jobs.  It is intentionally
    # the last fallback, after real val/best-pt metrics, but still exposed via
    # the authoritative contract so job detail quality gates do not report a
    # false "val metrics missing" state.
    authoritative = (
        best_pt
        or (best if train_dir is not None else None)
        or legacy_final
        or auto_evaluation
    )
    if best_pt:
        authoritative_source = "best_pt_val_metrics"
    elif best is not None and train_dir is not None:
        authoritative_source = "results_csv_best_epoch"
    elif legacy_final:
        authoritative_source = "final_metrics"
    elif auto_evaluation:
        authoritative_source = "auto_training_evaluation"
    else:
        authoritative_source = "none"
    return {
        "version": int(obj.get("metrics_contract_version") or 1),
        "last_epoch_metrics": last,
        "best_epoch_metrics": best,
        "best_pt_val_metrics": best_pt,
        "auto_training_evaluation_metrics": auto_evaluation,
        "authoritative_val_metrics": authoritative,
        # Consumers that need quality evidence can use this alias; the
        # ``metric_basis`` field identifies auto-evaluation recall explicitly.
        "authoritative_quality_metrics": authoritative or auto_evaluation,
        "authoritative_source": authoritative_source,
        "legacy_fallback": best_pt is None and authoritative is not None,
    }


def format_training_metrics_line(fm: Optional[Dict[str, Any]]) -> Optional[str]:
    """单行展示训练验证指标（用于前端表格）。"""
    if not fm:
        return None
    parts: List[str] = []
    if fm.get("map50_95") is not None:
        parts.append(f"mAP50-95={float(fm['map50_95']):.4f}")
    if fm.get("map50") is not None:
        parts.append(f"mAP50={float(fm['map50']):.4f}")
    if fm.get("precision") is not None:
        parts.append(f"P={float(fm['precision']):.4f}")
    if fm.get("recall") is not None:
        parts.append(f"R={float(fm['recall']):.4f}")
    if not parts and fm.get("overall_recall") is not None:
        parts.append(f"overall_recall={float(fm['overall_recall']):.4f}")
    if not parts:
        return None
    return " ".join(parts)


def validation_pass_assessment(
    map50_95: Optional[float],
    map50: Optional[float],
    *,
    threshold_map5095: float,
    threshold_map50: float,
) -> Dict[str, Optional[str]]:
    """
    仅依据 Ultralytics 验证集 mAP 与配置的阈值判定达标（模型列表展示）。
    优先 mAP50-95；若无则回退 mAP50。
    """
    fmt = lambda x: f"{float(x):.4f}"

    if map50_95 is not None:
        v = float(map50_95)
        thr = float(threshold_map5095)
        ok = v >= thr
        detail = f"mAP50-95={fmt(v)}，达标线≥{fmt(thr)}"
        return {
            "status": "达标" if ok else "不达标",
            "detail": detail,
        }
    if map50 is not None:
        v = float(map50)
        thr = float(threshold_map50)
        ok = v >= thr
        detail = f"mAP50={fmt(v)}（无 mAP50-95 时回退），达标线≥{fmt(thr)}"
        return {
            "status": "达标" if ok else "不达标",
            "detail": detail,
        }
    return {
        "status": "无验证指标",
        "detail": "缺少 mAP50-95 / mAP50，无法判定（请确认 results.csv 或 metrics_summary.final_metrics）",
    }


_AI_METRIC_PATTERNS = [
    re.compile(
        r"mAP\s*50\s*[-–]?\s*95\s*[：:]\s*([\d.]+%?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"mAP\s*50\s*[：:]\s*([\d.]+%?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:平均精度|均值平均精度|验证\s*mAP)\s*[：:]\s*([\d.]+%?)",
    ),
    re.compile(
        r"(?:precision|P\s*值)\s*[：:]\s*([\d.]+%?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:recall|召回(?:率)?)\s*[：:]\s*([\d.]+%?)",
        re.IGNORECASE,
    ),
]


def _conclusion_fragment_implies_fail(frag: str) -> bool:
    if any(
        k in frag
        for k in (
            "不达标",
            "未达标",
            "不合格",
            "较差",
            "不佳",
            "明显不足",
            "严重不足",
            "不建议",
            "不推荐",
            "难以",
            "欠妥",
            "有限",
            "偏弱",
        )
    ):
        return True
    # 「达标」的否定表述
    if re.search(r"(尚未|没能|未能|无法|难以).{0,6}达标", frag):
        return True
    return False


def _conclusion_fragment_implies_pass(frag: str) -> bool:
    if _conclusion_fragment_implies_fail(frag):
        return False
    return any(
        k in frag
        for k in (
            "达标",
            "合格",
            "良好",
            "优秀",
            "满意",
            "理想",
            "较好",
            "可用于",
            "可部署",
            "上线",
        )
    )


def infer_conclusion_from_ai_report(markdown: str | None) -> str:
    """
    根据 full_analysis 正文启发式推断「达标 / 不达标 / 未明确」。
    优先读取显式「结论/评价」句，其次依据常见正负表述打分。
    """
    if not markdown or not str(markdown).strip():
        return "未明确"
    text = str(markdown)

    rx_lines = (
        re.compile(
            r"(?:^|[\n#])\s*(?:[*\-•]{0,3}\s*)?(?:综合|总体|评估)?(?:结论|研判)\s*[：:]\s*([^\n]{1,120})",
            re.MULTILINE,
        ),
        re.compile(
            r"(?:^|[\n#])\s*(?:[*\-•]{0,3}\s*)?(?:整体|综合)(?:评价|评估)\s*[：:]\s*([^\n]{1,120})",
            re.MULTILINE,
        ),
    )
    for rx in rx_lines:
        m = rx.search(text)
        if not m:
            continue
        frag = (m.group(1) or "").strip()
        frag = frag.split("。")[0].strip()
        if _conclusion_fragment_implies_fail(frag):
            return "不达标"
        if _conclusion_fragment_implies_pass(frag):
            return "达标"

    neg_markers = (
        "不达标",
        "未达标",
        "不合格",
        "不满足要求",
        "不满足指标",
        "难以满足",
        "难以直接上线",
        "不建议上线",
        "不推荐直接使用",
        "不推荐部署",
        "存在明显不足",
        "存在较大差距",
        "效果不理想",
        "效果较差",
        "漏检严重",
        "误检率过高",
        "需重大改进",
        "仍需持续优化",
        "距离生产要求",
        "暂不具备上线",
    )
    pos_markers = (
        "满足上线要求",
        "满足生产要求",
        "符合上线标准",
        "达到预期",
        "整体良好",
        "表现良好",
        "可直接部署",
        "可以直接使用",
        "具备上线条件",
        "指标达标",
        "已达到可用",
        "模型可用",
        "未见明显缺陷",
    )
    neg_score = sum(2 for p in neg_markers if p in text)
    pos_score = sum(2 for p in pos_markers if p in text)

    if (
        re.search(r"(?<![不未非无])达标", text)
        and "不达标" not in text
        and "未达标" not in text
    ):
        pos_score += 1
    if "合格" in text and "不合格" not in text:
        pos_score += 1
    if any(x in text for x in ("不达标", "未达标", "不合格")):
        neg_score += 1

    if neg_score > pos_score:
        return "不达标"
    if pos_score > neg_score:
        return "达标"
    return "未明确"


def _strip_markdown_line(line: str) -> str:
    text = re.sub(r"^[#>*\-\s`]+", "", str(line or "").strip())
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ：:-")


def _first_ai_report_brief(markdown: str | None) -> Optional[str]:
    if not markdown:
        return None
    for raw_line in str(markdown).splitlines():
        line = _strip_markdown_line(raw_line)
        if not line:
            continue
        return line[:80] if len(line) <= 80 else f"{line[:77]}..."
    return None


def summarize_ai_report(
    markdown: str | None, *, error_message: str | None = None
) -> Dict[str, str]:
    """Stable structured AI report summary for list contracts."""
    if error_message:
        brief = (
            _strip_markdown_line(str(error_message))
            or "AI\u8bc4\u4f30\u6267\u884c\u5931\u8d25"
        )
        return {"summary_level": "abnormal", "summary_brief": brief[:80]}

    text = str(markdown or "").strip()
    if not text:
        return {
            "summary_level": "unknown",
            "summary_brief": "AI\u8bc4\u4f30\u62a5\u544a\u4e3a\u7a7a\uff0c\u5f85\u4eba\u5de5\u5224\u65ad",
        }

    brief = (
        _first_ai_report_brief(text)
        or "AI\u8bc4\u4f30\u5df2\u751f\u6210\uff0c\u8bf7\u67e5\u770b\u8be6\u60c5"
    )
    conc = infer_conclusion_from_ai_report(text)
    lowered = text.lower()

    manual_review_markers = (
        "\u4eba\u5de5",
        "\u590d\u6838",
        "\u4e13\u5bb6",
        "\u5f85\u786e\u8ba4",
        "\u5f85\u5224\u65ad",
        "manual review",
    )
    abnormal_markers = (
        "\u5f02\u5e38",
        "\u9519\u8bef",
        "\u5931\u8d25",
        "\u4e2d\u65ad",
        "\u65e0\u6cd5",
        "error",
        "exception",
    )
    optimize_markers = (
        "\u5efa\u8bae\u4f18\u5316",
        "\u53ef\u7ee7\u7eed\u4f18\u5316",
        "\u4f18\u5316",
        "\u8c03\u4f18",
        "\u6539\u8fdb",
        "\u6539\u5584",
    )
    risk_markers = (
        "\u98ce\u9669",
        "\u4e0d\u8db3",
        "\u504f\u5dee",
        "\u4e0d\u5efa\u8bae",
        "\u672a\u8fbe\u6807",
        "\u4e0d\u7a33\u5b9a",
        "risk",
    )

    if any(marker in text or marker in lowered for marker in manual_review_markers):
        level = "manual_review"
    elif any(marker in text or marker in lowered for marker in abnormal_markers):
        level = "abnormal"
    elif conc == "\u4e0d\u8fbe\u6807" or any(
        marker in text or marker in lowered for marker in risk_markers
    ):
        level = "risk"
    elif conc == "\u8fbe\u6807" and any(
        marker in text or marker in lowered for marker in optimize_markers
    ):
        level = "optimize"
    elif conc == "\u8fbe\u6807":
        level = "pass"
    elif any(marker in text or marker in lowered for marker in optimize_markers):
        level = "optimize"
    else:
        level = "unknown"

    return {"summary_level": level, "summary_brief": brief}


def summary_level_to_legacy_conclusion(summary_level: str) -> str:
    mapping = {
        "pass": "\u8fbe\u6807",
        "optimize": "\u5efa\u8bae\u4f18\u5316",
        "risk": "\u5b58\u5728\u98ce\u9669",
        "abnormal": "\u660e\u663e\u5f02\u5e38",
        "manual_review": "\u5f85\u4eba\u5de5\u5224\u65ad",
        "unknown": "\u672a\u660e\u786e",
    }
    return mapping.get(str(summary_level or "").strip(), "\u672a\u660e\u786e")


def extract_ai_metric_snippets(
    markdown: str | None, *, max_len: int = 280
) -> Optional[str]:
    """从 AI 全维度报告 Markdown 中摘录与精度相关的短文本（启发式，非结构化存储场景）。"""
    if not markdown or not markdown.strip():
        return None
    text = markdown.strip()
    hits: List[str] = []

    for rx in _AI_METRIC_PATTERNS:
        for m in rx.finditer(text):
            g = (m.group(0) or "").strip()
            if g and g not in hits:
                hits.append(g)
            if len("; ".join(hits)) >= max_len:
                break
        if len("; ".join(hits)) >= max_len:
            break

    if not hits:
        # 退化为：取含关键词的较短行
        for line in text.splitlines():
            s = line.strip()
            if len(s) < 8:
                continue
            lower = s.lower()
            if any(
                k in lower or k in s
                for k in (
                    "map50",
                    "map50-95",
                    "mAP",
                    "精度",
                    "召回",
                    "precision",
                    "recall",
                )
            ):
                if s not in hits:
                    hits.append(s[:120])
            if len(hits) >= 4:
                break

    if not hits:
        return None
    out = "; ".join(hits)
    if len(out) > max_len:
        return out[: max_len - 1] + "…"
    return out


def read_classes_from_metrics_summary(raw: Any) -> List[str]:
    """从 metrics_summary JSON 读取训练类别名（与拉框预设顺序一致）。"""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return []
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    classes = obj.get("classes")
    if isinstance(classes, list):
        return [str(x).strip() for x in classes if str(x).strip()]
    return []


def read_per_class_metrics_from_summary(
    raw: Any,
    *,
    split: str = "val",
) -> List[Dict[str, Any]]:
    """读取各类别指标：val 来自 per_class_val_metrics；test 来自 test_val_metrics.per_class_metrics。"""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return []
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    if split == "test":
        tvm = obj.get("test_val_metrics")
        if isinstance(tvm, dict):
            pcm = tvm.get("per_class_metrics")
            if isinstance(pcm, list):
                return [x for x in pcm if isinstance(x, dict)]
        return []
    pcm = obj.get("per_class_val_metrics")
    if isinstance(pcm, list):
        return [x for x in pcm if isinstance(x, dict)]
    return []


def read_class_instance_counts_from_summary(raw: Any) -> List[Dict[str, Any]]:
    """从 metrics_summary.class_instance_counts 读取各类别 train/val/test 框数。"""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return []
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    rows = obj.get("class_instance_counts")
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return []


def build_class_metrics_detail(
    metrics_summary_raw: str | None,
    *,
    fallback_classes: Optional[List[str]] = None,
    fallback_instance_counts: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    组装拉框预设各类别精度与框数分布，供 API 与前端展示。
    若仅有 classes 无 per_class，仍返回标签行（指标为 null）。
    """
    classes = read_classes_from_metrics_summary(metrics_summary_raw)
    if not classes and fallback_classes:
        classes = list(fallback_classes)
    if not classes:
        return None

    val_rows = read_per_class_metrics_from_summary(metrics_summary_raw, split="val")
    test_rows = read_per_class_metrics_from_summary(metrics_summary_raw, split="test")

    count_rows = read_class_instance_counts_from_summary(metrics_summary_raw)
    if not count_rows and fallback_instance_counts:
        count_rows = fallback_instance_counts

    def _counts_map(rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        out: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index", -1))
            except (TypeError, ValueError):
                continue
            if idx >= 0:
                out[idx] = r
        return out

    counts_by_index = _counts_map(count_rows)

    def _normalize_metric_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_index = {int(r.get("index", -1)): r for r in rows if isinstance(r, dict)}
        by_label = {
            str(r.get("label", "")).strip(): r for r in rows if isinstance(r, dict)
        }
        out: List[Dict[str, Any]] = []
        for i, label in enumerate(classes):
            src = by_index.get(i) or by_label.get(label) or {}
            cnt = counts_by_index.get(i) or by_label.get(label) or {}
            out.append(
                {
                    "index": i,
                    "label": label,
                    "train_instances": cnt.get("train_instances"),
                    "val_instances": cnt.get("val_instances"),
                    "test_instances": cnt.get("test_instances"),
                    "train_images": cnt.get("train_images"),
                    "val_images": cnt.get("val_images"),
                    "test_images": cnt.get("test_images"),
                    "map50": src.get("map50"),
                    "map50_95": src.get("map50_95"),
                    "precision": src.get("precision"),
                    "recall": src.get("recall"),
                }
            )
        return out

    val_metrics = _normalize_metric_rows(val_rows)
    test_by_index = {
        int(r.get("index", -1)): r
        for r in test_rows
        if isinstance(r, dict) and r.get("index") is not None
    }
    test_by_label = {
        str(r.get("label", "")).strip(): r for r in test_rows if isinstance(r, dict)
    }

    unified_rows: List[Dict[str, Any]] = []
    for vm in val_metrics:
        i = int(vm["index"])
        label = vm["label"]
        tm = test_by_index.get(i) or test_by_label.get(label) or {}
        unified_rows.append(
            {
                **vm,
                "test_map50": tm.get("map50"),
                "test_map50_95": tm.get("map50_95"),
                "test_precision": tm.get("precision"),
                "test_recall": tm.get("recall"),
            }
        )

    test_metrics = [
        {
            "index": r["index"],
            "label": r["label"],
            "train_instances": r.get("train_instances"),
            "val_instances": r.get("val_instances"),
            "test_instances": r.get("test_instances"),
            "map50": r.get("test_map50"),
            "map50_95": r.get("test_map50_95"),
            "precision": r.get("test_precision"),
            "recall": r.get("test_recall"),
        }
        for r in unified_rows
    ]

    return {
        "classes": classes,
        "rows": unified_rows,
        "val_metrics": val_metrics,
        "test_metrics": test_metrics,
    }


def build_ai_provider_metric_rows(
    reports_by_provider: Dict[str, Any],
    *,
    provider_order: tuple[str, ...] = DEFAULT_EVAL_PROVIDER_ORDER,
) -> List[Dict[str, Any]]:
    """组装当前默认模型行，并保留实际存在的历史模型报告。"""
    out: List[Dict[str, Any]] = []
    keys = (*provider_order, *(key for key in reports_by_provider if key not in provider_order))
    for pk in keys:
        label = YOLO_EVAL_PROVIDER_LABELS.get(pk, pk)
        row = reports_by_provider.get(pk)
        if row is None:
            out.append(
                {
                    "provider_key": pk,
                    "provider_label": label,
                    "metric_text": None,
                    "has_report": False,
                    "error_message": None,
                    "conclusion": "未评估",
                }
            )
            continue
        err = getattr(row, "error_message", None)
        body = getattr(row, "body", None)
        if err:
            out.append(
                {
                    "provider_key": pk,
                    "provider_label": label,
                    "metric_text": None,
                    "has_report": True,
                    "error_message": str(err)[:500],
                    "conclusion": "评估失败",
                }
            )
        else:
            snippet = extract_ai_metric_snippets(
                body if isinstance(body, str) else None
            )
            summary = summarize_ai_report(body if isinstance(body, str) else None)
            out.append(
                {
                    "provider_key": pk,
                    "provider_label": label,
                    "metric_text": snippet,
                    "has_report": True,
                    "error_message": None,
                    "conclusion": summary_level_to_legacy_conclusion(
                        summary.get("summary_level", "unknown")
                    ),
                }
            )
    return out
