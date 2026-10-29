#!/usr/bin/env python3
"""在生产机重试指定 export 的边端 SSH 验证。"""
import sys

from labelu.internal.common.db import SessionLocal
from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
from labelu.internal.application.service.edge_ssh_validation import (
    run_edge_ssh_validation_sync,
)


def main() -> int:
    export_id = int(sys.argv[1]) if len(sys.argv) > 1 else 26
    db = SessionLocal()
    try:
        row = crud_yolo_bm1688_export.get(db, export_id)
        if not row:
            print(f"export {export_id} not found")
            return 1
        summary = run_edge_ssh_validation_sync(
            db,
            export_id=export_id,
            yolo_train_job_id=int(row.yolo_train_job_id),
            imgsz=int(row.imgsz or 640),
        )
        print(summary)
        return 0 if summary.get("status") == "ok" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
