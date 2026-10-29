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
    PLATE_DETECTION_JOB_TYPE,
    plate_detection_artifact_contract,
)
from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.model_asset_manifest import file_asset
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus

BMODEL_NAME = "plate_detection_fp16_1b.bmodel"


def _resolve_pose_source(job: Any) -> dict[str, Any]:
    try:
        artifacts = json.loads(job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("车牌检测产物 JSON 无法解析") from exc
    pose = artifacts.get("pose") if isinstance(artifacts, dict) else None
    if not isinstance(pose, dict):
        raise RuntimeError("车牌检测缺少 Pose 产物")
    expected = plate_detection_artifact_contract()
    if str(pose.get("network") or "").strip() != expected["network"]:
        raise RuntimeError("车牌检测网络名不匹配")
    onnx_path = Path(str(pose.get("onnx") or "")).resolve()
    if not onnx_path.is_file():
        raise RuntimeError(f"车牌检测 ONNX 不存在: {onnx_path}")
    job_root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{int(job.id)}"
    ).resolve()
    try:
        onnx_path.relative_to(job_root)
    except ValueError as exc:
        raise RuntimeError("车牌检测 ONNX 不在训练作业目录内") from exc
    input_size = pose.get("input_size") or [int(job.imgsz or 640)] * 2
    if len(input_size) != 2 or int(input_size[0]) != int(input_size[1]):
        raise RuntimeError(f"车牌检测输入尺寸无效: {input_size}")
    return {**pose, "onnx_path": onnx_path, "imgsz": int(input_size[0])}


def _runtime_contract(job: Any, pose: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "labelu_plate_detection_bm1688_v1",
        "training_profile": "plate_recognition_v1",
        "job_type": PLATE_DETECTION_JOB_TYPE,
        "bmodel": BMODEL_NAME,
        "precision": "fp16",
        "network": {
            "name": "plate_detection",
            "input": {"name": "images", "shape": [1, 3, pose["imgsz"], pose["imgsz"]]},
            "output": "Ultralytics YOLOv8 Pose output",
            "class_names": pose.get("class_names") or ["doubleEP"],
            "keypoint_order": pose.get("keypoint_order")
            or ["top_left", "top_right", "bottom_right", "bottom_left"],
        },
        "preprocess": {
            "pixel_format": "rgb",
            "scale": [0.0039216, 0.0039216, 0.0039216],
            "mean": [0.0, 0.0, 0.0],
            "resize": "letterbox",
        },
    }


def _compile_command(
    *, export_root: Path, onnx_path: Path, imgsz: int, file_cfg: dict[str, Any]
) -> tuple[str | None, str | None]:
    if not bool(file_cfg.get("auto_compile", True)):
        return None, "BM1688 自动编译已关闭，车牌检测 ONNX 与运行契约已生成"
    root = str(file_cfg.get("tpu_mlir_root") or "").strip()
    tpu_python = str(file_cfg.get("tpu_mlir_python") or "").strip()
    if not root or not tpu_python:
        return None, "未配置 tpu_mlir_root 或 tpu_mlir_python，车牌检测 ONNX 已生成"
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "bm1688_plate_detection_to_bmodel.sh"
    )
    if not script.is_file():
        raise RuntimeError(f"车牌检测 BM1688 编译脚本不存在: {script}")
    command = (
        f"export TPU_MLIR_ROOT={shlex.quote(root)} && "
        f"export TPU_MLIR_PYTHON={shlex.quote(tpu_python)} && "
        f"bash {shlex.quote(str(script))} "
        f"{shlex.quote(str(export_root))} {shlex.quote(str(onnx_path))} {int(imgsz)}"
    )
    return command, None


