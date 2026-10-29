#!/usr/bin/env python3
import json
import sys
import time
import requests

BASE = "http://127.0.0.1:10001/api/v1"
USER = "admin@123.com"
PASS = "Admin@123"
EXPORT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 27


def login():
    r = requests.post(f"{BASE}/users/login", json={"username": USER, "password": PASS}, timeout=60)
    r.raise_for_status()
    tok = (r.json().get("data") or {}).get("token", "")
    if not tok.lower().startswith("bearer "):
        tok = f"Bearer {tok}"
    return {"Authorization": tok}


def main():
    h = login()
    print(f"publish export_id={EXPORT_ID}")
    r = requests.post(f"{BASE}/yolo-aicm-publish/publish", json={"export_id": EXPORT_ID}, headers=h, timeout=120)
    print(r.status_code, r.text[:500])
    r.raise_for_status()
    job = (r.json().get("data") or {})
    job_id = job.get("job_id") or job.get("id")
    print(f"publish job_id={job_id}")
    for _ in range(120):
        rr = requests.get(f"{BASE}/yolo-aicm-publish/publish-jobs/{job_id}", headers=h, timeout=60)
        rr.raise_for_status()
        data = rr.json().get("data") or {}
        st = data.get("status")
        print(f"status={st}")
        if st == "COMPLETED":
            print("PUBLISH OK")
            print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
            return 0
        if st == "FAILED":
            print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
            return 1
        time.sleep(10)
    print("timeout")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
