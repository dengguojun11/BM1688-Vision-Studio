#!/usr/bin/env python3
"""对已有训练作业新建 BM1688 导出并打印具体评估数值。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:10001/api/v1"


def api(method, path, token=None, data=None, timeout=120):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fmt(v):
    if v is None:
        return "null"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def main():
    task_id = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    job_id = int(sys.argv[2]) if len(sys.argv) > 2 else 80

    token = api("POST", "/users/login", data={"username": "admin@123.com", "password": "Admin@123"})[
        "data"
    ]["token"]
    created = api(
        "POST",
        f"/tasks/{task_id}/yolo-train-jobs/{job_id}/bm1688-export",
        token=token,
        data={},
    )
    export_id = created["data"]["id"]
    print(f"export_id={export_id} task_id={task_id} job_id={job_id}")

    for i in range(120):
        d = api("GET", f"/yolo-bm1688-exports/{export_id}", token=token)["data"]
        st = d.get("status")
        err = (d.get("error_message") or "")[:120]
        print(f"poll {i}: status={st} err={err}")
        if st not in ("PENDING", "PROCESSING", "ONNX_READY"):
            break
        time.sleep(30)

    d = api("GET", f"/yolo-bm1688-exports/{export_id}", token=token)["data"]
    se = (d.get("quality_report") or {}).get("sop_eval") or {}
    for key in ("pt", "onnx", "bmodel"):
        m = (se.get(key) or {}).get("metrics") or {}
        print(
            f"{key}: mAP50={fmt(m.get('map50'))} precision={fmt(m.get('precision'))} "
            f"recall={fmt(m.get('recall'))} images={fmt(m.get('images'))}"
        )
    print("comparison_pt_onnx", se.get("comparison_pt_onnx"))
    print("comparison_pt_bmodel", se.get("comparison_pt_bmodel"))
    print("export_status", d.get("status"), "error_message", d.get("error_message"))

    bm = (se.get("bmodel") or {}).get("metrics") or {}
    if bm.get("precision") is None or bm.get("recall") is None or bm.get("map50") is None:
        return 1
    if d.get("status") != "COMPLETED":
        return 1
    if (d.get("error_message") or "").strip():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
