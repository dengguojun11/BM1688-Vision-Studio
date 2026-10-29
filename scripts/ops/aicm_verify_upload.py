#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机验证 AICM 模型是否上传成功（登录 + 列表 + 可选重新上传）。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

AICM_URL = (os.environ.get("YOLO_AICM_BASE_URL") or "http://192.168.54.169:8102").strip()
AICM_USER = (os.environ.get("YOLO_AICM_USERNAME") or "7g").strip()
AICM_PASSWORD = (os.environ.get("YOLO_AICM_PASSWORD") or "").strip()


def _session() -> requests.Session:
    if not AICM_URL or not AICM_USER or not AICM_PASSWORD:
        raise SystemExit(
            "请通过 YOLO_AICM_BASE_URL、YOLO_AICM_USERNAME、"
            "YOLO_AICM_PASSWORD 配置 AICM 连接信息"
        )
    s = requests.Session()
    r = s.post(
        f"{AICM_URL.rstrip('/')}/v2/login/",
        data={"username": AICM_USER, "password": AICM_PASSWORD},
        timeout=60,
    )
    if r.status_code >= 400:
        raise SystemExit(f"登录失败 HTTP {r.status_code}: {r.text[:300]}")
    return s


def list_top_models(s: requests.Session, *, like_name: str = "", limit: int = 10) -> list[dict]:
    params = {
        "_page": 1,
        "_limit": limit,
        "_method": "list",
        "_filter_in_use": 0,
        "_filter_engine_id": 0,
        "_like_name": like_name,
        "_sort": "",
        "_filter-exact_list-learning_task__label_class_set__id": "",
        "_filter-gte-created_at": "",
        "_filter-lt-created_at": "",
        "_filter_model_source": "",
    }
    r = s.get(f"{AICM_URL.rstrip('/')}/v2/model-set/", params=params, timeout=60)
    body = r.json()
    if body.get("error_code") not in (0, None, "0"):
        raise SystemExit(f"列表失败: {body}")
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("rows", "list", "items"):
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
    return []


def _row_ts(row: dict) -> int:
    try:
        return int(row.get("created_at") or 0)
    except (TypeError, ValueError):
        return 0


def _fmt_ts(ts: int) -> str:
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def upload_zip(s: requests.Session, zip_path: str, *, name: str) -> dict:
    data = {
        "_method": (None, "upload_model"),
        "chip_type": "SOPHGO,BM1688,NPU",
        "algorithm_type": "Detection",
        "name": name,
    }
    with open(zip_path, "rb") as f:
        r = s.post(
            f"{AICM_URL.rstrip('/')}/v2/model-set/",
            data=data,
            files={"model_file": f},
            timeout=300,
        )
    try:
        return r.json()
    except ValueError:
        return {"error_code": -1, "error_info": r.text[:500]}


def main() -> int:
    like = sys.argv[1] if len(sys.argv) > 1 else ""
    zip_path = sys.argv[2] if len(sys.argv) > 2 else ""
    upload_name = (
        sys.argv[3]
        if len(sys.argv) > 3
        else (os.environ.get("AICM_UPLOAD_NAME") or "").strip()
    )
    if zip_path and not upload_name:
        upload_name = Path(zip_path).stem

    s = _session()
    before_top = list_top_models(s, limit=1)
    before_id = int(before_top[0].get("id") or 0) if before_top else 0

    if zip_path:
        print(f">>> 上传: {zip_path}  name={upload_name}")
        resp = upload_zip(s, zip_path, name=upload_name)
        print(json.dumps(resp, ensure_ascii=False, indent=2))
        if resp.get("error_code") not in (0, None, "0"):
            print("\n结论: 上传接口返回失败，模型未入库。")
            return 1

    rows = list_top_models(s, like_name=like, limit=10)
    if not rows:
        print("模型列表为空（或关键字无匹配）。")
        return 2

    print("\n>>> 当前 AICM 模型（最新在前）")
    for i, row in enumerate(rows[:10], 1):
        rid = row.get("id")
        name = row.get("name") or row.get("model_name") or ""
        print(f"{i}. id={rid}  name={name}  created={_fmt_ts(_row_ts(row))}")

    after_top = list_top_models(s, limit=1)
    after_id = int(after_top[0].get("id") or 0) if after_top else 0

    if zip_path and after_id > before_id:
        print(f"\n结论: 上传成功（最新 id {before_id} -> {after_id}）。")
        return 0
    if zip_path:
        print(f"\n结论: 上传后最新 id 仍为 {after_id}，与上传前 {before_id} 相同，未新增模型。")
        return 1

    print(
        "\n提示: 仅查看列表。要验证上传请: "
        "python scripts/ops/aicm_verify_upload.py '' <model.zip> [name]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
