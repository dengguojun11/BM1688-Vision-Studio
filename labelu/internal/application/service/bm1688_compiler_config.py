"""BM1688 编译器配置读写与导出流程中的编译命令解析（对齐 sophon-demo BM1688）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple

from fastapi import status

from labelu.internal.application.command.bm1688_compiler import PatchBm1688CompilerConfigCommand
from labelu.internal.application.response.bm1688_compiler import Bm1688CompilerConfigResponse
from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.user import User

_DOC = (
    "对齐 sophon-demo YOLOv8_plus_det README §3.2：导出目录为 models/onnx、models/BM1688 等；"
    "每平台仅 yolov11n_fp32/fp16/int8_1b.bmodel（与 yolo11n 训练一致）；"
    "主读写来源为 MySQL 治理表，bm1688_compiler_config.json 仅作兼容同步；§4–§6 例程见 sophon-demo。"
)


def _builtin_script() -> Path:
    import labelu

    return Path(labelu.__file__).resolve().parent / "scripts" / "bm1688_onnx_to_bmodel.sh"


def _assert_yolo_train_enabled() -> None:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def get_bm1688_compiler_config_view() -> Bm1688CompilerConfigResponse:
    _assert_yolo_train_enabled()
    from labelu.internal.common.bm1688_deploy_bootstrap import ensure_bm1688_compiler_ready

    ensure_bm1688_compiler_ready()
    raw = cfg_io.load_bm1688_compiler_config()
    p = _builtin_script()
    return Bm1688CompilerConfigResponse(
        tpu_mlir_root=raw.get("tpu_mlir_root") or "",
        auto_compile=bool(raw.get("auto_compile", True)),
        quantize=raw.get("quantize") or "full",
        bmodel_basename=str(raw.get("bmodel_basename") or cfg_io.DEFAULT_BMODEL_BASENAME),
        onnx_filename=str(raw.get("onnx_filename") or cfg_io.DEFAULT_ONNX_FILENAME),
        qtable_filename=str(raw.get("qtable_filename") or cfg_io.DEFAULT_QTABLE_FILENAME),
        compile_platforms=str(raw.get("compile_platforms") or "bm1688"),
        custom_compile_command=raw.get("custom_compile_command") or "",
        calibration_dataset_dir=raw.get("calibration_dataset_dir") or "",
        quantize_table_path=raw.get("quantize_table_path") or "",
        int8_batches=str(raw.get("int8_batches") or "1"),
        cali_input_num=int(raw.get("cali_input_num") or 128),
        cali_inference_num=int(raw.get("cali_inference_num") or 10),
        calibration_balance_scene=bool(raw.get("calibration_balance_scene", True)),
        calibration_filename_prefix=str(raw.get("calibration_filename_prefix") or ""),
        calibration_infer_lighting_opencv=bool(
            raw.get("calibration_infer_lighting_opencv", False)
        ),
        calibration_count_mode=str(raw.get("calibration_count_mode") or "adaptive"),
        quantize_backend=str(raw.get("quantize_backend") or "docker"),
        docker_fpfwd_outputs=str(
            raw.get("docker_fpfwd_outputs") or "/model.23/dfl/conv/Conv_output_0_Conv"
        ),
        sophon_export=bool(raw.get("sophon_export", True)),
        tpu_mlir_python=str(raw.get("tpu_mlir_python") or ""),
        builtin_script_path=str(p) if p.is_file() else "",
        sophon_doc_url=cfg_io.SOPHON_DOC_URL,
        sophon_export_guide_url=cfg_io.SOPHON_EXPORT_GUIDE,
        config_backend_label="MySQL 治理表（system_config_*）",
        legacy_sync_file=str(cfg_io.config_file_path().resolve()),
        doc_hint=_DOC,
    )


def patch_bm1688_compiler_config(
    cmd: PatchBm1688CompilerConfigCommand,
    *,
    current_user: User | None = None,
) -> Bm1688CompilerConfigResponse:
    _assert_yolo_train_enabled()
    if cmd.custom_compile_command is not None and str(cmd.custom_compile_command).strip():
        if not current_user or not is_task_superuser(current_user):
            raise LabelUException(
                code=ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=status.HTTP_403_FORBIDDEN,
                message="仅任务超级用户可修改自定义编译命令（custom_compile_command）",
            )
    cur = cfg_io.load_bm1688_compiler_config()
    if cmd.tpu_mlir_root is not None:
        cur["tpu_mlir_root"] = cmd.tpu_mlir_root.strip()[:1024]
    if cmd.auto_compile is not None:
        cur["auto_compile"] = cmd.auto_compile
    if cmd.quantize is not None:
        cur["quantize"] = cmd.quantize
    if cmd.custom_compile_command is not None:
        cur["custom_compile_command"] = cmd.custom_compile_command.strip()[:8000]
    if cmd.calibration_dataset_dir is not None:
        cur["calibration_dataset_dir"] = cmd.calibration_dataset_dir.strip()[:2048]
    if cmd.quantize_table_path is not None:
        cur["quantize_table_path"] = cmd.quantize_table_path.strip()[:2048]
    if cmd.int8_batches is not None:
        cur["int8_batches"] = cmd.int8_batches.strip()[:32] or "1,4"
    if cmd.cali_input_num is not None:
        cur["cali_input_num"] = cmd.cali_input_num
    if cmd.cali_inference_num is not None:
        cur["cali_inference_num"] = cmd.cali_inference_num
    if cmd.calibration_balance_scene is not None:
        cur["calibration_balance_scene"] = cmd.calibration_balance_scene
    if cmd.calibration_filename_prefix is not None:
        cur["calibration_filename_prefix"] = cmd.calibration_filename_prefix.strip()[:256]
    if cmd.calibration_infer_lighting_opencv is not None:
        cur["calibration_infer_lighting_opencv"] = cmd.calibration_infer_lighting_opencv
    if cmd.calibration_count_mode is not None:
        mode = str(cmd.calibration_count_mode).strip().lower()
        if mode in ("adaptive", "fixed"):
            cur["calibration_count_mode"] = mode
    if cmd.quantize_backend is not None:
        backend = str(cmd.quantize_backend).strip().lower()
        if backend in ("docker", "host"):
            cur["quantize_backend"] = backend
    if cmd.docker_fpfwd_outputs is not None:
        cur["docker_fpfwd_outputs"] = cmd.docker_fpfwd_outputs.strip()[:256]
    if cmd.sophon_export is not None:
        cur["sophon_export"] = cmd.sophon_export
    if cmd.tpu_mlir_python is not None:
        cur["tpu_mlir_python"] = cmd.tpu_mlir_python.strip()[:1024]
    if cmd.bmodel_basename is not None:
        cur["bmodel_basename"] = cmd.bmodel_basename.strip()[:64] or cfg_io.DEFAULT_BMODEL_BASENAME
    if cmd.onnx_filename is not None:
        cur["onnx_filename"] = Path(cmd.onnx_filename.strip()).name[:128] or cfg_io.DEFAULT_ONNX_FILENAME
    if cmd.qtable_filename is not None:
        cur["qtable_filename"] = Path(cmd.qtable_filename.strip()).name[:128] or cfg_io.DEFAULT_QTABLE_FILENAME
    if cmd.compile_platforms is not None:
        cur["compile_platforms"] = ",".join(cfg_io.parse_compile_platforms(cmd.compile_platforms))
    cfg_io.save_bm1688_compiler_config(cur)
    return get_bm1688_compiler_config_view()


def resolve_compile_for_export(
    *,
    onnx_abs: Path,
    out_dir: Path,
    imgsz: int,
    compile_overrides: Optional[dict[str, Any]] = None,
) -> Tuple[bool, str]:
    file_cfg = cfg_io.load_bm1688_compiler_config()
    ov = compile_overrides or {}
    custom = (file_cfg.get("custom_compile_command") or "").strip()
    if custom:
        return True, custom.format(
            onnx=str(onnx_abs.resolve()),
            out_dir=str(out_dir.resolve()),
            imgsz=int(imgsz),
            platform="bm1688",
        )

    env_cmd = (settings.YOLO_BM1688_COMPILE_COMMAND or "").strip()
    if settings.YOLO_BM1688_COMPILE_ENABLED and env_cmd:
        return True, env_cmd.format(
            onnx=str(onnx_abs.resolve()),
            out_dir=str(out_dir.resolve()),
            imgsz=int(imgsz),
            platform="bm1688",
        )

    root = (file_cfg.get("tpu_mlir_root") or "").strip()
    auto = bool(file_cfg.get("auto_compile", True))
    if not auto or not root:
        return False, ""

    script = _builtin_script()
    if not script.is_file():
        return False, ""

    mode = str(ov.get("quantize") or file_cfg.get("quantize") or "full").strip().lower()
    if mode not in ("fp16", "f32", "int8", "int8_only", "full", "fp32_fp16"):
        mode = "full"
    basename = str(
        ov.get("bmodel_basename")
        or file_cfg.get("bmodel_basename")
        or cfg_io.DEFAULT_BMODEL_BASENAME
    ).strip()
    if not basename:
        basename = cfg_io.DEFAULT_BMODEL_BASENAME
    cali = str(
        ov.get("calibration_dataset_dir")
        or file_cfg.get("calibration_dataset_dir")
        or ""
    ).strip()
    qtable_fn_cfg = str(
        ov.get("qtable_filename") or file_cfg.get("qtable_filename") or cfg_io.DEFAULT_QTABLE_FILENAME
    )
    qtable_in_export = out_dir / "models" / "onnx" / qtable_fn_cfg
    qtable = str(ov.get("quantize_table_path") or file_cfg.get("quantize_table_path") or "").strip()
    if qtable_in_export.is_file():
        qtable = str(qtable_in_export.resolve())
    batches = str(file_cfg.get("int8_batches") or "1").strip()
    cali_dir = str(
        ov.get("calibration_dataset_dir") or file_cfg.get("calibration_dataset_dir") or ""
    ).strip()
    if cali_dir and Path(cali_dir).is_dir():
        from labelu.internal.common.yolo_bm1688_calibration import count_calibration_images

        cali_in = count_calibration_images(Path(cali_dir))
    else:
        cali_in = int(file_cfg.get("cali_input_num") or 128)
    cali_in = max(1, cali_in)
    cali_inf = int(file_cfg.get("cali_inference_num") or 10)

    platforms = cfg_io.parse_compile_platforms(file_cfg.get("compile_platforms"))
    platforms_arg = ",".join(platforms)
    primary_chip = platforms[0] if platforms else "bm1688"

    qtable_arg = f'"{qtable}"' if qtable and Path(qtable).is_file() else '""'
    cali_arg = f'"{cali}"' if cali else '""'

    from labelu.internal.common.bm1688_deploy_bootstrap import discover_tpu_mlir_python

    tpu_py = str(file_cfg.get("tpu_mlir_python") or "").strip()
    from labelu.internal.common.bm1688_deploy_bootstrap import _is_invalid_tpu_mlir_python

    if not tpu_py or _is_invalid_tpu_mlir_python(tpu_py):
        discovered = discover_tpu_mlir_python()
        if discovered:
            tpu_py = discovered
    py_export = (
        f'export TPU_MLIR_PYTHON="{tpu_py}" && ' if tpu_py else ""
    )
    path_prefix = ""
    if tpu_py:
        # 勿 resolve：venv/bin/python3 -> /usr/bin/python3.10 会把 PATH 写错
        py_dir = str(Path(tpu_py).parent)
        path_prefix = f'export PATH="{py_dir}:/usr/bin:/bin:/usr/local/bin" && '
    elif conda310.is_file():
        path_prefix = 'export PATH="/root/miniconda3/bin:/usr/bin:/bin" && '

    compile_bin = str(Path(tpu_py).parent) if tpu_py else "/data/labeIU/labeIuMedia/venv/bin"
    cmd = (
        f'export TPU_MLIR_ROOT="{root}" && '
        f'export RELEASE_PATH="{root}" && '
        f'export PROJECT_ROOT="{root}" && '
        f'export TPUC_ROOT="{root}" && '
        f'export LABELU_VENV_BIN="{compile_bin}" && '
        f"{py_export}"
        f"{path_prefix}"
        f"unset PYTHONPATH && "
        f'export LD_LIBRARY_PATH="${{LD_LIBRARY_PATH:-}}" && '
        f'export CALI_INPUT_NUM={cali_in} && '
        f'export CALI_INFERENCE_NUM={cali_inf} && '
        f'bash "{script}" '
        f'"{onnx_abs.resolve()}" "{out_dir.resolve()}" {int(imgsz)} {primary_chip} {mode} '
        f'{cali_arg} {qtable_arg} "{batches}" "{basename}" "{platforms_arg}"'
    )
    return True, cmd
