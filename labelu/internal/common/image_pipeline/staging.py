from __future__ import annotations

import gc
import shutil
from pathlib import Path

import numpy as np

from labelu.internal.common.config import settings


def _task_staging_dir(task_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / ".image_pipeline_staging" / str(task_id)


def staging_path(task_id: int, attachment_id: int) -> Path:
    return _task_staging_dir(task_id) / f"{attachment_id}.npy"


def save_feature(task_id: int, attachment_id: int, feature: np.ndarray) -> None:
    path = staging_path(task_id, attachment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(path), feature, allow_pickle=False)


def load_feature(task_id: int, attachment_id: int) -> np.ndarray | None:
    path = staging_path(task_id, attachment_id)
    if not path.is_file():
        return None
    return np.load(str(path), allow_pickle=False)


def clear_staging_for_attachments(task_id: int, attachment_ids: list[int]) -> None:
    """删除本批附件的特征暂存文件；目录空则移除，并触发 gc。"""
    root = _task_staging_dir(task_id)
    for att_id in attachment_ids:
        path = root / f"{att_id}.npy"
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
            parent = root.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass
    gc.collect()


def clear_stale_task_staging(task_id: int, active_attachment_ids: set[int]) -> None:
    """Remove feature files whose attachment no longer belongs to the task.

    Task IDs can be reused by isolated test databases, and production tasks can
    accumulate soft-deleted attachments. Keeping only active attachment IDs
    prevents those records from leaving orphaned cache files behind.
    """
    root = _task_staging_dir(task_id)
    if not root.is_dir():
        return
    for path in root.glob("*.npy"):
        try:
            attachment_id = int(path.stem)
        except ValueError:
            continue
        if attachment_id not in active_attachment_ids:
            try:
                path.unlink()
            except OSError:
                pass
    try:
        if not any(root.iterdir()):
            root.rmdir()
            parent = root.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass
    gc.collect()


def clear_all_task_staging(task_id: int) -> None:
    root = _task_staging_dir(task_id)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    gc.collect()
