"""BM1688 编译配置：对齐 sophon-demo YOLOv8_plus_det §3.2 models/ 目录布局。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Tuple

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)

SOPHON_DOC_URL = (
    "https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/README.md"
)
SOPHON_EXPORT_GUIDE = (
    "https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/docs/YOLOv8_Export_Guide.md"
)

DEFAULT_BMODEL_BASENAME = "yolov11n"
DEFAULT_ONNX_FILENAME = "yolo11n.onnx"
DEFAULT_QTABLE_FILENAME = "yolov11n_qtable"
BM1688_PROFILE_SCHEMA_VERSION = "bm1688.profile.v1"
DEFAULT_BM1688_PROFILE_ID = "bm1688-yolov11-detection-v1"


def default_bm1688_profile_config() -> dict[str, Any]:
    """一期默认 Profile 的可配置部分；不改变既有 yolov11n 编译口径。

    该配置只描述输入/输出和前后处理约定，不代表已经具备真实 TPU-MLIR
    或 BM1688 证据。真实工具链版本、校准集摘要和 qtable 摘要由编译节点补齐。
    """
    return {
        "schema_version": BM1688_PROFILE_SCHEMA_VERSION,
        "profile_id": DEFAULT_BM1688_PROFILE_ID,
        "model_family": "yolov11",
        "chip": "bm1688",
        "input_layout": "NCHW",
        "input_color": "RGB",
        "input_dtype": "float32",
        "resize": "letterbox",
        "normalization": {
            "scale": 1.0 / 255.0,
            "mean": [0.0, 0.0, 0.0],
            "std": [1.0, 1.0, 1.0],
        },
        "output_layout": "[batch,4+classes,anchors]",
        "output_order": "xywh_then_class_scores",
        "preprocess_version": "yolo-letterbox-v1",
        "postprocess_version": "yolo-detection-v1",
        "output_nodes": [],
        "output_shape": [],
    }

# sophon-demo models/ 下平台目录名（与 gen_*bmodel_mlir.sh 的 target_dir 一致）
SOPHON_PLATFORM_DIRS: dict[str, str] = {
    "bm1684x": "BM1684X",
    "bm1688": "BM1688",
    "cv186x": "CV186X",
}
SOPHON_PLATFORM_DIR_NAMES: Tuple[str, ...] = tuple(SOPHON_PLATFORM_DIRS.values())


def chip_to_platform_dir(chip: str) -> str:
    key = (chip or "bm1688").strip().lower()
    return SOPHON_PLATFORM_DIRS.get(key, key.upper())


def parse_compile_platforms(raw: Any) -> List[str]:
    if isinstance(raw, list):
        parts = [str(x).strip().lower() for x in raw if str(x).strip()]
    else:
        parts = [p.strip().lower() for p in str(raw or "bm1688").split(",") if p.strip()]
    valid = [p for p in parts if p in SOPHON_PLATFORM_DIRS]
    return valid or ["bm1688"]


def canonical_bmodel_names(basename: str = DEFAULT_BMODEL_BASENAME) -> Tuple[str, ...]:
    """与 sophon-demo 的 {basename}_fp32/fp16/int8_1b.bmodel 命名一致（平台默认 yolov11n）。"""
    b = (basename or DEFAULT_BMODEL_BASENAME).strip() or DEFAULT_BMODEL_BASENAME
    return (
        f"{b}_fp32_1b.bmodel",
        f"{b}_fp16_1b.bmodel",
        f"{b}_int8_1b.bmodel",
    )


def sophon_models_root(export_root: Path) -> Path:
    return export_root / "models"


def sophon_onnx_dir(export_root: Path) -> Path:
    return sophon_models_root(export_root) / "onnx"


def sophon_platform_dir(export_root: Path, chip: str) -> Path:
    return sophon_models_root(export_root) / chip_to_platform_dir(chip)


def default_qtable_path() -> Path:
    import labelu

    return (
        Path(labelu.__file__).resolve().parent
        / "scripts"
        / "sophon_assets"
        / DEFAULT_QTABLE_FILENAME
    )


def config_file_path() -> Path:
    return Path(settings.BASE_DATA_DIR) / "bm1688_compiler_config.json"


def default_config() -> dict[str, Any]:
    dq = default_qtable_path()
    return {
        "tpu_mlir_root": "",
        "auto_compile": True,
        "quantize": "full",
        "bmodel_basename": DEFAULT_BMODEL_BASENAME,
        "onnx_filename": DEFAULT_ONNX_FILENAME,
        "qtable_filename": DEFAULT_QTABLE_FILENAME,
        "compile_platforms": "bm1688",
        "custom_compile_command": "",
        "calibration_dataset_dir": "",
        "quantize_table_path": str(dq) if dq.is_file() else "",
        "int8_batches": "1",
        "cali_input_num": 128,
        "cali_inference_num": 10,
        # 3.5 校准集抽样（对齐 sample_calibration_images.py，写入作业 dataset 后自动执行）
        "calibration_balance_scene": True,
        "calibration_filename_prefix": "",
        "calibration_infer_lighting_opencv": False,
        "calibration_seed": 0,
        "calibration_count_mode": "adaptive",
        "quantize_backend": "docker",
        "docker_fpfwd_outputs": "/model.23/dfl/conv/Conv_output_0_Conv",
        "sophon_export": True,
        "tpu_mlir_python": "",
        # 新增契约字段保持嵌套，旧编译参数和默认 yolov11n 行为不变。
        "bm1688_profile": default_bm1688_profile_config(),
    }


def load_bm1688_compiler_config_legacy() -> dict[str, Any]:
    path = config_file_path()
    base = default_config()
    if not path.is_file():
        return dict(base)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(base)
    if not isinstance(raw, dict):
        return dict(base)
    out = dict(base)
    root = str(raw.get("tpu_mlir_root") or "").strip()
    if root:
        out["tpu_mlir_root"] = root[:1024]
    if isinstance(raw.get("auto_compile"), bool):
        out["auto_compile"] = raw["auto_compile"]
    q = str(raw.get("quantize") or "full").strip().lower()
    if q in ("fp16", "f32", "int8", "int8_only", "full", "fp32_fp16"):
        out["quantize"] = q
    cmd = str(raw.get("custom_compile_command") or "").strip()
    if cmd:
        out["custom_compile_command"] = cmd[:8000]
    cali = str(raw.get("calibration_dataset_dir") or "").strip()
    if cali:
        out["calibration_dataset_dir"] = cali[:2048]
    qt = str(raw.get("quantize_table_path") or "").strip()
    if qt:
        out["quantize_table_path"] = qt[:2048]
    elif not out.get("quantize_table_path"):
        dq = default_qtable_path()
        if dq.is_file():
            out["quantize_table_path"] = str(dq)
    bb = str(raw.get("bmodel_basename") or DEFAULT_BMODEL_BASENAME).strip()
    if bb:
        out["bmodel_basename"] = bb[:64]
    onnx_fn = str(raw.get("onnx_filename") or DEFAULT_ONNX_FILENAME).strip()
    if onnx_fn:
        out["onnx_filename"] = Path(onnx_fn).name[:128]
    qtable_fn = str(raw.get("qtable_filename") or DEFAULT_QTABLE_FILENAME).strip()
    if qtable_fn:
        out["qtable_filename"] = Path(qtable_fn).name[:128]
    cp = raw.get("compile_platforms")
    if cp is not None:
        out["compile_platforms"] = ",".join(parse_compile_platforms(cp))
    batches = str(raw.get("int8_batches") or "1").strip()
    if batches:
        out["int8_batches"] = batches[:32]
    for key, default_val in (
        ("cali_input_num", 128),
        ("cali_inference_num", 10),
    ):
        try:
            out[key] = int(raw.get(key, default_val))
        except (TypeError, ValueError):
            out[key] = default_val
    if isinstance(raw.get("sophon_export"), bool):
        out["sophon_export"] = raw["sophon_export"]
    tpu_py = str(raw.get("tpu_mlir_python") or "").strip()
    if tpu_py:
        out["tpu_mlir_python"] = tpu_py[:1024]
    profile = raw.get("bm1688_profile")
    if isinstance(profile, dict):
        base_profile = default_bm1688_profile_config()
        base_profile.update(profile)
        out["bm1688_profile"] = base_profile
    from labelu.internal.common.bm1688_deploy_bootstrap import normalize_tpu_mlir_python_config

    return normalize_tpu_mlir_python_config(out)


def _legacy_bm1688_config_to_db_items(data: dict[str, Any]) -> dict[str, Any]:
    return {"config": load_bm1688_compiler_config_legacy() if not isinstance(data, dict) else data}


def _write_legacy_bm1688_compiler_config(data: dict[str, Any]) -> None:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_bm1688_compiler_config() -> dict[str, Any]:
    from labelu.internal.common.bm1688_deploy_bootstrap import normalize_tpu_mlir_python_config

    legacy = load_bm1688_compiler_config_legacy()
    db_config = read_group_values("bm1688_compiler_config").get("config")
    candidate = resolve_value(
        runtime_value=get_runtime_override("bm1688_compiler_config", "config"),
        db_value=db_config if isinstance(db_config, dict) else legacy,
        default_value=legacy,
    )
    if not isinstance(candidate, dict):
        candidate = legacy
    merged = dict(default_config())
    merged.update(candidate)
    return normalize_tpu_mlir_python_config(merged)


def save_bm1688_compiler_config(data: dict[str, Any]) -> None:
    from labelu.internal.common.bm1688_deploy_bootstrap import normalize_tpu_mlir_python_config

    payload = normalize_tpu_mlir_python_config(data)
    write_group_values(
        "bm1688_compiler_config",
        {"config": payload},
        value_source="db",
        change_reason="save bm1688 compiler config",
    )
    _write_legacy_bm1688_compiler_config(payload)
