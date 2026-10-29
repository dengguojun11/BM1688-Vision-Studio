#!/usr/bin/env python3
import sys

sys.path.insert(0, "/data/labeIU/labeIuproject/labeIU")

from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.application.service.yolo_train import _job_work_root
from labelu.internal.application.service.yolo_train_queue import (
    _QUEUE_LOCK,
    _TRAIN_START_QUEUE,
    command_from_job,
    dispatch_queued_train_jobs,
    request_train_start,
)
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def main() -> None:
    with _QUEUE_LOCK:
        n = len(_TRAIN_START_QUEUE)
        _TRAIN_START_QUEUE.clear()
        print(f"cleared queue ({n})")

    with session_scope() as db:
        pending = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.status == YoloTrainStatus.PENDING.value)
            .order_by(YoloTrainJob.id.asc())
            .all()
        )

    for job in pending:
        jid = int(job.id)
        mode = str(getattr(job, "execution_mode", None) or "local").strip()
        cmd = command_from_job(job)
        task_id = int(job.task_id)
        resume = (_job_work_root(jid) / "train" / "weights" / "last.pt").is_file()
        if mode == "remote":
            from labelu.internal.application.service.remote_yolo_train import (
                request_remote_train_start,
            )

            print(f"remote dispatch #{jid}")
            request_remote_train_start(jid, task_id, cmd, resume=resume)
        else:
            print(f"local request_train_start #{jid}")
            ok = request_train_start(jid, task_id, cmd, resume=resume)
            print(f"  started={ok}")

    started = dispatch_queued_train_jobs()
    print(f"dispatch started={started}")


if __name__ == "__main__":
    main()
