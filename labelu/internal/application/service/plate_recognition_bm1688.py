from __future__ import annotations

import json
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from loguru import logger

from labelu.internal.adapter.persistence import (
    crud_yolo_bm1688_export,
    crud_yolo_train_job,
)
from labelu.internal.application.service.plate_recognition_train import (
    PLATE_RECOGNITION_JOB_TYPE,
    component_artifact_contract,
)
from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.model_asset_manifest import file_asset
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus

COMBINED_BMODEL_NAME = "plate_recognition_fp16_1b.bmodel"


def _pose_input_size(job: Any, components: dict[str, dict[str, Any]]) -> int:
    raw = components.get("pose", {}).get("input_size")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        width, height = int(raw[0]), int(raw[1])
        if width <= 0 or height <= 0 or width != height:
            raise RuntimeError(f"复合车牌 Pose 输入尺寸无效: {raw}")
        return width
    return int(job.imgsz or 640)


def _parse_artifacts(job: Any) -> dict[str, dict[str, Any]]:
    try:
        artifacts = json.loads(job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("复合训练组件产物 JSON 无法解析") from exc
    if not isinstance(artifacts, dict):
        raise RuntimeError("复合训练组件产物格式错误")
    return artifacts


def _hydrate_ocr_vocabulary(artifact: dict[str, Any], onnx_path: Path) -> None:
    characters = artifact.get("characters")
    if isinstance(characters, list) and characters:
        return

    candidates: list[Path] = []
    configured = str(artifact.get("vocabulary") or "").strip()
    if configured:
        candidates.append(Path(configured).resolve())
    candidates.append(onnx_path.parent / "plate_ocr_vocab.json")

    for vocabulary_path in candidates:
        if not vocabulary_path.is_file():
            continue
        try:
            payload = json.loads(vocabulary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        recovered = payload.get("characters") if isinstance(payload, dict) else None
        if isinstance(recovered, list) and recovered:
            artifact["characters"] = [str(value) for value in recovered]
            artifact["vocabulary"] = str(vocabulary_path.resolve())
            return

    raise RuntimeError("OCR component vocabulary is missing or empty")


def _resolve_component_sources(job: Any) -> dict[str, dict[str, Any]]:
    artifacts = _parse_artifacts(job)
    contract = component_artifact_contract()
    job_root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{int(job.id)}"
    ).resolve()
    resolved: dict[str, dict[str, Any]] = {}
    for component, expected in contract.items():
        artifact = artifacts.get(component)
        if not isinstance(artifact, dict):
            raise RuntimeError(f"复合训练缺少 {component} 组件产物")
        onnx_path = Path(str(artifact.get("onnx") or "")).resolve()
        if not onnx_path.is_file():
            raise RuntimeError(f"{component} ONNX 不存在: {onnx_path}")
        try:
            onnx_path.relative_to(job_root)
        except ValueError as exc:
            raise RuntimeError(f"{component} ONNX 不在训练作业目录内") from exc
        actual_network = str(artifact.get("network") or "").strip()
        if actual_network != expected["network"]:
            raise RuntimeError(
                f"{component} 网络名不匹配: {actual_network or '<empty>'}"
            )
        resolved_artifact = {**artifact, "onnx_path": onnx_path}
        if component == "ocr":
            _hydrate_ocr_vocabulary(resolved_artifact, onnx_path)
        resolved[component] = resolved_artifact
    return resolved


def _runtime_contract(
    *,
    job: Any,
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pose_size = _pose_input_size(job, components)
    return {
        "format": "labelu_plate_recognition_bm1688_v1",
        "training_profile": "plate_recognition_v1",
        "job_type": PLATE_RECOGNITION_JOB_TYPE,
        "combined_bmodel": COMBINED_BMODEL_NAME,
        "precision": "fp16",
        "pipeline": [
            "plate_pose",
            "perspective_warp",
            "lower_row_extract",
            ["plate_ocr", "plate_color", "plate_type"],
        ],
        "networks": {
            "plate_pose": {
                "input": {
                    "name": "images",
                    "shape": [1, 3, pose_size, pose_size],
                },
                "output": "Ultralytics YOLOv8 Pose output",
                "class_names": ["doubleEP"],
                "keypoint_order": [
                    "top_left",
                    "top_right",
                    "bottom_right",
                    "bottom_left",
                ],
            },
            "plate_ocr": {
                "input": {"name": "images", "shape": [1, 3, 48, 192]},
                "output": {"name": "sequence_logits", "decode": "ctc_greedy"},
                "characters": components["ocr"].get("characters") or [],
                "length_counts": components["ocr"].get("length_counts") or {},
                "preferred_length": components["ocr"].get("preferred_length"),
                "crop_ratios": components["ocr"].get("crop_ratios") or [0.5],
                "fallback_crop_ratios": components["ocr"].get("fallback_crop_ratios")
                or [],
                "length_anchor_crop_ratio": components["ocr"].get(
                    "length_anchor_crop_ratio", 0.5
                ),
                "length_preference_tolerance": components["ocr"].get(
                    "length_preference_tolerance", 0.1
                ),
                "non_preferred_length_preserve_confidence": components["ocr"].get(
                    "non_preferred_length_preserve_confidence", 0.99
                ),
                "guarded_consensus_enabled": components["ocr"].get(
                    "guarded_consensus_enabled", False
                ),
                "anchor_group_min_confidence": components["ocr"].get(
                    "anchor_group_min_confidence", 0.90
                ),
                "non_anchor_group_min_confidence": components["ocr"].get(
                    "non_anchor_group_min_confidence", 0.94
                ),
            },
            "plate_color": {
                "input": {"name": "images", "shape": [1, 3, 64, 128]},
                "output": {
                    "name": "logits",
                    "classes": components["color"].get("classes") or [],
                },
            },
            "plate_type": {
                "input": {"name": "images", "shape": [1, 3, 64, 128]},
                "output": {
                    "name": "logits",
                    "classes": components["plate_type"].get("classes") or [],
                },
            },
        },
        "preprocess": {
            "pixel_format": "rgb",
            "scale": [0.0039216, 0.0039216, 0.0039216],
            "mean": [0.0, 0.0, 0.0],
            "pose_resize": "letterbox",
            "ocr_crop": (
                "four-point perspective warp, extract lower row, resize to 192x48, "
                "grayscale CLAHE"
            ),
        },
    }


def _artifact_manifest(
    *,
    export_root: Path,
    export_id: int,
    job: Any,
    components: dict[str, dict[str, Any]],
    contract_path: Path,
    combined_path: Path | None,
    status: str,
) -> dict[str, Any]:
    assets = []
    for component in ("pose", "ocr", "color", "plate_type"):
        for suffix, role in (
            ("pt", "component_weights_pt"),
            ("onnx", "component_model_onnx"),
        ):
            asset = file_asset(
                components[component].get(suffix),
                role=f"{role}:{component}",
                root=export_root,
            )
            if asset:
                assets.append(asset)
    contract_asset = file_asset(
        contract_path, role="runtime_contract", root=export_root
    )
    if contract_asset:
        assets.append(contract_asset)
    if combined_path and combined_path.is_file():
        combined_asset = file_asset(
            combined_path, role="combined_bmodel", root=export_root
        )
        if combined_asset:
            assets.append(combined_asset)
    return {
        "manifest_version": "plate_recognition_v1",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "export_id": int(export_id),
        "train_job_id": int(job.id),
        "job_type": PLATE_RECOGNITION_JOB_TYPE,
        "status": status,
        "runtime_contract": contract_path.relative_to(export_root).as_posix(),
        "combined_bmodel": (
            combined_path.relative_to(export_root).as_posix()
            if combined_path and combined_path.is_file()
            else None
        ),
        "network_names": ["plate_pose", "plate_ocr", "plate_color", "plate_type"],
        "artifacts": assets,
    }


def _copy_runtime_assets(
    export_root: Path,
    components: dict[str, dict[str, Any]],
) -> dict[str, Path]:
    onnx_dir = export_root / "models" / "onnx"
    onnx_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    for component, artifact in components.items():
        destination = (
            onnx_dir
            / f"plate_{component if component != 'plate_type' else 'type'}.onnx"
        )
        if component == "pose":
            destination = onnx_dir / "plate_pose.onnx"
        shutil.copy2(artifact["onnx_path"], destination)
        copied[component] = destination
    vocab = components["ocr"].get("vocabulary")
    if vocab and Path(str(vocab)).is_file():
        shutil.copy2(Path(str(vocab)), export_root / "plate_ocr_vocab.json")
    return copied


def _compile_command(
    *, export_root: Path, copied: dict[str, Path], imgsz: int, file_cfg: dict[str, Any]
) -> tuple[str | None, str | None]:
    if not bool(file_cfg.get("auto_compile", True)):
        return None, "BM1688 自动编译已关闭，四个 ONNX 与运行契约已生成"
    root = str(file_cfg.get("tpu_mlir_root") or "").strip()
    tpu_python = str(file_cfg.get("tpu_mlir_python") or "").strip()
    if not root or not tpu_python:
        return (
            None,
            "未配置 tpu_mlir_root 或 tpu_mlir_python，四个 ONNX 与运行契约已生成",
        )
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "bm1688_plate_recognition_to_bmodel.sh"
    )
    if not script.is_file():
        raise RuntimeError(f"复合 BM1688 编译脚本不存在: {script}")
    values = [
        export_root,
        copied["pose"],
        copied["ocr"],
        copied["color"],
        copied["plate_type"],
    ]
    command = (
        f"export TPU_MLIR_ROOT={shlex.quote(root)} && "
        f"export TPU_MLIR_PYTHON={shlex.quote(tpu_python)} && "
        f"bash {shlex.quote(str(script))} "
        + " ".join(shlex.quote(str(value)) for value in values)
        + f" {int(imgsz)}"
    )
    return command, None


def run_plate_recognition_bm1688_export(export_id: int) -> None:
    from labelu.internal.application.service import yolo_bm1688_export as base

    guard = base._acquire_export_execution_guard(export_id)
    if guard is None:
        logger.warning("skip duplicate plate BM1688 export export_id={}", export_id)
        return
    compile_writer = quantize_writer = None
    compile_log_parts: list[str] = []
    execution_monitor = None
    try:
        execution_monitor, fencing_unavailable, already_claimed = (
            base._start_export_execution_monitor(
                export_id, label="plate-recognition-export"
            )
        )
        if already_claimed and not fencing_unavailable:
            logger.warning(
                "skip plate recognition export claimed by another worker export_id={}",
                export_id,
            )
            return
        compile_writer, quantize_writer = base._open_bm1688_process_log_writers(
            export_id
        )
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            job = crud_yolo_train_job.get(db, row.yolo_train_job_id) if row else None
            if not row or not job or job.job_type != PLATE_RECOGNITION_JOB_TYPE:
                raise RuntimeError("复合车牌 BM1688 导出作业不存在")
            if job.status != YoloTrainStatus.COMPLETED.value:
                raise RuntimeError("复合车牌训练尚未完成")
            job_snapshot = SimpleNamespace(
                id=int(job.id),
                imgsz=int(job.imgsz or 640),
                component_artifacts_json=str(job.component_artifacts_json or "{}"),
            )
            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    {"status": "PROCESSING", "error_message": None},
                    db=db,
                    manage_transaction=True,
                )
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(
                        db, row, status="PROCESSING", error_message=None
                    )

        components = _resolve_component_sources(job_snapshot)
        export_root = base._export_dir_for_row(export_id)
        export_root.mkdir(parents=True, exist_ok=True)
        copied = _copy_runtime_assets(export_root, components)
        contract = _runtime_contract(job=job_snapshot, components=components)
        contract_path = export_root / "plate_recognition_contract.json"
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        compile_log_parts.append("四组件 ONNX 与运行契约已生成")

        file_cfg = cfg_io.load_bm1688_compiler_config()
        command, skip_reason = _compile_command(
            export_root=export_root,
            copied=copied,
            imgsz=_pose_input_size(job_snapshot, components),
            file_cfg=file_cfg,
        )
        combined_path = export_root / "models" / "BM1688" / COMBINED_BMODEL_NAME
        final_status = "ONNX_READY"
        error_message = skip_reason
        if command:
            for filename in (
                "plate_pose_fp16_1b.bmodel",
                "plate_ocr_fp16_1b.bmodel",
                "plate_color_fp16_1b.bmodel",
                "plate_type_fp16_1b.bmodel",
                COMBINED_BMODEL_NAME,
            ):
                stale = combined_path.parent / filename
                if stale.is_file():
                    stale.unlink()
            preflight_error = base._preflight_tpu_mlir_python(
                str(file_cfg.get("tpu_mlir_python") or "")
            )
            if preflight_error:
                raise RuntimeError(f"TPU-MLIR 预检失败: {preflight_error}")
            compile_writer.write_block("plate_recognition_compile_command", command)
            exit_code = base._run_compile_shell_command(
                command,
                env=base._compile_subprocess_env(file_cfg),
                timeout=base._compile_timeout_seconds(),
                compile_log_parts=compile_log_parts,
                compile_writer=compile_writer,
                quantize_writer=quantize_writer,
                quantize_block=False,
            )
            if exit_code != 0 or not combined_path.is_file():
                raise RuntimeError(f"复合 BM1688 编译失败，exit_code={exit_code}")
            final_status = "COMPLETED"
            error_message = None
            compile_log_parts.append(f"combined_bmodel={combined_path}")
        elif combined_path.is_file():
            final_status = "COMPLETED"
            error_message = None
            compile_log_parts.append(f"reuse_combined_bmodel={combined_path}")

        manifest = _artifact_manifest(
            export_root=export_root,
            export_id=export_id,
            job=job_snapshot,
            components=components,
            contract_path=contract_path,
            combined_path=combined_path if combined_path.is_file() else None,
            status=final_status,
        )
        media_root = Path(settings.MEDIA_ROOT).resolve()
        primary_onnx_rel = copied["pose"].resolve().relative_to(media_root).as_posix()
        compile_log = "\n".join(compile_log_parts)[-12000:]
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            updates = {
                "status": final_status,
                "onnx_rel_path": primary_onnx_rel,
                "bmodel_rel_path": (
                    f"models/BM1688/{COMBINED_BMODEL_NAME}"
                    if combined_path.is_file()
                    else None
                ),
                "artifact_manifest_json": json.dumps(manifest, ensure_ascii=False),
                "compile_log": compile_log,
                "error_message": error_message,
            }
            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    updates, db=db, manage_transaction=True
                )
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(db, row, **updates)
        if final_status == "COMPLETED":
            edge_kwargs = {
                "yolo_train_job_id": int(job_snapshot.id),
                "imgsz": _pose_input_size(job_snapshot, components),
            }
            if execution_monitor is not None:
                edge_kwargs["execution_monitor"] = execution_monitor
            base._maybe_run_edge_ssh_validation_sync(export_id, **edge_kwargs)
        quality_kwargs = {"run_onnx_val": False}
        if execution_monitor is not None:
            quality_kwargs["execution_monitor"] = execution_monitor
        base._generate_quality_report_sync(export_id, **quality_kwargs)
    except Exception as exc:
        logger.exception(
            "plate recognition BM1688 export failed export_id={}", export_id
        )
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            if row:
                updates = {
                    "status": "FAILED",
                    "compile_log": "\n".join(compile_log_parts)[-12000:],
                    "error_message": str(exc)[:4000],
                }
                if execution_monitor is not None:
                    try:
                        execution_monitor.conditional_update(
                            updates, db=db, manage_transaction=True
                        )
                    except (
                        base.ExecutionFencingLost,
                        base.ExecutionFencingUnavailable,
                    ):
                        logger.warning(
                            "skip stale plate recognition failure write export_id={}",
                            export_id,
                        )
                else:
                    with begin_transaction(db):
                        crud_yolo_bm1688_export.update_row(db, row, **updates)
    finally:
        for writer in (compile_writer, quantize_writer):
            if writer is not None:
                writer.close()
        base._stop_export_execution_monitor(execution_monitor)
        guard.release()
