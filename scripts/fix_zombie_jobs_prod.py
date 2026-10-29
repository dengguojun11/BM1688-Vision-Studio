#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, "/data/labeIU/labeIuproject/labeIU")

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

root = Path(settings.MEDIA_ROOT) / settings.YOLO_TRAIN_WORK_SUBDIR
with session_scope() as db:
    rows = db.query(YoloTrainJob).filter(YoloTrainJob.status == YoloTrainStatus.PROCESSING.value).all()
    for job in rows:
        jid = int(job.id)
        phase = ""
        p = root / f"job-{jid}" / "phase.json"
        if p.is_file():
            try:
                phase = json.loads(p.read_text(encoding="utf-8")).get("phase", "")
            except Exception:
                pass
        print(f"job #{jid} phase={phase!r}")
        if phase == "failed":
            st = YoloTrainStatus.FAILED.value
        else:
            st = YoloTrainStatus.PENDING.value
        with begin_transaction(db):
            crud_yolo_train_job.update_status(db, job, st, error_message=None)
        print(f"  -> {st}")

print("done")
