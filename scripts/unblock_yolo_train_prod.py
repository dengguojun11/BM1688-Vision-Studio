#!/usr/bin/env python3
"""生产机：清理僵尸训练作业、释放 LA 显存、触发队列派发。"""
from __future__ import annotations

import subprocess
import sys

sys.path.insert(0, "/data/labeIU/labeIuproject/labeIU")


def main() -> int:
    from labelu.internal.application.service.yolo_train_queue import (
        _QUEUE_LOCK,
        _TRAIN_START_QUEUE,
        dispatch_queued_train_jobs,
        drain_db_pending_train_jobs,
        reconcile_stale_processing_jobs,
    )
    from labelu.internal.common.config import settings

    print("MEDIA_ROOT", settings.MEDIA_ROOT)
    try:
        subprocess.run(
            ["curl", "-sf", "-X", "POST", "http://127.0.0.1:5001/unload"],
            timeout=10,
            check=False,
        )
        print("LocateAnything unload requested")
    except OSError as exc:
        print("LocateAnything unload skip:", exc)

    with _QUEUE_LOCK:
        n_q = len(_TRAIN_START_QUEUE)
        _TRAIN_START_QUEUE.clear()
        print(f"cleared in-memory train queue ({n_q} items)")

    fixed = reconcile_stale_processing_jobs()
    print(f"reconcile_stale_processing_jobs: {fixed}")

    started = dispatch_queued_train_jobs()
    print(f"dispatch_queued_train_jobs: started={started}")

    drained = drain_db_pending_train_jobs()
    print(f"drain_db_pending_train_jobs: submitted={drained}")

    started2 = dispatch_queued_train_jobs()
    print(f"dispatch again: started={started2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
