#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机直连 AICM 上传模型 zip（与平台 /v2/model-set/ 相同接口）。"""
from __future__ import annotations

import os
import sys

import requests

AICM_URL = (os.environ.get("YOLO_AICM_BASE_URL") or "http://192.168.54.169:8102").strip()
AICM_USER = (os.environ.get("YOLO_AICM_USERNAME") or "7g").strip()
AICM_PASSWORD = (os.environ.get("YOLO_AICM_PASSWORD") or "").strip()
DEFAULT_MODEL_ZIP = r"E:\7G\AI\模型发布\model-20260603130630.zip"


def create_model_new_aicm(model_path: str, *, name: str | None = None) -> str:
    if not AICM_URL or not AICM_USER or not AICM_PASSWORD:
        raise SystemExit(
            "请通过 YOLO_AICM_BASE_URL、YOLO_AICM_USERNAME、"
            "YOLO_AICM_PASSWORD 配置 AICM 连接信息"
        )
    login_session = requests.Session()
    login_response = login_session.post(
        f"{AICM_URL.rstrip('/')}/v2/login/",
        data={"username": AICM_USER, "password": AICM_PASSWORD},
        timeout=60,
    )
    login_response.raise_for_status()
    from pathlib import Path

    model_name = (name or "").strip() or Path(model_path).stem or "model"
    data = {
        "_method": (None, "upload_model"),
        "chip_type": "SOPHGO,BM1688,NPU",
        "algorithm_type": "Detection",
        "name": model_name,
    }
    with open(model_path, "rb") as f:
        r = login_session.post(
            f"{AICM_URL.rstrip('/')}/v2/model-set/",
            data=data,
            files={"model_file": f},
            timeout=300,
        )
    print(r.text)
    return r.text


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL_ZIP
    upload_name = sys.argv[2] if len(sys.argv) > 2 else None
    create_model_new_aicm(path, name=upload_name)