def run_plate_detection_bm1688_export(export_id: int) -> None:
    from labelu.internal.application.service import yolo_bm1688_export as base

    guard = base._acquire_export_execution_guard(export_id)
    if guard is None:
        logger.warning("skip duplicate plate detection export_id={}", export_id)
        return
    compile_writer = quantize_writer = None
    compile_log_parts: list[str] = []
    execution_monitor = None
    try:
        execution_monitor, fencing_unavailable, already_claimed = (
            base._start_export_execution_monitor(
                export_id, label="plate-detection-export"
            )
        )
        if already_claimed and not fencing_unavailable:
            logger.warning(
                "skip plate detection export claimed by another worker export_id={}",
                export_id,
            )
            return
        compile_writer, quantize_writer = base._open_bm1688_process_log_writers(
            export_id
        )
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            job = crud_yolo_train_job.get(db, row.yolo_train_job_id) if row else None
            if not row or not job or job.job_type != PLATE_DETECTION_JOB_TYPE:
                raise RuntimeError("车牌检测 BM1688 导出作业不存在")
            if job.status != YoloTrainStatus.COMPLETED.value:
                raise RuntimeError("车牌检测训练尚未完成")
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

        pose = _resolve_pose_source(job_snapshot)
        export_root = base._export_dir_for_row(export_id)
        onnx_dir = export_root / "models" / "onnx"
        onnx_dir.mkdir(parents=True, exist_ok=True)
        copied_onnx = onnx_dir / "plate_detection.onnx"
        shutil.copy2(pose["onnx_path"], copied_onnx)
        contract_path = export_root / "plate_detection_contract.json"
        contract_path.write_text(
            json.dumps(
                _runtime_contract(job_snapshot, pose), ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        compile_log_parts.append("车牌检测 ONNX 与运行契约已生成")

        file_cfg = cfg_io.load_bm1688_compiler_config()
        command, skip_reason = _compile_command(
            export_root=export_root,
            onnx_path=copied_onnx,
            imgsz=pose["imgsz"],
            file_cfg=file_cfg,
        )
        bmodel_path = export_root / "models" / "BM1688" / BMODEL_NAME
        final_status = "ONNX_READY"
        error_message = skip_reason
        if command:
            if bmodel_path.is_file():
                bmodel_path.unlink()
            preflight_error = base._preflight_tpu_mlir_python(
                str(file_cfg.get("tpu_mlir_python") or "")
            )
            if preflight_error:
                raise RuntimeError(f"TPU-MLIR 预检失败: {preflight_error}")
            compile_writer.write_block("plate_detection_compile_command", command)
            exit_code = base._run_compile_shell_command(
                command,
                env=base._compile_subprocess_env(file_cfg),
                timeout=base._compile_timeout_seconds(),
                compile_log_parts=compile_log_parts,
                compile_writer=compile_writer,
                quantize_writer=quantize_writer,
                quantize_block=False,
            )
            if exit_code != 0 or not bmodel_path.is_file():
                raise RuntimeError(f"车牌检测 BM1688 编译失败，exit_code={exit_code}")
            final_status = "COMPLETED"
            error_message = None
            compile_log_parts.append(f"bmodel={bmodel_path}")
        elif bmodel_path.is_file():
            final_status = "COMPLETED"
            error_message = None
            compile_log_parts.append(f"reuse_bmodel={bmodel_path}")

        assets = []
        for path, role in (
            (copied_onnx, "model_onnx"),
            (contract_path, "runtime_contract"),
            (bmodel_path if bmodel_path.is_file() else None, "bmodel"),
        ):
            asset = file_asset(path, role=role, root=export_root) if path else None
            if asset:
                assets.append(asset)
        manifest = {
            "manifest_version": "plate_detection_v1",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "export_id": int(export_id),
            "train_job_id": int(job_snapshot.id),
            "job_type": PLATE_DETECTION_JOB_TYPE,
            "status": final_status,
            "runtime_contract": contract_path.relative_to(export_root).as_posix(),
            "bmodel": (
                bmodel_path.relative_to(export_root).as_posix()
                if bmodel_path.is_file()
                else None
            ),
            "network_names": ["plate_detection"],
            "artifacts": assets,
        }
        media_root = Path(settings.MEDIA_ROOT).resolve()
        onnx_rel = copied_onnx.resolve().relative_to(media_root).as_posix()
        with session_scope() as db:
            row = crud_yolo_bm1688_export.get(db, export_id)
            updates = {
                "status": final_status,
                "onnx_rel_path": onnx_rel,
                "bmodel_rel_path": (
                    f"models/BM1688/{BMODEL_NAME}" if bmodel_path.is_file() else None
                ),
                "artifact_manifest_json": json.dumps(manifest, ensure_ascii=False),
                "compile_log": "\n".join(compile_log_parts)[-12000:],
                "error_message": error_message,
            }
            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    updates, db=db, manage_transaction=True
                )
            else:
                with begin_transaction(db):
                    crud_yolo_bm1688_export.update_row(db, row, **updates)
    except Exception as exc:
        logger.exception("plate detection BM1688 export failed export_id={}", export_id)
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
                            "skip stale plate detection failure write export_id={}",
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
