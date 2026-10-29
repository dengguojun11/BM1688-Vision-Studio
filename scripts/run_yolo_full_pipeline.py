#!/usr/bin/env python3
"""
生产机端到端闭环：训练 -> BM1688 导出 -> 边端 SSH 验证 -> AICM 发布。
用法（在生产 venv 内）:
  python scripts/run_yolo_full_pipeline.py --task-id 14 --epochs 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_BASE = "http://127.0.0.1:10000/api/v1"
DEFAULT_USER = "admin@123.com"
DEFAULT_PASS = ""


def login(base: str, user: str, password: str) -> str:
    r = requests.post(
        f"{base}/users/login",
        json={"username": user, "password": password},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json().get("data") or {}
    token = data.get("token")
    if not token:
        raise RuntimeError(f"login failed: {r.text[:500]}")
    return token


def headers(token: str) -> Dict[str, str]:
    tok = (token or "").strip()
    if not tok.lower().startswith("bearer "):
        tok = f"Bearer {tok}"
    return {"Authorization": tok}


def poll(
    fn,
    *,
    label: str,
    interval: float = 15.0,
    timeout: float = 7200.0,
    ok_statuses: tuple = ("COMPLETED", "ok"),
) -> Any:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        cur = fn()
        st = cur.get("status") if isinstance(cur, dict) else getattr(cur, "status", None)
        if st != last:
            print(f"[poll] {label} status={st}")
            last = st
        if st in ok_statuses:
            return cur
        if st in ("FAILED", "fail", "CANCELLED"):
            raise RuntimeError(f"{label} failed: {json.dumps(cur, ensure_ascii=False, default=str)[:2000]}")
        time.sleep(interval)
    raise TimeoutError(f"{label} timeout after {timeout}s, last={last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--password", default=DEFAULT_PASS)
    ap.add_argument("--task-id", type=int, required=True)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--category", default="闭环巡检")
    ap.add_argument("--skip-train", action="store_true")
    ap.add_argument("--job-id", type=int, default=0, help="skip-train 时指定已完成作业")
    ap.add_argument("--export-id", type=int, default=0, help="跳过训练与导出，直接边端验证+发布")
    ap.add_argument("--skip-edge", action="store_true")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--env-id", default="", help="AICM 发布环境 id")
    args = ap.parse_args()

    if not (args.password or "").strip():
        raise SystemExit("missing --password (or LABELU_ADMIN_PASSWORD)")
    token = login(args.base, args.user, args.password)
    print(f"login ok token_prefix={token[:16]}...")
    h = headers(token)

    if args.skip_train:
        job_id = int(args.job_id)
        if job_id <= 0:
            raise SystemExit("--skip-train 需要 --job-id")
    else:
        payload = {
            "model_weights": "yolo11n.pt",
            "epochs": args.epochs,
            "imgsz": args.imgsz,
            "category": args.category,
            "hyperparams": {"batch": 16, "patience": 50},
        }
        print(f"create train job task_id={args.task_id} epochs={args.epochs}")
        r = requests.post(
            f"{args.base}/tasks/{args.task_id}/yolo-train-jobs",
            json=payload,
            headers=h,
            timeout=120,
        )
        r.raise_for_status()
        job = (r.json().get("data") or {})
        job_id = int(job["id"])
        print(f"train job created id={job_id}")

        def _get_job():
            rr = requests.get(
                f"{args.base}/tasks/{args.task_id}/yolo-train-jobs/{job_id}",
                headers=h,
                timeout=60,
            )
            rr.raise_for_status()
            return rr.json().get("data") or {}

        poll(_get_job, label=f"train job-{job_id}", timeout=max(7200, args.epochs * 180))

    if int(args.export_id or 0) > 0:
        export_id = int(args.export_id)
        print(f"use existing export_id={export_id}")
    elif args.skip_export:
        raise SystemExit("--skip-export 需要 --export-id")
    else:
        print(f"start bm1688 export job_id={job_id}")
        r = requests.post(
            f"{args.base}/tasks/{args.task_id}/yolo-train-jobs/{job_id}/bm1688-export",
            headers=h,
            timeout=120,
        )
        r.raise_for_status()
        export_row = (r.json().get("data") or {})
        export_id = int(export_row["id"])
        print(f"export started id={export_id}")

        def _get_export():
            rr = requests.get(
                f"{args.base}/yolo-bm1688-exports/{export_id}",
                headers=h,
                timeout=60,
            )
            rr.raise_for_status()
            return rr.json().get("data") or {}

        poll(_get_export, label=f"export-{export_id}", timeout=3600)

    if not args.skip_edge:
        print(f"edge ssh validation export_id={export_id}")
        rr = requests.post(
            f"{args.base}/yolo-bm1688-exports/{export_id}/edge-ssh-validation",
            headers=h,
            timeout=7200,
        )
        if rr.status_code >= 400:
            raise RuntimeError(
                f"edge validation HTTP {rr.status_code}: {rr.text[:1200]}"
            )
        edge_resp = rr.json().get("data") or {}
        bv = edge_resp.get("board_validation") or {}
        edge_st = str(bv.get("status") or "").upper()
        edge_err = bv.get("error_message") or ""
        print(f"edge board_validation status={edge_st}")
        if edge_st not in ("COMPLETED", "OK", "SKIPPED"):
            raise RuntimeError(
                f"edge validation failed ({edge_st}): {edge_err[:1500]}"
            )

    pub_body: Dict[str, Any] = {"export_id": export_id}
    if args.env_id:
        pub_body["environment_id"] = args.env_id
    print(f"aicm publish export_id={export_id}")
    r = requests.post(
        f"{args.base}/yolo-aicm-publish/publish",
        json=pub_body,
        headers=h,
        timeout=120,
    )
    r.raise_for_status()
    pub_job = (r.json().get("data") or {})
    pub_job_id = pub_job.get("job_id") or pub_job.get("id")
    print(f"publish job id={pub_job_id}")

    def _get_pub():
        rr = requests.get(
            f"{args.base}/yolo-aicm-publish/publish-jobs/{pub_job_id}",
            headers=h,
            timeout=60,
        )
        rr.raise_for_status()
        return rr.json().get("data") or {}

    result = poll(
        _get_pub,
        label=f"publish-{pub_job_id}",
        ok_statuses=("COMPLETED", "SUCCESS", "ok", "completed"),
        timeout=1800,
    )
    print("PIPELINE OK")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PIPELINE FAIL: {exc}", file=sys.stderr)
        raise
