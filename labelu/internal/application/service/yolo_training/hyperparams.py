"""YOLO runtime hyperparameter helpers."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional


def job_hyperparams_dict(job) -> Optional[dict]:
    raw = getattr(job, "hyperparams_json", None)
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def merge_runtime_hyperparams_into_job(
    job_hyperparams_dict_fn: Callable[[Any], Optional[dict]],
    job,
    runtime_hyperparams: dict[str, Any],
) -> tuple[int, int, dict[str, Any]]:
    merged_hp = dict(job_hyperparams_dict_fn(job) or {})
    epochs = int(job.epochs or 50)
    imgsz = int(job.imgsz or 640)
    for key, value in runtime_hyperparams.items():
        if key == "epochs":
            epochs = int(value)
        elif key == "imgsz":
            imgsz = int(value)
        else:
            merged_hp[key] = value
    return epochs, imgsz, merged_hp


def apply_runtime_hyperparams_to_job_row(
    merge_runtime_hyperparams_into_job_fn: Callable[[Any, dict[str, Any]], tuple[int, int, dict[str, Any]]],
    normalize_hyperparams_fn: Callable[[dict | None], dict | None],
    db,
    job,
    runtime_hyperparams: dict[str, Any],
):
    epochs, imgsz, merged_hp = merge_runtime_hyperparams_into_job_fn(job, runtime_hyperparams)
    job.epochs = max(1, min(3000, epochs))
    job.imgsz = max(320, min(1280, imgsz))
    job.hyperparams_json = (
        json.dumps(normalize_hyperparams_fn(merged_hp), ensure_ascii=False)
        if merged_hp
        else None
    )
    db.flush()
    db.refresh(job)
    return job


def apply_pending_runtime_hyperparams_and_restart(
    read_pending_runtime_hyperparams_fn: Callable[[int], Optional[dict[str, Any]]],
    normalize_runtime_editable_hyperparams_fn: Callable[[dict | None], dict | None],
    session_scope_fn: Callable[[], Any],
    begin_transaction_fn: Callable[[Any], Any],
    crud_yolo_train_job,
    apply_runtime_hyperparams_to_job_row_fn: Callable[[Any, Any, dict[str, Any]], Any],
    clear_pending_runtime_hyperparams_fn: Callable[[int], None],
    clear_stop_train_job_fn: Callable[[int], None],
    clear_user_pause_requested_fn: Callable[[int], None],
    append_train_process_log_fn: Callable[[int, str], None],
    launch_train_job_from_existing_config_fn: Callable[[Any, int], bool],
    job_id: int,
    *,
    before_apply_fn: Callable[[Any, Any], None] | None = None,
) -> bool:
    pending = read_pending_runtime_hyperparams_fn(job_id)
    runtime_hp = normalize_runtime_editable_hyperparams_fn(
        (pending or {}).get("hyperparams") if isinstance(pending, dict) else None
    )
    if not runtime_hp:
        return False
    with session_scope_fn() as db:
        with begin_transaction_fn(db):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                return False
            if before_apply_fn is not None:
                before_apply_fn(db, job)
            apply_runtime_hyperparams_to_job_row_fn(db, job, runtime_hp)
        clear_pending_runtime_hyperparams_fn(job_id)
        clear_stop_train_job_fn(job_id)
        clear_user_pause_requested_fn(job_id)
        append_train_process_log_fn(
            job_id,
            f"runtime_hyperparams_apply detail={json.dumps(runtime_hp, ensure_ascii=False)}",
        )
        return launch_train_job_from_existing_config_fn(db, job_id)


def consume_pending_runtime_hyperparams_for_current_run(
    read_pending_runtime_hyperparams_fn: Callable[[int], Optional[dict[str, Any]]],
    normalize_runtime_editable_hyperparams_fn: Callable[[dict | None], dict | None],
    session_scope_fn: Callable[[], Any],
    begin_transaction_fn: Callable[[Any], Any],
    crud_yolo_train_job,
    apply_runtime_hyperparams_to_job_row_fn: Callable[[Any, Any, dict[str, Any]], Any],
    command_from_job_fn: Callable[[Any], Any],
    clear_pending_runtime_hyperparams_fn: Callable[[int], None],
    clear_stop_train_job_fn: Callable[[int], None],
    clear_user_pause_requested_fn: Callable[[int], None],
    append_train_process_log_fn: Callable[[int, str], None],
    job_id: int,
    *,
    before_apply_fn: Callable[[Any, Any], None] | None = None,
):
    pending = read_pending_runtime_hyperparams_fn(job_id)
    runtime_hp = normalize_runtime_editable_hyperparams_fn(
        (pending or {}).get("hyperparams") if isinstance(pending, dict) else None
    )
    if not runtime_hp:
        return None
    with session_scope_fn() as db:
        with begin_transaction_fn(db):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job:
                return None
            if before_apply_fn is not None:
                before_apply_fn(db, job)
            apply_runtime_hyperparams_to_job_row_fn(db, job, runtime_hp)
            cmd = command_from_job_fn(job)
        clear_pending_runtime_hyperparams_fn(job_id)
        clear_stop_train_job_fn(job_id)
        clear_user_pause_requested_fn(job_id)
        append_train_process_log_fn(
            job_id,
            f"runtime_hyperparams_apply_current_run detail={json.dumps(runtime_hp, ensure_ascii=False)}",
        )
        return cmd
