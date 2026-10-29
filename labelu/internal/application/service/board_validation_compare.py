"""训练质量报告与板端验证报告的对照摘要。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_train_board_comparison(
    *,
    quality_report: Optional[Dict[str, Any]],
    board_report: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    qr = quality_report or {}
    br = board_report or {}
    metrics = br.get("metrics") if isinstance(br.get("metrics"), dict) else {}
    eval_ds = metrics.get("eval_dataset") if isinstance(metrics.get("eval_dataset"), dict) else {}

    rows: List[Dict[str, Any]] = []

    tv = qr.get("train_val") or {}
    if tv.get("status") == "ok":
        if tv.get("map50_95") is not None:
            rows.append(
                {
                    "metric": "val_map50_95",
                    "train": _fmt(tv.get("map50_95")),
                    "edge": "n/a",
                    "delta": "n/a",
                }
            )
        if tv.get("map50") is not None:
            rows.append(
                {
                    "metric": "val_map50",
                    "train": _fmt(tv.get("map50")),
                    "edge": "n/a",
                    "delta": "n/a",
                }
            )

    tt = qr.get("test_val") or {}
    if tt.get("status") == "ok" and tt.get("map50_95") is not None:
        rows.append(
            {
                "metric": "test_map50_95",
                "train": _fmt(tt.get("map50_95")),
                "edge": "n/a",
                "delta": "n/a",
            }
        )

    if eval_ds.get("image_count") is not None:
        rows.append(
            {
                "metric": "eval_image_count",
                "train": str(tt.get("images") or "n/a"),
                "edge": str(eval_ds.get("image_count")),
                "delta": "0" if eval_ds.get("same_as_training") else "diff",
            }
        )

    if eval_ds.get("imgsz") is not None:
        rows.append(
            {
                "metric": "eval_imgsz",
                "train": str(eval_ds.get("imgsz")),
                "edge": str(eval_ds.get("imgsz")),
                "delta": "0",
            }
        )

    for row in metrics.get("bmrt_tests") or []:
        if not isinstance(row, dict):
            continue
        ct = row.get("calculate_time_ms") or (
            round(float(row.get("calculate_time_s") or 0) * 1000, 3)
            if row.get("calculate_time_s") is not None
            else None
        )
        if ct is None and not row.get("ok"):
            continue
        rows.append(
            {
                "metric": f"bmrt_ms_{row.get('bmodel') or 'bmodel'}",
                "train": "n/a",
                "edge": str(ct) if ct is not None else "n/a",
                "delta": "n/a",
            }
        )

    for row in metrics.get("infer_runs") or []:
        if not isinstance(row, dict):
            continue
        avg = row.get("infer_ms_avg")
        if avg is None and not row.get("ok"):
            continue
        rows.append(
            {
                "metric": f"infer_ms_avg_{row.get('bmodel') or row.get('tag') or 'run'}",
                "train": "n/a",
                "edge": str(avg) if avg is not None else "n/a",
                "delta": "n/a",
            }
        )

    ov = qr.get("onnx_val") or {}
    if ov.get("status") == "ok" and ov.get("map50_95") is not None:
        rows.append(
            {
                "metric": "coco128_onnx_map50_95",
                "train": _fmt(ov.get("map50_95")),
                "edge": "n/a",
                "delta": "n/a",
            }
        )

    return {
        "rows": rows,
        "note": metrics.get("compare_note")
        or br.get("metrics", {}).get("compare_note")
        or "训练侧为精度；板端为可部署性与同分布图推理。",
        "board_passed": br.get("passed"),
        "board_summary": br.get("summary"),
    }


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)
