#!/usr/bin/env python3
import requests

for pwd in ("Admin@123", "labelu123", "admin@123"):
    r = requests.post(
        "http://127.0.0.1:10001/api/v1/users/login",
        json={"username": "admin@123.com", "password": pwd},
        timeout=30,
    )
    tok = (r.json().get("data") or {}).get("token", "")
    print(pwd, r.status_code, "ok" if tok else r.text[:120])
