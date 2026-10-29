#!/usr/bin/env python3
import json
from pathlib import Path

p = Path("/data/labeIU/labelu_appdata/edge_ssh_config.json")
d = json.loads(p.read_text(encoding="utf-8"))
d["edge_root"] = "/data/labelu_edge"
d["remote_base"] = "/data/labelu_edge/jobs"
d["sophon_demo_root"] = "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("ok", d["sophon_demo_root"])
