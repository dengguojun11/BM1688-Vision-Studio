"""YOLO train job lifecycle helpers.

These helpers move file-backed runtime/lifecycle state handling out of the
monolithic ``yolo_train`` service while preserving wrapper points there.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


def handle_spawn_child_exit(
    job_train_completed_successfully_fn: Callable[[int], bool],
    run_post_train_side_effects_fn: Callable[[int], None],
    mark_spawn_child_failed_fn: Callable[[int, int], None],
    job_id: int,
    exit_code: Optional[int],
) -> None:
    if exit_code in (0, None):
        if job_train_completed_successfully_fn(job_id):
            run_post_train_side_effects_fn(job_id)
        return
    mark_spawn_child_failed_fn(job_id, int(exit_code))


def auto_resume_backoff_path(job_work_root_fn: Callable[[int], Path], job_id: int) -> Path:
    return job_work_root_fn(job_id) / "auto_resume_backoff.json"


def record_auto_resume_failure(
    auto_resume_backoff_path_fn: Callable[[int], Path],
    logger,
    job_id: int,
    reason: str,
) -> None:
    path = auto_resume_backoff_path_fn(job_id)
    data: dict[str, Any] = {"failures": [], "count": 0}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    failures = list(data.get("failures") or [])
    failures.append(
        {
            "at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "reason": reason[:200],
        }
    )
    data["failures"] = failures[-20:]
    data["count"] = int(data.get("count") or 0) + 1
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("auto_resume_backoff write failed job_id={}: {}", job_id, exc)


def clear_auto_resume_backoff(
    auto_resume_backoff_path_fn: Callable[[int], Path], job_id: int
) -> None:
    try:
        auto_resume_backoff_path_fn(job_id).unlink(missing_ok=True)
    except OSError:
        pass


def auto_resume_backoff_active(
    auto_resume_backoff_path_fn: Callable[[int], Path],
    job_id: int,
    *,
    max_failures: int = 5,
) -> bool:
    path = auto_resume_backoff_path_fn(job_id)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    count = int((data or {}).get("count") or 0)
    return count >= max_failures


def cleanup_train_runtime_after_terminal_failure(
    read_train_phase_fn: Callable[[int], tuple[str, str, int, Optional[str]]],
    terminate_train_process_tree_fn: Callable[..., None],
    clear_pending_runtime_hyperparams_fn: Callable[[int], None],
    clear_stop_train_job_fn: Callable[[int], None],
    clear_user_pause_requested_fn: Callable[[int], None],
    job_id: int,
) -> None:
    _phase, _label, runtime_pid, _detail = read_train_phase_fn(job_id)
    terminate_train_process_tree_fn(job_id, runtime_pid, include_root=False)
    clear_pending_runtime_hyperparams_fn(job_id)
    clear_stop_train_job_fn(job_id)
    clear_user_pause_requested_fn(job_id)
