"""训练作业：将 GT 与预测严格一致的样本自动汇总到「{分类}-训练筛选一致」任务。"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_sample,
    crud_strategy_task_integration,
    crud_task,
    crud_training_task,
    crud_user,
    crud_yolo_train_job,
)
from labelu.internal.application.service.yolo_train_gt_pred_compare import (
    _manifest_path,
)
from labelu.internal.application.service import (
    annotation_contract as annotation_contract_service,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.label_contract import (
    canonical_json,
    label_set_signature as build_label_set_signature,
    sha256_text,
)
from labelu.internal.common.pipeline_process_log import yolo_train_work_root
from labelu.internal.common.storage import (
    build_thumbnail_key,
    create_thumbnail_bytes,
    get_storage_backend,
)
from labelu.internal.common.task_access import is_consistent_pool_admin
from labelu.internal.common.text_integrity import contains_encoding_replacement_marker
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    DatasetCategory,
)
from labelu.internal.domain.models.annotation_contract import SampleAnnotationScope
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus
from labelu.internal.domain.models.yolo_consistent_pool_registry import (
    YoloConsistentPoolRegistry,
)

_REGISTRY_LOCK = threading.Lock()
_SYNC_LOCK = threading.RLock()
_POOL_META_KEY = "_consistent_pool"
_POOL_KIND = "train_filtered_consistent"
_TASK_NAME_SUFFIX = "训练筛选一致"
_POOL_OWNER_USERNAME = "admin@123.com"
_POOL_SOURCE_TYPE = "consistent_pool"
_POOL_SOURCE_SYSTEM = "labelu"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def pool_task_name(category: str) -> str:
    cat = (category or "").strip()
    if contains_encoding_replacement_marker(cat):
        raise ValueError(
            "consistent pool category contains encoding replacement markers"
        )
    return f"{cat}-{_TASK_NAME_SUFFIX}"


def _category_scope_key(dataset_category_id: Optional[int]) -> str:
    if dataset_category_id is None:
        return "uncategorized"
    return f"category:{int(dataset_category_id)}"


def _active_dataset_category(
    db: Session, dataset_category_id: Optional[int]
) -> Optional[DatasetCategory]:
    if dataset_category_id is None:
        return None
    return (
        db.query(DatasetCategory)
        .filter(
            DatasetCategory.id == int(dataset_category_id),
            DatasetCategory.status == "active",
            DatasetCategory.deleted_at.is_(None),
        )
        .first()
    )


def is_consistent_manifest_item(item: Dict[str, Any]) -> bool:
    """GT/pred counts, classes and every matched box satisfy the strict IoU."""
    if int(item.get("fn") or 0) != 0 or int(item.get("fp") or 0) != 0:
        return False
    issues = {str(value) for value in (item.get("issues") or [])}
    if issues != {"ok"}:
        return False
    gt = int(item.get("gt") or 0)
    pred = int(item.get("pred") or 0)
    tp = int(item.get("tp") or 0)
    if not (gt > 0 and gt == pred == tp):
        return False
    required_iou = float(settings.YOLO_CONSISTENT_POOL_MATCH_IOU)
    try:
        recorded_threshold = float(item.get("match_iou_threshold"))
        min_matched_iou = float(item.get("min_matched_iou"))
    except (TypeError, ValueError):
        # Legacy reports did not preserve per-image IoU evidence. They must be
        # regenerated before they can add data to a stricter consistent pool.
        return False
    return recorded_threshold >= required_iou and min_matched_iou >= required_iou


def _registry_path() -> Path:
    return (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / "consistent_pool_registry.json"
    )


def _read_registry() -> Dict[str, Any]:
    path = _registry_path()
    if not path.is_file():
        return {"categories": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"categories": {}}
    if not isinstance(data, dict):
        return {"categories": {}}
    data.setdefault("categories", {})
    return data


def _write_registry(data: Dict[str, Any]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_report_path(job_id: int) -> Path:
    return yolo_train_work_root(job_id) / "reports" / "consistent_pool_sync.json"


def _write_sync_report(job_id: int, report: Dict[str, Any]) -> None:
    path = _sync_report_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_manifest(job_id: int) -> Dict[str, Any]:
    path = _manifest_path(job_id)
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _load_manifest_items(job_id: int) -> List[Dict[str, Any]]:
    manifest = _load_manifest(job_id)
    items = manifest.get("items") if isinstance(manifest, dict) else None
    return items if isinstance(items, list) else []


def _normalized_class_names(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    result: List[str] = []
    seen: Set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _task_rect_label_values(task: Task) -> List[str]:
    config = _parse_task_config(task.config)
    for tool in config.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
            continue
        attrs = (tool.get("config") or {}).get("attributes") or []
        return _normalized_class_names(
            [item.get("value") for item in attrs if isinstance(item, dict)]
        )
    return []


def _signature_from_label_values(db: Session, class_names: List[str]) -> str:
    normalized = _normalized_class_names(class_names)
    if not normalized:
        return ""
    labels = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.deleted_at == None,
            AnnotationLabel.normalized_label_value.in_(
                [value.casefold() for value in normalized]
            ),
        )
        .all()
    )
    ids_by_value = {
        str(row.normalized_label_value or "").casefold(): int(row.id) for row in labels
    }
    if all(value.casefold() in ids_by_value for value in normalized):
        return build_label_set_signature(
            ids_by_value[value.casefold()] for value in normalized
        )
    # Legacy jobs may reference labels that predate the independent label
    # table.  Keep those pools stable by hashing the unordered label values.
    return sha256_text(
        canonical_json(sorted({value.casefold() for value in normalized}))
    )


def _label_ids_for_class_names(db: Session, class_names: List[str]) -> List[int]:
    normalized = _normalized_class_names(class_names)
    if not normalized:
        return []
    rows = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.deleted_at == None,
            AnnotationLabel.status == "active",
            AnnotationLabel.normalized_label_value.in_(
                [value.casefold() for value in normalized]
            ),
        )
        .all()
    )
    ids_by_value = {
        str(row.normalized_label_value or "").casefold(): int(row.id) for row in rows
    }
    if not all(value.casefold() in ids_by_value for value in normalized):
        return []
    return [ids_by_value[value.casefold()] for value in normalized]


def _ensure_pool_annotation_contract(
    db: Session,
    *,
    pool_task: Task,
    class_names: List[str],
    owner_id: int,
    backfill_existing_samples: bool,
) -> Tuple[Optional[Any], int]:
    """Keep the internal pool consumable by the current training workflow."""
    label_ids = _label_ids_for_class_names(db, class_names)
    if not label_ids:
        logger.warning(
            "consistent_pool task_id={} cannot build contract; independent labels missing for {}",
            pool_task.id,
            class_names,
        )
        return None, 0

    contract = annotation_contract_service.create_contract(
        db,
        task=pool_task,
        label_ids=label_ids,
        created_by=owner_id,
    )
    if not backfill_existing_samples:
        return contract, 0

    samples = (
        db.query(TaskSample)
        .filter(
            TaskSample.task_id == int(pool_task.id),
            TaskSample.deleted_at == None,
        )
        .order_by(TaskSample.id.asc())
        .all()
    )
    existing_scope_sample_ids = {
        int(row[0])
        for row in db.query(SampleAnnotationScope.sample_id)
        .filter(
            SampleAnnotationScope.contract_id == int(contract.id),
            SampleAnnotationScope.sample_id.in_(
                [int(sample.id) for sample in samples] or [-1]
            ),
        )
        .all()
    }
    backfilled = 0
    for sample in samples:
        if int(sample.id) in existing_scope_sample_ids:
            continue
        try:
            annotation_data = json.loads(sample.data or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            annotation_data = {}
        annotation_contract_service.upsert_sample_scope(
            db,
            sample=sample,
            contract=contract,
            annotation_data=annotation_data,
            state=sample.state,
            reviewed_by=owner_id,
            explicit_negative=False,
        )
        backfilled += 1
    if samples and all(sample.state == SampleState.DONE.value for sample in samples):
        contract.status = "active"
        pool_task.training_ready = True
        db.add(contract)
        db.add(pool_task)
    return contract, backfilled


def _resolve_pool_contract(
    db: Session,
    *,
    job: YoloTrainJob,
    template_task: Task,
    manifest: Dict[str, Any],
) -> Tuple[str, List[str], str]:
    class_names = _normalized_class_names(manifest.get("class_names"))
    if not class_names:
        try:
            metrics = json.loads(job.metrics_summary or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metrics = {}
        class_names = _normalized_class_names(
            metrics.get("classes") if isinstance(metrics, dict) else None
        )
    if not class_names:
        try:
            class_names = _normalized_class_names(
                json.loads(job.class_names_json or "[]")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            class_names = []
    if not class_names:
        class_names = _task_rect_label_values(template_task)

    label_signature = str(getattr(job, "label_set_signature", None) or "").strip()
    order_signature = str(getattr(job, "class_order_signature", None) or "").strip()
    if getattr(job, "training_task_id", None):
        training_task = crud_training_task.get(db, int(job.training_task_id))
        if training_task:
            label_signature = (
                label_signature
                or str(
                    getattr(training_task, "label_set_signature", None) or ""
                ).strip()
            )
            order_signature = (
                order_signature
                or str(
                    getattr(training_task, "class_order_signature", None) or ""
                ).strip()
            )
    label_signature = label_signature or _signature_from_label_values(db, class_names)
    if not label_signature:
        raise ValueError("训练作业没有可用的标签集合，无法确定训练一致数据集")
    return label_signature, class_names, order_signature


def _parse_task_config(config_raw: Any) -> dict:
    if not config_raw:
        return {}
    try:
        data = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _pool_config_marker(
    category: str,
    template_config: Optional[dict] = None,
    *,
    rect_preset_slug: str = "",
    label_set_signature: str = "",
    class_order_signature: str = "",
    class_names: Optional[List[str]] = None,
    dataset_category_id: Optional[int] = None,
    dataset_category_key: str = "",
) -> dict:
    slug = (rect_preset_slug or "").strip()
    if not slug and isinstance(template_config, dict):
        slug = str(template_config.get("defaultRectPresetId") or "").strip()
    marker: Dict[str, Any] = {
        "category": category.strip(),
        "kind": _POOL_KIND,
        "admin_only": True,
        "dataset_category_scope": _category_scope_key(dataset_category_id),
    }
    if dataset_category_id is not None:
        marker["dataset_category_id"] = int(dataset_category_id)
    if dataset_category_key:
        marker["dataset_category_key"] = dataset_category_key.strip()
    if slug:
        marker["rect_preset_slug"] = slug
    if label_set_signature:
        marker["label_set_signature"] = label_set_signature.strip()
    if class_order_signature:
        marker["class_order_signature"] = class_order_signature.strip()
    if class_names:
        marker["class_names"] = _normalized_class_names(class_names)
    return {"consistent_pool": marker}


def _merge_pool_config(
    existing_config: dict,
    category: str,
    *,
    rect_preset_slug: str = "",
    label_set_signature: str = "",
    class_order_signature: str = "",
    class_names: Optional[List[str]] = None,
    dataset_category_id: Optional[int] = None,
    dataset_category_key: str = "",
) -> str:
    merged = dict(existing_config or {})
    merged.update(
        _pool_config_marker(
            category,
            merged,
            rect_preset_slug=rect_preset_slug,
            label_set_signature=label_set_signature,
            class_order_signature=class_order_signature,
            class_names=class_names,
            dataset_category_id=dataset_category_id,
            dataset_category_key=dataset_category_key,
        )
    )
    return json.dumps(merged, ensure_ascii=False)


def _resolve_template_preset_slug(db: Session, template_task: Task) -> str:
    from labelu.internal.application.service.task import (
        _load_preset_classes_by_slug,
        _resolve_task_rect_preset_slug,
    )

    cfg = _parse_task_config(template_task.config)
    slug = str(cfg.get("defaultRectPresetId") or "").strip()
    if slug:
        return slug
    _, preset_classes_by_slug = _load_preset_classes_by_slug(db)
    return _resolve_task_rect_preset_slug(
        template_task,
        preset_classes_by_slug=preset_classes_by_slug,
    )


def _resolve_pool_owner_id(db: Session, fallback_owner_id: int) -> int:
    admin = crud_user.get_user_by_username(db, username=_POOL_OWNER_USERNAME)
    if admin and admin.id:
        return int(admin.id)
    raise RuntimeError(
        f"管理员账号 {_POOL_OWNER_USERNAME} 不存在，不能创建训练一致数据集"
    )


def _ensure_pool_owner(db: Session, task: Task, owner_id: int) -> Task:
    if int(task.created_by or 0) == int(owner_id):
        return task
    crud_task.update(
        db=db,
        db_obj=task,
        obj_in={
            Task.created_by.key: int(owner_id),
            Task.updated_by.key: int(owner_id),
        },
    )
    refreshed = crud_task.get(db=db, task_id=int(task.id))
    return refreshed or task


def _persist_registry_entry(
    *,
    category: str,
    task_id: int,
    label_set_signature: str,
    class_names: List[str],
    dataset_category_id: Optional[int],
    dataset_category_key: str = "",
) -> None:
    cat = category.strip()
    signature = label_set_signature.strip()
    scope_key = _category_scope_key(dataset_category_id)
    pool_registry_key = f"{scope_key}|{signature}"
    with _REGISTRY_LOCK:
        registry = _read_registry()
        registry["version"] = 3
        categories = registry.setdefault("categories", {})
        if not isinstance(categories, dict):
            categories = {}
            registry["categories"] = categories
        categories[scope_key] = {
            **(
                categories.get(scope_key)
                if isinstance(categories.get(scope_key), dict)
                else {}
            ),
            "task_id": int(task_id),
            "name": cat,
            "dataset_category_id": dataset_category_id,
            "dataset_category_key": dataset_category_key.strip() or None,
            "updated_at": _iso_now(),
        }
        if "created_at" not in categories[scope_key]:
            categories[scope_key]["created_at"] = _iso_now()
        pools = registry.setdefault("pools", {})
        if not isinstance(pools, dict):
            pools = {}
            registry["pools"] = pools
        previous = (
            pools.get(pool_registry_key)
            if isinstance(pools.get(pool_registry_key), dict)
            else {}
        )
        pools[pool_registry_key] = {
            **previous,
            "task_id": int(task_id),
            "category_scope_key": scope_key,
            "dataset_category_id": dataset_category_id,
            "dataset_category_key": dataset_category_key.strip() or None,
            "label_set_signature": signature,
            "class_names": _normalized_class_names(class_names),
            "last_category": cat,
            "updated_at": _iso_now(),
        }
        if "created_at" not in pools[pool_registry_key]:
            pools[pool_registry_key]["created_at"] = _iso_now()
        _write_registry(registry)


def _existing_source_keys(db: Session, pool_task_id: int) -> Set[Tuple[int, int]]:
    """已收录的 (source_task_id, source_sample_id) 集合。"""
    keys: Set[Tuple[int, int]] = set()
    page = 0
    size = 500
    while True:
        rows = crud_sample.list_by(
            db=db,
            task_id=pool_task_id,
            after=None,
            before=None,
            page=page,
            size=size,
            sorting=None,
        )
        if not rows:
            break
        for sample in rows:
            meta = _read_pool_meta_from_sample(sample)
            if meta:
                st = meta.get("source_task_id")
                ss = meta.get("source_sample_id")
                if st is not None and ss is not None:
                    keys.add((int(st), int(ss)))
        if len(rows) < size:
            break
        page += 1
    return keys


def _filename_key(filename: str) -> str:
    return Path(str(filename or "")).name.strip().casefold()


def _existing_filename_keys(db: Session, pool_task_id: int) -> Set[str]:
    rows = (
        db.query(TaskAttachment.filename)
        .join(TaskSample, TaskSample.file_id == TaskAttachment.id)
        .filter(
            TaskSample.task_id == int(pool_task_id),
            TaskSample.deleted_at == None,
            TaskAttachment.deleted_at == None,
        )
        .all()
    )
    return {key for (filename,) in rows if (key := _filename_key(str(filename or "")))}


def _read_pool_meta_from_sample(sample: TaskSample) -> Optional[dict]:
    if not sample.data:
        return None
    try:
        outer = json.loads(sample.data)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(outer, dict):
        return None
    meta = outer.get(_POOL_META_KEY)
    return meta if isinstance(meta, dict) else None


def _sanitize_filename(filename: str) -> str:
    base = Path(filename).name
    sanitized = re.sub(r"%", "_pct_", base)
    return re.sub(r'[\\/*?:"<>|#]', "_", sanitized)


def _copy_storage_object(src_key: str, dest_key: str) -> None:
    storage = get_storage_backend()
    if storage.exists(dest_key):
        return
    local_src = storage.get_local_path(src_key)
    if local_src and local_src.is_file():
        dest_local = storage.get_local_path(dest_key)
        if dest_local is None:
            raise RuntimeError("本地存储目标路径解析失败")
        dest_local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_src, dest_local)
        return
    if storage.is_remote:
        client = getattr(storage, "_client", None)
        bucket = getattr(storage, "_bucket", None)
        if client is None or bucket is None:
            raise RuntimeError("S3 存储无法复制附件")
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            client.download_file(bucket, src_key.lstrip("/"), str(tmp_path))
            storage.save_file(tmp_path, dest_key)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return
    raise FileNotFoundError(f"附件不存在: {src_key}")


def _copy_attachment_with_thumbnail(src_path: str, dest_path: str) -> None:
    _copy_storage_object(src_path, dest_path)
    thumb_src = build_thumbnail_key(src_path)
    thumb_dest = build_thumbnail_key(dest_path)
    if get_storage_backend().exists(thumb_src):
        try:
            _copy_storage_object(thumb_src, thumb_dest)
        except Exception:
            local_dest = get_storage_backend().get_local_path(dest_path)
            if local_dest and local_dest.is_file():
                thumb_bytes = create_thumbnail_bytes(local_dest)
                if thumb_bytes:
                    get_storage_backend().save_bytes(thumb_bytes, thumb_dest)


def _inject_pool_meta(sample_data: str, *, meta: dict) -> str:
    try:
        outer = json.loads(sample_data) if sample_data else {}
    except (TypeError, json.JSONDecodeError):
        outer = {}
    if not isinstance(outer, dict):
        outer = {}
    outer[_POOL_META_KEY] = meta
    return json.dumps(outer, ensure_ascii=False)


def _get_task_with_collaborators(db: Session, task_id: int) -> Optional[Task]:
    return (
        db.query(Task)
        .options(joinedload(Task.collaborators))
        .filter(Task.id == task_id, Task.deleted_at == None)
        .first()
    )


def _ensure_pool_task_admin_only(db: Session, pool_task: Task, owner_id: int) -> int:
    """Own the pool by admin and remove every collaborator."""
    pool_full = _get_task_with_collaborators(db, pool_task.id) or pool_task
    removed = len(pool_full.collaborators or [])
    if removed:
        pool_full.collaborators.clear()
    if int(pool_full.created_by or 0) != int(owner_id):
        pool_full.created_by = int(owner_id)
    pool_full.updated_by = int(owner_id)
    db.add(pool_full)
    if removed:
        logger.info(
            "consistent_pool task_id={} removed {} collaborator(s)",
            pool_task.id,
            removed,
        )
    return removed


def repair_pool_task_access(db: Session, pool_task_id: int) -> Dict[str, Any]:
    """Repair a legacy pool so that only the built-in admin can access it."""
    pool_task = _get_task_with_collaborators(db, pool_task_id)
    if not pool_task or pool_task.deleted_at is not None:
        return {"status": "failed", "message": "任务不存在"}
    cfg = _parse_task_config(pool_task.config)
    if cfg.get("consistent_pool", {}).get("kind") != _POOL_KIND:
        return {"status": "skipped", "message": "非训练筛选一致任务"}

    owner_id = _resolve_pool_owner_id(db, int(pool_task.created_by or 0))
    removed = _ensure_pool_task_admin_only(db, pool_task, owner_id)
    return {
        "status": "completed",
        "pool_task_id": pool_task_id,
        "owner_id": owner_id,
        "collaborators_removed": removed,
        "admin_only": True,
    }


def _get_pool_registry(
    db: Session,
    label_set_signature: str,
    *,
    dataset_category_id: Optional[int],
    lock: bool = False,
) -> Optional[YoloConsistentPoolRegistry]:
    scope_key = _category_scope_key(dataset_category_id)
    query = db.query(YoloConsistentPoolRegistry).filter(
        YoloConsistentPoolRegistry.category_scope_key == scope_key,
        YoloConsistentPoolRegistry.label_set_signature == label_set_signature,
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def _claim_pool_registry(
    db: Session,
    label_set_signature: str,
    *,
    dataset_category_id: Optional[int],
) -> YoloConsistentPoolRegistry:
    """Atomically claim one row for a dataset-category and label-set pair."""
    scope_key = _category_scope_key(dataset_category_id)
    existing = _get_pool_registry(
        db,
        label_set_signature,
        dataset_category_id=dataset_category_id,
        lock=True,
    )
    if existing:
        return existing

    try:
        with db.begin_nested():
            claimed = YoloConsistentPoolRegistry(
                category_scope_key=scope_key,
                dataset_category_id=dataset_category_id,
                label_set_signature=label_set_signature,
            )
            db.add(claimed)
            db.flush()
        return claimed
    except IntegrityError:
        # The scoped unique key is the cross-process creation lock.
        existing = _get_pool_registry(
            db,
            label_set_signature,
            dataset_category_id=dataset_category_id,
            lock=True,
        )
        if existing:
            return existing
        raise


def _bind_pool_registry_task(
    db: Session,
    registry: YoloConsistentPoolRegistry,
    task_id: int,
) -> None:
    # A label recreated under a new system-label ID can produce a new
    # signature while the class values still resolve to the same legacy pool.
    # Keep only the current signature binding for that canonical task.
    db.query(YoloConsistentPoolRegistry).filter(
        YoloConsistentPoolRegistry.task_id == int(task_id),
        YoloConsistentPoolRegistry.id != int(registry.id),
    ).delete(synchronize_session=False)
    registry.task_id = int(task_id)
    db.add(registry)
    db.flush()


def _find_or_create_pool_task(
    db: Session,
    *,
    category: str,
    template_task: Task,
    owner_id: int,
    label_set_signature: str,
    class_names: List[str],
    class_order_signature: str = "",
    dataset_category: Optional[DatasetCategory] = None,
) -> Task:
    dataset_category_id = (
        int(dataset_category.id) if dataset_category is not None else None
    )
    scope_key = _category_scope_key(dataset_category_id)
    cat = (
        (
            str(dataset_category.name or "").strip()
            if dataset_category is not None
            else ""
        )
        or category.strip()
        or "未分类"
    )
    name = pool_task_name(cat)
    pool_owner_id = _resolve_pool_owner_id(db, owner_id)
    signature = label_set_signature.strip()
    normalized_classes = _normalized_class_names(class_names)
    registry = _claim_pool_registry(
        db,
        signature,
        dataset_category_id=dataset_category_id,
    )

    existing: Optional[Task] = None
    if registry.task_id:
        existing = crud_task.get(db=db, task_id=int(registry.task_id))
        if not existing:
            registry.task_id = None
            db.add(registry)
            db.flush()

    pools = _read_registry().get("pools", {})
    if not existing and isinstance(pools, dict):
        entry = pools.get(f"{scope_key}|{signature}")
        if isinstance(entry, dict) and entry.get("task_id"):
            existing = crud_task.get(db=db, task_id=int(entry["task_id"]))

    if not existing:
        existing = (
            db.query(Task)
            .filter(
                Task.deleted_at == None,
                Task.source_type == _POOL_SOURCE_TYPE,
                Task.source_external_task_id == signature,
                (
                    Task.dataset_category_id == dataset_category_id
                    if dataset_category_id is not None
                    else Task.dataset_category_id.is_(None)
                ),
            )
            .order_by(Task.id.asc())
            .first()
        )

    if not existing:
        candidates = (
            db.query(Task)
            .filter(
                Task.deleted_at == None,
                Task.config.contains(_POOL_KIND),
            )
            .order_by(Task.id.asc())
            .all()
        )
        expected_set = {value.casefold() for value in normalized_classes}
        for candidate in candidates:
            if candidate.dataset_category_id != dataset_category_id:
                continue
            cfg = _parse_task_config(candidate.config)
            marker = cfg.get("consistent_pool")
            marker = marker if isinstance(marker, dict) else {}
            marker_signature = str(marker.get("label_set_signature") or "").strip()
            if marker_signature == signature:
                existing = candidate
                break
            candidate_set = {
                value.casefold() for value in _task_rect_label_values(candidate)
            }
            if expected_set and candidate_set == expected_set:
                existing = candidate
                break

    if existing:
        cfg = _parse_task_config(existing.config)
        existing_marker = cfg.get("consistent_pool")
        existing_marker = existing_marker if isinstance(existing_marker, dict) else {}
        stored_category = str(existing_marker.get("category") or "").strip()
        effective_category = cat
        if (
            dataset_category is None
            and stored_category
            and not contains_encoding_replacement_marker(stored_category)
        ):
            effective_category = stored_category
        preset_slug = _resolve_template_preset_slug(db, template_task)
        update_values = {
            Task.config.key: _merge_pool_config(
                cfg,
                effective_category,
                rect_preset_slug=preset_slug,
                label_set_signature=signature,
                class_order_signature=class_order_signature,
                class_names=normalized_classes,
                dataset_category_id=dataset_category_id,
                dataset_category_key=(
                    str(dataset_category.category_key or "")
                    if dataset_category is not None
                    else ""
                ),
            ),
            Task.created_by.key: pool_owner_id,
            Task.updated_by.key: pool_owner_id,
            Task.source_type.key: _POOL_SOURCE_TYPE,
            Task.source_system.key: _POOL_SOURCE_SYSTEM,
            Task.source_external_task_id.key: signature,
            Task.dataset_category_id.key: dataset_category_id,
        }
        if contains_encoding_replacement_marker(existing.name):
            update_values[Task.name.key] = pool_task_name(effective_category)
        existing = crud_task.update(
            db=db,
            db_obj=existing,
            obj_in=update_values,
        )
        _ensure_pool_task_admin_only(db, existing, pool_owner_id)
        existing = crud_task.get(db=db, task_id=int(existing.id)) or existing
        _ensure_pool_annotation_contract(
            db,
            pool_task=existing,
            class_names=normalized_classes,
            owner_id=pool_owner_id,
            backfill_existing_samples=True,
        )
        _bind_pool_registry_task(db, registry, int(existing.id))
        return existing

    template_config = _parse_task_config(template_task.config)
    preset_slug = _resolve_template_preset_slug(db, template_task)
    primary_owner = pool_owner_id
    new_task = crud_task.create(
        db=db,
        task=Task(
            name=name,
            description=f"由训练作业自动汇总：{cat} 分类下 GT 与模型预测严格一致的样本。",
            media_type=template_task.media_type or MediaType.IMAGE.value,
            config=_merge_pool_config(
                template_config,
                cat,
                rect_preset_slug=preset_slug,
                label_set_signature=signature,
                class_order_signature=class_order_signature,
                class_names=normalized_classes,
                dataset_category_id=dataset_category_id,
                dataset_category_key=(
                    str(dataset_category.category_key or "")
                    if dataset_category is not None
                    else ""
                ),
            ),
            status=(
                TaskStatus.CONFIGURED.value
                if template_config
                else TaskStatus.DRAFT.value
            ),
            created_by=primary_owner,
            updated_by=primary_owner,
            source_type=_POOL_SOURCE_TYPE,
            source_system=_POOL_SOURCE_SYSTEM,
            source_external_task_id=signature,
            dataset_category_id=dataset_category_id,
        ),
    )
    logger.info(
        "consistent_pool created task_id={} category={!r} name={!r}",
        new_task.id,
        cat,
        name,
    )
    _ensure_pool_annotation_contract(
        db,
        pool_task=new_task,
        class_names=normalized_classes,
        owner_id=pool_owner_id,
        backfill_existing_samples=False,
    )
    _bind_pool_registry_task(db, registry, int(new_task.id))
    return new_task


def _copy_sample_into_pool(
    db: Session,
    *,
    pool_task: Task,
    source_sample: TaskSample,
    source_task_id: int,
    job_id: int,
    owner_id: int,
    label_set_signature: str,
    min_matched_iou: float,
    required_match_iou: float,
) -> Optional[int]:
    att = source_sample.file
    if not att or not att.path:
        return None
    if getattr(att, "data_source_id", None):
        logger.warning(
            "consistent_pool skip external datasource sample_id={} job={}",
            source_sample.id,
            job_id,
        )
        return None

    filename = _sanitize_filename(att.filename or Path(att.path).name)
    dest_rel = str(
        Path(settings.UPLOAD_DIR)
        / str(pool_task.id)
        / f"cp_{source_sample.id}_{filename}"
    ).replace("\\", "/")

    try:
        _copy_attachment_with_thumbnail(att.path, dest_rel)
    except Exception as exc:
        logger.warning(
            "consistent_pool copy file failed sample_id={} job={}: {}",
            source_sample.id,
            job_id,
            exc,
        )
        return None

    meta = {
        "source_task_id": source_task_id,
        "source_sample_id": source_sample.id,
        "source_job_id": job_id,
        "source_filename": filename,
        "label_set_signature": label_set_signature,
        "min_matched_iou": float(min_matched_iou),
        "required_match_iou": float(required_match_iou),
        "matched_at": _iso_now(),
    }
    data_json = _inject_pool_meta(source_sample.data or "{}", meta=meta)

    pool_locked = crud_task.get(db=db, task_id=pool_task.id, lock=True)
    if not pool_locked:
        return None
    new_att = crud_attachment.create(
        db=db,
        attachment=TaskAttachment(
            filename=filename,
            url="",
            path=dest_rel,
            task_id=pool_locked.id,
            created_by=owner_id,
            updated_by=owner_id,
        ),
    )
    inner_id = int(pool_locked.last_sample_inner_id or 0) + 1
    new_sample = TaskSample(
        inner_id=inner_id,
        task_id=pool_locked.id,
        file_id=new_att.id,
        created_by=owner_id,
        updated_by=owner_id,
        data=data_json,
        state=SampleState.DONE.value,
        annotated_count=int(source_sample.annotated_count or 0),
    )
    crud_sample.batch(db=db, samples=[new_sample])
    crud_task.update(
        db=db,
        db_obj=pool_locked,
        obj_in={
            Task.last_sample_inner_id.key: inner_id,
            Task.status.key: (
                TaskStatus.IMPORTED.value
                if pool_locked.status == TaskStatus.DRAFT.value
                else pool_locked.status
            ),
            Task.training_ready.key: True,
        },
    )
    db.flush()
    if not new_sample.id:
        return None
    contract = annotation_contract_service.current_contract(db, int(pool_locked.id))
    if contract is not None:
        try:
            annotation_data = json.loads(data_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            annotation_data = {}
        annotation_contract_service.upsert_sample_scope(
            db,
            sample=new_sample,
            contract=contract,
            annotation_data=annotation_data,
            state=SampleState.DONE.value,
            reviewed_by=owner_id,
            explicit_negative=False,
        )
    return int(new_sample.id)


def sync_consistent_pool_from_job(job_id: int) -> Dict[str, Any]:
    with _SYNC_LOCK:
        return _sync_consistent_pool_from_job_locked(job_id)


def _sync_consistent_pool_from_job_locked(job_id: int) -> Dict[str, Any]:
    """从训练作业标注对比 manifest 同步严格一致样本到分类池任务。"""
    if not settings.YOLO_CONSISTENT_POOL_ENABLED:
        return {"status": "disabled", "job_id": job_id}

    from labelu.internal.common.db import session_scope

    report: Dict[str, Any] = {
        "status": "skipped",
        "job_id": job_id,
        "started_at": _iso_now(),
    }

    try:
        with session_scope() as db:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if not job or job.status != YoloTrainStatus.COMPLETED.value:
                report.update({"status": "skipped", "message": "训练作业未完成"})
                _write_sync_report(job_id, report)
                return report
            strategy_row = crud_strategy_task_integration.get_by_derived_train_job_id(
                db,
                derived_train_job_id=int(job.id),
            )
            if (
                strategy_row
                and str(strategy_row.task_type or "").strip() == "model_capability"
            ):
                report.update(
                    {
                        "status": "skipped",
                        "message": "专家回流模型能力优化训练不生成训练筛选一致任务",
                        "external_task_id": strategy_row.external_task_id,
                        "task_type": strategy_row.task_type,
                    }
                )
                _write_sync_report(job_id, report)
                return report
            category = (job.category or "").strip()
            if not category:
                category = f"训练作业 #{int(job.id)}"

            template_task = _get_task_with_collaborators(db, int(job.task_id))
            if not template_task:
                report.update({"status": "failed", "message": "找不到训练来源任务"})
                _write_sync_report(job_id, report)
                return report

            manifest = _load_manifest(job_id)
            items = (
                manifest.get("items") if isinstance(manifest.get("items"), list) else []
            )
            if not items:
                report.update(
                    {"status": "skipped", "message": "标注对比 manifest 不存在"}
                )
                _write_sync_report(job_id, report)
                return report

            label_signature, class_names, order_signature = _resolve_pool_contract(
                db,
                job=job,
                template_task=template_task,
                manifest=manifest,
            )
            ok_items = [
                it
                for it in items
                if isinstance(it, dict) and is_consistent_manifest_item(it)
            ]
            required_match_iou = float(settings.YOLO_CONSISTENT_POOL_MATCH_IOU)
            if not ok_items:
                report.update(
                    {
                        "status": "skipped",
                        "finished_at": _iso_now(),
                        "message": "本次训练没有达到严格一致标准的图片",
                        "category": category,
                        "label_set_signature": label_signature,
                        "class_order_signature": order_signature or None,
                        "class_names": class_names,
                        "total_manifest_items": len(items),
                        "matched_ok_with_boxes": 0,
                        "required_match_iou": required_match_iou,
                    }
                )
                _write_sync_report(job_id, report)
                return report

            source_groups: Dict[str, Dict[str, Any]] = {}
            skipped_missing = 0
            source_task_cache: Dict[int, Optional[Task]] = {}
            category_cache: Dict[int, Optional[DatasetCategory]] = {}
            for item in ok_items:
                source_task_id = int(item.get("task_id") or job.task_id)
                source_sample_id = item.get("sample_id")
                if source_sample_id is None:
                    skipped_missing += 1
                    continue
                source_sample = crud_sample.get(db=db, sample_id=int(source_sample_id))
                if (
                    not source_sample
                    or source_sample.deleted_at is not None
                    or int(source_sample.task_id) != source_task_id
                ):
                    skipped_missing += 1
                    continue
                if source_task_id not in source_task_cache:
                    source_task_cache[source_task_id] = crud_task.get(
                        db=db, task_id=source_task_id
                    )
                source_task = source_task_cache[source_task_id]
                if not source_task:
                    skipped_missing += 1
                    continue
                source_category_id = (
                    int(source_task.dataset_category_id)
                    if source_task.dataset_category_id is not None
                    else None
                )
                if (
                    source_category_id is not None
                    and source_category_id not in category_cache
                ):
                    category_cache[source_category_id] = _active_dataset_category(
                        db, source_category_id
                    )
                source_category = (
                    category_cache.get(source_category_id)
                    if source_category_id is not None
                    else None
                )
                effective_category_id = (
                    int(source_category.id) if source_category is not None else None
                )
                scope_key = _category_scope_key(effective_category_id)
                group = source_groups.setdefault(
                    scope_key,
                    {
                        "dataset_category": source_category,
                        "items": [],
                        "source_task_ids": set(),
                    },
                )
                group["items"].append((item, source_sample, source_task_id))
                group["source_task_ids"].add(source_task_id)

            if not source_groups:
                report.update(
                    {
                        "status": "skipped",
                        "finished_at": _iso_now(),
                        "message": "严格一致图片无法追溯到有效来源任务",
                        "category": category,
                        "label_set_signature": label_signature,
                        "matched_ok_with_boxes": len(ok_items),
                        "skipped_missing": skipped_missing,
                    }
                )
                _write_sync_report(job_id, report)
                return report

            skipped_zero_box = sum(
                1
                for it in items
                if isinstance(it, dict)
                and int(it.get("fn") or 0) == 0
                and int(it.get("fp") or 0) == 0
                and "ok" in (it.get("issues") or [])
                and int(it.get("gt") or 0) == 0
            )
            skipped_low_iou = sum(
                1
                for it in items
                if isinstance(it, dict)
                and int(it.get("fn") or 0) == 0
                and int(it.get("fp") or 0) == 0
                and "ok" in (it.get("issues") or [])
                and int(it.get("gt") or 0) > 0
                and not is_consistent_manifest_item(it)
            )
            pool_reports: List[Dict[str, Any]] = []
            total_added = 0
            total_duplicate = 0
            total_collaborators_removed = 0

            for scope_key, group in source_groups.items():
                dataset_category = group["dataset_category"]
                dataset_category_id = (
                    int(dataset_category.id) if dataset_category is not None else None
                )
                category_name = (
                    str(dataset_category.name or "").strip()
                    if dataset_category is not None
                    else "未分类"
                )
                with begin_transaction(db):
                    pool_task = _find_or_create_pool_task(
                        db=db,
                        category=category_name,
                        dataset_category=dataset_category,
                        template_task=template_task,
                        owner_id=int(job.created_by or template_task.created_by),
                        label_set_signature=label_signature,
                        class_names=class_names,
                        class_order_signature=order_signature,
                    )
                    pool_owner_id = _resolve_pool_owner_id(
                        db,
                        int(
                            pool_task.created_by
                            or job.created_by
                            or template_task.created_by
                        ),
                    )
                    collaborators_removed = _ensure_pool_task_admin_only(
                        db, pool_task, pool_owner_id
                    )
                pool_task_id = int(pool_task.id)

                with begin_transaction(db):
                    pool_task = crud_task.get(db=db, task_id=pool_task_id, lock=True)
                    if not pool_task:
                        raise RuntimeError("一致池登记已创建，但任务不存在")
                    existing_filenames = _existing_filename_keys(db, pool_task.id)
                    added = 0
                    skipped_duplicate = 0
                    group_missing = 0
                    for item, source_sample, source_task_id in group["items"]:
                        filename_key = _filename_key(
                            str(
                                getattr(
                                    getattr(source_sample, "file", None),
                                    "filename",
                                    "",
                                )
                                or Path(
                                    str(
                                        getattr(
                                            getattr(source_sample, "file", None),
                                            "path",
                                            "",
                                        )
                                        or ""
                                    )
                                ).name
                            )
                        )
                        if not filename_key:
                            group_missing += 1
                            continue
                        if filename_key in existing_filenames:
                            skipped_duplicate += 1
                            continue
                        new_id = _copy_sample_into_pool(
                            db=db,
                            pool_task=pool_task,
                            source_sample=source_sample,
                            source_task_id=source_task_id,
                            job_id=job_id,
                            owner_id=pool_owner_id,
                            label_set_signature=label_signature,
                            min_matched_iou=float(item.get("min_matched_iou")),
                            required_match_iou=required_match_iou,
                        )
                        if new_id:
                            added += 1
                            existing_filenames.add(filename_key)
                        else:
                            group_missing += 1
                    pool_report = {
                        "category_scope_key": scope_key,
                        "dataset_category_id": dataset_category_id,
                        "dataset_category_key": (
                            str(dataset_category.category_key or "")
                            if dataset_category is not None
                            else None
                        ),
                        "dataset_category_name": category_name,
                        "source_task_ids": sorted(group["source_task_ids"]),
                        "pool_task_id": int(pool_task.id),
                        "pool_task_name": pool_task.name,
                        "pool_task_sample_count": crud_sample.task_list_sample_total(
                            db=db, task_id=pool_task.id
                        ),
                        "matched_ok_with_boxes": len(group["items"]),
                        "added": added,
                        "skipped_duplicate": skipped_duplicate,
                        "skipped_missing": group_missing,
                        "collaborators_removed": collaborators_removed,
                    }
                    pool_reports.append(pool_report)
                    total_added += added
                    total_duplicate += skipped_duplicate
                    skipped_missing += group_missing
                    total_collaborators_removed += collaborators_removed

                _persist_registry_entry(
                    category=category_name,
                    task_id=pool_task_id,
                    label_set_signature=label_signature,
                    class_names=class_names,
                    dataset_category_id=dataset_category_id,
                    dataset_category_key=(
                        str(dataset_category.category_key or "")
                        if dataset_category is not None
                        else ""
                    ),
                )
                logger.info(
                    "consistent_pool job={} dataset_category_id={} pool_task={} added={} dup={}",
                    job_id,
                    dataset_category_id,
                    pool_task_id,
                    added,
                    skipped_duplicate,
                )

            report.update(
                {
                    "status": "completed",
                    "finished_at": _iso_now(),
                    "category": category,
                    "label_set_signature": label_signature,
                    "class_order_signature": order_signature or None,
                    "class_names": class_names,
                    "pool_task_ids": [row["pool_task_id"] for row in pool_reports],
                    "pools": pool_reports,
                    "total_manifest_items": len(items),
                    "matched_ok_with_boxes": len(ok_items),
                    "required_match_iou": required_match_iou,
                    "added": total_added,
                    "skipped_duplicate": total_duplicate,
                    "skipped_missing": skipped_missing,
                    "skipped_zero_box_ok": skipped_zero_box,
                    "skipped_low_iou_or_legacy": skipped_low_iou,
                    "collaborators_removed": total_collaborators_removed,
                    "admin_only": True,
                    "deduplicate_by": "filename_casefold_per_dataset_category",
                }
            )
            if len(pool_reports) == 1:
                report.update(
                    {
                        "pool_task_id": pool_reports[0]["pool_task_id"],
                        "pool_task_name": pool_reports[0]["pool_task_name"],
                        "pool_task_sample_count": pool_reports[0][
                            "pool_task_sample_count"
                        ],
                        "dataset_category_id": pool_reports[0]["dataset_category_id"],
                        "dataset_category_name": pool_reports[0][
                            "dataset_category_name"
                        ],
                    }
                )
    except Exception as exc:
        logger.exception("consistent_pool sync failed job={}", job_id)
        report.update(
            {"status": "failed", "finished_at": _iso_now(), "message": str(exc)[:500]}
        )

    _write_sync_report(job_id, report)
    return report


def schedule_consistent_pool_sync_after_compare(job_id: int) -> None:
    """保留旧调用入口，但 PT 训练完成后不再自动创建一致数据集任务。"""
    _ = job_id
    logger.debug("consistent_pool auto sync is disabled; no dataset task is created")


def _pool_task_id_for_category(category: str) -> Optional[int]:
    cat = (category or "").strip()
    if not cat:
        return None

    registry = _read_registry()
    pools = registry.get("pools", {})
    matched_pool_ids: Set[int] = set()
    if isinstance(pools, dict):
        for scope_signature, reg_entry in pools.items():
            if not isinstance(reg_entry, dict) or not reg_entry.get("task_id"):
                continue
            aliases = {
                str(scope_signature or "").split("|", 1)[0].strip(),
                str(reg_entry.get("category_scope_key") or "").strip(),
                str(reg_entry.get("dataset_category_key") or "").strip(),
                str(reg_entry.get("last_category") or "").strip(),
            }
            if cat in aliases:
                matched_pool_ids.add(int(reg_entry["task_id"]))
        if matched_pool_ids:
            # A legacy report only stores the category name. Once a category
            # contains multiple label contracts there is no honest way to
            # infer which pool it belongs to, so do not display a wrong count.
            return next(iter(matched_pool_ids)) if len(matched_pool_ids) == 1 else None

    categories = registry.get("categories", {})
    if not isinstance(categories, dict):
        return None
    for scope_or_name, reg_entry in categories.items():
        if not isinstance(reg_entry, dict) or not reg_entry.get("task_id"):
            continue
        if scope_or_name == cat or str(reg_entry.get("name") or "").strip() == cat:
            return int(reg_entry["task_id"])
    return None


def pool_task_sample_count(db: Session, *, category: str) -> Optional[int]:
    """筛选池任务样本数（与任务列表卡片 total 口径一致）。"""
    task_id = _pool_task_id_for_category(category)
    if not task_id:
        return None
    task = crud_task.get(db=db, task_id=task_id)
    if not task or task.deleted_at is not None:
        return None
    return crud_sample.task_list_sample_total(db=db, task_id=task_id)


def _attach_live_pool_sample_count(db: Session, report: Dict[str, Any]) -> None:
    pools = report.get("pools")
    if isinstance(pools, list):
        for pool in pools:
            if not isinstance(pool, dict) or not pool.get("pool_task_id"):
                continue
            pool["pool_task_sample_count"] = crud_sample.task_list_sample_total(
                db=db, task_id=int(pool["pool_task_id"])
            )
        if len(pools) == 1:
            report["pool_task_sample_count"] = pools[0].get("pool_task_sample_count")
        return
    pool_id = report.get("pool_task_id")
    if pool_id:
        report["pool_task_sample_count"] = crud_sample.task_list_sample_total(
            db=db, task_id=int(pool_id)
        )
        return
    category = str(report.get("category") or "").strip()
    if category:
        count = pool_task_sample_count(db=db, category=category)
        if count is not None:
            report["pool_task_sample_count"] = count


def read_sync_report(job_id: int) -> Dict[str, Any]:
    path = _sync_report_path(job_id)
    if not path.is_file():
        return {"status": "idle", "job_id": job_id}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"status": "idle", "job_id": job_id}
    except (OSError, json.JSONDecodeError):
        return {"status": "idle", "job_id": job_id}
    if data.get("pool_task_id") or data.get("pool_task_ids") or data.get("category"):
        from labelu.internal.common.db import session_scope

        try:
            with session_scope() as db:
                _attach_live_pool_sample_count(db, data)
        except Exception:
            pass
    return data


def _pool_task_signature(db: Session, task: Task) -> Tuple[str, List[str], str]:
    config = _parse_task_config(task.config)
    marker = config.get("consistent_pool")
    marker = marker if isinstance(marker, dict) else {}
    class_names = _normalized_class_names(marker.get("class_names"))
    if not class_names:
        class_names = _task_rect_label_values(task)
    signature = str(marker.get("label_set_signature") or "").strip()
    if not signature and str(task.source_type or "").strip() == _POOL_SOURCE_TYPE:
        signature = str(task.source_external_task_id or "").strip()
    signature = signature or _signature_from_label_values(db, class_names)
    category = str(marker.get("category") or "").strip()
    return signature, class_names, category


def reconcile_consistent_pools(db: Session, *, apply: bool = False) -> Dict[str, Any]:
    """Preview or merge duplicate pools within one dataset-category scope."""
    with _SYNC_LOCK:
        tasks = (
            db.query(Task)
            .options(joinedload(Task.collaborators))
            .filter(
                Task.deleted_at == None,
                (Task.source_type == _POOL_SOURCE_TYPE)
                | Task.config.contains(_POOL_KIND),
            )
            .order_by(Task.id.asc())
            .all()
        )
        grouped: Dict[Tuple[str, str], List[Task]] = {}
        group_classes: Dict[Tuple[str, str], List[str]] = {}
        group_categories: Dict[Tuple[str, str], List[str]] = {}
        unresolved: List[int] = []
        for task in tasks:
            signature, class_names, category = _pool_task_signature(db, task)
            if not signature:
                unresolved.append(int(task.id))
                continue
            group_key = (
                _category_scope_key(task.dataset_category_id),
                signature,
            )
            grouped.setdefault(group_key, []).append(task)
            if class_names and not group_classes.get(group_key):
                group_classes[group_key] = class_names
            if category:
                group_categories.setdefault(group_key, []).append(category)

        admin = crud_user.get_user_by_username(db, username=_POOL_OWNER_USERNAME)
        if apply and (not admin or not admin.id):
            raise RuntimeError(
                f"管理员账号 {_POOL_OWNER_USERNAME} 不存在，不能修复一致数据集"
            )
        admin_id = int(admin.id) if admin and admin.id else None

        reports: List[Dict[str, Any]] = []
        total_moved = 0
        total_hidden = 0
        total_duplicate_rows_removed = 0
        total_collaborators_removed = 0
        total_collaborators_to_remove = 0
        total_metadata_repairs = 0
        for group_key, pool_tasks in grouped.items():
            scope_key, signature = group_key
            canonical = pool_tasks[0]
            dataset_category = _active_dataset_category(
                db, canonical.dataset_category_id
            )
            dataset_category_id = (
                int(dataset_category.id) if dataset_category is not None else None
            )
            filename_sets = {
                int(task.id): _existing_filename_keys(db, int(task.id))
                for task in pool_tasks
            }
            union_names: Set[str] = set()
            total_rows = 0
            for task in pool_tasks:
                names = filename_sets[int(task.id)]
                union_names.update(names)
                total_rows += crud_sample.task_list_sample_total(
                    db=db, task_id=int(task.id)
                )
            canonical_names = set(filename_sets[int(canonical.id)])
            duplicate_names = max(0, total_rows - len(union_names))
            moved = 0
            duplicate_rows_removed = 0
            collaborators_removed = 0
            collaborators_to_remove = sum(
                len(task.collaborators or []) for task in pool_tasks
            )
            metadata_repair_task_ids = [
                int(task.id)
                for task in pool_tasks
                if str(task.source_type or "") != _POOL_SOURCE_TYPE
                or str(task.source_external_task_id or "") != signature
                or (admin_id is not None and int(task.created_by or 0) != admin_id)
                or not task.current_annotation_contract_id
            ]
            total_collaborators_to_remove += collaborators_to_remove
            total_metadata_repairs += len(metadata_repair_task_ids)
            contract_id = None
            contract_backfilled_samples = 0

            if apply and admin_id is not None:
                cfg = _parse_task_config(canonical.config)
                marker = cfg.get("consistent_pool")
                marker = marker if isinstance(marker, dict) else {}
                category = str(marker.get("category") or "").strip()
                if not category:
                    category = (group_categories.get(group_key) or [canonical.name])[0]
                canonical = crud_task.update(
                    db=db,
                    db_obj=canonical,
                    obj_in={
                        Task.config.key: _merge_pool_config(
                            cfg,
                            category,
                            rect_preset_slug=str(marker.get("rect_preset_slug") or ""),
                            label_set_signature=signature,
                            class_order_signature=str(
                                marker.get("class_order_signature") or ""
                            ),
                            class_names=group_classes.get(group_key) or [],
                            dataset_category_id=dataset_category_id,
                            dataset_category_key=(
                                str(dataset_category.category_key or "")
                                if dataset_category is not None
                                else ""
                            ),
                        ),
                        Task.created_by.key: admin_id,
                        Task.updated_by.key: admin_id,
                        Task.source_type.key: _POOL_SOURCE_TYPE,
                        Task.source_system.key: _POOL_SOURCE_SYSTEM,
                        Task.source_external_task_id.key: signature,
                        Task.dataset_category_id.key: dataset_category_id,
                    },
                )
                collaborators_removed += _ensure_pool_task_admin_only(
                    db, canonical, admin_id
                )
                canonical_samples = (
                    db.query(TaskSample)
                    .options(joinedload(TaskSample.file))
                    .filter(
                        TaskSample.task_id == int(canonical.id),
                        TaskSample.deleted_at == None,
                    )
                    .order_by(TaskSample.id.asc())
                    .all()
                )
                seen_canonical_names: Set[str] = set()
                duplicate_sample_ids: List[int] = []
                duplicate_attachment_ids: List[int] = []
                for sample in canonical_samples:
                    attachment = sample.file
                    filename_key = _filename_key(
                        str(
                            getattr(attachment, "filename", "")
                            or Path(str(getattr(attachment, "path", "") or "")).name
                        )
                    )
                    if not filename_key or filename_key not in seen_canonical_names:
                        if filename_key:
                            seen_canonical_names.add(filename_key)
                        continue
                    duplicate_sample_ids.append(int(sample.id))
                    if attachment and attachment.id:
                        duplicate_attachment_ids.append(int(attachment.id))
                if duplicate_sample_ids:
                    crud_sample.delete(db, duplicate_sample_ids)
                    duplicate_rows_removed = len(duplicate_sample_ids)
                if duplicate_attachment_ids:
                    crud_attachment.delete(db, duplicate_attachment_ids)
                next_inner_id = int(canonical.last_sample_inner_id or 0)
                for duplicate in pool_tasks[1:]:
                    duplicate_samples = (
                        db.query(TaskSample)
                        .options(joinedload(TaskSample.file))
                        .filter(
                            TaskSample.task_id == int(duplicate.id),
                            TaskSample.deleted_at == None,
                        )
                        .order_by(TaskSample.id.asc())
                        .all()
                    )
                    for sample in duplicate_samples:
                        attachment = sample.file
                        filename_key = _filename_key(
                            str(
                                getattr(attachment, "filename", "")
                                or Path(str(getattr(attachment, "path", "") or "")).name
                            )
                        )
                        if not filename_key or filename_key in canonical_names:
                            continue
                        next_inner_id += 1
                        sample.task_id = int(canonical.id)
                        sample.inner_id = next_inner_id
                        sample.created_by = admin_id
                        sample.updated_by = admin_id
                        if attachment:
                            attachment.task_id = int(canonical.id)
                            attachment.created_by = admin_id
                            attachment.updated_by = admin_id
                            db.add(attachment)
                        db.add(sample)
                        canonical_names.add(filename_key)
                        moved += 1
                    collaborators_removed += _ensure_pool_task_admin_only(
                        db, duplicate, admin_id
                    )
                    crud_task.delete(db, duplicate)
                    total_hidden += 1
                canonical.last_sample_inner_id = next_inner_id
                db.add(canonical)
                contract, contract_backfilled_samples = (
                    _ensure_pool_annotation_contract(
                        db,
                        pool_task=canonical,
                        class_names=group_classes.get(group_key) or [],
                        owner_id=admin_id,
                        backfill_existing_samples=True,
                    )
                )
                contract_id = int(contract.id) if contract is not None else None
                total_moved += moved
                total_duplicate_rows_removed += duplicate_rows_removed
                total_collaborators_removed += collaborators_removed
                registry = _claim_pool_registry(
                    db,
                    signature,
                    dataset_category_id=dataset_category_id,
                )
                _bind_pool_registry_task(db, registry, int(canonical.id))
                for category in group_categories.get(group_key) or [category]:
                    _persist_registry_entry(
                        category=category,
                        task_id=int(canonical.id),
                        label_set_signature=signature,
                        class_names=group_classes.get(group_key) or [],
                        dataset_category_id=dataset_category_id,
                        dataset_category_key=(
                            str(dataset_category.category_key or "")
                            if dataset_category is not None
                            else ""
                        ),
                    )

            reports.append(
                {
                    "label_set_signature": signature,
                    "category_scope_key": scope_key,
                    "dataset_category_id": dataset_category_id,
                    "dataset_category_name": (
                        str(dataset_category.name or "")
                        if dataset_category is not None
                        else None
                    ),
                    "class_names": group_classes.get(group_key) or [],
                    "canonical_task_id": int(canonical.id),
                    "task_ids": [int(task.id) for task in pool_tasks],
                    "task_names": [str(task.name) for task in pool_tasks],
                    "pool_count": len(pool_tasks),
                    "sample_rows_before": total_rows,
                    "unique_filename_count": len(union_names),
                    "duplicate_filename_rows": duplicate_names,
                    "would_move_unique_samples": len(
                        union_names - filename_sets[int(canonical.id)]
                    ),
                    "moved_unique_samples": moved,
                    "duplicate_rows_removed": duplicate_rows_removed,
                    "collaborators_to_remove": collaborators_to_remove,
                    "collaborators_removed": collaborators_removed,
                    "metadata_repair_task_ids": metadata_repair_task_ids,
                    "annotation_contract_id": contract_id,
                    "contract_backfilled_samples": contract_backfilled_samples,
                }
            )

        return {
            "status": "completed" if apply else "preview",
            "applied": bool(apply),
            "group_count": len(reports),
            "duplicate_group_count": sum(1 for row in reports if row["pool_count"] > 1),
            "unresolved_task_ids": unresolved,
            "moved_unique_samples": total_moved,
            "duplicate_rows_removed": total_duplicate_rows_removed,
            "hidden_duplicate_tasks": total_hidden,
            "collaborators_to_remove": total_collaborators_to_remove,
            "collaborators_removed": total_collaborators_removed,
            "metadata_repair_task_count": total_metadata_repairs,
            "groups": reports,
        }


def list_consistent_pools(db: Session, *, current_user: User) -> List[Dict[str, Any]]:
    """List canonical consistent pools for the built-in administrator only."""
    if not is_consistent_pool_admin(current_user):
        return []

    tasks = (
        db.query(Task)
        .filter(
            Task.deleted_at == None,
            (Task.source_type == _POOL_SOURCE_TYPE) | Task.config.contains(_POOL_KIND),
        )
        .order_by(Task.id.asc())
        .all()
    )
    canonical_by_scope: Dict[Tuple[str, str], Task] = {}
    classes_by_scope: Dict[Tuple[str, str], List[str]] = {}
    category_by_scope: Dict[Tuple[str, str], str] = {}
    for task in tasks:
        config = _parse_task_config(task.config)
        marker = config.get("consistent_pool")
        marker = marker if isinstance(marker, dict) else {}
        class_names = _normalized_class_names(marker.get("class_names"))
        if not class_names:
            class_names = _task_rect_label_values(task)
        signature = str(marker.get("label_set_signature") or "").strip()
        if not signature and str(task.source_type or "") == _POOL_SOURCE_TYPE:
            signature = str(task.source_external_task_id or "").strip()
        signature = signature or _signature_from_label_values(db, class_names)
        scope_signature = (
            _category_scope_key(task.dataset_category_id),
            signature,
        )
        if not signature or scope_signature in canonical_by_scope:
            continue
        canonical_by_scope[scope_signature] = task
        classes_by_scope[scope_signature] = class_names
        category_by_scope[scope_signature] = str(marker.get("category") or "").strip()

    rows: List[Dict[str, Any]] = []
    for scope_signature, task in canonical_by_scope.items():
        scope_key, signature = scope_signature
        dataset_category = _active_dataset_category(db, task.dataset_category_id)
        rows.append(
            {
                "category": category_by_scope.get(scope_signature) or task.name,
                "category_scope_key": scope_key,
                "dataset_category_id": (
                    int(dataset_category.id) if dataset_category is not None else None
                ),
                "dataset_category_key": (
                    str(dataset_category.category_key or "")
                    if dataset_category is not None
                    else None
                ),
                "dataset_category_name": (
                    str(dataset_category.name or "")
                    if dataset_category is not None
                    else None
                ),
                "task_id": task.id,
                "task_name": task.name,
                "sample_count": crud_sample.task_list_sample_total(
                    db=db, task_id=task.id
                ),
                "label_set_signature": signature,
                "class_names": classes_by_scope.get(scope_signature) or [],
                "admin_only": True,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "created_at": task.created_at.isoformat() if task.created_at else None,
            }
        )
    rows.sort(key=lambda r: int(r.get("task_id") or 0))
    return rows
