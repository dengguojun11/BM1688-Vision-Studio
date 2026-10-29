from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.label_contract import annotation_sha256
from labelu.internal.domain.models.auto_training import AutoTrainingRoundSample
from labelu.internal.domain.models.sample import SampleState, TaskSample


@dataclass(frozen=True)
class FrozenSampleLoadResult:
    samples: list[Any]
    forced_splits: dict[int, str]


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_sample_proxies(
    db: Session, *, round_id: int
) -> tuple[list[Any], dict[int, str]]:
    rows = (
        db.query(AutoTrainingRoundSample)
        .filter_by(round_id=round_id)
        .order_by(AutoTrainingRoundSample.id)
        .all()
    )
    if not rows:
        raise RuntimeError(f"auto training round #{round_id} has no frozen samples")
    sample_ids = [int(row.sample_id) for row in rows]
    current = {
        int(sample.id): sample
        for sample in db.query(TaskSample).filter(TaskSample.id.in_(sample_ids)).all()
    }
    samples: list[Any] = []
    forced_splits: dict[int, str] = {}
    for row in rows:
        manifest = _loads(row.source_sample_manifest_json)
        sample = current.get(int(row.sample_id))
        if sample is None:
            raise RuntimeError(f"frozen sample #{row.sample_id} no longer exists")
        annotation = manifest.get("annotation_snapshot")
        if not isinstance(annotation, dict):
            raise RuntimeError(
                f"frozen sample #{row.sample_id} has no annotation snapshot"
            )
        if annotation_sha256(annotation) != row.annotation_sha256:
            raise RuntimeError(
                f"frozen sample #{row.sample_id} annotation hash mismatch"
            )
        source_path = str(manifest.get("source_path") or "").strip()
        if not source_path:
            raise RuntimeError(f"frozen sample #{row.sample_id} has no source path")
        absolute_path = Path(settings.MEDIA_ROOT) / source_path.lstrip("/\\")
        if not absolute_path.is_file():
            raise RuntimeError(f"frozen sample image does not exist: {absolute_path}")
        if _file_sha256(absolute_path) != row.image_sha256:
            raise RuntimeError(f"frozen sample #{row.sample_id} image hash mismatch")
        filename = str(
            manifest.get("source_filename")
            or getattr(getattr(sample, "file", None), "filename", None)
            or absolute_path.name
        )
        proxy = SimpleNamespace(
            id=int(row.sample_id),
            inner_id=sample.inner_id,
            task_id=int(row.task_id),
            state=SampleState.DONE.value,
            deleted_at=None,
            data=json.dumps(annotation, ensure_ascii=False),
            file=SimpleNamespace(path=source_path, filename=filename),
            frozen_split=str(row.split),
        )
        samples.append(proxy)
        forced_splits[int(row.sample_id)] = str(row.split)
    return samples, forced_splits


def group_frozen_samples_by_task(samples: list[Any]) -> dict[int, list[Any]]:
    grouped: dict[int, list[Any]] = {}
    for sample in samples:
        grouped.setdefault(int(sample.task_id), []).append(sample)
    return grouped
