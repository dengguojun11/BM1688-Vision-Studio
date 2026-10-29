from __future__ import annotations

import csv
import json
import hashlib
import os
import random
import re
import shutil
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.application.service.yolo_dataset import _parse_sample_annotation
from labelu.internal.adapter.persistence import (
    crud_annotation_taxonomy,
    crud_task,
    crud_training_task,
    crud_yolo_train_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.plate_recognition_profile import (
    PLATE_RECOGNITION_PROFILE,
    PlateSampleAudit,
    audit_plate_sample,
    parse_profile_config,
    task_config_matches_profile,
)
from labelu.internal.common.training_sample import is_explicit_negative_sample
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

PLATE_RECOGNITION_JOB_TYPE = "plate_recognition"
PLATE_RECOGNITION_TRAINING_MODE = "plate_recognition"
PLATE_DETECTION_JOB_TYPE = "plate_detection"
PLATE_DETECTION_TRAINING_MODE = "plate_detection"
PLATE_JOB_TYPES = (PLATE_DETECTION_JOB_TYPE, PLATE_RECOGNITION_JOB_TYPE)
PLATE_COMPONENTS = ("pose", "ocr", "color", "plate_type")
PLATE_DETECTION_COMPONENTS = ("pose",)
PLATE_POSE_PRETRAINED_MODEL = "yolov8n-pose.pt"
PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM = "_plate_iteration_source_job_id"


@dataclass(frozen=True)
class PlateTaskAudit:
    task_id: int
    positive_image_count: int = 0
    negative_image_count: int = 0
    instance_count: int = 0
    invalid_image_count: int = 0
    done_sample_count: int = 0
    invalid_samples: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def included_image_count(self) -> int:
        return self.positive_image_count + self.negative_image_count


@dataclass(frozen=True)
class PlateDatasetSample:
    sample: TaskSample
    audit: PlateSampleAudit


def parse_task_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_plate_pose_pretrained_model(
    model_weights: str = PLATE_POSE_PRETRAINED_MODEL,
) -> str:
    selected = str(model_weights or "").strip()
    if selected != PLATE_POSE_PRETRAINED_MODEL:
        raise RuntimeError(
            "车牌 Pose 训练权重不兼容："
            f"期望 {PLATE_POSE_PRETRAINED_MODEL}，实际 {selected or '未传入'}"
        )
    explicit = str(os.environ.get("LABELU_PLATE_POSE_PRETRAINED_MODEL") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError(f"车牌 Pose 预训练权重不存在：{path}")

    candidates: list[Path] = []
    configured_dir = str(settings.YOLO_PRETRAINED_WEIGHTS_DIR or "").strip()
    if configured_dir:
        candidates.append(Path(configured_dir) / PLATE_POSE_PRETRAINED_MODEL)
    candidates.extend(
        [
            Path(settings.MEDIA_ROOT) / "pretrained" / PLATE_POSE_PRETRAINED_MODEL,
            Path.home()
            / ".config"
            / "Ultralytics"
            / "weights"
            / PLATE_POSE_PRETRAINED_MODEL,
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    searched = "、".join(str(path) for path in candidates)
    raise RuntimeError(
        f"缺少车牌 Pose 预训练权重 {PLATE_POSE_PRETRAINED_MODEL}；已检查：{searched}"
    )


def task_matches_plate_profile(task: Any, profile_config: Any) -> bool:
    return task_config_matches_profile(
        parse_task_config(getattr(task, "config", task)),
        profile_config,
    )


def audit_task_done_samples(
    db: Session,
    *,
    task_id: int,
    profile_config: Any,
    include_samples: bool = False,
) -> tuple[PlateTaskAudit, list[PlateDatasetSample]]:
    samples = (
        db.query(TaskSample)
        .filter(
            TaskSample.task_id == task_id,
            TaskSample.state == SampleState.DONE.value,
            TaskSample.deleted_at.is_(None),
        )
        .order_by(TaskSample.id.asc())
        .all()
    )
    return audit_plate_samples(
        samples,
        task_id=task_id,
        profile_config=profile_config,
        include_samples=include_samples,
    )


def audit_plate_samples(
    samples: list[Any],
    *,
    task_id: int,
    profile_config: Any,
    include_samples: bool = False,
) -> tuple[PlateTaskAudit, list[PlateDatasetSample]]:
    profile = parse_profile_config(profile_config)
    positive = negative = instances = invalid = 0
    invalid_rows: list[dict[str, Any]] = []
    dataset_samples: list[PlateDatasetSample] = []
    for sample in samples:
        parsed = _parse_sample_annotation(sample)
        if parsed is None:
            audit = PlateSampleAudit(
                kind="invalid",
                errors=("sample annotation cannot be parsed",),
            )
        else:
            outer, result = parsed
            audit = audit_plate_sample(result, profile)
            if audit.kind == "negative" and not is_explicit_negative_sample(outer):
                audit = PlateSampleAudit(
                    kind="invalid",
                    errors=(
                        "empty plate annotation is not an explicit negative sample",
                    ),
                )
        if audit.kind == "positive":
            positive += 1
            instances += len(audit.instances)
            if audit.errors:
                invalid += 1
                invalid_rows.append(
                    {
                        "sample_id": sample.id,
                        "inner_id": sample.inner_id,
                        "filename": sample.file.filename if sample.file else None,
                        "errors": list(audit.errors),
                    }
                )
        elif audit.kind == "negative":
            negative += 1
        else:
            invalid += 1
            invalid_rows.append(
                {
                    "sample_id": sample.id,
                    "inner_id": sample.inner_id,
                    "filename": sample.file.filename if sample.file else None,
                    "errors": list(audit.errors),
                }
            )
        if include_samples and audit.usable:
            dataset_samples.append(PlateDatasetSample(sample=sample, audit=audit))
    return (
        PlateTaskAudit(
            task_id=task_id,
            positive_image_count=positive,
            negative_image_count=negative,
            instance_count=instances,
            invalid_image_count=invalid,
            done_sample_count=len(samples),
            invalid_samples=tuple(invalid_rows),
        ),
        dataset_samples,
    )


def initial_component_status(
    components: tuple[str, ...] = PLATE_COMPONENTS,
) -> dict[str, dict[str, Any]]:
    return {
        component: {
            "status": "PENDING",
            "artifact": None,
            "error": None,
        }
        for component in components
    }


def is_plate_job_type(value: Any) -> bool:
    return str(value or "").strip() in PLATE_JOB_TYPES


def component_artifact_contract() -> dict[str, dict[str, str]]:
    return {
        "pose": {
            "pt": "plate_pose_best.pt",
            "onnx": "plate_pose.onnx",
            "network": "plate_pose",
        },
        "ocr": {
            "pt": "plate_ocr_best.pt",
            "onnx": "plate_ocr.onnx",
            "network": "plate_ocr",
        },
        "color": {
            "pt": "plate_color_best.pt",
            "onnx": "plate_color.onnx",
            "network": "plate_color",
        },
        "plate_type": {
            "pt": "plate_type_best.pt",
            "onnx": "plate_type.onnx",
            "network": "plate_type",
        },
    }


def plate_detection_artifact_contract() -> dict[str, str]:
    return {
        "pt": "plate_detection_best.pt",
        "onnx": "plate_detection.onnx",
        "network": "plate_detection",
    }


def assert_supported_profile(profile: str | None) -> None:
    if profile != PLATE_RECOGNITION_PROFILE:
        raise ValueError(f"unsupported training profile: {profile or '<empty>'}")


def _job_work_root(job_id: int) -> Path:
    return (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{int(job_id)}"
    )


def _load_training_context(
    db: Session,
    *,
    job_id: int,
    expected_job_type: str = PLATE_RECOGNITION_JOB_TYPE,
) -> tuple[Any, Any, dict[str, Any], list[int], list[PlateDatasetSample]]:
    job = crud_yolo_train_job.get(db, job_id)
    if not job or job.job_type != expected_job_type:
        raise RuntimeError(f"{expected_job_type} job not found")
    training_task = crud_training_task.get(db, int(job.training_task_id or 0))
    if not training_task:
        raise RuntimeError(f"{expected_job_type} training task not found")
    preset_snapshots = crud_training_task.parse_snapshot(
        training_task.selected_presets_snapshot_json
    )
    profile_snapshot = next(
        (
            row
            for row in preset_snapshots
            if row.get("training_profile") == PLATE_RECOGNITION_PROFILE
        ),
        None,
    )
    if not profile_snapshot:
        raise RuntimeError("training task has no plate recognition profile snapshot")
    profile_config = parse_profile_config(
        profile_snapshot.get("training_profile_config")
    )
    source_snapshots = crud_training_task.parse_snapshot(
        training_task.source_tasks_snapshot_json
    )
    source_task_ids = sorted(
        {
            int(row["task_id"])
            for row in source_snapshots
            if row.get("task_id") is not None
        }
    )
    samples: list[PlateDatasetSample] = []
    if getattr(job, "auto_training_round_id", None):
        from labelu.internal.application.service.auto_training_dataset import (
            group_frozen_samples_by_task,
            load_frozen_sample_proxies,
        )

        frozen, _forced_splits = load_frozen_sample_proxies(
            db, round_id=int(job.auto_training_round_id)
        )
        grouped_frozen = group_frozen_samples_by_task(frozen)
        frozen_task_ids = sorted(grouped_frozen)
        if frozen_task_ids != source_task_ids:
            raise RuntimeError(
                "frozen plate sample tasks do not match training task snapshot"
            )
        for task_id in source_task_ids:
            task = crud_task.get(db, task_id=task_id)
            if not task or not task_matches_plate_profile(task, profile_config):
                raise RuntimeError(
                    f"source task #{task_id} no longer matches plate profile"
                )
            _audit, task_samples = audit_plate_samples(
                grouped_frozen[task_id],
                task_id=task_id,
                profile_config=profile_config,
                include_samples=True,
            )
            samples.extend(task_samples)
        if not any(item.audit.kind == "positive" for item in samples):
            raise RuntimeError("plate recognition training requires positive samples")
        return job, training_task, profile_config, source_task_ids, samples
    for task_id in source_task_ids:
        task = crud_task.get(db, task_id=task_id)
        if not task or not task_matches_plate_profile(task, profile_config):
            raise RuntimeError(
                f"source task #{task_id} no longer matches plate profile"
            )
        _audit, task_samples = audit_task_done_samples(
            db,
            task_id=task_id,
            profile_config=profile_config,
            include_samples=True,
        )
        samples.extend(task_samples)
    if not any(item.audit.kind == "positive" for item in samples):
        raise RuntimeError("plate recognition training requires positive samples")
    return job, training_task, profile_config, source_task_ids, samples


def _sample_group_key(item: PlateDatasetSample) -> str:
    if item.audit.kind == "positive":
        identities = sorted(
            {
                _normalize_plate_number(instance.plate_number)
                for instance in item.audit.instances
            }
        )
        return "plate:" + "|".join(identities)
    file_path = normalize_source_path(
        getattr(getattr(item.sample, "file", None), "path", None)
        or getattr(getattr(item.sample, "file", None), "filename", None)
        or f"sample-{item.sample.id}"
    )
    return "source:" + file_path


def normalize_source_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").lower()


def _normalize_plate_number(value: Any) -> str:
    return "".join(str(value or "").upper().split())


def _grouped_splits(
    samples: list[PlateDatasetSample],
    *,
    seed: int,
) -> dict[str, list[PlateDatasetSample]]:
    frozen = [str(getattr(item.sample, "frozen_split", "")) for item in samples]
    if frozen and all(split in {"train", "val", "test"} for split in frozen):
        identity_splits: dict[str, str] = {}
        for item, split in zip(samples, frozen):
            if item.audit.kind != "positive":
                continue
            for instance in item.audit.instances:
                identity = _normalize_plate_number(instance.plate_number)
                if not identity:
                    continue
                previous = identity_splits.setdefault(identity, split)
                if previous != split:
                    raise RuntimeError(
                        "plate identity cannot cross frozen splits: "
                        f"{identity} appears in {previous} and {split}"
                    )
        assigned = {split: [] for split in ("train", "val", "test")}
        for item, split in zip(samples, frozen):
            assigned[split].append(item)
        if not assigned["train"] or not assigned["val"]:
            raise RuntimeError(
                "frozen plate dataset must contain train and val samples"
            )
        return assigned
    parents = list(range(len(samples)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    first_by_identity: dict[str, int] = {}
    identities_by_sample: list[list[str]] = []
    for index, item in enumerate(samples):
        if item.audit.kind == "positive":
            identities = sorted(
                {
                    "plate:" + _normalize_plate_number(instance.plate_number)
                    for instance in item.audit.instances
                }
            )
        else:
            identities = [_sample_group_key(item)]
        identities_by_sample.append(identities)
        for identity in identities:
            previous = first_by_identity.setdefault(identity, index)
            union(index, previous)

    grouped_indexes: dict[int, list[int]] = {}
    for index in range(len(samples)):
        grouped_indexes.setdefault(find(index), []).append(index)
    groups: dict[str, list[PlateDatasetSample]] = {}
    for indexes in grouped_indexes.values():
        identities = sorted(
            {identity for index in indexes for identity in identities_by_sample[index]}
        )
        group_key = "|".join(identities)
        groups[group_key] = [samples[index] for index in indexes]

    def ordered(keys: list[str], kind: str) -> list[str]:
        return sorted(
            keys,
            key=lambda key: hashlib.sha256(
                f"{seed}:{kind}:{key}".encode("utf-8")
            ).hexdigest(),
        )

    def allocate(keys: list[str], kind: str) -> dict[str, set[str]]:
        ordered_keys = ordered(keys, kind)
        group_count = len(ordered_keys)
        val_count = max(1, int(round(group_count * 0.15))) if group_count >= 3 else 0
        test_count = max(1, int(round(group_count * 0.10))) if group_count >= 6 else 0
        if val_count + test_count >= group_count:
            test_count = 0
            val_count = 1 if group_count >= 2 else 0
        return {
            "val": set(ordered_keys[:val_count]),
            "test": set(ordered_keys[val_count : val_count + test_count]),
            "train": set(ordered_keys[val_count + test_count :]),
        }

    positive_keys = [
        key
        for key, items in groups.items()
        if any(item.audit.kind == "positive" for item in items)
    ]
    positive_key_set = set(positive_keys)
    negative_keys = [key for key in groups if key not in positive_key_set]
    positive_allocations = allocate(positive_keys, "positive")
    negative_allocations = allocate(negative_keys, "negative")

    def coverage_features(key: str) -> set[str]:
        features: set[str] = set()
        for item in groups[key]:
            for instance in item.audit.instances:
                plate_number = _normalize_plate_number(instance.plate_number)
                features.update(f"ocr:{character}" for character in plate_number)
                if instance.color:
                    features.add(f"color:{instance.color}")
                if instance.plate_type:
                    features.add(f"plate_type:{instance.plate_type}")
        return features

    feature_by_key = {key: coverage_features(key) for key in positive_keys}
    required_features = (
        set().union(*feature_by_key.values()) if feature_by_key else set()
    )
    train_features = (
        set().union(*(feature_by_key[key] for key in positive_allocations["train"]))
        if positive_allocations["train"]
        else set()
    )
    missing_features = required_features - train_features
    while missing_features:
        candidates = [
            (key, split)
            for split in ("val", "test")
            for key in positive_allocations[split]
            if len(positive_allocations[split]) > 1
            if feature_by_key[key] & missing_features
        ]
        if not candidates:
            break
        key, source_split = min(
            candidates,
            key=lambda item: (
                -len(feature_by_key[item[0]] & missing_features),
                hashlib.sha256(
                    f"{seed}:coverage:{item[0]}".encode("utf-8")
                ).hexdigest(),
            ),
        )
        positive_allocations[source_split].remove(key)
        positive_allocations["train"].add(key)
        train_features.update(feature_by_key[key])
        missing_features = required_features - train_features

    split_keys = {
        split: positive_allocations[split] | negative_allocations[split]
        for split in ("train", "val", "test")
    }
    ordered_keys = ordered(list(groups), "all")
    return {
        split: [item for key in ordered_keys if key in keys for item in groups[key]]
        for split, keys in split_keys.items()
    }


def _read_image(path: Path):
    import cv2
    import numpy as np

    encoded = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"cannot read image: {path}")
    return image


def _prepare_ocr_component_image(dataset_root: Path, row: dict[str, Any]):
    import cv2

    image = _read_image(dataset_root / row["ocr_path"])
    if row.get("ocr_layout") != "lower_row":
        image = image[:, image.shape[1] // 2 :]
    image = cv2.resize(image, (192, 48), interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 4)).apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)


def _write_image(path: Path, image) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".jpg"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise RuntimeError(f"cannot encode image: {path}")
    encoded.tofile(str(path))


def _perspective_plate_crop(image, corners):
    import cv2
    import numpy as np

    points = np.asarray(corners, dtype=np.float32)
    top = np.linalg.norm(points[1] - points[0])
    bottom = np.linalg.norm(points[2] - points[3])
    left = np.linalg.norm(points[3] - points[0])
    right = np.linalg.norm(points[2] - points[1])
    width = max(8, int(round(max(top, bottom))))
    height = max(8, int(round(max(left, right))))
    target = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(points, target)
    return cv2.warpPerspective(image, matrix, (width, height))


def _double_row_ocr_image(plate_crop):
    import cv2

    height = plate_crop.shape[0]
    split = max(1, height // 2)
    lower = plate_crop[split:]
    if lower.size == 0:
        lower = plate_crop[:split]
    return cv2.resize(lower, (192, 48), interpolation=cv2.INTER_LINEAR)


def _rect_crop(image, rect: dict[str, Any]):
    import cv2

    x = max(0, int(round(float(rect.get("x") or 0))))
    y = max(0, int(round(float(rect.get("y") or 0))))
    x2 = min(image.shape[1], int(round(x + float(rect.get("width") or 0))))
    y2 = min(image.shape[0], int(round(y + float(rect.get("height") or 0))))
    if x2 <= x or y2 <= y:
        raise RuntimeError("empty plate rectangle crop")
    return cv2.resize(image[y:y2, x:x2], (128, 64), interpolation=cv2.INTER_LINEAR)


def _pose_label_lines(
    item: PlateDatasetSample, image_width: int, image_height: int
) -> list[str]:
    lines: list[str] = []
    for instance in item.audit.instances:
        rect = instance.rect
        x = float(rect.get("x") or 0)
        y = float(rect.get("y") or 0)
        width = float(rect.get("width") or 0)
        height = float(rect.get("height") or 0)
        values = [
            "0",
            f"{(x + width / 2) / image_width:.6f}",
            f"{(y + height / 2) / image_height:.6f}",
            f"{width / image_width:.6f}",
            f"{height / image_height:.6f}",
        ]
        for point_x, point_y in instance.corners:
            values.extend(
                [
                    f"{point_x / image_width:.6f}",
                    f"{point_y / image_height:.6f}",
                    "2",
                ]
            )
        lines.append(" ".join(values))
    return lines


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_plate_recognition_dataset(
    *,
    job_id: int,
    profile_config: dict[str, Any],
    source_task_ids: list[int],
    samples: list[PlateDatasetSample],
    seed: int,
) -> tuple[Path, dict[str, Any]]:
    dataset_root = _job_work_root(job_id) / "plate_dataset"
    if dataset_root.exists():
        shutil.rmtree(dataset_root)
    dataset_root.mkdir(parents=True, exist_ok=True)
    splits = _grouped_splits(samples, seed=seed)
    crop_rows: list[dict[str, Any]] = []
    manifest_samples: list[dict[str, Any]] = []
    composition = {
        "positive_images": 0,
        "negative_images": 0,
        "instances": 0,
        "splits": {},
    }
    for split in ("train", "val", "test"):
        split_positive = split_negative = split_instances = 0
        for item in splits[split]:
            sample = item.sample
            attachment = sample.file
            if not attachment or not attachment.path:
                raise RuntimeError(f"sample #{sample.id} has no image attachment")
            source_path = Path(settings.MEDIA_ROOT) / str(attachment.path).lstrip("/\\")
            if not source_path.is_file():
                raise RuntimeError(f"sample image does not exist: {source_path}")
            image = _read_image(source_path)
            height, width = image.shape[:2]
            suffix = (
                Path(attachment.filename or source_path.name).suffix.lower() or ".jpg"
            )
            stem = f"t{sample.task_id}_s{sample.id}"
            pose_image = dataset_root / "pose" / "images" / split / f"{stem}{suffix}"
            pose_image.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, pose_image)
            pose_label = dataset_root / "pose" / "labels" / split / f"{stem}.txt"
            pose_label.parent.mkdir(parents=True, exist_ok=True)
            pose_lines = _pose_label_lines(item, width, height)
            pose_label.write_text(
                ("\n".join(pose_lines) + "\n") if pose_lines else "",
                encoding="utf-8",
            )
            if item.audit.kind == "positive":
                split_positive += 1
                for index, instance in enumerate(item.audit.instances):
                    plate = _perspective_plate_crop(image, instance.corners)
                    ocr_image = _double_row_ocr_image(plate)
                    classifier_image = _rect_crop(image, instance.rect)
                    crop_stem = f"{stem}_i{index}"
                    ocr_path = dataset_root / "ocr" / split / f"{crop_stem}.png"
                    classifier_path = (
                        dataset_root / "classification" / split / f"{crop_stem}.png"
                    )
                    _write_image(ocr_path, ocr_image)
                    _write_image(classifier_path, classifier_image)
                    crop_rows.append(
                        {
                            "split": split,
                            "sample_id": sample.id,
                            "task_id": sample.task_id,
                            "ocr_path": str(ocr_path.relative_to(dataset_root)).replace(
                                "\\", "/"
                            ),
                            "ocr_layout": "lower_row",
                            "classification_path": str(
                                classifier_path.relative_to(dataset_root)
                            ).replace("\\", "/"),
                            "plateNumber": _normalize_plate_number(
                                instance.plate_number
                            ),
                            "color": instance.color,
                            "plateType": instance.plate_type,
                        }
                    )
                    split_instances += 1
            else:
                split_negative += 1
            manifest_samples.append(
                {
                    "sample_id": sample.id,
                    "task_id": sample.task_id,
                    "split": split,
                    "kind": item.audit.kind,
                    "instance_count": len(item.audit.instances),
                    "source": normalize_source_path(attachment.path),
                    "source_sha256": _sha256_file(source_path),
                    "pose_image": str(pose_image.relative_to(dataset_root)).replace(
                        "\\", "/"
                    ),
                    "pose_label": str(pose_label.relative_to(dataset_root)).replace(
                        "\\", "/"
                    ),
                    "group_key": _sample_group_key(item),
                }
            )
        composition["positive_images"] += split_positive
        composition["negative_images"] += split_negative
        composition["instances"] += split_instances
        composition["splits"][split] = {
            "positive_images": split_positive,
            "negative_images": split_negative,
            "instances": split_instances,
            "total_images": split_positive + split_negative,
        }

    pose_yaml = dataset_root / "pose" / "data.yaml"
    pose_yaml.write_text(
        "\n".join(
            [
                f"path: {str((dataset_root / 'pose').resolve()).replace(chr(92), '/')}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "kpt_shape: [4, 3]",
                "flip_idx: [1, 0, 3, 2]",
                "names:",
                "  0: doubleEP",
                "",
            ]
        ),
        encoding="utf-8",
    )
    crop_manifest = dataset_root / "component_samples.jsonl"
    crop_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in crop_rows),
        encoding="utf-8",
    )
    manifest = {
        "format": "labelu_plate_recognition_dataset_v1",
        "training_profile": PLATE_RECOGNITION_PROFILE,
        "profile_config": profile_config,
        "source_task_ids": source_task_ids,
        "split_policy": {
            "kind": "global_grouped",
            "positive_group": "normalized_plate_number",
            "negative_group": "normalized_source_path",
            "seed": seed,
        },
        "composition": composition,
        "samples": manifest_samples,
        "component_sample_count": len(crop_rows),
        "component_sample_counts": {
            "pose": composition["instances"],
            "ocr": sum(bool(row.get("plateNumber")) for row in crop_rows),
            "color": sum(bool(row.get("color")) for row in crop_rows),
            "plate_type": sum(bool(row.get("plateType")) for row in crop_rows),
        },
    }
    manifest_path = dataset_root / "dataset_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest["manifest_sha256"] = _sha256_file(manifest_path)
    return dataset_root, manifest


def _read_component_rows(dataset_root: Path) -> list[dict[str, Any]]:
    rows = []
    path = dataset_root / "component_samples.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _final_fit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "original_split": row.get("original_split") or row.get("split"),
            "split": "train",
        }
        for row in rows
    ]


