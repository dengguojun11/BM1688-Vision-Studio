"""Ultralytics 在指定 data.yaml 划分（val / test）上运行 model.val。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_class_names_from_data_yaml(data_yaml: Path) -> List[str]:
    """读取 Ultralytics data.yaml 中的 names，顺序与训练 rectTool 一致。"""
    if not data_yaml.is_file():
        return []
    try:
        text = data_yaml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        import yaml  # noqa: WPS433

        parsed = yaml.safe_load(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        names = parsed.get("names")
        if isinstance(names, list):
            return [str(x).strip() for x in names if str(x).strip()]
        if isinstance(names, dict):
            out: List[str] = []
            for k in sorted(names.keys(), key=lambda x: int(x)):
                v = str(names[k]).strip()
                if v:
                    out.append(v)
            return out
    return []


def extract_per_class_box_metrics(
    metrics: Any,
    class_names: List[str],
) -> List[Dict[str, Any]]:
    """
    从 Ultralytics val 结果抽取各类别精度（顺序与 class_names / 拉框预设一致）。
    指标：mAP50、mAP50-95、Precision、Recall。
    """
    if not class_names:
        return []
    box = getattr(metrics, "box", None)
    if box is None:
        return []

    class_result_positions: Dict[int, int] = {}
    try:
        ap_class_index = getattr(box, "ap_class_index", None)
        if ap_class_index is not None:
            class_result_positions = {
                int(class_id): position
                for position, class_id in enumerate(list(ap_class_index))
            }
    except Exception:
        class_result_positions = {}

    rows: List[Dict[str, Any]] = []
    for i, label in enumerate(class_names):
        row: Dict[str, Any] = {"index": i, "label": label}
        metric_position = class_result_positions.get(i)
        if class_result_positions and metric_position is None:
            rows.append(row)
            continue
        if metric_position is None:
            metric_position = i
        try:
            p, r, ap50, ap = box.class_result(metric_position)
            row["precision"] = _float_or_none(p)
            row["recall"] = _float_or_none(r)
            row["map50"] = _float_or_none(ap50)
            row["map50_95"] = _float_or_none(ap)
        except Exception:
            try:
                ap50_arr = box.ap50
                ap_arr = getattr(box, "ap", None)
                if ap50_arr is not None and metric_position < len(list(ap50_arr)):
                    row["map50"] = _float_or_none(list(ap50_arr)[metric_position])
                if ap_arr is not None and metric_position < len(list(ap_arr)):
                    row["map50_95"] = _float_or_none(list(ap_arr)[metric_position])
            except Exception:
                pass
            try:
                p_arr = getattr(box, "p", None)
                r_arr = getattr(box, "r", None)
                if p_arr is not None and metric_position < len(list(p_arr)):
                    row["precision"] = _float_or_none(list(p_arr)[metric_position])
                if r_arr is not None and metric_position < len(list(r_arr)):
                    row["recall"] = _float_or_none(list(r_arr)[metric_position])
            except Exception:
                pass
        rows.append(row)
    return rows


def _extract_speed_ms(results: Any) -> Dict[str, Optional[float]]:
    speed = getattr(results, "speed", None) or {}
    if isinstance(speed, dict):
        d = speed
    else:
        try:
            d = dict(speed)
        except Exception:
            d = {}
    out: Dict[str, Optional[float]] = {}
    for key, label in (
        ("preprocess", "preprocess_ms"),
        ("inference", "inference_ms"),
        ("loss", "loss_ms"),
        ("postprocess", "postprocess_ms"),
    ):
        raw = d.get(key)
        if raw is None:
            out[label] = None
        else:
            try:
                out[label] = round(float(raw) * 1000.0, 2)
            except (TypeError, ValueError):
                out[label] = None
    return out


def run_split_val(
    *,
    weights_path: Path,
    data_yaml: Path,
    split: str = "test",
    imgsz: int = 640,
    device: str = "",
    batch: int = 8,
) -> Dict[str, Any]:
    """
    等价于 CLI: yolo detect val model=... data=... split=test
    返回可写入 metrics_summary.test_val_metrics 的字典。
    """
    if not weights_path.is_file():
        return {"status": "fail", "detail": f"权重不存在: {weights_path}"}
    if not data_yaml.is_file():
        return {"status": "fail", "detail": f"data.yaml 不存在: {data_yaml}"}

    test_img_dir = data_yaml.parent / "images" / split
    if not test_img_dir.is_dir() or not any(test_img_dir.iterdir()):
        return {
            "status": "skip",
            "detail": f"未划分 {split} 集（images/{split} 为空）。请设置 YOLO_TRAIN_TEST_RATIO>0 后重新训练。",
            "split": split,
        }

    try:
        from ultralytics import YOLO  # noqa: WPS433
    except ImportError as exc:
        return {"status": "fail", "detail": f"未安装 ultralytics: {exc}"}

    os.environ.setdefault("YOLO_OFFLINE", "True")
    dev = (device or "").strip() or None
    try:
        model = YOLO(str(weights_path))
        metrics = model.val(
            data=str(data_yaml),
            split=split,
            imgsz=int(imgsz),
            batch=int(batch),
            device=dev,
            verbose=False,
        )
    except Exception as exc:
        logger.exception("split val failed split={} weights={}", split, weights_path)
        return {"status": "fail", "detail": str(exc)[:800], "split": split}

    class_names = read_class_names_from_data_yaml(data_yaml)
    per_class = extract_per_class_box_metrics(metrics, class_names)

    box = getattr(metrics, "box", None)
    out: Dict[str, Any] = {
        "status": "ok",
        "split": split,
        "run_at": _iso_now(),
        "classes": class_names,
        "per_class_metrics": per_class,
        "map50": _float_or_none(getattr(box, "map50", None) if box else None),
        "map50_95": _float_or_none(getattr(box, "map", None) if box else None),
        "precision": _float_or_none(getattr(box, "mp", None) if box else None),
        "recall": _float_or_none(getattr(box, "mr", None) if box else None),
        "speed": _extract_speed_ms(metrics),
    }
    try:
        out["images"] = int(getattr(metrics, "n_img", 0) or 0)
    except (TypeError, ValueError):
        out["images"] = None
    try:
        out["instances"] = int(getattr(metrics, "n_labels", 0) or 0)
    except (TypeError, ValueError):
        out["instances"] = None
    return out


def merge_test_val_into_metrics_summary(
    metrics_summary_raw: Optional[str],
    test_val: Dict[str, Any],
) -> str:
    base = _parse_metrics_summary_dict(metrics_summary_raw)
    base["test_val_metrics"] = test_val
    if test_val.get("classes") and not base.get("classes"):
        base["classes"] = test_val["classes"]
    return json.dumps(base, ensure_ascii=False, default=str)


def _parse_metrics_summary_dict(metrics_summary_raw: Optional[str]) -> Dict[str, Any]:
    if metrics_summary_raw and metrics_summary_raw.strip():
        try:
            parsed = json.loads(metrics_summary_raw)
            if isinstance(parsed, dict):
                return dict(parsed)
        except json.JSONDecodeError:
            return {"legacy_text": metrics_summary_raw[:2000]}
    return {}


def merge_val_per_class_into_metrics_summary(
    metrics_summary_raw: Optional[str],
    val_result: Dict[str, Any],
) -> str:
    """将 val 划分的各类别指标写入 metrics_summary（供历史作业刷新补全）。"""
    base = _parse_metrics_summary_dict(metrics_summary_raw)
    base["metrics_contract_version"] = max(
        int(base.get("metrics_contract_version") or 1), 2
    )
    if val_result.get("classes"):
        base["classes"] = val_result["classes"]
    if val_result.get("status") == "ok":
        base["best_pt_val_metrics"] = {
            key: val_result.get(key)
            for key in (
                "status",
                "split",
                "run_at",
                "map50",
                "map50_95",
                "precision",
                "recall",
                "speed",
                "images",
                "instances",
            )
        }
        base["best_pt_val_metrics"]["weights"] = "best.pt"
        if val_result.get("per_class_metrics"):
            base["per_class_val_metrics"] = val_result["per_class_metrics"]
    return json.dumps(base, ensure_ascii=False, default=str)
