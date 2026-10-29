"""YOLO runtime state and process-control helpers.

These helpers are intentionally side-effect oriented but dependency-light so
``labelu.internal.application.service.yolo_train`` can delegate to them while
preserving its existing public surface and monkeypatch points.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from labelu.internal.common.pipeline_process_log import (
    append_log_line,
    yolo_train_log_path,
)


def job_work_root(media_root: str | Path, work_subdir: str, job_id: int) -> Path:
    return Path(media_root) / work_subdir / f"job-{job_id}"


def stop_flag_path(job_work_root_fn: Callable[[int], Path], job_id: int) -> Path:
    return job_work_root_fn(job_id) / "stop_requested"


def user_pause_flag_path(job_work_root_fn: Callable[[int], Path], job_id: int) -> Path:
    return job_work_root_fn(job_id) / "user_pause_requested"


def pending_runtime_hyperparams_path(
    job_work_root_fn: Callable[[int], Path], job_id: int
) -> Path:
    return job_work_root_fn(job_id) / "pending_runtime_hyperparams.json"


def mark_user_pause_requested(
    job_work_root_fn: Callable[[int], Path],
    user_pause_flag_path_fn: Callable[[int], Path],
    logger,
    job_id: int,
) -> None:
    root = job_work_root_fn(job_id)
    root.mkdir(parents=True, exist_ok=True)
    try:
        user_pause_flag_path_fn(job_id).write_text("1", encoding="utf-8")
    except OSError as exc:
        logger.warning("write user_pause_requested failed job_id={}: {}", job_id, exc)


def is_user_pause_requested(
    user_pause_flag_path_fn: Callable[[int], Path], job_id: int
) -> bool:
    return user_pause_flag_path_fn(job_id).is_file()


def clear_user_pause_requested(
    user_pause_flag_path_fn: Callable[[int], Path], logger, job_id: int
) -> None:
    try:
        user_pause_flag_path_fn(job_id).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("clear user_pause_requested failed job_id={}: {}", job_id, exc)


def is_stop_requested(stop_flag_path_fn: Callable[[int], Path], job_id: int) -> bool:
    return stop_flag_path_fn(job_id).is_file()


def request_stop_train_job(
    job_work_root_fn: Callable[[int], Path],
    stop_flag_path_fn: Callable[[int], Path],
    logger,
    job_id: int,
) -> None:
    root = job_work_root_fn(job_id)
    root.mkdir(parents=True, exist_ok=True)
    try:
        stop_flag_path_fn(job_id).write_text("1", encoding="utf-8")
    except OSError as exc:
        logger.warning("write stop_requested failed job_id={}: {}", job_id, exc)


def clear_stop_train_job(
    stop_flag_path_fn: Callable[[int], Path], logger, job_id: int
) -> None:
    try:
        stop_flag_path_fn(job_id).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("clear stop_requested failed job_id={}: {}", job_id, exc)


def read_pending_runtime_hyperparams(
    pending_runtime_hyperparams_path_fn: Callable[[int], Path], job_id: int
) -> Optional[dict[str, Any]]:
    path = pending_runtime_hyperparams_path_fn(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_pending_runtime_hyperparams(
    job_work_root_fn: Callable[[int], Path],
    pending_runtime_hyperparams_path_fn: Callable[[int], Path],
    logger,
    job_id: int,
    hyperparams: dict[str, Any],
) -> None:
    root = job_work_root_fn(job_id)
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "hyperparams": hyperparams,
        "requested_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "restart_after_epoch": True,
    }
    try:
        pending_runtime_hyperparams_path_fn(job_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("write pending runtime hyperparams failed job_id={}: {}", job_id, exc)


def clear_pending_runtime_hyperparams(
    pending_runtime_hyperparams_path_fn: Callable[[int], Path], logger, job_id: int
) -> None:
    try:
        pending_runtime_hyperparams_path_fn(job_id).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("clear pending runtime hyperparams failed job_id={}: {}", job_id, exc)


def has_pending_runtime_hyperparams(
    pending_runtime_hyperparams_path_fn: Callable[[int], Path], job_id: int
) -> bool:
    return pending_runtime_hyperparams_path_fn(job_id).is_file()


def append_train_process_log(
    job_work_root_fn: Callable[[int], Path],
    job_id: int,
    line: str,
) -> None:
    root = job_work_root_fn(job_id)
    root.mkdir(parents=True, exist_ok=True)
    append_log_line(
        yolo_train_log_path(job_id),
        line,
        logger_prefix=f"YOLO train job_id={job_id}",
    )


def log_weight_files_to_process_log(
    writer: Any, weights_dir: Path, *, prefix: str = "weights"
) -> None:
    for name in ("last.pt", "best.pt"):
        p = weights_dir / name
        if not p.is_file():
            continue
        try:
            nbytes = p.stat().st_size
        except OSError:
            nbytes = -1
        writer.write(f"{prefix} {name} path={p} bytes={nbytes}")


def make_train_process_callbacks(
    job_id: int,
    train_log: Any,
    *,
    work_root: Path,
    target_epochs: int,
    format_results_csv_epoch_log_line: Callable[..., str | None],
    has_pending_runtime_hyperparams_fn: Callable[[int], bool],
    request_stop_train_job_fn: Callable[[int], None],
    is_stop_requested_fn: Callable[[int], bool],
    log_weight_files_fn: Callable[..., None],
    execution_guard: Callable[[], None] | None = None,
):
    """Ultralytics callbacks: stop checks and process-log updates."""
    train_dir = work_root / "train"
    weights_dir = train_dir / "weights"

    def _on_train_start(trainer) -> None:
        if execution_guard is not None:
            execution_guard()
        save_dir = getattr(trainer, "save_dir", None) or train_dir
        epochs = getattr(trainer, "epochs", None) or target_epochs
        train_log.write(
            f"ultralytics_train_start save_dir={save_dir} epochs={epochs} "
            f"device={getattr(trainer, 'device', '')}"
        )

    def _on_train_epoch_start(trainer) -> None:
        if execution_guard is not None:
            execution_guard()
        ep = getattr(trainer, "epoch", None)
        if ep is not None:
            train_log.write(f"epoch={int(ep) + 1}/{target_epochs} train_started")

    def _on_fit_epoch_end(trainer) -> None:
        if execution_guard is not None:
            execution_guard()
        line = format_results_csv_epoch_log_line(
            train_dir, target_epochs=target_epochs or None
        )
        if line:
            train_log.write(line)
        log_weight_files_fn(train_log, weights_dir)
        if has_pending_runtime_hyperparams_fn(job_id):
            request_stop_train_job_fn(job_id)
            trainer.stop = True
            train_log.write(
                f"runtime_hyperparams_restart_requested at_epoch={getattr(trainer, 'epoch', '?')}"
            )
        if is_stop_requested_fn(job_id):
            trainer.stop = True
            train_log.write(
                f"epoch_stop_requested at={getattr(trainer, 'epoch', '?')}"
            )
            return

    def _on_train_batch_end(trainer) -> None:
        if execution_guard is not None:
            execution_guard()
        if is_stop_requested_fn(job_id):
            trainer.stop = True

    def _on_fit_end(trainer) -> None:
        if execution_guard is not None:
            execution_guard()
        ep = getattr(trainer, "epoch", None)
        train_log.write(
            f"ultralytics_fit_end last_epoch={ep} target_epochs={target_epochs}"
        )
        log_weight_files_fn(train_log, weights_dir)

    return (
        _on_train_start,
        _on_train_epoch_start,
        _on_fit_epoch_end,
        _on_train_batch_end,
        _on_fit_end,
    )
