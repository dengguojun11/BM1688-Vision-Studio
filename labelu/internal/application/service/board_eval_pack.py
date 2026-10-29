"""为板端 Agent 打包与训练/任务一致的验证图片（写入导出目录 board_eval/）。"""
from __future__ import annotations

import json
import hashlib
import random
import re
import shutil
from pathlib import Path
from typing import Any, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.application.service.yolo_bm1688_export import _export_dir_for_row
from labelu.internal.application.service.attachment import (
    _materialize_datasource_attachment_to_cache,
)
from labelu.internal.application.service.yolo_dataset import build_yolo_rect_class_names
from labelu.internal.common.config import settings
from labelu.internal.common.fixed_eval_signature import fixed_eval_content_signature
from labelu.internal.domain.models.sample import SampleState, TaskSample

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def _is_image_path(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in _IMAGE_EXTS


def _label_candidates(image_path: Path) -> list[Path]:
    """Resolve labels beside a YOLO training image.

    Training exports use ``dataset/images/<split>`` with labels in the
    parallel ``dataset/labels`` or ``dataset/labelsxml`` tree.  Keeping both
    formats in the board package lets the existing BM1688 evaluators consume
    the same ground truth without another conversion step.
    """

    split = image_path.parent.name
    dataset_root = image_path.parent.parent.parent
    stem = image_path.stem
    return [
        dataset_root / "labels" / split / f"{stem}.txt",
        dataset_root / "labelsxml" / split / f"{stem}.xml",
        dataset_root / "labels" / split / f"{stem}.xml",
    ]


def _file_entry(path: Path, relative_to: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _seal_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256", None)
    unsigned["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return unsigned


def _safe_board_eval_filename(index: int, src: Path) -> str:
    suffix = src.suffix.lower() or ".jpg"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem or "")
    stem = stem.strip("._-") or "image"
    return f"{index:04d}_{stem}{suffix}"


def _collect_from_train_dataset(job_id: int, limit: int) -> List[Path]:
    """优先使用本次训练作业 dataset 的 val/test/train 图（与模型同分布）。"""
    base = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "dataset"
        / "images"
    )
    found: List[Path] = []
    for split in ("val", "test", "train"):
        split_dir = base / split
        if not split_dir.is_dir():
            continue
        for fp in sorted(split_dir.iterdir()):
            if _is_image_path(fp):
                found.append(fp.resolve())
        if len(found) >= limit:
            break
    return found[:limit]


def _collect_from_task_samples(
    db: Session, *, task_id: int, limit: int
) -> List[Path]:
    media_root = Path(settings.MEDIA_ROOT)
    rows = (
        db.query(TaskSample)
        .filter(
            TaskSample.task_id == task_id,
            TaskSample.state == SampleState.DONE.value,
            TaskSample.deleted_at.is_(None),
        )
        .order_by(TaskSample.id.desc())
        .limit(max(limit * 3, limit))
        .all()
    )
    out: List[Path] = []
    for sample in rows:
        if not sample.file:
            continue
        if getattr(sample.file, "data_source_id", None):
            try:
                src, _content_type = _materialize_datasource_attachment_to_cache(
                    sample.file
                )
            except Exception as exc:
                logger.warning(
                    "board_eval datasource materialize failed attachment_id={}: {}",
                    getattr(sample.file, "id", None),
                    exc,
                )
                continue
        else:
            rel = (sample.file.path or "").lstrip("/\\")
            if not rel:
                continue
            src = media_root / rel
        if _is_image_path(src):
            out.append(src.resolve())
        if len(out) >= limit:
            break
    return out[:limit]


def clear_board_eval_pack_for_export(export_id: int) -> None:
    """删除导出目录下板端验证打包产物（board_eval、board_models.zip）。"""
    root = _export_dir_for_row(export_id)
    eval_root = root / "board_eval"
    zip_path = root / "board_models.zip"
    if eval_root.exists():
        shutil.rmtree(eval_root, ignore_errors=True)
    if zip_path.is_file():
        try:
            zip_path.unlink()
        except OSError as e:
            logger.warning("remove board_models.zip failed export_id={}: {}", export_id, e)


def pack_board_eval_images_for_export(
    db: Session,
    *,
    export_id: int,
    task_id: int,
    yolo_train_job_id: int,
    imgsz: int,
    task_config_json: Optional[str] = None,
) -> Path:
    """
    在导出目录生成 board_eval/images + manifest.json，供 models.zip 带给算能盒。
    """
    root = _export_dir_for_row(export_id)
    eval_root = root / "board_eval"
    img_dir = eval_root / "images"
    if eval_root.exists():
        shutil.rmtree(eval_root)
    img_dir.mkdir(parents=True, exist_ok=True)

    # Board Agent and SSH validation must consume the same immutable set.  Keep
    # one canonical limit instead of allowing two channels to select different
    # images from the same training job.
    limit = max(4, int(getattr(settings, "YOLO_EDGE_VALIDATION_MAX_IMAGES", 50) or 50))
    sources: List[Path] = _collect_from_train_dataset(yolo_train_job_id, limit)
    source_kind = "yolo_train_dataset"
    if len(sources) < max(4, limit // 4):
        extra = _collect_from_task_samples(db, task_id=task_id, limit=limit)
        seen = {p.name for p in sources}
        for p in extra:
            if p.name not in seen:
                sources.append(p)
                seen.add(p.name)
        if extra:
            source_kind = "yolo_train_dataset+task_samples" if sources else "task_samples"

    if len(sources) > limit:
        rng = random.Random(task_id + export_id)
        sources = rng.sample(sources, limit)

    classes: List[str] = []
    if task_config_json:
        try:
            cfg = json.loads(task_config_json)
            if isinstance(cfg, dict):
                classes = build_yolo_rect_class_names(cfg)
        except json.JSONDecodeError:
            pass

    copied = 0
    labeled = 0
    file_mappings: List[dict[str, Any]] = []
    label_mappings: List[dict[str, Any]] = []
    labels_dir = eval_root / "labels"
    labels_xml_dir = eval_root / "labelsxml"
    labels_dir.mkdir(parents=True, exist_ok=True)
    labels_xml_dir.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(sources):
        dst_name = _safe_board_eval_filename(i, src)
        dst = img_dir / dst_name
        try:
            shutil.copy2(src, dst)
            copied += 1
            mapping: dict[str, Any] = {
                "packed_name": dst_name,
                "original_name": src.name,
                "source_path": str(src),
            }
            for label_src in _label_candidates(src):
                if not label_src.is_file():
                    continue
                target_dir = (
                    labels_xml_dir if label_src.suffix.lower() == ".xml" else labels_dir
                )
                label_dst = target_dir / f"{Path(dst_name).stem}{label_src.suffix.lower()}"
                try:
                    shutil.copy2(label_src, label_dst)
                except OSError as exc:
                    logger.warning("board_eval skip label copy {}: {}", label_src, exc)
                    continue
                label_rel = label_dst.relative_to(eval_root).as_posix()
                label_mappings.append(
                    {
                        "image": f"images/{dst_name}",
                        "path": label_rel,
                        "source_path": str(label_src),
                        # Keep the annotation evidence explicit.  The edge
                        # runner must be able to distinguish a real label
                        # mapping from a file-only transport entry.
                        "has_annotation": True,
                        "has_annotation_file": True,
                    }
                )
                mapping.setdefault("label_paths", []).append(label_rel)
            if mapping.get("label_paths"):
                labeled += 1
            file_mappings.append(mapping)
        except OSError as e:
            logger.warning("board_eval skip copy {}: {}", src, e)

    manifest: dict[str, Any] = {
        "source": source_kind,
        "schema_version": "fixed_eval.v1",
        "task_id": task_id,
        "export_id": export_id,
        "yolo_train_job_id": yolo_train_job_id,
        "imgsz": int(imgsz or 640),
        "class_names": classes,
        "image_count": copied,
        "labeled_image_count": labeled,
        "unlabeled_image_count": max(0, copied - labeled),
        "file_mappings": file_mappings,
        "label_mappings": label_mappings,
        "note": "板端 Agent 优先使用本目录图片做 yolov8_bmcv 推理",
    }
    manifest["files"] = [
        _file_entry(path, eval_root)
        for path in sorted(eval_root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    manifest["fixed_eval_signature"] = fixed_eval_content_signature(manifest)
    manifest = _seal_manifest(manifest)
    (eval_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "board_eval packed export_id={} images={} source={}",
        export_id,
        copied,
        source_kind,
    )
    return eval_root
