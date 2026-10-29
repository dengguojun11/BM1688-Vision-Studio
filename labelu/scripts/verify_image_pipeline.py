#!/usr/bin/env python
"""本地验证图像导入流水线（需服务已启动且 IMAGE_PIPELINE_ENABLED=true）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

BASE = os.environ.get("LABELU_VERIFY_BASE", "http://127.0.0.1:8002")
API = f"{BASE}/api/v1"
USER = os.environ.get("LABELU_VERIFY_USER", "test@example.com")
PASSWORD = os.environ.get("LABELU_VERIFY_PASSWORD", "test@123")
PNG = Path(__file__).resolve().parents[1] / "tests" / "data" / "test.png"


def main() -> int:
    if not PNG.is_file():
        print(f"缺少测试图: {PNG}", file=sys.stderr)
        return 1

    r = requests.post(
        f"{API}/users/login",
        json={"username": USER, "password": PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    token = r.json()["data"]["token"]
    headers = {"Authorization": token if str(token).startswith("Bearer") else f"Bearer {token}"}

    task = requests.post(
        f"{API}/tasks",
        headers=headers,
        json={"name": "pipeline-verify", "description": "", "tips": ""},
        timeout=30,
    )
    task.raise_for_status()
    task_id = task.json()["data"]["id"]
    print(f"task_id={task_id}")

    file_ids = []
    for i in range(1, 4):
        with PNG.open("rb") as f:
            up = requests.post(
                f"{API}/tasks/{task_id}/attachments",
                headers=headers,
                files={"file": (f"v_{task_id}_{i}.png", f, "image/png")},
                timeout=120,
            )
        if up.status_code != 201:
            print("upload failed:", up.status_code, up.text, file=sys.stderr)
            return 1
        fid = up.json()["data"]["id"]
        file_ids.append(fid)
        print(f"  uploaded attachment id={fid}")

    staging = Path(os.environ.get("MEDIA_ROOT", "")) if os.environ.get("MEDIA_ROOT") else None
    if staging:
        for fid in file_ids:
            p = staging / ".image_pipeline_staging" / str(task_id) / f"{fid}.npy"
            print(f"  staging {p.name}: {'yes' if p.is_file() else 'no'}")

    samples = requests.post(
        f"{API}/tasks/{task_id}/samples",
        headers=headers,
        json=[{"file_id": fid, "data": {}} for fid in file_ids],
        timeout=60,
    )
    if samples.status_code != 201:
        print("create samples failed:", samples.status_code, samples.text, file=sys.stderr)
        return 1
    sample_ids = samples.json()["data"]["ids"]
    print(f"sample_ids={sample_ids}")

    ok = True
    for sid in sample_ids:
        detail = requests.get(
            f"{API}/tasks/{task_id}/samples/{sid}",
            headers=headers,
            timeout=30,
        )
        detail.raise_for_status()
        data = detail.json()["data"].get("data") or {}
        if isinstance(data, str):
            data = json.loads(data)
        pipe = data.get("image_pipeline") or {}
        print(f"  sample {sid}: scene_id={pipe.get('scene_id')} is_outlier={pipe.get('is_outlier')}")
        if not pipe.get("scene_id"):
            ok = False

    print("PASS" if ok else "FAIL: missing image_pipeline metadata")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
