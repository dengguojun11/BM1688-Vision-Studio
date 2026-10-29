import json
import hashlib
import re
import shutil
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.application.service.yolo_bm1688_export import _export_dir_for_row
from labelu.internal.application.service.attachment import (
    _materialize_datasource_attachment_to_cache,
    resolve_attachment_local_path,
)
from labelu.internal.application.service.yolo_dataset import (
    _rect_lines_for_sample,
    build_yolo_rect_class_names,
    merge_scene_attrs,
    write_voc_xml_with_scene,
)
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_contract import class_order_signature


def edge_validation_job_root(*, export_id: int, job_id: int) -> Path:
    return _export_dir_for_row(export_id) / "manual_edge_validation" / f"job-{int(job_id)}"


def clear_job_pack(*, export_id: int, job_id: int) -> None:
    root = edge_validation_job_root(export_id=export_id, job_id=job_id)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _safe_eval_filename(index: int, src_name: str) -> str:
    path = Path(src_name or "")
    suffix = path.suffix.lower() or ".jpg"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem or "")
    stem = stem.strip("._-") or "image"
    return f"{index:04d}_{stem}{suffix}"


def _decode_json(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _class_names_from_yaml(path: Path) -> list[str]:
    """Read YOLO names in numeric order without re-sorting by label text."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names: dict[int, str] = {}
    in_names = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("names:"):
            in_names = True
            # Support the compact YAML form: names: [cat, dog]
            value = line.split(":", 1)[1].strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    import yaml

                    parsed = yaml.safe_load(value)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed]
                except Exception:
                    pass
            continue
        if not in_names:
            continue
        match = re.match(r"^(\d+)\s*:\s*(.+)$", line)
        if match:
            names[int(match.group(1))] = match.group(2).strip().strip("'\"")
            continue
        if line and not line.startswith("#"):
            in_names = False
    return [names[index] for index in sorted(names)]


def _class_names_from_training_artifacts(job) -> list[str]:
    """Recover the exact class-id order used by the completed training job.

    Older jobs did not persist ``class_names_json``. Their dataset manifest,
    metrics summary, or generated data.yaml still contains the authoritative
    order. This is intentionally preferred over task label UI order.
    """
    for raw, key in (
        (getattr(job, "dataset_manifest_json", None), "class_names"),
        (getattr(job, "metrics_summary", None), "classes"),
    ):
        payload = _decode_json(raw, {})
        values = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(values, list) and values:
            return [str(value) for value in values]

    weights_path = str(getattr(job, "weights_path", None) or "").strip()
    if weights_path:
        weights = Path(weights_path)
        dataset_root = weights.parent.parent / "dataset"
        for filename in ("data.yaml", "dataset.yaml"):
            values = _class_names_from_yaml(dataset_root / filename)
            if values:
                return values
        class_file = dataset_root / "class_names.txt"
        if class_file.is_file():
            try:
                values = [
                    line.strip()
                    for line in class_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                if values:
                    return values
            except OSError:
                pass
    return []


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _manifest_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def edge_validation_pack_ready(row) -> bool:
    """Return whether the committed sample pack can be consumed by a worker.

    Automatic jobs must carry the explicit READY marker. Existing manual jobs
    created before the marker was introduced remain compatible when their
    persisted manifest is present.
    """
    try:
        payload = json.loads(getattr(row, "result_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    pack = payload.get("pack") if isinstance(payload, dict) else None
    if not isinstance(pack, dict):
        return not (
            getattr(row, "auto_training_round_id", None) is not None
            or getattr(row, "auto_training_plan_id", None) is not None
        )
    marker = str(pack.get("state") or "").upper()
    is_automatic = (
        getattr(row, "auto_training_round_id", None) is not None
        or getattr(row, "auto_training_plan_id", None) is not None
    )
    if is_automatic and marker != "READY":
        return False
    if marker and marker != "READY":
        return False
    if isinstance(pack, dict) and marker == "READY":
        # Do not allocate a device to a package that the SSH consumer would
        # reject. Historical manual jobs without a pack remain compatible
        # below, but an explicit pack must pass the same strict gate.
        return edge_validation_pack_committed(row)
    manifest_path = str(pack.get("manifest_path") or "").strip()
    if not manifest_path:
        return False
    return Path(manifest_path).is_file()


def edge_validation_pack_committed(row) -> bool:
    """严格的 SSH 消费门禁：只有显式 READY 且清单存在才允许执行。

    调度器仍可把历史手工任务放回队列以兼容旧数据，但执行器不能把
    ``PENDING/PREPARING/FAILED`` 或缺少清单的任务送到边端。
    """
    try:
        payload = json.loads(getattr(row, "result_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    pack = payload.get("pack") if isinstance(payload, dict) else None
    if not isinstance(pack, dict) or str(pack.get("state") or "").upper() != "READY":
        return False
    manifest_path = str(pack.get("manifest_path") or "").strip()
    if not manifest_path:
        return False
    manifest = Path(manifest_path)
    try:
        if not manifest.is_file() or manifest.stat().st_size <= 0:
            return False
        # 只要有一张选中图片没有物化/复制成功，就不能把部分包送到
        # BM1688；否则真机指标会被误认为覆盖了完整验收集。
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        blocking_skipped_count = payload.get("blocking_skipped_count") if isinstance(payload, dict) else None
        if blocking_skipped_count is None:
            skipped = payload.get("skipped") if isinstance(payload, dict) else None
            blocking_skipped_count = len(skipped) if isinstance(skipped, list) else 0
        try:
            if int(blocking_skipped_count or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False
        expected_digest = payload.get("manifest_sha256") if isinstance(payload, dict) else None
        if expected_digest:
            unsigned = dict(payload)
            unsigned.pop("manifest_sha256", None)
            if str(expected_digest) != _manifest_sha256(unsigned):
                return False
        return True
    except OSError:
        return False
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _parse_sample_annotation(sample):
    outer = _decode_json(getattr(sample, "data", None), {})
    if not isinstance(outer, dict):
        return {}, {}
    result = outer.get("result")
    if isinstance(result, str):
        result = _decode_json(result, {})
    if not isinstance(result, dict):
        return outer, {}
    return outer, result


def _resolve_sample_local_path(sample) -> Path | None:
    attachment = getattr(sample, "file", None)
    if not attachment:
        return None
    return resolve_attachment_local_path(attachment)


def _resolve_attachment_local_path(attachment) -> Path | None:
    if not attachment:
        return None
    return resolve_attachment_local_path(attachment)


def _load_class_names(task_config_json: str | None) -> list[str]:
    config = _decode_json(task_config_json, {})
    if not isinstance(config, dict):
        return []
    return build_yolo_rect_class_names(config)


def class_contract_for_export(
    db: Session, export_id: int, *, fallback_class_names: list[str] | None = None
) -> dict:
    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    job = (
        crud_yolo_train_job.get(db, int(export_row.yolo_train_job_id))
        if export_row is not None
        else None
    )
    frozen_names = _decode_json(getattr(job, "class_names_json", None), [])
    class_names = (
        [str(value) for value in frozen_names]
        if isinstance(frozen_names, list) and frozen_names
        else _class_names_from_training_artifacts(job)
        or [str(value) for value in (fallback_class_names or [])]
    )
    order_signature = getattr(job, "class_order_signature", None)
    if not order_signature and class_names:
        order_signature = class_order_signature(class_names)
    return {
        "class_names": class_names,
        "label_set_signature": getattr(job, "label_set_signature", None),
        "class_order_signature": order_signature,
        "data_yaml_sha256": getattr(job, "data_yaml_sha256", None),
        "dataset_content_signature": getattr(job, "dataset_content_signature", None),
    }


def pack_selected_samples_for_job(
    db: Session,
    *,
    export_id: int,
    job_id: int,
    task_id: int,
    mode: str,
    sample_ids: list[int],
    sample_task_ids: dict[int, int] | None = None,
    attachment_ids: list[int] | None = None,
    task_config_json: str | None = None,
    root_override: Path | None = None,
) -> dict:
    root = Path(root_override) if root_override is not None else edge_validation_job_root(
        export_id=export_id, job_id=job_id
    )
    eval_root = root / "eval_data"
    images_dir = eval_root / "images" / "test"
    xml_dir = eval_root / "labelsxml" / "test"
    if root_override is None:
        clear_job_pack(export_id=export_id, job_id=job_id)
    elif root.exists():
        shutil.rmtree(root, ignore_errors=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    xml_dir.mkdir(parents=True, exist_ok=True)

    normalized_sample_ids = [int(sample_id) for sample_id in sample_ids]
    normalized_task_ids = (
        {int(sample_id): int(source_task_id) for sample_id, source_task_id in sample_task_ids.items()}
        if sample_task_ids is not None
        else None
    )
    if normalized_task_ids is not None and set(normalized_task_ids) != set(normalized_sample_ids):
        raise ValueError("sample task membership must cover every selected sample exactly once")
    rows = crud_sample.get_by_ids(
        db,
        sample_ids=normalized_sample_ids,
        task_id=None if normalized_task_ids is not None else task_id,
    )
    row_map = {int(row.id): row for row in rows}
    if normalized_task_ids is not None:
        missing = sorted(set(normalized_sample_ids) - set(row_map))
        mismatched = sorted(
            sample_id
            for sample_id, row in row_map.items()
            if normalized_task_ids.get(sample_id) != int(row.task_id)
        )
        if missing or mismatched:
            raise ValueError(
                "sample task membership does not match current samples: "
                f"missing={missing}, mismatched={mismatched}"
            )
    task_class_names = _load_class_names(task_config_json)
    class_contract = class_contract_for_export(
        db, export_id, fallback_class_names=task_class_names
    )
    class_names = class_contract["class_names"]
    copied_count = 0
    labeled_count = 0
    unlabeled_count = 0
    skipped: list[dict[str, str | int]] = []
    file_mappings: list[dict[str, str | int | bool]] = []
    attachment_ids = [int(attachment_id) for attachment_id in (attachment_ids or []) if int(attachment_id) > 0]
    attachments = crud_attachment.get_by_ids(db, attachment_ids=attachment_ids) if attachment_ids else []
    attachment_map = {int(row.id): row for row in attachments}

    task_sample_groups: dict[str, list[int]] = {}
    for sample_id in normalized_sample_ids:
        sample = row_map.get(int(sample_id))
        if sample is None:
            skipped.append({"sample_id": int(sample_id), "reason": "sample_not_found"})
            continue
        local_path = _resolve_sample_local_path(sample)
        if local_path is None:
            skipped.append({"sample_id": int(sample_id), "reason": "image_not_found"})
            continue

        packed_name = _safe_eval_filename(copied_count, local_path.name)
        dst_img = images_dir / packed_name
        shutil.copy2(local_path, dst_img)

        outer, result = _parse_sample_annotation(sample)
        rotate = result.get("rotate") or 0
        if rotate:
            with Image.open(dst_img) as img:
                img = img.rotate(rotate, expand=True)
                img.save(dst_img)
        with Image.open(dst_img) as img:
            image_width, image_height = img.size

        yolo_lines, _skipped_invalid = _rect_lines_for_sample(
            result,
            class_names,
            image_width,
            image_height,
        )
        has_label = bool(yolo_lines)
        if has_label:
            write_voc_xml_with_scene(
                xml_dir / f"{Path(packed_name).stem}.xml",
                image_name=dst_img.name,
                width=image_width,
                height=image_height,
                class_names=class_names,
                yolo_lines=yolo_lines,
                scene_attrs=merge_scene_attrs(outer, result) or None,
            )
            labeled_count += 1
        else:
            unlabeled_count += 1

        file_mappings.append(
            {
                "sample_id": int(sample.id),
                "task_id": int(sample.task_id),
                "attachment_id": int(getattr(sample, "file_id", 0) or 0) or None,
                "source_kind": (
                    "sample_datasource"
                    if getattr(sample.file, "data_source_id", None)
                    else "sample"
                ),
                "packed_name": packed_name,
                "original_name": local_path.name,
                "source_path": str(local_path),
                "has_annotation": has_label,
                "data_source_id": (
                    int(sample.file.data_source_id)
                    if getattr(sample.file, "data_source_id", None)
                    else None
                ),
                "source_object_key": (
                    str(sample.file.path)
                    if getattr(sample.file, "data_source_id", None)
                    else None
                ),
                "source_sha256": getattr(sample.file, "sha256", None),
                "source_content_sha256": _sha256_file(local_path),
            }
        )
        file_mappings[-1]["packed_sha256"] = _sha256_file(dst_img)
        xml_path = xml_dir / f"{Path(packed_name).stem}.xml"
        file_mappings[-1]["annotation_sha256"] = (
            _sha256_file(xml_path) if xml_path.is_file() else None
        )
        task_sample_groups.setdefault(str(int(sample.task_id)), []).append(int(sample.id))
        copied_count += 1

    for attachment_id in attachment_ids:
        attachment = attachment_map.get(int(attachment_id))
        if attachment is None:
            skipped.append({"attachment_id": int(attachment_id), "reason": "attachment_not_found"})
            continue
        local_path = _resolve_attachment_local_path(attachment)
        if local_path is None:
            skipped.append({"attachment_id": int(attachment_id), "reason": "image_not_found"})
            continue

        packed_name = _safe_eval_filename(copied_count, local_path.name)
        dst_img = images_dir / packed_name
        shutil.copy2(local_path, dst_img)

        unlabeled_count += 1
        file_mappings.append(
            {
                "sample_id": None,
                "task_id": int(task_id),
                "attachment_id": int(attachment.id),
                "source_kind": (
                    "attachment_datasource"
                    if getattr(attachment, "data_source_id", None)
                    else "attachment"
                ),
                "packed_name": packed_name,
                "original_name": local_path.name,
                "source_path": str(local_path),
                "has_annotation": False,
                "data_source_id": (
                    int(attachment.data_source_id)
                    if getattr(attachment, "data_source_id", None)
                    else None
                ),
                "source_object_key": (
                    str(attachment.path)
                    if getattr(attachment, "data_source_id", None)
                    else None
                ),
                "source_sha256": getattr(attachment, "sha256", None),
                "source_content_sha256": _sha256_file(local_path),
            }
        )
        file_mappings[-1]["packed_sha256"] = _sha256_file(dst_img)
        file_mappings[-1]["annotation_sha256"] = None
        copied_count += 1

    if copied_count <= 0:
        if root_override is None:
            clear_job_pack(export_id=export_id, job_id=job_id)
        else:
            shutil.rmtree(root, ignore_errors=True)
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            message="所选样本没有可用图片，无法生成边端验证数据包",
        )

    if labeled_count <= 0:
        shutil.rmtree(xml_dir.parent, ignore_errors=True)

    manifest = {
        "export_id": int(export_id),
        "job_id": int(job_id),
        "task_id": int(task_id),
        "mode": str(mode),
        "sample_count": len(normalized_sample_ids),
        "task_sample_groups": task_sample_groups,
        "attachment_count": len(attachment_ids),
        "copied_count": copied_count,
        # Keep the fixed-evaluation contract explicit.  The edge runner uses
        # these names while older server code used ``*_count`` and
        # ``file_mappings``; retaining both forms keeps existing packs readable.
        "image_count": copied_count,
        "labeled_image_count": labeled_count,
        "labeled_count": labeled_count,
        "unlabeled_count": unlabeled_count,
        "class_names": class_names,
        "class_contract": class_contract,
        "label_mappings": file_mappings,
        "file_mappings": file_mappings,
        "skipped": skipped,
        "blocking_skipped_count": len(skipped),
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "job_root": str(root),
        "eval_data_root": str(eval_root),
        "manifest_path": str(root / "manifest.json"),
        "copied_count": copied_count,
        "labeled_count": labeled_count,
        "unlabeled_count": unlabeled_count,
        "skipped_count": len(skipped),
        "task_sample_groups": task_sample_groups,
        "file_mappings": file_mappings,
        "skipped": skipped,
        "blocking_skipped_count": len(skipped),
    }


def load_manual_edge_board_report(
    *, export_id: int, job_id: int
) -> dict | None:
    """读取边端 SSH 回传的 board_report.json（兼容 API report.metrics 为空）。"""
    path = (
        edge_validation_job_root(export_id=export_id, job_id=job_id)
        / "edge_ssh_pull"
        / "output"
        / "board_report.json"
    )
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    preview_dir = (
        edge_validation_job_root(export_id=export_id, job_id=job_id)
        / "server_eval"
        / "bmcv_preview"
    )
    if preview_dir.is_dir():
        metrics = data.setdefault("metrics", {})
        if isinstance(metrics, dict):
            prev = metrics.setdefault("bmcv_preview", {})
            if isinstance(prev, dict):
                prev["platform_dir"] = str(preview_dir)
                local_files = sorted(
                    p.name
                    for p in preview_dir.iterdir()
                    if p.is_file()
                    and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
                )
                if local_files:
                    prev["preview_files"] = local_files
                    prev["preview_count"] = len(local_files)
                    prev["viz_image_count"] = prev.get("viz_image_count") or len(local_files)
    return data
