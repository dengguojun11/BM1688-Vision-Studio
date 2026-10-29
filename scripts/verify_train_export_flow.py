#!/usr/bin/env python3
"""生产机：选小单任务 → 新建训练 → BM1688 导出 → 输出具体 P/R/mAP 数值。"""
from __future__ import annotations

import json
import os
import sys
import time

# 避免 SSH 管道下输出缓冲导致日志滞后
os.environ.setdefault("PYTHONUNBUFFERED", "1")
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

BASE = "http://127.0.0.1:10001/api/v1"
MEDIA = Path("/data/labeIU/labeIuMedia")
LOG_ROOT = MEDIA / "yolo_bm1688_exports"

# 加速验证：少 epoch；可按环境变量覆盖
TRAIN_EPOCHS = int(__import__("os").environ.get("VERIFY_TRAIN_EPOCHS", "15"))


def api(method: str, path: str, token: Optional[str] = None, data: Any = None, timeout: int = 120) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def login() -> str:
    d = api("POST", "/users/login", data={"username": "admin@123.com", "password": "Admin@123"}, timeout=30)
    return d["data"]["token"]


def pick_small_finished_task(token: str) -> Optional[Tuple[int, str, int]]:
    """返回 (task_id, name, labeled_count)。"""
    tasks = api("GET", "/tasks?page=0&size=100", token=token, timeout=60)["data"]
    best: Optional[Tuple[int, str, int]] = None
    for t in tasks:
        if (t.get("media_type") or "").upper() != "IMAGE":
            continue
        if (t.get("status") or "").upper() != "FINISHED":
            continue
        name = t.get("name") or ""
        low = name.lower()
        if "合并" in name or "merge" in low:
            continue
        stats = t.get("stats") or {}
        labeled = int(stats.get("done") or 0)
        if labeled < 3:
            continue
        tid = int(t["id"])
        cand = (tid, name, labeled)
        if best is None or labeled < best[2]:
            best = cand
    return best


def fmt_num(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return f"{v:.4f}" if isinstance(v, float) else str(v)
    return str(v)


def print_metrics_block(label: str, block: Optional[dict]) -> None:
    m = (block or {}).get("metrics") or {}
    print(
        f"  {label}: mAP50={fmt_num(m.get('map50'))} "
        f"precision={fmt_num(m.get('precision'))} "
        f"recall={fmt_num(m.get('recall'))} "
        f"images={fmt_num(m.get('images'))} "
        f"status={(block or {}).get('status')}"
    )


def tail_log(path: Path, lines: int = 40) -> None:
    if not path.is_file():
        print(f"  (log missing: {path})")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"--- tail {path} ({min(lines, len(text))} lines) ---")
        for ln in text[-lines:]:
            print(ln)
    except OSError as e:
        print(f"  (read log failed: {e})")


def wait_train_job(token: str, task_id: int, job_id: int, max_poll: int = 180) -> dict:
    for i in range(max_poll):
        j = api("GET", f"/tasks/{task_id}/yolo-train-jobs/{job_id}", token=token, timeout=60)["data"]
        st = (j.get("status") or "").upper()
        err = (j.get("error_message") or "")[:200]
        ep = j.get("current_epoch")
        print(f"train poll {i}: status={st} epoch={ep} err={err}")
        if st in ("COMPLETED", "FAILED", "CANCELLED"):
            return j
        time.sleep(20)
    return api("GET", f"/tasks/{task_id}/yolo-train-jobs/{job_id}", token=token, timeout=60)["data"]


def wait_export(token: str, export_id: int, max_poll: int = 120) -> dict:
    for i in range(max_poll):
        d = api("GET", f"/yolo-bm1688-exports/{export_id}", token=token, timeout=60)["data"]
        st = d.get("status")
        err = (d.get("error_message") or "")[:200]
        print(f"export poll {i}: status={st} err={err}")
        if st not in ("PENDING", "PROCESSING", "ONNX_READY"):
            return d
        time.sleep(30)
    return api("GET", f"/yolo-bm1688-exports/{export_id}", token=token, timeout=60)["data"]


