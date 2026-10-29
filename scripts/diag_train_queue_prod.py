#!/usr/bin/env python3
"""生产机训练队列/GPU 诊断（一次性脚本）。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from labelu.internal.common.config import settings
from labelu.internal.common.db import session_scope
from labelu.internal.common.yolo_train_gpu_allocator import (
    build_gpu_occupancy,
    gpu_display_fields_for_job,
    train_gpu_pool_indices,
    yolo_train_gpu_auto_alloc_enabled,
)
from labelu.internal.common.yolo_train_resource_profile import (
    compute_yolo_train_resource_profile,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _gpu_free_mb() -> dict[int, int]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError:
        return {}
    out: dict[int, int] = {}
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
            out[idx] = int(float(parts[1]))
        except ValueError:
            continue
    return out


def main() -> None:
    print("=== LabelU YOLO train queue diagnostic ===")
    print(f"YOLO_TRAIN_ENABLED={settings.YOLO_TRAIN_ENABLED}")
    print(f"YOLO_TRAIN_DEVICE={settings.YOLO_TRAIN_DEVICE!r}")
    print(f"YOLO_TRAIN_GPU_AUTO_ALLOC={getattr(settings, 'YOLO_TRAIN_GPU_AUTO_ALLOC', True)}")
    print(f"auto_alloc_enabled={yolo_train_gpu_auto_alloc_enabled()}")
    print(f"train_gpu_pool={list(train_gpu_pool_indices())}")
    profile = compute_yolo_train_resource_profile()
    print(f"profile: {profile.summary()}")

    free = _gpu_free_mb()
    print(f"nvidia-smi free_mb (approx): {free}")
    print(f"min_free_threshold={profile.min_free_mb_per_gpu}MB")

    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        print("--- compute processes ---")
        print((proc.stdout or "").strip() or "(none)")
    except OSError:
        pass

    occ = build_gpu_occupancy()
    print(f"labelu GPU occupancy map: {occ}")

    root = Path(settings.MEDIA_ROOT) / settings.YOLO_TRAIN_WORK_SUBDIR
    resume_q = root / ".deploy_resume_queue.json"
    if resume_q.is_file():
        print(f"deploy_resume_queue: {resume_q.read_text(encoding='utf-8')[:500]}")

    with session_scope() as db:
        pending = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.status == "PENDING")
            .order_by(YoloTrainJob.id.asc())
            .all()
        )
        processing = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.status == "PROCESSING")
            .order_by(YoloTrainJob.id.asc())
            .all()
        )
        failed_recent = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.status.in_(["FAILED", "CANCELLED"]))
            .order_by(YoloTrainJob.id.desc())
            .limit(8)
            .all()
        )

    print(f"\n--- PENDING ({len(pending)}) ---")
    for j in pending:
        gpu = gpu_display_fields_for_job(int(j.id))
        phase_path = root / f"job-{j.id}" / "phase.json"
        phase = ""
        if phase_path.is_file():
            try:
                phase = json.loads(phase_path.read_text(encoding="utf-8")).get("detail", "")
            except Exception:
                pass
        print(
            f"  #{j.id} mode={j.execution_mode or 'local'} gpu={gpu.get('train_gpu_label')} "
            f"phase_detail={phase[:100]}"
        )

    print(f"\n--- PROCESSING ({len(processing)}) ---")
    for j in processing:
        gpu = gpu_display_fields_for_job(int(j.id))
        print(f"  #{j.id} mode={j.execution_mode or 'local'} gpu={gpu.get('train_gpu_label')}")

    print("\n--- recent FAILED/CANCELLED ---")
    for j in failed_recent:
        print(f"  #{j.id} {j.status} err={(j.error_message or '')[:90]}")


    print("\n--- all jobs id>=114 ---")
    with session_scope() as db:
        all_recent = (
            db.query(YoloTrainJob)
            .filter(YoloTrainJob.id >= 114)
            .order_by(YoloTrainJob.id.asc())
            .all()
        )
    for j in all_recent:
        gpu = gpu_display_fields_for_job(int(j.id))
        print(
            f"  #{j.id} {j.status} mode={j.execution_mode or 'local'} "
            f"gpu={gpu.get('train_gpu_label') or '-'}"
        )


if __name__ == "__main__":
    main()