def _write_pose_final_fit_yaml(dataset_root: Path) -> tuple[Path, int]:
    pose_root = dataset_root / "pose"
    image_paths: list[Path] = []
    for split in ("train", "val", "test"):
        image_paths.extend(
            sorted(
                path.resolve()
                for path in (pose_root / "images" / split).glob("*")
                if path.is_file()
            )
        )
    if not image_paths:
        raise RuntimeError("pose final fit has no images")
    all_images_path = pose_root / "all_images.txt"
    all_images_path.write_text(
        "\n".join(str(path).replace("\\", "/") for path in image_paths) + "\n",
        encoding="utf-8",
    )
    yaml_path = pose_root / "data_final_fit.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                f"path: {str(pose_root.resolve()).replace(chr(92), '/')}",
                f"train: {str(all_images_path.resolve()).replace(chr(92), '/')}",
                "val: images/val",
                "test: images/test",
                "kpt_shape: [4, 3]",
                "flip_idx: [1, 0, 3, 2]",
                "names:",
                "  0: doubleEP",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return yaml_path, len(image_paths)


def _box_iou(first, second) -> float:
    left = max(float(first[0]), float(second[0]))
    top = max(float(first[1]), float(second[1]))
    right = min(float(first[2]), float(second[2]))
    bottom = min(float(first[3]), float(second[3]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, float(first[2]) - float(first[0])) * max(
        0.0, float(first[3]) - float(first[1])
    )
    second_area = max(0.0, float(second[2]) - float(second[0])) * max(
        0.0, float(second[3]) - float(second[1])
    )
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _pose_label_box(values: list[str], width: int, height: int):
    import numpy as np

    center_x = float(values[1]) * width
    center_y = float(values[2]) * height
    box_width = float(values[3]) * width
    box_height = float(values[4]) * height
    return np.asarray(
        [
            center_x - box_width / 2,
            center_y - box_height / 2,
            center_x + box_width / 2,
            center_y + box_height / 2,
        ],
        dtype=np.float32,
    )


def _component_instance_index(row: dict[str, Any]) -> int:
    match = re.search(r"_i(\d+)", str(row.get("ocr_path") or ""))
    return int(match.group(1)) if match else 0


def _augment_ocr_rows_from_pose(
    *,
    dataset_root: Path,
    rows: list[dict[str, Any]],
    pose_pt: Path,
    imgsz: int,
    device: str | None,
    confidence: float = 0.05,
    minimum_iou: float = 0.3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    from ultralytics import YOLO

    eligible_rows = [
        row for row in rows if _normalize_plate_number(row.get("plateNumber"))
    ]
    rows_by_sample: dict[int, list[dict[str, Any]]] = {}
    for row in eligible_rows:
        rows_by_sample.setdefault(int(row["sample_id"]), []).append(row)
    for sample_rows in rows_by_sample.values():
        sample_rows.sort(key=_component_instance_index)

    manifest = json.loads(
        (dataset_root / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    samples = {
        int(sample["sample_id"]): sample
        for sample in manifest.get("samples") or []
        if int(sample.get("sample_id") or 0) in rows_by_sample
    }
    model = YOLO(str(pose_pt.resolve()))
    output_root = dataset_root / "ocr_pose_predicted" / "train"
    augmented_rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    matched_ious: list[float] = []

    for sample_id, sample_rows in sorted(rows_by_sample.items()):
        sample = samples.get(sample_id)
        if not sample:
            unmatched.append({"sample_id": sample_id, "reason": "missing manifest"})
            continue
        image = _read_image(dataset_root / sample["pose_image"])
        height, width = image.shape[:2]
        label_lines = [
            line.split()
            for line in (dataset_root / sample["pose_label"])
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        prediction = model.predict(
            source=image,
            imgsz=max(640, int(imgsz)),
            conf=float(confidence),
            iou=0.5,
            device=device or "cpu",
            verbose=False,
        )[0]
        predicted_boxes = prediction.boxes.xyxy.detach().cpu().numpy()
        predicted_corners = prediction.keypoints.xy.detach().cpu().numpy()
        available = set(range(len(predicted_boxes)))
        for instance_index, row in enumerate(sample_rows):
            if instance_index >= len(label_lines):
                unmatched.append(
                    {
                        "sample_id": sample_id,
                        "instance_index": instance_index,
                        "reason": "missing pose label",
                    }
                )
                continue
            ground_truth_box = _pose_label_box(
                label_lines[instance_index], width, height
            )
            candidates = sorted(
                (
                    (_box_iou(ground_truth_box, predicted_boxes[index]), index)
                    for index in available
                ),
                reverse=True,
            )
            if not candidates or candidates[0][0] < float(minimum_iou):
                unmatched.append(
                    {
                        "sample_id": sample_id,
                        "instance_index": instance_index,
                        "reason": "no matched pose prediction",
                        "best_iou": candidates[0][0] if candidates else 0.0,
                    }
                )
                continue
            match_iou, prediction_index = candidates[0]
            available.remove(prediction_index)
            corners = predicted_corners[prediction_index]
            if corners.shape != (4, 2) or not np.isfinite(corners).all():
                unmatched.append(
                    {
                        "sample_id": sample_id,
                        "instance_index": instance_index,
                        "reason": "invalid predicted corners",
                    }
                )
                continue
            plate_image = _perspective_plate_crop(image, corners.tolist())
            ocr_image = _double_row_ocr_image(plate_image)
            crop_name = (
                f"t{row['task_id']}_s{sample_id}_i{_component_instance_index(row)}.png"
            )
            crop_path = output_root / crop_name
            classification_path = (
                dataset_root / "classification_pose_predicted" / "train" / crop_name
            )
            _write_image(crop_path, ocr_image)
            _write_image(classification_path, plate_image)
            augmented_rows.append(
                {
                    **row,
                    "original_split": row.get("split"),
                    "split": "train",
                    "ocr_path": str(crop_path.relative_to(dataset_root)).replace(
                        "\\", "/"
                    ),
                    "classification_path": str(
                        classification_path.relative_to(dataset_root)
                    ).replace("\\", "/"),
                    "ocr_layout": "lower_row",
                    "source_variant": "pose_predicted",
                    "pose_match_iou": float(match_iou),
                }
            )
            matched_ious.append(float(match_iou))

    report = {
        "eligible_row_count": len(eligible_rows),
        "augmented_row_count": len(augmented_rows),
        "unmatched_count": len(unmatched),
        "minimum_match_iou": float(minimum_iou),
        "match_iou": {
            "min": min(matched_ious) if matched_ious else None,
            "mean": float(np.mean(matched_ious)) if matched_ious else None,
            "max": max(matched_ious) if matched_ious else None,
        },
        "unmatched": unmatched[:100],
    }
    report_path = dataset_root / "ocr_pose_predicted" / "augmentation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report["report_path"] = str(report_path.resolve())
    return augmented_rows, report


def _torch_device(train_device: str | None):
    import torch

    raw = str(train_device or "").strip()
    if torch.cuda.is_available() and raw.lower() != "cpu":
        first = raw.split(",", 1)[0].replace("cuda:", "").strip()
        return torch.device(f"cuda:{int(first or 0)}")
    return torch.device("cpu")


def _load_component_iteration_checkpoint(
    model,
    *,
    component: str,
    labels: list[str],
    checkpoint_path: str | Path | None,
) -> dict[str, Any] | None:
    if not checkpoint_path:
        return None
    import torch

    path = Path(checkpoint_path)
    if not path.is_file():
        raise RuntimeError(f"missing {component} iteration checkpoint: {path}")
    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint = torch.load(path, map_location="cpu")
    if checkpoint.get("format") != "labelu_plate_recognition_v1":
        raise RuntimeError(f"invalid {component} iteration checkpoint format: {path}")
    if str(checkpoint.get("component") or "") != component:
        raise RuntimeError(f"iteration checkpoint component mismatch: {path}")
    source_labels = [str(value) for value in checkpoint.get("labels") or []]
    source_state = checkpoint.get("state_dict")
    if not source_labels or not isinstance(source_state, dict):
        raise RuntimeError(f"invalid {component} iteration checkpoint payload: {path}")

    target_state = model.state_dict()
    head_prefix = "head." if component == "ocr" else "classifier."
    for key, value in source_state.items():
        if key.startswith(head_prefix) or key not in target_state:
            continue
        if tuple(value.shape) == tuple(target_state[key].shape):
            target_state[key] = value

    source_label_index = {label: index for index, label in enumerate(source_labels)}
    copied_labels = [label for label in labels if label in source_label_index]
    for suffix in ("weight", "bias"):
        key = head_prefix + suffix
        source_value = source_state.get(key)
        target_value = target_state.get(key)
        if source_value is None or target_value is None:
            continue
        for target_index, label in enumerate(labels):
            source_index = source_label_index.get(label)
            if source_index is None:
                continue
            if (
                source_index >= source_value.shape[0]
                or target_index >= target_value.shape[0]
            ):
                continue
            target_value[target_index] = source_value[source_index]
        target_state[key] = target_value

    model.load_state_dict(target_state)
    return {
        "checkpoint": str(path.resolve()),
        "source_labels": source_labels,
        "copied_labels": copied_labels,
    }


def resolve_plate_train_device(job_id: int, train_device: str | None) -> str:
    from labelu.internal.application.service.yolo_train import (
        _resolve_train_device_for_job,
    )

    return _resolve_train_device_for_job(int(job_id), override=train_device)


def _load_tensor(path: Path, *, width: int, height: int):
    import cv2
    import torch

    image = _read_image(path)
    image = cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(image.transpose(2, 0, 1).copy()).float() / 255.0
    return tensor


def _write_component_analysis_artifacts(
    *,
    component: str,
    analysis_dir: Path,
    loss_history: list[dict[str, Any]],
    evaluation_metrics: dict[str, dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    class_names: list[str],
) -> dict[str, Any]:
    """Persist component evidence independently from its final-fit checkpoint."""
    analysis_dir.mkdir(parents=True, exist_ok=True)
    csv_path = analysis_dir / "results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("epoch", "phase", "train/loss", "lr/pg0"),
        )
        writer.writeheader()
        for row in loss_history:
            writer.writerow(
                {
                    "epoch": row.get("epoch"),
                    "phase": row.get("phase") or "train",
                    "train/loss": row.get("loss"),
                    "lr/pg0": row.get("learning_rate"),
                }
            )

    analysis_json_path = analysis_dir / "analysis.json"
    analysis_json_path.write_text(
        json.dumps(
            {
                "component": component,
                "loss_history": loss_history,
                "evaluation_metrics": evaluation_metrics,
                "class_names": class_names,
                "predictions": [
                    {
                        "split": row.get("split"),
                        "target": str(row.get("target") or ""),
                        "prediction": str(row.get("prediction") or ""),
                        "correct": bool(row.get("correct")),
                    }
                    for row in prediction_rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot as plt
    import numpy as np

    epochs = [
        int(row.get("epoch") or index + 1) for index, row in enumerate(loss_history)
    ]
    losses = [float(row.get("loss") or 0.0) for row in loss_history]
    split_labels: list[str] = []
    accuracies: list[float] = []
    for split in ("train", "val", "test"):
        metric = evaluation_metrics.get(split) or {}
        if metric.get("accuracy") is None:
            continue
        split_labels.append(split)
        accuracies.append(float(metric["accuracy"]))

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].plot(epochs, losses, color="#2563eb", linewidth=2)
    axes[0].set_title("Training loss")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].grid(alpha=0.25)
    if split_labels:
        colors = ["#64748b", "#0f766e", "#d97706"][: len(split_labels)]
        axes[1].bar(split_labels, accuracies, color=colors)
        axes[1].set_ylim(0, 1)
        for index, value in enumerate(accuracies):
            axes[1].text(
                index,
                min(0.98, value + 0.025),
                f"{value:.3f}",
                ha="center",
                fontsize=9,
            )
    axes[1].set_title("Exact accuracy by split")
    axes[1].set_ylabel("Accuracy")
    axes[1].grid(axis="y", alpha=0.25)
    figure.suptitle(component.replace("_", " ").title())
    figure.tight_layout()
    figure.savefig(analysis_dir / "results.png", dpi=150, bbox_inches="tight")
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(7.5, 4.5))
    axis.plot(epochs, losses, color="#2563eb", linewidth=2)
    axis.fill_between(epochs, losses, color="#bfdbfe", alpha=0.45)
    axis.set_title("Training loss")
    axis.set_xlabel("Epoch")
    axis.set_ylabel("Loss")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(analysis_dir / "loss_curve.png", dpi=150, bbox_inches="tight")
    plt.close(figure)

    confusion_pairs: list[tuple[str, str]] = []
    for row in prediction_rows:
        target = str(row.get("target") or "")
        prediction = str(row.get("prediction") or "")
        if component == "ocr":
            confusion_pairs.extend(
                (expected or "<missing>", actual or "<missing>")
                for expected, actual in zip_longest(target, prediction, fillvalue="")
            )
        else:
            confusion_pairs.append((target or "<missing>", prediction or "<missing>"))
    counts: dict[str, int] = {}
    for expected, actual in confusion_pairs:
        counts[expected] = counts.get(expected, 0) + 1
        counts[actual] = counts.get(actual, 0) + 1
    ordered_labels = [label for label in class_names if label in counts]
    ordered_labels.extend(
        label
        for label, _count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
        if label not in ordered_labels
    )
    ordered_labels = ordered_labels[:24]
    if ordered_labels:
        index_by_label = {label: index for index, label in enumerate(ordered_labels)}
        matrix = np.zeros((len(ordered_labels), len(ordered_labels)), dtype=np.int64)
        for expected, actual in confusion_pairs:
            if expected in index_by_label and actual in index_by_label:
                matrix[index_by_label[expected], index_by_label[actual]] += 1
        normalized = matrix.astype(np.float64)
        row_sums = normalized.sum(axis=1, keepdims=True)
        normalized = np.divide(
            normalized,
            row_sums,
            out=np.zeros_like(normalized),
            where=row_sums > 0,
        )
        for filename, values, title in (
            ("confusion_matrix.png", matrix, "Confusion matrix"),
            (
                "confusion_matrix_normalized.png",
                normalized,
                "Normalized confusion matrix",
            ),
        ):
            side = max(6.5, min(14.0, 0.48 * len(ordered_labels) + 4.0))
            figure, axis = plt.subplots(figsize=(side, side))
            image = axis.imshow(values, interpolation="nearest", cmap="Blues", vmin=0)
            figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
            axis.set_title(title)
            axis.set_xlabel("Prediction")
            axis.set_ylabel("Ground truth")
            axis.set_xticks(
                range(len(ordered_labels)),
                ordered_labels,
                rotation=45,
                ha="right",
            )
            axis.set_yticks(range(len(ordered_labels)), ordered_labels)
            figure.tight_layout()
            figure.savefig(analysis_dir / filename, dpi=150, bbox_inches="tight")
            plt.close(figure)

    preview_rows = sorted(
        [row for row in prediction_rows if row.get("image_rgb") is not None],
        key=lambda row: (
            {"val": 0, "test": 1, "train": 2}.get(str(row.get("split") or ""), 3),
            bool(row.get("correct")),
        ),
    )[:12]
    if preview_rows:
        column_count = 3
        row_count = (len(preview_rows) + column_count - 1) // column_count
        figure, axes = plt.subplots(
            row_count,
            column_count,
            figsize=(12, max(3.2, row_count * 3.0)),
        )
        axes_array = np.asarray(axes, dtype=object).reshape(-1)
        for axis, row in zip(axes_array, preview_rows):
            image = row.get("image_rgb")
            if image is not None:
                axis.imshow(image)
            axis.set_title(
                f"GT: {row.get('target') or '-'}\n"
                f"Pred: {row.get('prediction') or '-'}",
                color="#15803d" if row.get("correct") else "#b91c1c",
                fontsize=9,
            )
            axis.axis("off")
        for axis in axes_array[len(preview_rows) :]:
            axis.axis("off")
        figure.suptitle("Validation predictions")
        figure.tight_layout()
        figure.savefig(
            analysis_dir / "validation_predictions.png",
            dpi=150,
            bbox_inches="tight",
        )
        plt.close(figure)

    return {
        "directory": str(analysis_dir.resolve()),
        "results_csv": str(csv_path.resolve()),
        "analysis_json": str(analysis_json_path.resolve()),
    }


def _reset_component_progress(
    *,
    component: str,
    analysis_dir: Path,
    progress_path: Path,
    target_epochs: int,
    execution_guard=None,
) -> None:
    """Create a live progress stream before a component training stage starts."""
    if execution_guard is not None:
        execution_guard()
    analysis_dir.mkdir(parents=True, exist_ok=True)
    csv_path = analysis_dir / "results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(
            handle,
            fieldnames=("epoch", "phase", "train/loss", "lr/pg0"),
        ).writeheader()
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    _write_component_progress_snapshot(
        progress_path,
        {
            "component": component,
            "current_epoch": 0,
            "target_epochs": max(1, int(target_epochs)),
            "phase": "starting",
            "loss": None,
            "learning_rate": None,
            "updated_at": time.time(),
        },
    )


def _write_component_progress_snapshot(
    progress_path: Path, payload: dict[str, Any]
) -> None:
    """Publish a complete snapshot so readers never observe partial JSON."""
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = progress_path.with_name(progress_path.name + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(progress_path)


def _write_component_progress(
    *,
    component: str,
    analysis_dir: Path,
    progress_path: Path,
    epoch: int,
    target_epochs: int,
    phase: str,
    loss: float,
    learning_rate: float | None,
    execution_guard=None,
) -> None:
    if execution_guard is not None:
        execution_guard()
    csv_path = analysis_dir / "results.csv"
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        csv.DictWriter(
            handle,
            fieldnames=("epoch", "phase", "train/loss", "lr/pg0"),
        ).writerow(
            {
                "epoch": int(epoch),
                "phase": phase,
                "train/loss": float(loss),
                "lr/pg0": learning_rate,
            }
        )
    _write_component_progress_snapshot(
        progress_path,
        {
            "component": component,
            "current_epoch": int(epoch),
            "target_epochs": max(1, int(target_epochs)),
            "phase": phase,
            "loss": float(loss),
            "learning_rate": learning_rate,
            "updated_at": time.time(),
        },
    )


def _train_classifier_component(
    *,
    component: str,
    dataset_root: Path,
    rows: list[dict[str, Any]],
    class_names: list[str],
    output_dir: Path,
    epochs: int,
    device,
    analysis_dir: Path | None = None,
    progress_path: Path | None = None,
    seed: int = 0,
    iteration_checkpoint: str | Path | None = None,
    execution_guard=None,
) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader, Dataset

    from labelu.internal.common.plate_recognition_models import (
        build_plate_classifier,
        save_component_checkpoint,
    )

    torch.manual_seed(int(seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(seed))

    label_field = "color" if component == "color" else "plateType"
    class_to_index = {name: index for index, name in enumerate(class_names)}
    eligible_rows = [row for row in rows if row.get(label_field) in class_to_index]
    train_rows = [row for row in eligible_rows if row.get("split") == "train"]
    train_rows = train_rows or eligible_rows

    class ComponentDataset(Dataset):
        def __len__(self):
            return len(train_rows)

        def __getitem__(self, index):
            row = train_rows[index]
            image = _load_tensor(
                dataset_root / row["classification_path"],
                width=128,
                height=64,
            )
            return image, class_to_index[row[label_field]]

    if not train_rows:
        raise RuntimeError(f"{component} component has no positive samples")
    analysis_dir = analysis_dir or (output_dir / "analysis" / component)
    progress_path = progress_path or (analysis_dir / "progress.json")
    _reset_component_progress(
        component=component,
        analysis_dir=analysis_dir,
        progress_path=progress_path,
        target_epochs=epochs,
        execution_guard=execution_guard,
    )
    model = build_plate_classifier(len(class_names))
    iteration_source = _load_component_iteration_checkpoint(
        model,
        component=component,
        labels=class_names,
        checkpoint_path=iteration_checkpoint,
    )
    model = model.to(device)
    loader = DataLoader(
        ComponentDataset(),
        batch_size=min(32, len(train_rows)),
        shuffle=True,
        generator=torch.Generator().manual_seed(int(seed)),
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    class_counts = torch.bincount(
        torch.tensor(
            [class_to_index[row[label_field]] for row in train_rows],
            dtype=torch.long,
        ),
        minlength=len(class_names),
    ).float()
    class_weights = torch.zeros_like(class_counts)
    observed_mask = class_counts > 0
    class_weights[observed_mask] = class_counts[observed_mask].sum() / (
        observed_mask.sum() * class_counts[observed_mask]
    )
    criterion = torch.nn.CrossEntropyLoss(weight=class_weights.to(device))
    model.train()
    final_loss = 0.0
    loss_history: list[dict[str, Any]] = []
    for epoch_index in range(max(1, int(epochs))):
        running_loss = 0.0
        batch_count = 0
        for images, targets in loader:
            images = images.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(images), targets)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.detach().cpu())
            batch_count += 1
        final_loss = running_loss / max(1, batch_count)
        loss_history.append(
            {
                "epoch": epoch_index + 1,
                "phase": "train",
                "loss": final_loss,
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
            }
        )
        _write_component_progress(
            component=component,
            analysis_dir=analysis_dir,
            progress_path=progress_path,
            epoch=epoch_index + 1,
            target_epochs=epochs,
            phase="train",
            loss=final_loss,
            learning_rate=float(optimizer.param_groups[0]["lr"]),
            execution_guard=execution_guard,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    contract = component_artifact_contract()[component]
    pt_path = output_dir / contract["pt"]
    save_component_checkpoint(
        pt_path,
        model=model,
        component=component,
        labels=class_names,
        input_size=(128, 64),
    )
    onnx_path = output_dir / contract["onnx"]
    model.eval().cpu()
    torch.onnx.export(
        model,
        torch.zeros(1, 3, 64, 128),
        str(onnx_path),
        input_names=["images"],
        output_names=["logits"],
        opset_version=17,
        dynamic_axes=None,
    )
    evaluation_counts = {
        split: {"correct": 0, "total": 0} for split in ("train", "val", "test")
    }
    prediction_rows: list[dict[str, Any]] = []
    model.eval()
    with torch.no_grad():
        for row in eligible_rows:
            split = str(row.get("split") or "train")
            if split not in evaluation_counts:
                continue
            image = _load_tensor(
                dataset_root / row["classification_path"],
                width=128,
                height=64,
            ).unsqueeze(0)
            prediction = int(model(image).argmax(1).item())
            target = class_to_index[row[label_field]]
            evaluation_counts[split]["total"] += 1
            evaluation_counts[split]["correct"] += int(prediction == target)
            raw_image = _read_image(dataset_root / row["classification_path"])
            prediction_rows.append(
                {
                    "split": split,
                    "target": class_names[target],
                    "prediction": class_names[prediction],
                    "correct": prediction == target,
                    "image_rgb": raw_image[:, :, ::-1],
                }
            )
    evaluation_metrics = {
        split: {
            **counts,
            "accuracy": (
                counts["correct"] / counts["total"] if counts["total"] else None
            ),
        }
        for split, counts in evaluation_counts.items()
    }
    observed = sorted({row[label_field] for row in eligible_rows})
    analysis = _write_component_analysis_artifacts(
        component=component,
        analysis_dir=analysis_dir,
        loss_history=loss_history,
        evaluation_metrics=evaluation_metrics,
        prediction_rows=prediction_rows,
        class_names=class_names,
    )
    return {
        "pt": str(pt_path.resolve()),
        "onnx": str(onnx_path.resolve()),
        "network": contract["network"],
        "classes": class_names,
        "observed_classes": observed,
        "train_sample_count": len(train_rows),
        "final_loss": final_loss,
        "loss_history": loss_history,
        "evaluation_metrics": evaluation_metrics,
        "analysis": analysis,
        "iteration_source": iteration_source,
    }


def _train_ocr_component(
    *,
    dataset_root: Path,
    rows: list[dict[str, Any]],
    output_dir: Path,
    epochs: int,
    device,
    analysis_dir: Path | None = None,
    progress_path: Path | None = None,
    real_finetune_epochs: int = 0,
    real_finetune_learning_rate: float = 2e-4,
    seed: int = 0,
    iteration_checkpoint: str | Path | None = None,
    execution_guard=None,
) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

    from labelu.internal.common.plate_recognition_models import (
        build_plate_ocr_model,
        save_component_checkpoint,
    )

    random.seed(int(seed))
    torch.manual_seed(int(seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(seed))

    eligible_rows = [
        row for row in rows if _normalize_plate_number(row.get("plateNumber"))
    ]
    train_rows = [row for row in eligible_rows if row.get("split") == "train"]
    train_rows = train_rows or eligible_rows
    if not train_rows:
        raise RuntimeError("ocr component has no positive samples")
    characters = sorted(
        {
            character
            for row in eligible_rows
            for character in str(row.get("plateNumber") or "").strip()
        }
    )
    if not characters:
        raise RuntimeError("ocr component has no plateNumber characters")
    char_to_index = {character: index + 1 for index, character in enumerate(characters)}
    length_counts: dict[int, int] = {}
    for row in eligible_rows:
        length = len(_normalize_plate_number(row.get("plateNumber")))
        length_counts[length] = length_counts.get(length, 0) + 1
    preferred_length = max(length_counts, key=length_counts.get)

    import cv2
    import numpy as np

    np.random.seed(int(seed))

    def prepare_image(row):
        return _prepare_ocr_component_image(dataset_root, row)

    prepared_images = []
    patch_bank: dict[str, list[Any]] = {}
    sequence_lengths = []
    for row in train_rows:
        image = prepare_image(row)
        prepared_images.append(image)
        plate_number = _normalize_plate_number(row.get("plateNumber"))
        sequence_lengths.append(len(plate_number))
        boundaries = np.linspace(0, image.shape[1], len(plate_number) + 1)
        for index, character in enumerate(plate_number):
            left = int(round(boundaries[index]))
            right = max(left + 1, int(round(boundaries[index + 1])))
            patch_bank.setdefault(character, []).append(image[:, left:right].copy())

    synthetic_count = min(4096, max(len(train_rows), len(train_rows) * 2))
    train_characters = sorted(patch_bank)

    def augment_image(image):
        image = image.astype(np.float32)
        if random.random() < 0.8:
            image *= random.uniform(0.75, 1.25)
            image += random.uniform(-12.0, 12.0)
        if random.random() < 0.35:
            image = cv2.GaussianBlur(image, (3, 3), random.uniform(0.1, 1.0))
        if random.random() < 0.35:
            scale = random.uniform(0.45, 0.8)
            small = cv2.resize(
                image,
                (max(32, int(round(192 * scale))), max(12, int(round(48 * scale)))),
                interpolation=cv2.INTER_AREA,
            )
            image = cv2.resize(small, (192, 48), interpolation=cv2.INTER_LINEAR)
        if random.random() < 0.5:
            transform = np.float32(
                [[1, 0, random.uniform(-2.0, 2.0)], [0, 1, random.uniform(-1.0, 1.0)]]
            )
            image = cv2.warpAffine(
                image,
                transform,
                (192, 48),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        if random.random() < 0.5:
            image += np.random.normal(0.0, random.uniform(1.0, 6.0), image.shape)
        return np.clip(image, 0, 255).astype(np.uint8)

    class OCRDataset(Dataset):
        def __len__(self):
            return len(train_rows) + synthetic_count

        def __getitem__(self, index):
            if index < len(train_rows):
                image = prepared_images[index].copy()
                plate_number = _normalize_plate_number(
                    train_rows[index].get("plateNumber")
                )
            else:
                length = random.choice(sequence_lengths)
                synthetic_characters = [
                    random.choice(train_characters) for _ in range(length)
                ]
                slot_width = 192.0 / length
                character_patches = []
                for character in synthetic_characters:
                    patch = random.choice(patch_bank[character])
                    character_patches.append(
                        cv2.resize(
                            patch,
                            (max(1, int(round(slot_width))), 48),
                            interpolation=cv2.INTER_LINEAR,
                        )
                    )
                image = cv2.resize(
                    cv2.hconcat(character_patches),
                    (192, 48),
                    interpolation=cv2.INTER_LINEAR,
                )
                plate_number = "".join(synthetic_characters)
            image = augment_image(image)
            image = torch.from_numpy(image.transpose(2, 0, 1).copy()).float() / 255.0
            target = torch.tensor(
                [char_to_index[character] for character in plate_number],
                dtype=torch.long,
            )
            return image, target

    def collate(batch):
        images = torch.stack([item[0] for item in batch])
        targets = torch.cat([item[1] for item in batch])
        lengths = torch.tensor([len(item[1]) for item in batch], dtype=torch.long)
        return images, targets, lengths

    analysis_dir = analysis_dir or (output_dir / "analysis" / "ocr")
    progress_path = progress_path or (analysis_dir / "progress.json")
    total_epochs = max(1, int(epochs)) + max(0, int(real_finetune_epochs))
    _reset_component_progress(
        component="ocr",
        analysis_dir=analysis_dir,
        progress_path=progress_path,
        target_epochs=total_epochs,
        execution_guard=execution_guard,
    )
    model = build_plate_ocr_model(len(characters) + 1)
    iteration_source = _load_component_iteration_checkpoint(
        model,
        component="ocr",
        labels=["<blank>", *characters],
        checkpoint_path=iteration_checkpoint,
    )
    model = model.to(device)
    plate_frequencies: dict[str, int] = {}
    for row in train_rows:
        plate_number = _normalize_plate_number(row.get("plateNumber"))
        plate_frequencies[plate_number] = plate_frequencies.get(plate_number, 0) + 1
    sample_weights = [
        1.0 / plate_frequencies[_normalize_plate_number(row.get("plateNumber"))]
        for row in train_rows
    ]
    identity_count = max(1, len(plate_frequencies))
    sample_weights.extend(
        [identity_count / synthetic_count for _index in range(synthetic_count)]
    )
    sampler = WeightedRandomSampler(
        sample_weights,
        num_samples=len(sample_weights),
        replacement=True,
    )
    loader = DataLoader(
        OCRDataset(),
        batch_size=min(32, len(train_rows)),
        sampler=sampler,
        collate_fn=collate,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    criterion = torch.nn.CTCLoss(blank=0, zero_infinity=True)
    model.train()
    final_loss = 0.0
    loss_history: list[dict[str, Any]] = []
    for epoch_index in range(max(1, int(epochs))):
        running_loss = 0.0
        batch_count = 0
        for images, targets, target_lengths in loader:
            images = images.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            log_probs = logits.log_softmax(2).transpose(0, 1)
            input_lengths = torch.full(
                (images.shape[0],),
                logits.shape[1],
                dtype=torch.long,
                device=device,
            )
            loss = criterion(log_probs, targets, input_lengths, target_lengths)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.detach().cpu())
            batch_count += 1
        final_loss = running_loss / max(1, batch_count)
        loss_history.append(
            {
                "epoch": epoch_index + 1,
                "phase": "train",
                "loss": final_loss,
                "learning_rate": float(optimizer.param_groups[0]["lr"]),
            }
        )
        _write_component_progress(
            component="ocr",
            analysis_dir=analysis_dir,
            progress_path=progress_path,
            epoch=epoch_index + 1,
            target_epochs=total_epochs,
            phase="train",
            loss=final_loss,
            learning_rate=float(optimizer.param_groups[0]["lr"]),
            execution_guard=execution_guard,
        )

    real_finetune_final_loss = None
    if int(real_finetune_epochs) > 0:

        class RealOCRDataset(Dataset):
            def __len__(self):
                return len(train_rows)

            def __getitem__(self, index):
                plate_number = _normalize_plate_number(
                    train_rows[index].get("plateNumber")
                )
                image = (
                    torch.from_numpy(
                        prepared_images[index].transpose(2, 0, 1).copy()
                    ).float()
                    / 255.0
                )
                target = torch.tensor(
                    [char_to_index[character] for character in plate_number],
                    dtype=torch.long,
                )
                return image, target

        finetune_loader = DataLoader(
            RealOCRDataset(),
            batch_size=min(32, len(train_rows)),
            shuffle=True,
            generator=torch.Generator().manual_seed(int(seed) + 1),
            collate_fn=collate,
        )
        finetune_optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=float(real_finetune_learning_rate),
            weight_decay=1e-5,
        )
        model.train()
        for _finetune_epoch in range(int(real_finetune_epochs)):
            running_loss = 0.0
            batch_count = 0
            for images, targets, target_lengths in finetune_loader:
                images = images.to(device)
                targets = targets.to(device)
                finetune_optimizer.zero_grad(set_to_none=True)
                logits = model(images)
                log_probs = logits.log_softmax(2).transpose(0, 1)
                input_lengths = torch.full(
                    (images.shape[0],),
                    logits.shape[1],
                    dtype=torch.long,
                    device=device,
                )
                loss = criterion(log_probs, targets, input_lengths, target_lengths)
                loss.backward()
                finetune_optimizer.step()
                running_loss += float(loss.detach().cpu())
                batch_count += 1
            real_finetune_final_loss = running_loss / max(1, batch_count)
            loss_history.append(
                {
                    "epoch": len(loss_history) + 1,
                    "phase": "real_finetune",
                    "loss": real_finetune_final_loss,
                    "learning_rate": float(finetune_optimizer.param_groups[0]["lr"]),
                }
            )
            _write_component_progress(
                component="ocr",
                analysis_dir=analysis_dir,
                progress_path=progress_path,
                epoch=len(loss_history),
                target_epochs=total_epochs,
                phase="real_finetune",
                loss=real_finetune_final_loss,
                learning_rate=float(finetune_optimizer.param_groups[0]["lr"]),
                execution_guard=execution_guard,
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    contract = component_artifact_contract()["ocr"]
    pt_path = output_dir / contract["pt"]
    labels = ["<blank>", *characters]
    save_component_checkpoint(
        pt_path,
        model=model,
        component="ocr",
        labels=labels,
        input_size=(192, 48),
    )
    onnx_path = output_dir / contract["onnx"]
    model.eval().cpu()
    torch.onnx.export(
        model,
        torch.zeros(1, 3, 48, 192),
        str(onnx_path),
        input_names=["images"],
        output_names=["sequence_logits"],
        opset_version=17,
        dynamic_axes=None,
    )
    evaluation_counts = {
        split: {"correct": 0, "total": 0} for split in ("train", "val", "test")
    }
    prediction_rows: list[dict[str, Any]] = []
    with torch.no_grad():
        for row in eligible_rows:
            split = str(row.get("split") or "train")
            if split not in evaluation_counts:
                continue
            image = prepare_image(row)
            tensor = (
                torch.from_numpy(image.transpose(2, 0, 1).copy()).float().unsqueeze(0)
                / 255.0
            )
            logits = model(tensor).numpy().reshape((-1, len(characters) + 1))
            sequence = np.argmax(logits, axis=1)
            decoded = []
            previous = None
            for raw_index in sequence:
                index = int(raw_index)
                if index and index != previous:
                    decoded.append(characters[index - 1])
                previous = index
            decoded_text = "".join(decoded)
            target_text = _normalize_plate_number(row.get("plateNumber"))
            correct = decoded_text == target_text
            evaluation_counts[split]["total"] += 1
            evaluation_counts[split]["correct"] += int(correct)
            prediction_rows.append(
                {
                    "split": split,
                    "target": target_text,
                    "prediction": decoded_text,
                    "correct": correct,
                    "image_rgb": image,
                }
            )
    evaluation_metrics = {
        split: {
            **counts,
            "accuracy": (
                counts["correct"] / counts["total"] if counts["total"] else None
            ),
        }
        for split, counts in evaluation_counts.items()
    }
    vocabulary_path = output_dir / "plate_ocr_vocab.json"
    vocabulary_path.write_text(
        json.dumps(
            {"blank_index": 0, "characters": characters}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    analysis = _write_component_analysis_artifacts(
        component="ocr",
        analysis_dir=analysis_dir,
        loss_history=loss_history,
        evaluation_metrics=evaluation_metrics,
        prediction_rows=prediction_rows,
        class_names=characters,
    )
    return {
        "pt": str(pt_path.resolve()),
        "onnx": str(onnx_path.resolve()),
        "network": contract["network"],
        "vocabulary": str(vocabulary_path.resolve()),
        "characters": characters,
        "length_counts": {
            str(key): value for key, value in sorted(length_counts.items())
        },
        "preferred_length": preferred_length,
        "crop_ratios": [0.45, 0.5, 0.55],
        "fallback_crop_ratios": [0.47],
        "length_anchor_crop_ratio": 0.5,
        "length_preference_tolerance": 0.1,
        "non_preferred_length_preserve_confidence": 0.99,
        "guarded_consensus_enabled": True,
        "anchor_group_min_confidence": 0.90,
        "non_anchor_group_min_confidence": 0.94,
        "train_sample_count": len(train_rows),
        "synthetic_sample_count": synthetic_count,
        "final_loss": final_loss,
        "loss_history": loss_history,
        "real_finetune_epochs": int(real_finetune_epochs),
        "real_finetune_learning_rate": float(real_finetune_learning_rate),
        "real_finetune_final_loss": real_finetune_final_loss,
        "evaluation_metrics": evaluation_metrics,
        "analysis": analysis,
        "decode": "ctc_greedy",
        "iteration_source": iteration_source,
    }


def regenerate_plate_component_analysis(
    job_id: int,
    *,
    component_artifacts: dict[str, Any] | None = None,
    epochs: int | None = None,
    device: str = "cpu",
) -> dict[str, Any]:
    """Rebuild custom-component plots for a completed legacy plate job."""
    import numpy as np
    import torch

    from labelu.internal.common.plate_recognition_models import (
        build_plate_classifier,
        build_plate_ocr_model,
    )

    job_root = _job_work_root(int(job_id))
    dataset_root = job_root / "plate_dataset"
    rows = _read_component_rows(dataset_root)
    if not rows:
        raise RuntimeError(f"plate job {int(job_id)} has no component samples")
    if component_artifacts is None:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db, int(job_id))
            if job is None:
                raise RuntimeError(f"plate job {int(job_id)} does not exist")
            try:
                component_artifacts = json.loads(job.component_artifacts_json or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                component_artifacts = {}
            epochs = int(epochs or job.epochs or 1)
    component_artifacts = component_artifacts or {}
    target_epochs = max(1, int(epochs or 1))
    results: dict[str, Any] = {}

    pose_artifact = component_artifacts.get("pose") or {}
    pose_checkpoint = Path(
        str(pose_artifact.get("evaluation_weights") or pose_artifact.get("pt") or "")
    )
    if not pose_checkpoint.is_file():
        for candidate in (
            job_root
            / "plate_components"
            / "pose_train"
            / "train"
            / "weights"
            / "best.pt",
            job_root
            / "plate_components"
            / "pose_train"
            / "train"
            / "weights"
            / "last.pt",
        ):
            if candidate.is_file():
                pose_checkpoint = candidate
                break
    pose_data = dataset_root / "pose" / "data.yaml"
    if pose_checkpoint.is_file() and pose_data.is_file():
        from ultralytics import YOLO

        pose_output_root = job_root / "plate_components" / "evaluation" / "pose"
        pose_result = YOLO(str(pose_checkpoint)).val(
            data=str(pose_data),
            split="val",
            imgsz=int((pose_artifact.get("input_size") or [640])[0]),
            device=str(device or "cpu"),
            workers=0,
            plots=True,
            project=str(pose_output_root),
            name="analysis",
            exist_ok=True,
            verbose=True,
        )
        generated_dir = Path(
            str(getattr(pose_result, "save_dir", pose_output_root / "analysis"))
        )
        pose_analysis_dir = job_root / "plate_components" / "pose_train" / "train"
        pose_analysis_dir.mkdir(parents=True, exist_ok=True)
        copied_files: list[str] = []
        for source in generated_dir.rglob("*"):
            if not source.is_file() or source.suffix.lower() not in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            }:
                continue
            if not any(
                hint in source.name.lower()
                for hint in (
                    "pred",
                    "val_batch",
                    "results",
                    "confusion_matrix",
                    "labels",
                    "curve",
                )
            ):
                continue
            target = pose_analysis_dir / source.name
            shutil.copy2(source, target)
            copied_files.append(str(target.resolve()))
        results["pose"] = {
            "checkpoint": str(pose_checkpoint.resolve()),
            "analysis_directory": str(pose_analysis_dir.resolve()),
            "generated_files": copied_files,
        }

    for component in ("ocr", "color", "plate_type"):
        artifact = component_artifacts.get(component) or {}
        evaluation_artifact = artifact.get("evaluation_stage") or artifact
        checkpoint_path = Path(str(evaluation_artifact.get("pt") or ""))
        if not checkpoint_path.is_file():
            checkpoint_path = (
                job_root
                / "plate_components"
                / "evaluation"
                / component
                / component_artifact_contract()[component]["pt"]
            )
        if not checkpoint_path.is_file():
            raise FileNotFoundError(
                f"missing {component} evaluation checkpoint: {checkpoint_path}"
            )
        try:
            checkpoint = torch.load(
                checkpoint_path, map_location="cpu", weights_only=True
            )
        except TypeError:
            checkpoint = torch.load(checkpoint_path, map_location="cpu")
        labels = [str(value) for value in checkpoint.get("labels") or []]
        if not labels:
            raise RuntimeError(f"{component} checkpoint has no labels")
        if component == "ocr":
            characters = labels[1:] if labels[0] == "<blank>" else labels
            model = build_plate_ocr_model(len(characters) + 1)
        else:
            characters = []
            model = build_plate_classifier(len(labels))
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()

        evaluation_counts = {
            split: {"correct": 0, "total": 0} for split in ("train", "val", "test")
        }
        prediction_rows: list[dict[str, Any]] = []
        preview_counts: dict[tuple[str, bool], int] = {}
        with torch.no_grad():
            for row in rows:
                split = str(row.get("split") or "train")
                if split not in evaluation_counts:
                    continue
                if component == "ocr":
                    target = _normalize_plate_number(row.get("plateNumber"))
                    if not target:
                        continue
                    image_rgb = _prepare_ocr_component_image(dataset_root, row)
                    tensor = (
                        torch.from_numpy(image_rgb.transpose(2, 0, 1).copy())
                        .float()
                        .unsqueeze(0)
                        / 255.0
                    )
                    logits = model(tensor).numpy().reshape((-1, len(characters) + 1))
                    sequence = np.argmax(logits, axis=1)
                    decoded: list[str] = []
                    previous = None
                    for raw_index in sequence:
                        index = int(raw_index)
                        if index and index != previous:
                            decoded.append(characters[index - 1])
                        previous = index
                    prediction = "".join(decoded)
                else:
                    field = "color" if component == "color" else "plateType"
                    target = str(row.get(field) or "")
                    if target not in labels:
                        continue
                    tensor = _load_tensor(
                        dataset_root / row["classification_path"],
                        width=128,
                        height=64,
                    ).unsqueeze(0)
                    prediction = labels[int(model(tensor).argmax(1).item())]
                    image_bgr = _read_image(dataset_root / row["classification_path"])
                    image_rgb = image_bgr[:, :, ::-1]
                correct = prediction == target
                evaluation_counts[split]["total"] += 1
                evaluation_counts[split]["correct"] += int(correct)
                preview_key = (split, correct)
                keep_preview = preview_counts.get(preview_key, 0) < 12
                preview_counts[preview_key] = preview_counts.get(preview_key, 0) + 1
                prediction_rows.append(
                    {
                        "split": split,
                        "target": target,
                        "prediction": prediction,
                        "correct": correct,
                        "image_rgb": image_rgb if keep_preview else None,
                    }
                )
        metrics = {
            split: {
                **counts,
                "accuracy": (
                    counts["correct"] / counts["total"] if counts["total"] else None
                ),
            }
            for split, counts in evaluation_counts.items()
        }
        loss_history = list(evaluation_artifact.get("loss_history") or [])
        if not loss_history and evaluation_artifact.get("final_loss") is not None:
            loss_history = [
                {
                    "epoch": target_epochs,
                    "phase": "legacy_final",
                    "loss": float(evaluation_artifact["final_loss"]),
                    "learning_rate": None,
                }
            ]
        analysis = _write_component_analysis_artifacts(
            component=component,
            analysis_dir=(
                job_root / "plate_components" / "evaluation" / component / "analysis"
            ),
            loss_history=loss_history,
            evaluation_metrics=metrics,
            prediction_rows=prediction_rows,
            class_names=characters if component == "ocr" else labels,
        )
        results[component] = {
            "checkpoint": str(checkpoint_path.resolve()),
            "evaluation_metrics": metrics,
            "analysis": analysis,
            "legacy_loss_history": not bool(evaluation_artifact.get("loss_history")),
        }
    return results


def _ultralytics_run_complete(run_dir: Path, target_epochs: int) -> bool:
    results_path = run_dir / "results.csv"
    if not results_path.is_file():
        return False
    try:
        with results_path.open(
            "r", encoding="utf-8", errors="replace", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return False
    if len(rows) >= max(1, int(target_epochs)):
        return True
    if not rows:
        return False
    try:
        last_epoch = int(float(rows[-1].get("epoch") or -1))
    except (TypeError, ValueError):
        return False
    return last_epoch >= max(1, int(target_epochs))


def _ultralytics_last_metrics(run_dir: Path) -> dict[str, float]:
    results_path = run_dir / "results.csv"
    if not results_path.is_file():
        return {}
    try:
        with results_path.open(
            "r", encoding="utf-8", errors="replace", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return {}
    if not rows:
        return {}
    metrics: dict[str, float] = {}
    for key, raw in rows[-1].items():
        if key in ("epoch", "time") or raw in (None, ""):
            continue
        try:
            metrics[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return metrics


def _train_pose_component(
    *,
    dataset_root: Path,
    output_dir: Path,
    epochs: int,
    imgsz: int,
    device: str | None,
    hyperparams: dict[str, Any],
    model_weights: str = PLATE_POSE_PRETRAINED_MODEL,
    resume: bool = False,
    iteration_checkpoint: str | Path | None = None,
    artifact_contract: dict[str, str] | None = None,
    execution_guard=None,
) -> dict[str, Any]:
    if execution_guard is not None:
        execution_guard()
    os.environ.setdefault("YOLO_OFFLINE", "true")
    from matplotlib import font_manager
    from ultralytics.utils import USER_CONFIG_DIR

    font_target = Path(USER_CONFIG_DIR) / "Arial.ttf"
    if not font_target.is_file():
        font_target.parent.mkdir(parents=True, exist_ok=True)
        for system_font in sorted(font_manager.findSystemFonts()):
            try:
                shutil.copyfile(system_font, font_target)
                break
            except OSError:
                continue
    if not font_target.is_file():
        raise RuntimeError("YOLOv8 Pose training requires an installed system font")

    from ultralytics import YOLO

    effective_imgsz = max(640, int(imgsz))
    if iteration_checkpoint:
        initial_path = Path(iteration_checkpoint)
        if not initial_path.is_file():
            raise RuntimeError(f"missing pose iteration checkpoint: {initial_path}")
        pretrained_model = str(initial_path.resolve())
    else:
        pretrained_model = resolve_plate_pose_pretrained_model(model_weights)
    workers = max(0, int(hyperparams.get("workers", 0) or 0))
    amp = bool(hyperparams.get("amp", False))
    cache = hyperparams.get("cache", False)
    train_kwargs = {
        "data": str(dataset_root / "pose" / "data.yaml"),
        "epochs": max(1, int(epochs)),
        "imgsz": effective_imgsz,
        "project": str(output_dir / "pose_train"),
        "name": "train",
        "exist_ok": True,
        "pretrained": True,
        "amp": amp,
        "device": device or "cpu",
        "workers": workers,
        "cache": cache,
        "plots": True,
        "save": True,
        "verbose": True,
    }
    for key in ("batch", "lr0", "lrf", "weight_decay", "patience"):
        if key in hyperparams:
            train_kwargs[key] = hyperparams[key]
    evaluation_run_dir = output_dir / "pose_train" / "train"
    evaluation_last = evaluation_run_dir / "weights" / "last.pt"
    final_run_dir = output_dir / "pose_final_fit" / "train"
    final_last = final_run_dir / "weights" / "last.pt"
    evaluation_result = None
    evaluation_complete = _ultralytics_run_complete(evaluation_run_dir, epochs)
    if (
        resume
        and evaluation_last.is_file()
        and not evaluation_complete
        and not final_last.is_file()
    ):
        evaluation_result = YOLO(str(evaluation_last)).train(
            resume=str(evaluation_last),
            device=device or "cpu",
        )
    elif not evaluation_complete and not (resume and final_last.is_file()):
        evaluation_result = YOLO(pretrained_model).train(**train_kwargs)

    evaluation_weights = evaluation_run_dir / "weights" / "best.pt"
    if not evaluation_weights.is_file():
        evaluation_weights = evaluation_last
    if not evaluation_weights.is_file():
        raise RuntimeError("YOLOv8 Pose did not produce a weight file")

    final_fit_yaml, final_fit_sample_count = _write_pose_final_fit_yaml(dataset_root)
    final_kwargs = {
        **train_kwargs,
        "data": str(final_fit_yaml),
        "project": str(output_dir / "pose_final_fit"),
        "name": "train",
        "pretrained": True,
        # The final fit includes all confirmed samples. Keep its validation plots
        # out of the analysis UI because they are not held-out evaluation results.
        "plots": False,
    }
    final_result = None
    if (
        resume
        and final_last.is_file()
        and not _ultralytics_run_complete(final_run_dir, epochs)
    ):
        final_result = YOLO(str(final_last)).train(
            resume=str(final_last),
            device=device or "cpu",
        )
    elif not _ultralytics_run_complete(final_run_dir, epochs):
        final_result = YOLO(str(evaluation_weights)).train(**final_kwargs)

    trained_weights = final_run_dir / "weights" / "best.pt"
    if not trained_weights.is_file():
        trained_weights = final_last
    if not trained_weights.is_file():
        raise RuntimeError("YOLOv8 Pose final fit did not produce a weight file")
    if execution_guard is not None:
        execution_guard()
    contract = artifact_contract or component_artifact_contract()["pose"]
    pt_path = output_dir / contract["pt"]
    shutil.copy2(trained_weights, pt_path)
    if execution_guard is not None:
        execution_guard()
    exported = YOLO(str(pt_path)).export(
        format="onnx",
        imgsz=effective_imgsz,
        opset=17,
        dynamic=False,
        batch=1,
    )
    exported_path = Path(str(exported))
    onnx_path = output_dir / contract["onnx"]
    if exported_path.resolve() != onnx_path.resolve():
        shutil.copy2(exported_path, onnx_path)
    return {
        "pt": str(pt_path.resolve()),
        "onnx": str(onnx_path.resolve()),
        "network": contract["network"],
        "class_names": ["doubleEP"],
        "keypoint_order": ["top_left", "top_right", "bottom_right", "bottom_left"],
        "keypoint_shape": [4, 3],
        "input_size": [effective_imgsz, effective_imgsz],
        "pretrained_model": pretrained_model,
        "iteration_source": (
            {"checkpoint": pretrained_model} if iteration_checkpoint else None
        ),
        "fit_strategy": "independent_split_then_all_confirmed",
        "train_sample_count": len(
            [
                path
                for path in (dataset_root / "pose" / "images" / "train").glob("*")
                if path.is_file()
            ]
        ),
        "base_final_fit_sample_count": final_fit_sample_count,
        "evaluation_weights": str(evaluation_weights.resolve()),
        "evaluation_metrics": (
            {
                str(key): float(value)
                for key, value in (
                    getattr(evaluation_result, "results_dict", {}) or {}
                ).items()
            }
            or _ultralytics_last_metrics(evaluation_run_dir)
        ),
        "final_fit_metrics": (
            {
                str(key): float(value)
                for key, value in (
                    getattr(final_result, "results_dict", {}) or {}
                ).items()
            }
            or _ultralytics_last_metrics(final_run_dir)
        ),
        "final_fit_sample_count": final_fit_sample_count,
    }


def _update_job_component(
    db: Session,
    *,
    job_id: int,
    component: str,
    status_value: str,
    artifact: dict[str, Any] | None = None,
    error: str | None = None,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    job = crud_yolo_train_job.get(db, job_id)
    if not job:
        return
    if execution_owner is not None or execution_fencing_token is not None:
        from labelu.internal.application.service.yolo_train import (
            _assert_train_execution_fence,
        )

        _assert_train_execution_fence(
            db,
            job_id=int(job_id),
            execution_owner=execution_owner,
            execution_fencing_token=execution_fencing_token,
        )
    try:
        statuses = json.loads(job.component_status_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        statuses = initial_component_status(
            PLATE_DETECTION_COMPONENTS
            if job.job_type == PLATE_DETECTION_JOB_TYPE
            else PLATE_COMPONENTS
        )
    status_artifact = None
    if artifact is not None:
        status_artifact = {
            key: artifact[key]
            for key in ("pt", "onnx")
            if artifact.get(key) is not None
        }
    statuses.setdefault(component, {})
    statuses[component].update(
        {
            "status": status_value,
            "artifact": status_artifact,
            "error": error,
        }
    )
    try:
        artifacts = json.loads(job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        artifacts = {}
    if artifact is not None:
        artifacts[component] = artifact
    with begin_transaction(db):
        # Re-check immediately before the write; a replacement worker may have
        # taken over after the initial read above.
        _assert_plate_train_execution_fence(
            db,
            job_id=int(job_id),
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
        crud_yolo_train_job.update_status(
            db,
            job,
            job.status,
            component_status_json=json.dumps(statuses, ensure_ascii=False),
            component_artifacts_json=json.dumps(artifacts, ensure_ascii=False),
        )


def _plate_execution_generation(
    *,
    execution_fencing_token: int | None,
    execution_owner: str | None,
) -> str | None:
    """Return the immutable execution generation for fenced plate artifacts.

    The legacy Web path deliberately has no execution lease.  It therefore
    returns ``None`` and keeps the historical on-disk compatibility behavior.
    A partial lease context is never a valid generation and must fail closed.
    """
    if execution_owner is None and execution_fencing_token is None:
        return None
    if execution_owner is None or execution_fencing_token is None:
        from labelu.internal.application.service.yolo_train import (
            TrainExecutionFenceLost,
        )

        raise TrainExecutionFenceLost("车牌制品执行世代上下文不完整")
    return f"{execution_owner}:{int(execution_fencing_token)}"


def _decorate_plate_artifact_generation(
    artifact: dict[str, Any],
    *,
    execution_fencing_token: int | None,
    execution_owner: str | None,
) -> dict[str, Any]:
    """Attach owner/token to the DB artifact snapshot when fencing is active."""
    generation = _plate_execution_generation(
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )
    if generation is None:
        return artifact
    return {
        **artifact,
        "execution_owner": str(execution_owner),
        "execution_fencing_token": int(execution_fencing_token),
        "phase_generation": generation,
    }


def _write_plate_artifact_generation_marker(
    *,
    output_dir: Path,
    component: str,
    artifact: dict[str, Any],
    execution_fencing_token: int | None,
    execution_owner: str | None,
) -> None:
    """Publish a small atomic marker used only for fenced disk recovery.

    PT/ONNX files are produced by third-party training tools before the
    database status can be fenced.  The marker is written only for leased
    executions; an old worker's marker cannot be accepted by a new token.
    """
    generation = _plate_execution_generation(
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )
    if generation is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    marker_path = output_dir / f".{component}.artifact-generation.json"
    temporary_path = marker_path.with_name(
        f".{component}.artifact-generation.{os.getpid()}.tmp"
    )
    payload = {
        "component": component,
        "execution_owner": str(execution_owner),
        "execution_fencing_token": int(execution_fencing_token),
        "phase_generation": generation,
        "pt": str(artifact.get("pt") or ""),
        "onnx": str(artifact.get("onnx") or ""),
    }
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary_path, marker_path)


def _plate_artifact_marker_matches(
    *,
    output_dir: Path,
    component: str,
    artifact: dict[str, Any],
    execution_fencing_token: int | None,
    execution_owner: str | None,
) -> bool:
    """Check that a recovered PT/ONNX pair belongs to this worker generation."""
    generation = _plate_execution_generation(
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )
    if generation is None:
        return True
    marker_path = output_dir / f".{component}.artifact-generation.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(marker, dict):
        return False
    try:
        marker_token = int(marker.get("execution_fencing_token"))
    except (TypeError, ValueError):
        # A truncated or manually edited marker must never become a valid
        # recovery claim merely because its JSON remains parseable.
        return False
    return (
        marker.get("component") == component
        and marker.get("phase_generation") == generation
        and marker.get("execution_owner") == str(execution_owner)
        and marker_token == int(execution_fencing_token)
        and Path(str(marker.get("pt") or "")).resolve()
        == Path(str(artifact.get("pt") or "")).resolve()
        and Path(str(marker.get("onnx") or "")).resolve()
        == Path(str(artifact.get("onnx") or "")).resolve()
    )


def _assert_plate_train_execution_fence(
    db: Session,
    *,
    job_id: int,
    execution_fencing_token: int | None,
    execution_owner: str | None,
) -> None:
    if execution_owner is None and execution_fencing_token is None:
        # Preserve the historical Web path only while no execution worker owns
        # the job.  Once a dedicated worker has a live lease, an unfenced Web
        # request must not overwrite component or terminal state.
        try:
            current = (
                db.query(
                    YoloTrainJob.execution_lease_owner,
                    YoloTrainJob.execution_lease_expires_at,
                )
                .filter(YoloTrainJob.id == int(job_id))
                .first()
            )
        except AttributeError:
            # Lightweight historical test doubles may not expose query()
            # semantics; retain their no-lease compatibility path.
            return
        except Exception as exc:
            # Lease inspection failure must fail closed. A stale legacy
            # process must not keep publishing component state while another
            # worker may have taken ownership.
            from labelu.internal.application.service.yolo_train import (
                TrainExecutionFenceLost,
            )

            raise TrainExecutionFenceLost(
                f"车牌训练任务 {job_id} 无法确认执行租约，拒绝写入"
            ) from exc
        if (
            current is not None
            and current.execution_lease_owner
            and current.execution_lease_expires_at is not None
            and current.execution_lease_expires_at > datetime.now()
        ):
            from labelu.internal.application.service.yolo_train import (
                TrainExecutionFenceLost,
            )

            raise TrainExecutionFenceLost(
                f"车牌训练任务 {job_id} 已由执行 Worker 持有租约，拒绝无上下文写入"
            )
        return
    from labelu.internal.application.service.yolo_train import (
        _assert_train_execution_fence,
    )

    _assert_train_execution_fence(
        db,
        job_id=int(job_id),
        execution_owner=execution_owner,
        execution_fencing_token=execution_fencing_token,
    )


def _plate_execution_write_guard(
    job_id: int,
    *,
    execution_fencing_token: int | None,
    execution_owner: str | None,
):
    """Guard component progress/artifact file writes by execution generation."""

    def _guard() -> None:
        with session_scope() as db:
            _assert_plate_train_execution_fence(
                db,
                job_id=int(job_id),
                execution_fencing_token=execution_fencing_token,
                execution_owner=execution_owner,
            )

    return _guard


def _quality_warnings(
    profile_config: dict[str, Any], rows: list[dict[str, Any]]
) -> list[str]:
    warnings: list[str] = []
    plate_numbers = {
        _normalize_plate_number(row.get("plateNumber"))
        for row in rows
        if _normalize_plate_number(row.get("plateNumber"))
    }
    if len(plate_numbers) < 20:
        warnings.append(f"车牌号码身份仅 {len(plate_numbers)} 个，OCR 泛化风险高")
    for field, display in (("color", "车牌颜色"), ("plateType", "车牌类型")):
        configured = set(profile_config["attributes"][field])
        observed = {row.get(field) for row in rows if row.get(field)}
        missing = sorted(configured - observed)
        if missing:
            warnings.append(f"{display}缺少训练样本：{', '.join(missing)}")
    return warnings


def _completed_component_artifacts(job) -> dict[str, dict[str, Any]]:
    try:
        statuses = json.loads(job.component_status_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        statuses = {}
    try:
        stored_artifacts = json.loads(job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        stored_artifacts = {}
    completed: dict[str, dict[str, Any]] = {}
    for component in PLATE_COMPONENTS:
        status_item = statuses.get(component) or {}
        artifact = stored_artifacts.get(component) or status_item.get("artifact") or {}
        if status_item.get("status") != "COMPLETED" or not isinstance(artifact, dict):
            continue
        pt_path = Path(str(artifact.get("pt") or ""))
        onnx_path = Path(str(artifact.get("onnx") or ""))
        if pt_path.is_file() and onnx_path.is_file():
            completed[component] = artifact
    return completed


def _recover_component_artifacts_from_disk(
    job_id: int,
    *,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> dict[str, dict[str, Any]]:
    output_dir = _job_work_root(int(job_id)) / "plate_components"
    recovered: dict[str, dict[str, Any]] = {}
    for component, contract in component_artifact_contract().items():
        pt_path = output_dir / contract["pt"]
        onnx_path = output_dir / contract["onnx"]
        if not pt_path.is_file() or not onnx_path.is_file():
            continue
        artifact = {
            "pt": str(pt_path.resolve()),
            "onnx": str(onnx_path.resolve()),
            "network": contract["network"],
            "recovered_from_disk": True,
        }
        if not _plate_artifact_marker_matches(
            output_dir=output_dir,
            component=component,
            artifact=artifact,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        ):
            # A fenced worker may only recover a pair published by the same
            # execution generation.  Legacy no-lease Web runs retain the
            # historical file-only recovery behavior.
            continue
        recovered[component] = artifact
    return recovered


def load_plate_iteration_component_artifacts(
    db: Session,
    source_job_id: int,
) -> dict[str, dict[str, Any]]:
    source_job = crud_yolo_train_job.get(db, int(source_job_id))
    if not source_job:
        raise RuntimeError(f"iteration source job #{int(source_job_id)} not found")
    if source_job.job_type != PLATE_RECOGNITION_JOB_TYPE:
        raise RuntimeError(
            f"iteration source job #{int(source_job_id)} is not plate recognition"
        )
    if source_job.status != YoloTrainStatus.COMPLETED.value:
        raise RuntimeError(
            f"iteration source job #{int(source_job_id)} is not completed"
        )
    artifacts = _completed_component_artifacts(source_job)
    missing = [
        component for component in PLATE_COMPONENTS if component not in artifacts
    ]
    if missing:
        raise RuntimeError(
            f"iteration source job #{int(source_job_id)} missing completed components: "
            + ", ".join(missing)
        )
    return artifacts


def load_plate_detection_iteration_artifact(
    db: Session,
    source_job_id: int,
) -> dict[str, Any]:
    source_job = crud_yolo_train_job.get(db, int(source_job_id))
    if not source_job:
        raise RuntimeError(
            f"plate detection source job #{int(source_job_id)} not found"
        )
    if source_job.job_type != PLATE_DETECTION_JOB_TYPE:
        raise RuntimeError(
            f"iteration source job #{int(source_job_id)} is not plate detection"
        )
    if source_job.status != YoloTrainStatus.COMPLETED.value:
        raise RuntimeError(
            f"iteration source job #{int(source_job_id)} is not completed"
        )
    try:
        artifacts = json.loads(source_job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        artifacts = {}
    pose = artifacts.get("pose") if isinstance(artifacts, dict) else None
    if not isinstance(pose, dict):
        pose = {}
    pt_path = Path(str(pose.get("pt") or source_job.weights_path or ""))
    onnx_path = Path(str(pose.get("onnx") or ""))
    if not pt_path.is_file() or not onnx_path.is_file():
        raise RuntimeError(
            f"iteration source job #{int(source_job_id)} missing plate detection artifacts"
        )
    return {**pose, "pt": str(pt_path.resolve()), "onnx": str(onnx_path.resolve())}


def run_plate_recognition_train_job(
    *,
    job_id: int,
    task_id: int,
    epochs: int,
    imgsz: int,
    hyperparams: dict[str, Any] | None,
    train_device: str | None = None,
    resume: bool = False,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    output_dir = _job_work_root(job_id) / "plate_components"
    execution_guard = _plate_execution_write_guard(
        job_id,
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )
    resumed_artifacts: dict[str, dict[str, Any]] = {}
    recovered_artifacts: dict[str, dict[str, Any]] = {}
    iteration_source_job_id = int(
        (hyperparams or {}).get(PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM) or 0
    )

    iteration_artifacts: dict[str, dict[str, Any]] = {}
    try:
        with session_scope() as db:
            job, training_task, profile_config, source_task_ids, samples = (
                _load_training_context(db, job_id=job_id)
            )
            if iteration_source_job_id:
                if iteration_source_job_id == int(job_id):
                    raise RuntimeError(
                        "plate iteration source cannot be the current job"
                    )
                iteration_artifacts = load_plate_iteration_component_artifacts(
                    db, iteration_source_job_id
                )
            model_weights = str(job.model_weights or "").strip()
            if resume:
                resumed_artifacts = _completed_component_artifacts(job)
                disk_artifacts = _recover_component_artifacts_from_disk(
                    job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                recovered_artifacts = {
                    component: artifact
                    for component, artifact in disk_artifacts.items()
                    if component not in resumed_artifacts
                }
                resumed_artifacts.update(recovered_artifacts)
                for component, artifact in recovered_artifacts.items():
                    _update_job_component(
                        db,
                        job_id=job_id,
                        component=component,
                        status_value="COMPLETED",
                        artifact=artifact,
                        execution_fencing_token=execution_fencing_token,
                        execution_owner=execution_owner,
                    )
            with begin_transaction(db):
                _assert_plate_train_execution_fence(
                    db,
                    job_id=job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    YoloTrainStatus.PROCESSING.value,
                    error_message=None,
                )
                crud_training_task.update(
                    db,
                    training_task,
                    status=YoloTrainStatus.PROCESSING.value,
                    latest_job_status=YoloTrainStatus.PROCESSING.value,
                )
            dataset_root, manifest = prepare_plate_recognition_dataset(
                job_id=job_id,
                profile_config=profile_config,
                source_task_ids=source_task_ids,
                samples=samples,
                seed=int(settings.YOLO_TRAIN_SPLIT_SEED),
            )
            rows = _read_component_rows(dataset_root)
            manifest["quality_warnings"] = _quality_warnings(profile_config, rows)
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db, job_id)
                _assert_plate_train_execution_fence(
                    db,
                    job_id=job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    job.status,
                    train_sample_count=manifest["composition"]["splits"]["train"][
                        "total_images"
                    ],
                    val_sample_count=manifest["composition"]["splits"]["val"][
                        "total_images"
                    ],
                    dataset_manifest_json=json.dumps(manifest, ensure_ascii=False),
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        resolved_train_device = resolve_plate_train_device(job_id, train_device)
        device = _torch_device(resolved_train_device)
        artifacts: dict[str, dict[str, Any]] = dict(resumed_artifacts)
        final_fit_rows = _final_fit_rows(rows)
        pose_augmented_rows: list[dict[str, Any]] = []
        pose_augmentation_report: dict[str, Any] = {}
        component_order = ("pose", "ocr", "color", "plate_type")
        for component in component_order:
            if component in artifacts:
                if component == "pose":
                    pose_augmented_rows, pose_augmentation_report = (
                        _augment_ocr_rows_from_pose(
                            dataset_root=dataset_root,
                            rows=rows,
                            pose_pt=Path(artifacts[component]["pt"]),
                            imgsz=imgsz,
                            device=str(device),
                        )
                    )
                continue
            with session_scope() as db:
                _update_job_component(
                    db,
                    job_id=job_id,
                    component=component,
                    status_value="PROCESSING",
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
            if component == "pose":
                artifact = _train_pose_component(
                    dataset_root=dataset_root,
                    output_dir=output_dir,
                    epochs=epochs,
                    imgsz=imgsz,
                    device=resolved_train_device,
                    hyperparams=hyperparams or {},
                    model_weights=model_weights,
                    resume=resume,
                    iteration_checkpoint=(
                        iteration_artifacts.get("pose", {}).get("pt")
                    ),
                    execution_guard=execution_guard,
                )
                pose_augmented_rows, pose_augmentation_report = (
                    _augment_ocr_rows_from_pose(
                        dataset_root=dataset_root,
                        rows=rows,
                        pose_pt=Path(artifact["pt"]),
                        imgsz=imgsz,
                        device=str(device),
                    )
                )
                artifact["ocr_pose_augmentation"] = pose_augmentation_report
            elif component == "ocr":
                evaluation_artifact = _train_ocr_component(
                    dataset_root=dataset_root,
                    rows=rows,
                    output_dir=output_dir / "evaluation" / "ocr",
                    analysis_dir=output_dir / "evaluation" / "ocr" / "analysis",
                    epochs=epochs,
                    device=device,
                    progress_path=output_dir / "progress" / "ocr.json",
                    iteration_checkpoint=(iteration_artifacts.get("ocr", {}).get("pt")),
                    execution_guard=execution_guard,
                )
                artifact = _train_ocr_component(
                    dataset_root=dataset_root,
                    rows=final_fit_rows + pose_augmented_rows,
                    output_dir=output_dir,
                    analysis_dir=output_dir / "final_analysis" / "ocr",
                    epochs=epochs,
                    device=device,
                    real_finetune_epochs=max(1, min(20, int(epochs) // 4)),
                    progress_path=output_dir / "progress" / "ocr.json",
                    iteration_checkpoint=(iteration_artifacts.get("ocr", {}).get("pt")),
                    execution_guard=execution_guard,
                )
                artifact.update(
                    {
                        "fit_strategy": "independent_split_then_all_confirmed",
                        "evaluation_stage": evaluation_artifact,
                        "base_final_fit_sample_count": sum(
                            bool(_normalize_plate_number(row.get("plateNumber")))
                            for row in final_fit_rows
                        ),
                        "pose_augmented_sample_count": len(pose_augmented_rows),
                        "pose_augmentation_report": pose_augmentation_report,
                    }
                )
            else:
                config_key = "color" if component == "color" else "plateType"
                class_names = list(profile_config["attributes"][config_key])
                evaluation_artifact = _train_classifier_component(
                    component=component,
                    dataset_root=dataset_root,
                    rows=rows,
                    class_names=class_names,
                    output_dir=output_dir / "evaluation" / component,
                    analysis_dir=(output_dir / "evaluation" / component / "analysis"),
                    epochs=epochs,
                    device=device,
                    progress_path=output_dir / "progress" / f"{component}.json",
                    iteration_checkpoint=(
                        iteration_artifacts.get(component, {}).get("pt")
                    ),
                    execution_guard=execution_guard,
                )
                artifact = _train_classifier_component(
                    component=component,
                    dataset_root=dataset_root,
                    rows=final_fit_rows + pose_augmented_rows,
                    class_names=class_names,
                    output_dir=output_dir,
                    analysis_dir=output_dir / "final_analysis" / component,
                    epochs=epochs,
                    device=device,
                    progress_path=output_dir / "progress" / f"{component}.json",
                    iteration_checkpoint=(
                        iteration_artifacts.get(component, {}).get("pt")
                    ),
                    execution_guard=execution_guard,
                )
                artifact.update(
                    {
                        "fit_strategy": "independent_split_then_all_confirmed",
                        "evaluation_stage": evaluation_artifact,
                        "base_final_fit_sample_count": sum(
                            row.get(config_key) in class_names for row in final_fit_rows
                        ),
                        "pose_augmented_sample_count": sum(
                            row.get(config_key) in class_names
                            for row in pose_augmented_rows
                        ),
                    }
                )
            artifact = _decorate_plate_artifact_generation(
                artifact,
                execution_fencing_token=execution_fencing_token,
                execution_owner=execution_owner,
            )
            _write_plate_artifact_generation_marker(
                output_dir=output_dir,
                component=component,
                artifact=artifact,
                execution_fencing_token=execution_fencing_token,
                execution_owner=execution_owner,
            )
            artifacts[component] = artifact
            with session_scope() as db:
                _update_job_component(
                    db,
                    job_id=job_id,
                    component=component,
                    status_value="COMPLETED",
                    artifact=artifact,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )

        with session_scope() as db:
            job = crud_yolo_train_job.get(db, job_id)
            training_task = crud_training_task.get(db, int(job.training_task_id or 0))
            summary = {
                "training_profile": PLATE_RECOGNITION_PROFILE,
                "components": {
                    key: {
                        "status": "COMPLETED",
                        "train_sample_count": value.get("train_sample_count"),
                        "fit_strategy": value.get("fit_strategy"),
                        "base_final_fit_sample_count": value.get(
                            "base_final_fit_sample_count"
                        ),
                        "pose_augmented_sample_count": value.get(
                            "pose_augmented_sample_count"
                        ),
                        "final_loss": value.get("final_loss"),
                        "evaluation_metrics": (
                            value.get("evaluation_stage", {}).get("evaluation_metrics")
                            or value.get("evaluation_metrics")
                        ),
                    }
                    for key, value in artifacts.items()
                },
                "quality_warnings": manifest.get("quality_warnings") or [],
                "iteration_source_job_id": iteration_source_job_id or None,
            }
            with begin_transaction(db):
                _assert_plate_train_execution_fence(
                    db,
                    job_id=job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    YoloTrainStatus.COMPLETED.value,
                    weights_path=artifacts["pose"]["pt"],
                    component_artifacts_json=json.dumps(artifacts, ensure_ascii=False),
                    metrics_summary=json.dumps(summary, ensure_ascii=False),
                    error_message=None,
                )
                if training_task:
                    crud_training_task.update(
                        db,
                        training_task,
                        status=YoloTrainStatus.COMPLETED.value,
                        latest_job_status=YoloTrainStatus.COMPLETED.value,
                    )
    except Exception as exc:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db, job_id)
            if job:
                try:
                    statuses = json.loads(job.component_status_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    statuses = initial_component_status()
                active_component = next(
                    (
                        name
                        for name, value in statuses.items()
                        if value.get("status") == "PROCESSING"
                    ),
                    None,
                )
                if active_component:
                    statuses[active_component]["status"] = "FAILED"
                    statuses[active_component]["error"] = str(exc)
                training_task = crud_training_task.get(
                    db, int(job.training_task_id or 0)
                )
                with begin_transaction(db):
                    _assert_plate_train_execution_fence(
                        db,
                        job_id=job_id,
                        execution_fencing_token=execution_fencing_token,
                        execution_owner=execution_owner,
                    )
                    crud_yolo_train_job.update_status(
                        db,
                        job,
                        YoloTrainStatus.FAILED.value,
                        error_message=str(exc)[:4000],
                        component_status_json=json.dumps(statuses, ensure_ascii=False),
                    )
                    if training_task:
                        crud_training_task.update(
                            db,
                            training_task,
                            status=YoloTrainStatus.FAILED.value,
                            latest_job_status=YoloTrainStatus.FAILED.value,
                        )
        raise


def run_plate_detection_train_job(
    *,
    job_id: int,
    task_id: int,
    epochs: int,
    imgsz: int,
    hyperparams: dict[str, Any] | None,
    train_device: str | None = None,
    resume: bool = False,
    execution_fencing_token: int | None = None,
    execution_owner: str | None = None,
) -> None:
    del task_id  # The immutable training-task snapshot is the source of truth.
    output_dir = _job_work_root(job_id) / "plate_detection"
    execution_guard = _plate_execution_write_guard(
        job_id,
        execution_fencing_token=execution_fencing_token,
        execution_owner=execution_owner,
    )
    iteration_source_job_id = int(
        (hyperparams or {}).get(PLATE_ITERATION_SOURCE_JOB_ID_HYPERPARAM) or 0
    )
    try:
        with session_scope() as db:
            job, training_task, profile_config, source_task_ids, samples = (
                _load_training_context(
                    db,
                    job_id=job_id,
                    expected_job_type=PLATE_DETECTION_JOB_TYPE,
                )
            )
            iteration_artifact: dict[str, Any] = {}
            if iteration_source_job_id:
                if iteration_source_job_id == int(job_id):
                    raise RuntimeError(
                        "plate detection iteration source cannot be the current job"
                    )
                iteration_artifact = load_plate_detection_iteration_artifact(
                    db, iteration_source_job_id
                )
            model_weights = str(job.model_weights or "").strip()
            with begin_transaction(db):
                _assert_plate_train_execution_fence(
                    db,
                    job_id=job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    YoloTrainStatus.PROCESSING.value,
                    error_message=None,
                )
                crud_training_task.update(
                    db,
                    training_task,
                    status=YoloTrainStatus.PROCESSING.value,
                    latest_job_status=YoloTrainStatus.PROCESSING.value,
                )
            dataset_root, manifest = prepare_plate_recognition_dataset(
                job_id=job_id,
                profile_config=profile_config,
                source_task_ids=source_task_ids,
                samples=samples,
                seed=int(settings.YOLO_TRAIN_SPLIT_SEED),
            )
            warnings: list[str] = []
            if int(manifest["composition"].get("negative_images") or 0) == 0:
                warnings.append("车牌检测数据没有负样本，边端误报风险较高")
            manifest["quality_warnings"] = warnings
            manifest["training_target"] = PLATE_DETECTION_JOB_TYPE
            with begin_transaction(db):
                job = crud_yolo_train_job.get(db, job_id)
                _assert_plate_train_execution_fence(
                    db,
                    job_id=job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    job.status,
                    train_sample_count=manifest["composition"]["splits"]["train"][
                        "total_images"
                    ],
                    val_sample_count=manifest["composition"]["splits"]["val"][
                        "total_images"
                    ],
                    dataset_manifest_json=json.dumps(manifest, ensure_ascii=False),
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        resolved_train_device = resolve_plate_train_device(job_id, train_device)
        with session_scope() as db:
            _update_job_component(
                db,
                job_id=job_id,
                component="pose",
                status_value="PROCESSING",
                execution_fencing_token=execution_fencing_token,
                execution_owner=execution_owner,
            )
        artifact = _train_pose_component(
            dataset_root=dataset_root,
            output_dir=output_dir,
            epochs=epochs,
            imgsz=imgsz,
            device=resolved_train_device,
            hyperparams=hyperparams or {},
            model_weights=model_weights,
            resume=resume,
            iteration_checkpoint=iteration_artifact.get("pt"),
            artifact_contract=plate_detection_artifact_contract(),
            execution_guard=execution_guard,
        )
        artifact = _decorate_plate_artifact_generation(
            artifact,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
        _write_plate_artifact_generation_marker(
            output_dir=output_dir,
            component="pose",
            artifact=artifact,
            execution_fencing_token=execution_fencing_token,
            execution_owner=execution_owner,
        )
        artifacts = {"pose": artifact}
        with session_scope() as db:
            _update_job_component(
                db,
                job_id=job_id,
                component="pose",
                status_value="COMPLETED",
                artifact=artifact,
                execution_fencing_token=execution_fencing_token,
                execution_owner=execution_owner,
            )

        with session_scope() as db:
            job = crud_yolo_train_job.get(db, job_id)
            training_task = crud_training_task.get(db, int(job.training_task_id or 0))
            evaluation_metrics = artifact.get("evaluation_metrics") or {}
            summary = {
                "training_profile": PLATE_RECOGNITION_PROFILE,
                "training_target": PLATE_DETECTION_JOB_TYPE,
                "final_metrics": {
                    "map50_95": evaluation_metrics.get("metrics/mAP50-95(B)"),
                    "map50": evaluation_metrics.get("metrics/mAP50(B)"),
                    "precision": evaluation_metrics.get("metrics/precision(B)"),
                    "recall": evaluation_metrics.get("metrics/recall(B)"),
                    "fitness": evaluation_metrics.get("fitness"),
                },
                "components": {
                    "pose": {
                        "status": "COMPLETED",
                        "train_sample_count": artifact.get("train_sample_count"),
                        "fit_strategy": artifact.get("fit_strategy"),
                        "base_final_fit_sample_count": artifact.get(
                            "base_final_fit_sample_count"
                        ),
                        "evaluation_metrics": evaluation_metrics,
                    }
                },
                "quality_warnings": manifest.get("quality_warnings") or [],
                "iteration_source_job_id": iteration_source_job_id or None,
            }
            with begin_transaction(db):
                _assert_plate_train_execution_fence(
                    db,
                    job_id=job_id,
                    execution_fencing_token=execution_fencing_token,
                    execution_owner=execution_owner,
                )
                crud_yolo_train_job.update_status(
                    db,
                    job,
                    YoloTrainStatus.COMPLETED.value,
                    weights_path=artifact["pt"],
                    component_artifacts_json=json.dumps(artifacts, ensure_ascii=False),
                    metrics_summary=json.dumps(summary, ensure_ascii=False),
                    error_message=None,
                )
                if training_task:
                    crud_training_task.update(
                        db,
                        training_task,
                        status=YoloTrainStatus.COMPLETED.value,
                        latest_job_status=YoloTrainStatus.COMPLETED.value,
                    )
    except Exception as exc:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db, job_id)
            if job:
                try:
                    statuses = json.loads(job.component_status_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    statuses = initial_component_status(PLATE_DETECTION_COMPONENTS)
                pose_status = statuses.setdefault(
                    "pose", {"status": "PENDING", "artifact": None, "error": None}
                )
                pose_status["status"] = "FAILED"
                pose_status["error"] = str(exc)
                training_task = crud_training_task.get(
                    db, int(job.training_task_id or 0)
                )
                with begin_transaction(db):
                    _assert_plate_train_execution_fence(
                        db,
                        job_id=job_id,
                        execution_fencing_token=execution_fencing_token,
                        execution_owner=execution_owner,
                    )
                    crud_yolo_train_job.update_status(
                        db,
                        job,
                        YoloTrainStatus.FAILED.value,
                        error_message=str(exc)[:4000],
                        component_status_json=json.dumps(statuses, ensure_ascii=False),
                    )
                    if training_task:
                        crud_training_task.update(
                            db,
                            training_task,
                            status=YoloTrainStatus.FAILED.value,
                            latest_job_status=YoloTrainStatus.FAILED.value,
                        )
        raise


def submit_plate_recognition_train_job(
    *,
    job_id: int,
    task_id: int,
    model_weights: str,
    epochs: int,
    imgsz: int,
    hyperparams: dict[str, Any] | None,
) -> None:
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service.yolo_train_queue import (
        dispatch_all_available_train_jobs,
        request_train_start,
    )

    command = CreateYoloTrainJobCommand(
        model_weights=model_weights,
        weights_source="preset",
        epochs=epochs,
        imgsz=imgsz,
        category="plate_recognition",
        hyperparams=hyperparams or {},
        execution_mode="local",
    )
    request_train_start(job_id, task_id, command)
    dispatch_all_available_train_jobs()