def validate_sop_eval(se: dict) -> list[str]:
    errors: list[str] = []
    for key in ("pt", "onnx", "bmodel"):
        m = (se.get(key) or {}).get("metrics") or {}
        for field in ("precision", "recall", "map50"):
            v = m.get(field)
            if v is None:
                errors.append(f"{key}.{field} is null")
            elif not isinstance(v, (int, float)):
                errors.append(f"{key}.{field} not numeric: {v!r}")
    comp_onnx = se.get("comparison_pt_onnx") or {}
    comp_bmodel = se.get("comparison_pt_bmodel") or {}
    print(f"  comparison_pt_onnx: status={comp_onnx.get('status')} gap={comp_onnx.get('map50_gap_pct')}")
    print(f"  comparison_pt_bmodel: status={comp_bmodel.get('status')} gap={comp_bmodel.get('map50_gap_pct')}")
    onnx_st = (comp_onnx.get("status") or "").lower()
    if onnx_st not in ("pass", "ok", "success"):
        errors.append(f"comparison_pt_onnx not pass: {comp_onnx}")
    bmodel_st = (comp_bmodel.get("status") or "").lower()
    if bmodel_st not in ("pass", "ok", "success"):
        errors.append(f"comparison_pt_bmodel not pass: {comp_bmodel}")
    return errors


def main() -> int:
    token = login()
    picked = pick_small_finished_task(token)
    if not picked:
        print("FAIL: no FINISHED single image task with >=3 labeled samples")
        return 1
    task_id, task_name, labeled = picked
    print(f"pick task_id={task_id} name={task_name!r} labeled_samples={labeled}")

    try:
        created = api(
            "POST",
            f"/tasks/{task_id}/yolo-train-jobs",
            token=token,
            data={"model_weights": "yolo11n.pt", "epochs": TRAIN_EPOCHS, "imgsz": 640},
            timeout=60,
        )
    except urllib.error.HTTPError as e:
        print("FAIL create train:", e.code, e.read().decode()[:800])
        return 1

    job_id = int(created["data"]["id"])
    print(f"new train job_id={job_id} epochs={TRAIN_EPOCHS} status={created['data'].get('status')}")

    train_log = MEDIA / "yolo_train" / f"job-{job_id}" / "train_process.log"
    job = wait_train_job(token, task_id, job_id)
    if (job.get("status") or "").upper() != "COMPLETED":
        print("FAIL: training not completed")
        tail_log(train_log)
        return 1
    print(f"train OK job_id={job_id} weights={job.get('weights_path')}")

    try:
        exp_created = api(
            "POST",
            f"/tasks/{task_id}/yolo-train-jobs/{job_id}/bm1688-export",
            token=token,
            data={},
            timeout=60,
        )
    except urllib.error.HTTPError as e:
        print("FAIL create export:", e.code, e.read().decode()[:800])
        tail_log(train_log)
        return 1

    export_id = int(exp_created["data"]["id"])
    print(f"export_id={export_id} initial_status={exp_created['data'].get('status')}")

    export_dir = LOG_ROOT / str(export_id)
    d = wait_export(token, export_id)
    se = (d.get("quality_report") or {}).get("sop_eval") or {}

    print("=== sop_eval (concrete values) ===")
    print_metrics_block("pt", se.get("pt"))
    print_metrics_block("onnx", se.get("onnx"))
    print_metrics_block("bmodel", se.get("bmodel"))

    if d.get("status") != "COMPLETED":
        print(f"FAIL: export status={d.get('status')}")
        for name in ("compile.log", "quantize.log", "edge_ssh_validation.log", "sop_eval.log"):
            tail_log(export_dir / name)
        return 1

    errors = validate_sop_eval(se)
    if errors:
        print("FAIL metrics validation:")
        for e in errors:
            print(f"  - {e}")
        for name in ("quantize.log", "edge_ssh_validation.log"):
            tail_log(export_dir / name)
        return 1

    print(f"OK task_id={task_id} job_id={job_id} export_id={export_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
