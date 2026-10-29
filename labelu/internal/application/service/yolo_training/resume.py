"""YOLO checkpoint resume helpers.

These helpers keep checkpoint inspection and resume kwarg construction out of
the monolithic ``yolo_train`` service while preserving wrapper points there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def is_nothing_to_resume_error(exc: BaseException) -> bool:
    return "nothing to resume" in str(exc).lower()


def _is_plate_recognition_job(job: Any) -> bool:
    return str(getattr(job, "job_type", None) or "").strip() in {
        "plate_detection",
        "plate_recognition",
    }


def resume_train_hyperparams(
    merge_train_hyperparams_fn: Callable[..., dict],
    normalize_hyperparams_fn: Callable[[dict | None], dict | None],
    cmd,
    *,
    train_device: str,
    job_id: int,
) -> dict:
    base = {
        "epochs": cmd.epochs,
        "imgsz": cmd.imgsz,
        "device": train_device,
    }
    return merge_train_hyperparams_fn(
        base,
        normalize_hyperparams_fn(cmd.hyperparams) or {},
        train_device=train_device,
        job_id=job_id,
    )


def read_checkpoint_train_field(checkpoint_path: Path, field: str) -> Any:
    if not checkpoint_path.is_file():
        return None
    try:
        import torch
    except Exception:
        return None
    try:
        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        ckpt = torch.load(checkpoint_path, map_location="cpu")
    except Exception:
        return None
    if not isinstance(ckpt, dict):
        return None
    for key in ("train_args", "args"):
        args = ckpt.get(key)
        if isinstance(args, dict) and field in args:
            return args.get(field)
    return None


def strip_checkpoint_scaler(checkpoint_path: Path, logger) -> bool:
    if not checkpoint_path.is_file():
        return False
    try:
        import torch
    except Exception:
        return False
    try:
        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        ckpt = torch.load(checkpoint_path, map_location="cpu")
    except Exception as exc:
        logger.warning("Unable to strip scaler from {}: {}", checkpoint_path, exc)
        return False
    if not isinstance(ckpt, dict):
        return False
    removed = False
    for key in ("scaler", "grad_scaler"):
        if key in ckpt:
            ckpt.pop(key, None)
            removed = True
    if not removed:
        return False
    try:
        torch.save(ckpt, checkpoint_path)
    except Exception as exc:
        logger.warning(
            "Unable to save scaler-stripped checkpoint {}: {}", checkpoint_path, exc
        )
        return False
    return True


def patch_checkpoint_train_args(
    checkpoint_path: Path,
    updates: dict[str, Any],
    logger,
) -> list[str]:
    if not updates or not checkpoint_path.is_file():
        return []
    patch = {
        key: value
        for key, value in updates.items()
        if key not in ("resume", "model") and value is not None
    }
    if not patch:
        return []
    try:
        import torch
    except Exception as exc:
        logger.warning(
            "Unable to patch YOLO checkpoint args: torch import failed: {}", exc
        )
        return []

    try:
        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        ckpt = torch.load(checkpoint_path, map_location="cpu")
    except Exception as exc:
        logger.warning(
            "Unable to patch YOLO checkpoint args {}: {}", checkpoint_path, exc
        )
        return []
    if not isinstance(ckpt, dict):
        return []

    changed: list[str] = []
    for field in ("train_args", "args"):
        args = ckpt.get(field)
        if not isinstance(args, dict):
            continue
        for key, value in patch.items():
            if args.get(key) != value:
                args[key] = value
                if key not in changed:
                    changed.append(key)

    if not changed:
        return []
    try:
        torch.save(ckpt, checkpoint_path)
    except Exception as exc:
        logger.warning(
            "Unable to save patched YOLO checkpoint args {}: {}", checkpoint_path, exc
        )
        return []
    return changed


def checkpoint_epoch_blocks_resume(
    checkpoint_path: Path,
    work_root: Path,
    *,
    target_epochs: int,
    read_last_epoch_from_results_csv_fn: Callable[[Path], int | None],
) -> bool:
    if not checkpoint_path.is_file():
        return False
    last_csv = read_last_epoch_from_results_csv_fn(work_root / "train")
    try:
        import torch
    except Exception:
        return False
    try:
        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        ckpt = torch.load(checkpoint_path, map_location="cpu")
    except Exception:
        return False
    if not isinstance(ckpt, dict):
        return False
    raw = ckpt.get("epoch")
    if raw is None:
        return True
    try:
        ep = int(raw)
    except (TypeError, ValueError):
        return True
    if ep < 0:
        return True
    if (
        ep >= int(target_epochs)
        and last_csv is not None
        and int(last_csv) < int(target_epochs)
    ):
        return True
    return False


def repair_checkpoint_epoch_before_resume(
    checkpoint_epoch_blocks_resume_fn: Callable[..., bool],
    read_last_epoch_from_results_csv_fn: Callable[[Path], int | None],
    append_train_process_log_fn: Callable[[int, str], None],
    logger,
    checkpoint_path: Path,
    work_root: Path,
    *,
    target_epochs: int,
    job_id: int,
) -> bool:
    if not checkpoint_epoch_blocks_resume_fn(
        checkpoint_path, work_root, target_epochs=target_epochs
    ):
        return False
    last_csv = read_last_epoch_from_results_csv_fn(work_root / "train")
    new_epoch = int(last_csv) if last_csv is not None else 0
    try:
        import torch
    except Exception as exc:
        logger.warning("repair checkpoint epoch skipped (torch): {}", exc)
        return False
    try:
        ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        ckpt = torch.load(checkpoint_path, map_location="cpu")
    except Exception as exc:
        logger.warning(
            "repair checkpoint epoch load failed {}: {}", checkpoint_path, exc
        )
        return False
    if not isinstance(ckpt, dict):
        return False
    old_epoch = ckpt.get("epoch")
    ckpt["epoch"] = new_epoch
    train_args = ckpt.get("train_args")
    if isinstance(train_args, dict):
        train_args["epochs"] = int(target_epochs)
        train_args["resume"] = False
    try:
        torch.save(ckpt, checkpoint_path)
    except Exception as exc:
        logger.warning(
            "repair checkpoint epoch save failed {}: {}", checkpoint_path, exc
        )
        return False
    append_train_process_log_fn(
        job_id,
        f"checkpoint_epoch_repair old={old_epoch} new={new_epoch} target_epochs={target_epochs}",
    )
    return True


def build_resume_train_kwargs(
    repair_checkpoint_epoch_before_resume_fn: Callable[..., bool],
    resume_train_hyperparams_fn: Callable[..., dict],
    read_checkpoint_train_field_fn: Callable[[Path, str], Any],
    patch_checkpoint_train_args_fn: Callable[[Path, dict[str, Any]], list[str]],
    strip_checkpoint_scaler_fn: Callable[[Path], bool],
    truthy_train_flag_fn: Callable[[Any], bool],
    append_train_process_log_fn: Callable[[int, str], None],
    resume_override_keys: set[str],
    *,
    last_path: Path,
    cmd,
    train_device: str,
    job_id: int,
) -> dict:
    work_root = last_path.parent.parent.parent
    target_epochs = int(cmd.epochs or 0)
    repair_checkpoint_epoch_before_resume_fn(
        last_path,
        work_root,
        target_epochs=target_epochs,
        job_id=job_id,
    )
    resume_updates = resume_train_hyperparams_fn(
        cmd,
        train_device=train_device,
        job_id=job_id,
    )
    ckpt_amp = read_checkpoint_train_field_fn(last_path, "amp")
    if ckpt_amp is False or not truthy_train_flag_fn(resume_updates.get("amp")):
        resume_updates["amp"] = False
    patched_keys = patch_checkpoint_train_args_fn(last_path, resume_updates)
    if not truthy_train_flag_fn(resume_updates.get("amp")):
        if strip_checkpoint_scaler_fn(last_path):
            append_train_process_log_fn(
                job_id, "resume_checkpoint_strip_scaler amp=false"
            )
    if patched_keys:
        append_train_process_log_fn(
            job_id,
            "runtime_hyperparams_checkpoint_patch keys="
            + ",".join(sorted(patched_keys)),
        )
    train_kw = {"resume": str(last_path)}
    for key, value in resume_updates.items():
        if key in resume_override_keys:
            train_kw[key] = value
    return train_kw


def resume_train_job_from_checkpoint(
    ensure_ultralytics_installed_fn: Callable[..., bool],
    crud_yolo_train_job,
    job_can_auto_resume_fn: Callable[[Any], bool],
    is_user_pause_requested_fn: Callable[[int], bool],
    active_train_jobs: set[int],
    job_work_root_fn: Callable[[int], Path],
    command_from_job_fn: Callable[[Any], Any],
    clear_stop_train_job_fn: Callable[[int], None],
    clear_auto_resume_backoff_fn: Callable[[int], None],
    begin_transaction_fn: Callable[[Any], Any],
    update_status_fn: Callable[..., Any],
    submit_yolo_train_job_fn: Callable[..., None],
    processing_status: str,
    execution_lease_active_fn: Callable[[Any], bool] | None = None,
    *,
    db,
    job_id: int,
    ensure_reason: str,
) -> bool:
    if not ensure_ultralytics_installed_fn(reason=ensure_reason):
        return False
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or not job_can_auto_resume_fn(job):
        return False
    if execution_lease_active_fn is not None and execution_lease_active_fn(job):
        return False
    if is_user_pause_requested_fn(job_id):
        return False
    if int(job_id) in active_train_jobs:
        return False

    task_id = int(job.task_id)
    last_path = job_work_root_fn(job_id) / "train" / "weights" / "last.pt"
    if not last_path.is_file() and not _is_plate_recognition_job(job):
        return False

    cmd = command_from_job_fn(job)
    clear_stop_train_job_fn(job_id)
    clear_auto_resume_backoff_fn(job_id)
    with begin_transaction_fn(db):
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job:
            return False
        if execution_lease_active_fn is not None and execution_lease_active_fn(job):
            return False
        update_status_fn(
            db,
            job,
            processing_status,
            error_message=None,
        )
    # Queue dispatch may update the same row through a different session.
    # Expire local state first to avoid stale-row updates on later flushes.
    db.expire_all()
    submit_yolo_train_job_fn(job_id, task_id, cmd, resume=True)
    return True


def restart_train_job_from_existing_job(
    ensure_ultralytics_installed_fn: Callable[..., bool],
    crud_yolo_train_job,
    job_can_manual_restart_fn: Callable[[Any], bool],
    launch_train_job_from_existing_config_fn: Callable[[Any, int], bool],
    *,
    db,
    job_id: int,
    ensure_reason: str,
) -> bool:
    if not ensure_ultralytics_installed_fn(reason=ensure_reason):
        return False
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or not job_can_manual_restart_fn(job):
        return False
    return launch_train_job_from_existing_config_fn(db, job_id)


def launch_train_job_from_existing_config(
    crud_yolo_train_job,
    active_train_jobs: set[int],
    command_from_job_fn: Callable[[Any], Any],
    clear_stop_train_job_fn: Callable[[int], None],
    clear_user_pause_requested_fn: Callable[[int], None],
    clear_auto_resume_backoff_fn: Callable[[int], None],
    begin_transaction_fn: Callable[[Any], Any],
    update_status_fn: Callable[..., Any],
    job_work_root_fn: Callable[[int], Path],
    append_train_process_log_fn: Callable[[int, str], None],
    submit_yolo_train_job_fn: Callable[..., None],
    processing_status: str,
    execution_lease_active_fn: Callable[[Any], bool] | None = None,
    *,
    db,
    job_id: int,
) -> bool:
    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job:
        return False
    if execution_lease_active_fn is not None and execution_lease_active_fn(job):
        return False

    job_id_int = int(job_id)
    active_train_jobs.discard(job_id_int)

    task_id = int(job.task_id)
    cmd = command_from_job_fn(job)
    clear_stop_train_job_fn(job_id_int)
    clear_user_pause_requested_fn(job_id_int)
    clear_auto_resume_backoff_fn(job_id_int)
    with begin_transaction_fn(db):
        job = crud_yolo_train_job.get(db=db, job_id=job_id_int)
        if not job:
            return False
        if execution_lease_active_fn is not None and execution_lease_active_fn(job):
            return False
        update_status_fn(
            db,
            job,
            processing_status,
            error_message=None,
        )
    # Queue dispatch may update this row from another session when re-queuing.
    db.expire_all()

    last_path = job_work_root_fn(job_id_int) / "train" / "weights" / "last.pt"
    resume = last_path.is_file() or _is_plate_recognition_job(job)
    if not resume:
        append_train_process_log_fn(
            job_id_int,
            "restart=fresh detail=last.pt missing; rerun existing job with original config",
        )
    elif _is_plate_recognition_job(job) and not last_path.is_file():
        append_train_process_log_fn(
            job_id_int,
            "restart=plate_component_resume detail=resume from component checkpoints and status",
        )
    submit_yolo_train_job_fn(job_id_int, task_id, cmd, resume=resume)
    return True
