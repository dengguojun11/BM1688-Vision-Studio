"""训练作业：标注(GT) vs 模型预测逐图对比报告。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from PIL import Image
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_sample, crud_yolo_train_job
from labelu.internal.common.config import settings
from labelu.internal.common.compare_sample_access_token import (
    create_compare_sample_access_token,
)
from labelu.internal.common.pipeline_process_log import yolo_train_work_root
from labelu.internal.common.yolo_gt_pred_boxes import (
    BBox,
    classify_image_issues,
    draw_labeled_image,
    load_yolo_label_file,
    match_boxes_per_class,
    parse_sample_id_from_stem,
    summarize_image_issues_cn,
    summarize_image_issues_cn_brief,
    summarize_image_issues_cn_from_row,
    weak_class_note_cn,
)
from labelu.internal.common.yolo_ultralytics_split_val import (
    read_class_names_from_data_yaml,
)
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_COMPARE_LOCK = threading.Lock()
_RUNNING_JOBS: set[int] = set()
_COMPARE_WORKER_SEMAPHORE = threading.Semaphore(1)

# 后台线程异常退出时 status.json 可能长期停在 running，导致页面空转。
_STALE_RUNNING_NO_PROGRESS_SEC = 300
_STALE_RUNNING_MAX_SEC = 900


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_utc(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_stale_running_status(status: Dict[str, Any]) -> bool:
    if status.get("status") != "running":
        return False
    started = _parse_iso_utc(status.get("started_at"))
    if started is None:
        return True
    age = (datetime.now(timezone.utc) - started).total_seconds()
    processed = int(status.get("processed") or 0)
    if age >= _STALE_RUNNING_NO_PROGRESS_SEC and processed == 0:
        return True
    return age >= _STALE_RUNNING_MAX_SEC


def _is_compare_running_in_process(job_id: int) -> bool:
    with _COMPARE_LOCK:
        return job_id in _RUNNING_JOBS


def should_block_compare_regenerate(job_id: int) -> bool:
    """仅当本进程内确有进行中的生成任务时，才拒绝重复触发。"""
    raw = _read_raw_compare_status(job_id)
    if raw.get("status") != "running":
        return False
    if _is_compare_running_in_process(job_id):
        return True
    return not _is_stale_running_status(raw)


def _read_raw_compare_status(job_id: int) -> Dict[str, Any]:
    path = _status_path(job_id)
    if not path.is_file():
        return {"status": "idle", "job_id": job_id}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "idle", "job_id": job_id}
    return data if isinstance(data, dict) else {"status": "idle", "job_id": job_id}


def _effective_compare_status(
    job_id: int, raw: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    status = dict(raw if raw is not None else _read_raw_compare_status(job_id))
    status.setdefault("job_id", job_id)
    if _manifest_path(job_id).is_file() and status.get("status") == "running":
        status["status"] = "completed"
        status["message"] = status.get("message") or "对比数据已就绪"
        return status
    if _is_stale_running_status(status) and not _is_compare_running_in_process(job_id):
        status["status"] = "failed"
        status["message"] = "对比生成已中断（可点「生成/重跑对比」重试）"
        status["stale"] = True
    return status


def compare_report_root(job_id: int) -> Path:
    return yolo_train_work_root(job_id) / "reports" / "gt_pred_compare"


_COMPARE_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_DATASET_SPLIT_ORDER = ("train", "val", "test")


def _split_dir_has_images(img_dir: Path) -> bool:
    if not img_dir.is_dir():
        return False
    return any(
        p.is_file() and p.suffix.lower() in _COMPARE_IMAGE_SUFFIXES
        for p in img_dir.iterdir()
    )


def _dataset_root_for_job(job_id: int, job: Any = None) -> Path:
    job_root = yolo_train_work_root(job_id)
    if str(getattr(job, "job_type", None) or "").strip() in {
        "plate_detection",
        "plate_recognition",
    }:
        return job_root / "plate_dataset" / "pose"
    return job_root / "dataset"


def _discover_splits_from_dataset_root(dataset_root: Path) -> List[str]:
    root = dataset_root / "images"
    if not root.is_dir():
        return []
    found: List[str] = []
    for split in _DATASET_SPLIT_ORDER:
        if _split_dir_has_images(root / split):
            found.append(split)
    for directory in sorted(root.iterdir()):
        if (
            directory.is_dir()
            and directory.name not in found
            and _split_dir_has_images(directory)
        ):
            found.append(directory.name)
    return found


def _discover_dataset_splits(job_id: int) -> List[str]:
    """训练导出 dataset/images 下所有含图片的划分（= 本作业任务全量）。"""
    return _discover_splits_from_dataset_root(_dataset_root_for_job(job_id))


def _resolve_compare_splits(
    job_id: int, splits: Optional[List[str]] = None
) -> List[str]:
    if splits:
        return [str(s).strip() for s in splits if str(s).strip()]
    if settings.YOLO_GT_PRED_COMPARE_USE_ALL_DATASET_SPLITS:
        discovered = _discover_dataset_splits(job_id)
        if discovered:
            return discovered
    return _default_splits()


def _dataset_image_counts(job_id: int) -> Dict[str, int]:
    """训练导出 dataset 各划分图片数（与 YOLO 训练包一致）。"""
    return _dataset_image_counts_from_root(_dataset_root_for_job(job_id))


def _dataset_image_counts_from_root(dataset_root: Path) -> Dict[str, int]:
    root = dataset_root / "images"
    counts: Dict[str, int] = {"train": 0, "val": 0, "test": 0, "total": 0}
    for split in (
        _discover_splits_from_dataset_root(dataset_root) or _DATASET_SPLIT_ORDER
    ):
        img_dir = root / split
        if not img_dir.is_dir():
            continue
        n = sum(
            1
            for p in img_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _COMPARE_IMAGE_SUFFIXES
        )
        counts[split] = n
        counts["total"] += n
    return counts


def _compare_splits_for_job(job_id: int) -> List[str]:
    raw_status = _read_raw_compare_status(job_id)
    splits = raw_status.get("splits")
    if isinstance(splits, list) and splits:
        return [str(s).strip() for s in splits if str(s).strip()]
    manifest_path = _manifest_path(job_id)
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            split_stats = manifest.get("splits")
            if isinstance(split_stats, dict) and split_stats:
                return list(split_stats.keys())
        except (OSError, json.JSONDecodeError):
            pass
    return _default_splits()


def _attach_compare_scope_summary(
    db: Session,
    *,
    job_id: int,
    job: Optional[YoloTrainJob],
    summary: Dict[str, Any],
) -> None:
    """补充对比范围说明：训练包总量 vs 实际参与对比的划分（默认可为全量 train+val+test）。"""
    counts = _dataset_image_counts(job_id)
    if counts.get("total"):
        summary["dataset_image_counts"] = counts
    split_list = _compare_splits_for_job(job_id)
    if split_list:
        summary["compare_splits"] = split_list
        compared = sum(int(counts.get(sp) or 0) for sp in split_list)
        summary["compare_scope_image_count"] = compared
    if job and job.task_id:
        summary["source_task_id"] = int(job.task_id)
        summary["source_task_sample_count"] = crud_sample.task_list_sample_total(
            db=db, task_id=int(job.task_id)
        )


def _class_count_dict(
    entries: List[Dict[str, Any]],
    class_names: List[str],
    box_key: str,
) -> Dict[str, int]:
    from collections import Counter

    counts: Counter = Counter()
    for entry in entries:
        box = entry.get(box_key) or {}
        cid = int(box.get("class_id", 0))
        if 0 <= cid < len(class_names):
            counts[str(class_names[cid])] += 1
        else:
            counts[f"类别{cid}"] += 1
    return dict(counts)


def _compute_job_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """本训练作业标注对比的全局统计（不受列表筛选影响）。"""
    stats: Dict[str, Any] = {
        "total_images": 0,
        "images_ok_with_boxes": 0,
        "images_ok_empty": 0,
        "images_pool_eligible": 0,
        "images_with_fn": 0,
        "images_with_fp": 0,
        "images_with_both": 0,
        "images_problem": 0,
        "total_gt_boxes": 0,
        "total_pred_boxes": 0,
        "total_tp": 0,
        "total_fp": 0,
        "total_fn": 0,
        "class_fn": {},
        "class_fp": {},
    }
    class_fn: Dict[str, int] = {}
    class_fp: Dict[str, int] = {}
    for row in items:
        stats["total_images"] += 1
        fn = int(row.get("fn") or 0)
        fp = int(row.get("fp") or 0)
        tp = int(row.get("tp") or 0)
        gt = int(row.get("gt") or 0)
        pred = int(row.get("pred") or 0)
        stats["total_gt_boxes"] += gt
        stats["total_pred_boxes"] += pred
        stats["total_fn"] += fn
        stats["total_fp"] += fp
        stats["total_tp"] += tp
        if fn == 0 and fp == 0:
            if gt > 0:
                stats["images_ok_with_boxes"] += 1
                issues = row.get("issues") or []
                if "ok" in issues and pred > 0 and tp > 0:
                    stats["images_pool_eligible"] += 1
            else:
                stats["images_ok_empty"] += 1
        else:
            stats["images_problem"] += 1
            if fn and fp:
                stats["images_with_both"] += 1
            elif fn:
                stats["images_with_fn"] += 1
            elif fp:
                stats["images_with_fp"] += 1
        fn_bc = (
            row.get("fn_by_class") if isinstance(row.get("fn_by_class"), dict) else {}
        )
        fp_bc = (
            row.get("fp_by_class") if isinstance(row.get("fp_by_class"), dict) else {}
        )
        if fn_bc or fp_bc:
            for name, cnt in fn_bc.items():
                class_fn[str(name)] = class_fn.get(str(name), 0) + int(cnt)
            for name, cnt in fp_bc.items():
                class_fp[str(name)] = class_fp.get(str(name), 0) + int(cnt)
        else:
            wc = row.get("worst_class")
            if wc and fn:
                class_fn[str(wc)] = class_fn.get(str(wc), 0) + fn
            if wc and fp:
                class_fp[str(wc)] = class_fp.get(str(wc), 0) + fp
    stats["class_fn"] = dict(sorted(class_fn.items(), key=lambda x: -x[1]))
    stats["class_fp"] = dict(sorted(class_fp.items(), key=lambda x: -x[1]))
    return stats


def _status_path(job_id: int) -> Path:
    return compare_report_root(job_id) / "status.json"


def _manifest_path(job_id: int) -> Path:
    return compare_report_root(job_id) / "manifest.json"


def _overlay_dir(job_id: int, split: str) -> Path:
    return compare_report_root(job_id) / "overlays" / split


def read_compare_status(job_id: int) -> Dict[str, Any]:
    return _effective_compare_status(job_id)


def _write_status(job_id: int, payload: Dict[str, Any]) -> None:
    root = compare_report_root(job_id)
    root.mkdir(parents=True, exist_ok=True)
    _status_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_conf_from_job(job: YoloTrainJob) -> float:
    raw = getattr(job, "hyperparams_json", None) or ""
    if raw:
        try:
            hp = json.loads(raw)
            conf = hp.get("conf")
            if conf is not None:
                return float(conf)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return float(settings.YOLO_GT_PRED_COMPARE_CONF)


def _default_splits() -> List[str]:
    raw = (settings.YOLO_GT_PRED_COMPARE_SPLITS or "val").strip()
    out = [s.strip() for s in raw.split(",") if s.strip()]
    return out or ["val"]


def _predict_image(
    model: Any,
    image_path: Path,
    *,
    imgsz: int,
    conf: float,
    iou: float,
    device: str,
) -> List[BBox]:
    results = model.predict(
        source=str(image_path),
        imgsz=int(imgsz),
        conf=float(conf),
        iou=float(iou),
        device=device,
        verbose=False,
    )
    if not results:
        return []
    res = results[0]
    out: List[BBox] = []
    boxes = getattr(res, "boxes", None)
    if boxes is None:
        return out
    for box in boxes:
        xyxy = box.xyxy[0].tolist()
        out.append(
            BBox(
                class_id=int(box.cls[0]),
                x1=float(xyxy[0]),
                y1=float(xyxy[1]),
                x2=float(xyxy[2]),
                y2=float(xyxy[3]),
                score=float(box.conf[0]),
            )
        )
    return out


def _resolve_compare_sample_reference(
    db: Session, sample_id: Optional[int]
) -> Dict[str, Optional[int]]:
    if not sample_id:
        return {
            "sample_id": None,
            "task_id": None,
            "compare_sample_id": None,
            "compare_task_id": None,
        }
    row = crud_sample.get(db=db, sample_id=int(sample_id))
    if not row or row.deleted_at is not None:
        return {
            "sample_id": None,
            "task_id": None,
            "compare_sample_id": None,
            "compare_task_id": None,
        }

    compare_sample_id = int(row.id)
    compare_task_id = int(row.task_id)
    resolved_sample_id = compare_sample_id
    resolved_task_id = compare_task_id

    if row.data:
        try:
            outer = json.loads(row.data)
        except (TypeError, json.JSONDecodeError):
            outer = {}
        if isinstance(outer, dict):
            meta = outer.get("_consistent_pool")
            if isinstance(meta, dict):
                source_task_id = meta.get("source_task_id")
                source_sample_id = meta.get("source_sample_id")
                if source_task_id is not None and source_sample_id is not None:
                    resolved_task_id = int(source_task_id)
                    resolved_sample_id = int(source_sample_id)

    return {
        "sample_id": resolved_sample_id,
        "task_id": resolved_task_id,
        "compare_sample_id": compare_sample_id,
        "compare_task_id": compare_task_id,
    }


def generate_gt_pred_compare(
    job_id: int,
    *,
    splits: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    if not settings.YOLO_GT_PRED_COMPARE_ENABLED:
        return {"status": "disabled", "job_id": job_id}

    with _COMPARE_LOCK:
        if job_id in _RUNNING_JOBS and not force:
            return read_compare_status(job_id)
        _RUNNING_JOBS.add(job_id)

    from labelu.internal.application.service.yolo_train import (
        _resolve_yolo_train_device,
        prepare_cuda_env_for_yolo_train,
    )
    from labelu.internal.common.db import session_scope

    split_list = [str(s).strip() for s in (splits or []) if str(s).strip()]
    if not split_list:
        split_list = _default_splits()
    status = {
        "status": "running",
        "job_id": job_id,
        "started_at": _iso_now(),
        "splits": split_list,
        "processed": 0,
        "total": 0,
        "message": "正在生成标注对比…",
    }
    _write_status(job_id, status)

    try:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job or job.status != YoloTrainStatus.COMPLETED.value:
                raise ValueError("训练作业未完成，无法生成对比")
            weights = Path((job.weights_path or "").strip())
            if not weights.is_file():
                alt = yolo_train_work_root(job_id) / "train" / "weights" / "best.pt"
                weights = alt if alt.is_file() else weights
            if not weights.is_file():
                raise FileNotFoundError(f"未找到 best.pt: {weights}")

            dataset_root = _dataset_root_for_job(job_id, job)
            if not splits and settings.YOLO_GT_PRED_COMPARE_USE_ALL_DATASET_SPLITS:
                discovered = _discover_splits_from_dataset_root(dataset_root)
                if discovered:
                    split_list = discovered
                    status["splits"] = split_list
                    _write_status(job_id, status)
            class_names = read_class_names_from_data_yaml(dataset_root / "data.yaml")
            if not class_names:
                raise ValueError("data.yaml 中无类别")
            class_count = len(class_names)
            imgsz = int(job.imgsz or 640)
            conf = _parse_conf_from_job(job)
            nms_iou = float(settings.YOLO_GT_PRED_COMPARE_IOU)
            match_iou = float(settings.YOLO_CONSISTENT_POOL_MATCH_IOU)
            device = _resolve_yolo_train_device()

            image_paths: List[Tuple[str, Path]] = []
            for split in split_list:
                img_dir = dataset_root / "images" / split
                if not img_dir.is_dir():
                    continue
                for path in sorted(img_dir.iterdir()):
                    if path.suffix.lower() not in {
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".webp",
                        ".bmp",
                    }:
                        continue
                    image_paths.append((split, path))

            max_images = int(settings.YOLO_GT_PRED_COMPARE_MAX_IMAGES)
            if len(image_paths) > max_images:
                image_paths = image_paths[:max_images]

            status["total"] = len(image_paths)
            _write_status(job_id, status)

            prepare_cuda_env_for_yolo_train()
            from ultralytics import YOLO  # noqa: WPS433

            model = YOLO(str(weights))
            items: List[Dict[str, Any]] = []
            split_stats: Dict[str, Dict[str, int]] = {}

            for idx, (split, image_path) in enumerate(image_paths, start=1):
                stem = image_path.stem
                label_path = dataset_root / "labels" / split / f"{stem}.txt"
                with Image.open(image_path) as im:
                    img_w, img_h = im.size
                gt_boxes = load_yolo_label_file(label_path, img_w=img_w, img_h=img_h)
                preds = _predict_image(
                    model,
                    image_path,
                    imgsz=imgsz,
                    conf=conf,
                    iou=nms_iou,
                    device=device,
                )
                match = match_boxes_per_class(
                    preds,
                    gt_boxes,
                    class_count=class_count,
                    iou_threshold=match_iou,
                )
                issues = classify_image_issues(match)
                issue_cn = summarize_image_issues_cn(match, class_names)
                compare_ref = _resolve_compare_sample_reference(
                    db, parse_sample_id_from_stem(stem)
                )
                sample_id = compare_ref.get("sample_id")
                task_id = compare_ref.get("task_id")

                worst_class = None
                if match.get("fns"):
                    worst_class = class_names[int(match["fns"][0]["class_id"])]
                elif match.get("fps"):
                    worst_class = class_names[int(match["fps"][0]["class_id"])]

                item_id = f"{split}/{stem}"
                overlay_split = _overlay_dir(job_id, split)
                overlay_split.mkdir(parents=True, exist_ok=True)
                gt_bytes = draw_labeled_image(
                    image_path,
                    ground_truth=gt_boxes,
                    predictions=preds,
                    class_names=class_names,
                    match=match,
                    mode="gt",
                )
                pred_bytes = draw_labeled_image(
                    image_path,
                    ground_truth=gt_boxes,
                    predictions=preds,
                    class_names=class_names,
                    match=match,
                    mode="pred",
                )
                (overlay_split / f"{stem}_gt.jpg").write_bytes(gt_bytes)
                (overlay_split / f"{stem}_pred.jpg").write_bytes(pred_bytes)

                matched_ious = [
                    float(pair.get("iou") or 0.0)
                    for pair in (match.get("matched") or [])
                    if isinstance(pair, dict)
                ]
                row = {
                    "id": item_id,
                    "split": split,
                    "filename": image_path.name,
                    "sample_id": sample_id,
                    "task_id": task_id,
                    "compare_sample_id": compare_ref.get("compare_sample_id"),
                    "compare_task_id": compare_ref.get("compare_task_id"),
                    "gt": match["gt"],
                    "pred": match["pred"],
                    "tp": match["tp"],
                    "fp": match["fp"],
                    "fn": match["fn"],
                    "min_matched_iou": min(matched_ious) if matched_ious else None,
                    "match_iou_threshold": match_iou,
                    "issues": issues,
                    "worst_class": worst_class,
                    "fn_by_class": _class_count_dict(
                        list(match.get("fns") or []), class_names, "gt"
                    ),
                    "fp_by_class": _class_count_dict(
                        list(match.get("fps") or []), class_names, "pred"
                    ),
                    "issue_summary_cn": issue_cn.get("summary"),
                    "issue_details_cn": issue_cn.get("details") or [],
                    "issue_level": issue_cn.get("level"),
                    "issue_suggestions_cn": issue_cn.get("suggestions") or [],
                }
                items.append(row)
                st = split_stats.setdefault(
                    split, {"images": 0, "tp": 0, "fp": 0, "fn": 0}
                )
                st["images"] += 1
                st["tp"] += int(match["tp"])
                st["fp"] += int(match["fp"])
                st["fn"] += int(match["fn"])

                if idx % 20 == 0 or idx == len(image_paths):
                    status["processed"] = idx
                    status["message"] = f"已处理 {idx}/{len(image_paths)} 张"
                    _write_status(job_id, status)

            items.sort(key=lambda r: (r.get("fn", 0) + r.get("fp", 0)), reverse=True)
            dataset_counts = _dataset_image_counts_from_root(dataset_root)
            manifest = {
                "version": 1,
                "job_id": job_id,
                "generated_at": _iso_now(),
                "weights": str(weights),
                "imgsz": imgsz,
                "conf": conf,
                "iou": match_iou,
                "nms_iou": nms_iou,
                "match_iou": match_iou,
                "class_names": class_names,
                "compare_splits": split_list,
                "dataset_image_counts": dataset_counts,
                "splits": split_stats,
                "items": items,
            }
            compare_report_root(job_id).mkdir(parents=True, exist_ok=True)
            _manifest_path(job_id).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            done = {
                "status": "completed",
                "job_id": job_id,
                "finished_at": _iso_now(),
                "splits": split_list,
                "processed": len(items),
                "total": len(items),
                "message": f"已生成 {len(items)} 张对比图",
            }
            _write_status(job_id, done)
            logger.info("gt_pred_compare job {} done images={}", job_id, len(items))
            from labelu.internal.application.service.yolo_train_consistent_pool import (
                schedule_consistent_pool_sync_after_compare,
            )

            schedule_consistent_pool_sync_after_compare(job_id)
            return done
    except Exception as exc:
        logger.exception("gt_pred_compare job {} failed", job_id)
        fail = {
            "status": "failed",
            "job_id": job_id,
            "finished_at": _iso_now(),
            "message": str(exc)[:500],
        }
        _write_status(job_id, fail)
        return fail
    finally:
        with _COMPARE_LOCK:
            _RUNNING_JOBS.discard(job_id)


def schedule_gt_pred_compare_after_train(
    job_id: int, *, recover_interrupted: bool = False
) -> bool:
    if (
        not settings.YOLO_GT_PRED_COMPARE_ENABLED
        or not settings.YOLO_GT_PRED_COMPARE_AUTO
    ):
        return False
    if _manifest_path(job_id).is_file() or _is_compare_running_in_process(job_id):
        return False

    raw = _read_raw_compare_status(job_id)
    raw_status = str(raw.get("status") or "idle").lower()
    if raw_status == "completed":
        return False
    if raw_status == "running" and not recover_interrupted:
        if not _is_stale_running_status(raw):
            return False
    if raw_status == "queued" and not recover_interrupted:
        return False

    _write_status(
        job_id,
        {
            "status": "queued",
            "job_id": int(job_id),
            "queued_at": _iso_now(),
            "message": "Comparison generation queued",
        },
    )

    def _run() -> None:
        try:
            with _COMPARE_WORKER_SEMAPHORE:
                generate_gt_pred_compare(job_id, force=recover_interrupted)
        except Exception:
            logger.exception("background gt_pred_compare failed job {}", job_id)

    try:
        threading.Thread(
            target=_run,
            name=f"gt-pred-compare-{job_id}",
            daemon=True,
        ).start()
    except Exception:
        _write_status(
            job_id,
            {
                "status": "failed",
                "job_id": int(job_id),
                "finished_at": _iso_now(),
                "message": "Comparison worker could not be started",
            },
        )
        raise
    return True


def recover_gt_pred_compare_after_restart(
    job_id: int, *, include_idle: bool = False
) -> bool:
    if _manifest_path(job_id).is_file() or _is_compare_running_in_process(job_id):
        return False
    raw = _read_raw_compare_status(job_id)
    raw_status = str(raw.get("status") or "idle").lower()
    if raw_status == "completed":
        return False
    if raw_status == "idle" and not include_idle:
        return False
    if raw_status == "failed" and not _is_stale_running_status(raw):
        return False
    return schedule_gt_pred_compare_after_train(job_id, recover_interrupted=True)


def list_compare_items(
    job_id: int,
    *,
    split: Optional[str] = None,
    issue: Optional[str] = None,
    class_name: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> Dict[str, Any]:
    status = read_compare_status(job_id)
    manifest_path = _manifest_path(job_id)
    if not manifest_path.is_file():
        return {
            "status": status,
            "items": [],
            "total": 0,
            "page": page,
            "size": size,
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": status, "items": [], "total": 0, "page": page, "size": size}

    items = list(manifest.get("items") or [])
    job_stats = _compute_job_stats(items)
    if split:
        items = [r for r in items if r.get("split") == split]
    if issue and issue != "all":
        items = [r for r in items if issue in (r.get("issues") or [])]
    if class_name:
        items = [r for r in items if r.get("worst_class") == class_name]

    total = len(items)
    start = max(0, int(page)) * max(1, int(size))
    end = start + max(1, int(size))
    page_items = items[start:end]
    class_names = list(manifest.get("class_names") or [])
    for row in page_items:
        rebuilt = summarize_image_issues_cn_from_row(row, class_names)
        row["issue_summary_cn"] = rebuilt.get("summary")
        row["issue_details_cn"] = rebuilt.get("details") or []
        row["issue_level"] = rebuilt.get("level")
        row["issue_suggestions_cn"] = rebuilt.get("suggestions") or []
        iid = row.get("id") or ""
        from urllib.parse import quote

        qid = quote(str(iid), safe="")
        row["gt_url"] = (
            f"/api/v1/yolo-train-jobs/{job_id}/gt-pred-compare/asset"
            f"?item_id={qid}&side=gt"
        )
        row["pred_url"] = (
            f"/api/v1/yolo-train-jobs/{job_id}/gt-pred-compare/asset"
            f"?item_id={qid}&side=pred"
        )
        if row.get("task_id") and row.get("sample_id"):
            compare_access_token = create_compare_sample_access_token(
                int(row["task_id"]),
                int(row["sample_id"]),
                job_id=job_id,
            )
            row["annotate_url"] = (
                f"/tasks/{row['task_id']}/samples/{row['sample_id']}"
                f"?compare_access_token={quote(compare_access_token, safe='')}"
                "&from=gt_pred_compare"
            )
        if row.get("worst_class"):
            row["weak_class_note_cn"] = weak_class_note_cn(row.get("worst_class"))
        row["class_names"] = class_names
    summary: Dict[str, Any] = {
        "generated_at": manifest.get("generated_at"),
        "conf": manifest.get("conf"),
        "iou": manifest.get("iou"),
        "imgsz": manifest.get("imgsz"),
        "class_names": manifest.get("class_names") or [],
        "splits": manifest.get("splits") or {},
        "job_stats": job_stats,
    }
    try:
        from labelu.internal.common.db import session_scope
        from labelu.internal.application.service import (
            yolo_train_consistent_pool as pool_svc,
        )

        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if job:
                _attach_compare_scope_summary(
                    db, job_id=job_id, job=job, summary=summary
                )
            pool_report = pool_svc.read_sync_report(job_id)
            pools = pool_report.get("pools")
            if isinstance(pools, list) and pools:
                summary["consistent_pools"] = pools
                summary["pool_task_ids"] = [
                    int(row["pool_task_id"])
                    for row in pools
                    if isinstance(row, dict) and row.get("pool_task_id")
                ]
                if len(pools) == 1:
                    summary["pool_task_id"] = pools[0].get("pool_task_id")
                    summary["pool_task_name"] = pools[0].get("pool_task_name")
                    summary["pool_task_sample_count"] = pools[0].get(
                        "pool_task_sample_count"
                    )
            elif pool_report.get("pool_task_id"):
                summary["pool_task_id"] = pool_report.get("pool_task_id")
                summary["pool_task_name"] = pool_report.get("pool_task_name")
                summary["pool_task_sample_count"] = pool_report.get(
                    "pool_task_sample_count"
                )
    except Exception:
        pass
    if "dataset_image_counts" not in summary:
        try:
            counts = _dataset_image_counts(job_id)
            if counts.get("total"):
                summary["dataset_image_counts"] = counts
                split_list = _compare_splits_for_job(job_id)
                if split_list:
                    summary["compare_splits"] = split_list
                    summary["compare_scope_image_count"] = sum(
                        int(counts.get(sp) or 0) for sp in split_list
                    )
        except Exception:
            pass
    return {
        "status": status,
        "summary": summary,
        "items": page_items,
        "total": total,
        "page": page,
        "size": size,
    }


def resolve_compare_asset_path(job_id: int, item_id: str, side: str) -> Optional[Path]:
    text = (item_id or "").strip().replace("\\", "/")
    if not text or ".." in text.split("/"):
        return None
    if side not in ("gt", "pred"):
        return None
    parts = text.split("/", 1)
    if len(parts) != 2:
        return None
    split, stem = parts[0], parts[1]
    if split not in ("train", "val", "test"):
        return None
    path = _overlay_dir(job_id, split) / f"{stem}_{side}.jpg"
    if not path.is_file():
        return None
    root = compare_report_root(job_id).resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    return path
