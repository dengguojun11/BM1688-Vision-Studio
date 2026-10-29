"""BM1688 编译链的本地证据契约。

该模块只判断导出目录和制品清单是否满足编译前/后置条件，不执行
TPU-MLIR，也不把模拟文件当作真实 BM1688 运行证据。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUIRED_PRECISIONS = ("fp32", "fp16", "int8")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
BM1688_PROFILE_SCHEMA_VERSION = "bm1688.profile.v1"
DEFAULT_BM1688_PROFILE_ID = "bm1688-yolov11-detection-v1"


def _sha256_file(path: Path | str | None) -> str | None:
    """计算文件摘要；不存在或不可读时返回 None，不伪造工具链证据。"""
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _directory_sha256(path: Path | str | None) -> str | None:
    """按相对路径、文件大小和内容摘要生成校准集摘要。"""
    if not path:
        return None
    root = Path(path)
    if not root.is_dir():
        return None
    files: list[dict[str, Any]] = []
    for item in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if not item.is_file():
            continue
        digest = _sha256_file(item)
        if not digest:
            continue
        try:
            relative = item.relative_to(root).as_posix()
            size = int(item.stat().st_size)
        except OSError:
            continue
        files.append({"path": relative, "bytes": size, "sha256": digest})
    if not files:
        return None
    content = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _profile_without_digest(profile: Mapping[str, Any]) -> dict[str, Any]:
    """返回用于签名的 Profile；签名字段自身不能参与签名。"""
    return {
        str(key): value
        for key, value in profile.items()
        if str(key) not in {"profile_digest", "snapshot_digest"}
    }


def profile_snapshot_digest(profile: Mapping[str, Any]) -> str:
    """对 Profile 快照做确定性摘要，供清单、门禁和后续真机证据复用。"""
    content = json.dumps(
        _profile_without_digest(profile),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _first_value(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _profile_section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def build_bm1688_profile_snapshot(
    *,
    model_family: str | None = None,
    model_stem: str | None = None,
    imgsz: int | Sequence[int] = 640,
    compiler_config: Mapping[str, Any] | None = None,
    class_names: Sequence[str] | None = None,
    calibration_dir: Path | str | None = None,
    qtable_path: Path | str | None = None,
    model_weights_sha256: str | None = None,
    model_weights_path: Path | str | None = None,
) -> dict[str, Any]:
    """构建版本化 BM1688 Profile 快照。

    该函数只组装编译和推理契约，不探测或执行 TPU-MLIR/BM1688。配置中的
    ``bm1688_profile`` 可以覆盖默认的 YOLO11 检测字段，历史配置没有该字段时
    仍使用现有 yolov11/sophon 默认口径。
    """
    config = dict(compiler_config or {})
    profile_config = _profile_section(config, "bm1688_profile")
    if isinstance(profile_config.get("input_size"), (list, tuple)):
        configured_size = profile_config.get("input_size")
    else:
        configured_size = None
    if isinstance(imgsz, (list, tuple)):
        values = list(imgsz)
        width = int(values[0]) if values else 640
        height = int(values[1]) if len(values) > 1 else width
    else:
        width = height = int(imgsz or 640)
    if configured_size:
        width = int(configured_size[0])
        height = int(configured_size[1] if len(configured_size) > 1 else configured_size[0])

    onnx = {
        "input_imgsz": int(
            _first_value(
                profile_config.get("onnx_input_imgsz"),
                config.get("onnx_input_imgsz"),
                max(width, height),
            )
        ),
        "opset": int(_first_value(profile_config.get("onnx_opset"), config.get("onnx_opset"), 17)),
        "dynamic": bool(_first_value(profile_config.get("onnx_dynamic"), config.get("onnx_dynamic"), False)),
        "batch": int(_first_value(profile_config.get("onnx_batch"), config.get("onnx_batch"), 1)),
        "simplify": bool(_first_value(profile_config.get("onnx_simplify"), config.get("onnx_simplify"), True)),
        "export_backend": str(
            _first_value(
                profile_config.get("onnx_export_backend"),
                config.get("onnx_export_backend"),
                "sophon",
            )
        ).strip().lower(),
    }
    input_contract = {
        "size": [width, height],
        "batch": onnx["batch"],
        "layout": str(_first_value(profile_config.get("input_layout"), "NCHW")),
        "color": str(_first_value(profile_config.get("input_color"), "RGB")),
        "dtype": str(_first_value(profile_config.get("input_dtype"), "float32")),
        "normalization": _first_value(
            profile_config.get("normalization"),
            {"scale": 0.00392156862745098, "mean": [0.0, 0.0, 0.0], "std": [1.0, 1.0, 1.0]},
        ),
        "resize": str(_first_value(profile_config.get("resize"), "letterbox")),
    }
    preprocess = _profile_section(profile_config, "preprocess")
    preprocess = {
        "version": str(_first_value(preprocess.get("version"), profile_config.get("preprocess_version"), "yolo-letterbox-v1")),
        "resize": str(_first_value(preprocess.get("resize"), input_contract["resize"], "letterbox")),
        "color": str(_first_value(preprocess.get("color"), input_contract["color"], "RGB")),
        "layout": str(_first_value(preprocess.get("layout"), input_contract["layout"], "NCHW")),
        "coordinate_restore": str(_first_value(preprocess.get("coordinate_restore"), "ratio_pad")),
    }
    output = _profile_section(profile_config, "output")
    output_contract = {
        "layout": str(_first_value(output.get("layout"), profile_config.get("output_layout"), "[batch,4+classes,anchors]")),
        "order": str(_first_value(output.get("order"), profile_config.get("output_order"), "xywh_then_class_scores")),
        "nodes": list(_first_value(output.get("nodes"), profile_config.get("output_nodes"), [])),
        "shape": list(_first_value(output.get("shape"), profile_config.get("output_shape"), [])),
    }
    postprocess = _profile_section(profile_config, "postprocess")
    postprocess_contract = {
        "version": str(_first_value(postprocess.get("version"), profile_config.get("postprocess_version"), "yolo-detection-v1")),
        "box_format": str(_first_value(postprocess.get("box_format"), "xywh")),
        "confidence": str(_first_value(postprocess.get("confidence"), "class_score")),
        "nms": str(_first_value(postprocess.get("nms"), "class_aware")),
        "max_detections": int(_first_value(postprocess.get("max_detections"), 300)),
    }
    toolchain = _profile_section(profile_config, "toolchain")
    toolchain.update(
        {
            "tpu_mlir_version": _first_value(
                toolchain.get("tpu_mlir_version"), config.get("compiler_version"), config.get("tpu_mlir_version")
            ),
            "tpu_mlir_digest": _first_value(
                toolchain.get("tpu_mlir_digest"), config.get("tpu_mlir_digest"), config.get("toolchain_digest")
            ),
            "docker_image_digest": _first_value(
                toolchain.get("docker_image_digest"), config.get("docker_image_digest")
            ),
            "runtime_abi": _first_value(
                toolchain.get("runtime_abi"), config.get("bm1688_runtime_abi")
            ),
        }
    )
    qtable = Path(qtable_path) if qtable_path else None
    if qtable is None:
        configured_qtable = _first_value(
            profile_config.get("qtable_path"), config.get("quantize_table_path")
        )
        qtable = Path(str(configured_qtable)) if configured_qtable else None
    calibration_count = _image_count(calibration_dir)
    calibration = _profile_section(profile_config, "calibration")
    calibration.update(
        {
            "dataset_sha256": _first_value(
                calibration.get("dataset_sha256"), config.get("calibration_dataset_sha256"), _directory_sha256(calibration_dir)
            ),
            "sample_count": int(_first_value(calibration.get("sample_count"), config.get("calibration_sample_count"), calibration_count)),
            "qtable": {
                "filename": _first_value(calibration.get("qtable", {}).get("filename") if isinstance(calibration.get("qtable"), Mapping) else None, qtable.name if qtable else None),
                "sha256": _first_value(calibration.get("qtable", {}).get("sha256") if isinstance(calibration.get("qtable"), Mapping) else None, config.get("qtable_sha256"), _sha256_file(qtable)),
            },
        }
    )
    names = [str(item) for item in (class_names or profile_config.get("class_names") or [])]
    profile: dict[str, Any] = {
        "schema_version": str(_first_value(profile_config.get("schema_version"), BM1688_PROFILE_SCHEMA_VERSION)),
        "profile_id": str(_first_value(profile_config.get("profile_id"), config.get("profile_id"), DEFAULT_BM1688_PROFILE_ID)),
        "model_family": str(_first_value(model_family, profile_config.get("model_family"), config.get("model_family"), "yolov11")),
        "model_stem": str(_first_value(model_stem, profile_config.get("model_stem"), config.get("bmodel_basename"), "yolov11n")),
        "chip": str(_first_value(profile_config.get("chip"), "bm1688")).lower(),
        "input": input_contract,
        "onnx": onnx,
        "preprocess": preprocess,
        "output": output_contract,
        "postprocess": postprocess_contract,
        "toolchain": toolchain,
        "calibration": calibration,
        "class_names": names,
        "model_weights_sha256": (
            model_weights_sha256
            or profile_config.get("model_weights_sha256")
            or _sha256_file(model_weights_path)
        ),
    }
    profile["profile_digest"] = profile_snapshot_digest(profile)
    profile["snapshot_digest"] = profile["profile_digest"]
    return profile


def validate_bm1688_profile_snapshot(profile: Mapping[str, Any] | None) -> list[str]:
    """校验 Profile 结构；返回机器可读原因，调用方决定是否阻断。"""
    if not isinstance(profile, Mapping):
        return ["profile_missing"]
    reasons: list[str] = []
    if str(profile.get("schema_version") or "") != BM1688_PROFILE_SCHEMA_VERSION:
        reasons.append("schema_version_invalid")
    for key in ("profile_id", "model_family", "model_stem", "chip"):
        if not str(profile.get(key) or "").strip():
            reasons.append(f"{key}_missing")
    if str(profile.get("chip") or "").lower() != "bm1688":
        reasons.append("chip_invalid")
    input_contract = profile.get("input")
    if not isinstance(input_contract, Mapping):
        reasons.append("input_contract_missing")
    else:
        size = input_contract.get("size")
        if (
            isinstance(size, (str, bytes))
            or not isinstance(size, Sequence)
            or len(size) != 2
            or any(int(value or 0) <= 0 for value in size)
        ):
            reasons.append("input_size_invalid")
        if int(input_contract.get("batch") or 0) != 1:
            reasons.append("input_batch_invalid")
        if not str(input_contract.get("layout") or "").strip():
            reasons.append("input_layout_missing")
    onnx = profile.get("onnx")
    if not isinstance(onnx, Mapping):
        reasons.append("onnx_contract_missing")
    else:
        if int(onnx.get("opset") or 0) < 1:
            reasons.append("onnx_opset_invalid")
        if onnx.get("dynamic") is not False:
            reasons.append("onnx_dynamic_invalid")
        if int(onnx.get("batch") or 0) != 1:
            reasons.append("onnx_batch_invalid")
    for key in ("preprocess", "output", "postprocess", "toolchain", "calibration"):
        if not isinstance(profile.get(key), Mapping):
            reasons.append(f"{key}_contract_missing")
    output = profile.get("output")
    if isinstance(output, Mapping) and not str(output.get("layout") or "").strip():
        reasons.append("output_layout_missing")
    postprocess = profile.get("postprocess")
    if isinstance(postprocess, Mapping) and not str(postprocess.get("version") or "").strip():
        reasons.append("postprocess_version_missing")
    digest = str(profile.get("profile_digest") or profile.get("snapshot_digest") or "").strip()
    if digest and digest != profile_snapshot_digest(profile):
        reasons.append("profile_digest_mismatch")
    return reasons


def build_onnx_export_contract(*, sophon_export: bool, imgsz: int) -> dict[str, Any]:
    """返回实际使用的 ONNX 导出参数，避免页面/清单写入过期默认值。"""
    return {
        "onnx_input_imgsz": int(imgsz),
        # Sophon Export Guide 的 YOLO 检测链固定使用 opset 17；兼容的
        # Ultralytics 直出分支仍保留当前实际使用的 opset 12。
        "onnx_opset": 17 if sophon_export else 12,
        "onnx_dynamic": False,
        "onnx_batch": 1,
        "onnx_simplify": True,
        "onnx_export_backend": "sophon" if sophon_export else "ultralytics",
    }


def _image_count(path: Path | str | None) -> int:
    if not path:
        return 0
    root = Path(path)
    if not root.is_dir():
        return 0
    return sum(
        1
        for item in root.rglob("*")
        if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
    )


def _expected_bmodel_name(stem: str, precision: str) -> str:
    return f"{stem}_{precision}_1b.bmodel"


def evaluate_bm1688_compile_contract(
    export_root: Path | str,
    *,
    model_stem: str,
    mode: str,
    platforms: Iterable[str] = ("bm1688",),
    onnx_path: Path | str | None = None,
    calibration_dir: Path | str | None = None,
    compiler_config: dict[str, Any] | None = None,
    artifact_manifest: dict[str, Any] | None = None,
    fixed_eval_evidence: dict[str, Any] | None = None,
    bm1688_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """检查编译证据是否足以进入下一阶段。

    ``passed`` 为假时调用方必须保持待编译/待复核状态。固定评估集是发布
    证据的一部分，编译阶段没有它时明确返回 ``fixed_eval_missing``，而不是
    猜测边端已经验证通过。
    """
    root = Path(export_root)
    cfg = compiler_config or {}
    normalized_mode = str(mode or "full").strip().lower()
    if normalized_mode == "f32":
        normalized_mode = "fp32"
    if normalized_mode not in {"fp32", "fp16", "int8", "int8_only", "full", "fp32_fp16"}:
        normalized_mode = "full"

    reasons: list[str] = []
    checks: dict[str, Any] = {}
    profile = bm1688_profile
    if profile is None and isinstance(cfg.get("bm1688_profile"), Mapping):
        profile = cfg.get("bm1688_profile")
    profile_reasons = validate_bm1688_profile_snapshot(profile) if profile is not None else []
    expected_profile_digest = profile_snapshot_digest(profile) if isinstance(profile, Mapping) else None
    manifest_profile = None
    if isinstance(artifact_manifest, Mapping):
        candidate = artifact_manifest.get("bm1688_profile")
        if not isinstance(candidate, Mapping):
            candidate = artifact_manifest.get("profile_snapshot")
        if not isinstance(candidate, Mapping):
            candidate = artifact_manifest.get("profile")
        if isinstance(candidate, Mapping):
            manifest_profile = candidate
    profile_check: dict[str, Any] = {
        "required": profile is not None,
        "present": profile is not None,
        "manifest_present": manifest_profile is not None,
        "digest": expected_profile_digest,
        "manifest_digest": (
            profile_snapshot_digest(manifest_profile)
            if isinstance(manifest_profile, Mapping)
            else None
        ),
        "reasons": list(profile_reasons),
    }
    if profile is not None and manifest_profile is None:
        profile_reasons.append("profile_missing_in_manifest")
    elif isinstance(profile, Mapping) and isinstance(manifest_profile, Mapping):
        if profile_snapshot_digest(manifest_profile) != expected_profile_digest:
            profile_reasons.append("profile_snapshot_mismatch")
    if profile_reasons:
        reasons.extend(f"profile:{item}" for item in profile_reasons)
    profile_check["reasons"] = list(profile_reasons)
    profile_check["passed"] = not profile_reasons
    checks["profile"] = profile_check
    if onnx_path:
        onnx_ok = Path(onnx_path).is_file()
        checks["onnx_exists"] = onnx_ok
        if not onnx_ok:
            reasons.append("onnx_missing")
    else:
        checks["onnx_exists"] = None

    backend = str(cfg.get("onnx_export_backend") or "").strip().lower()
    opset = cfg.get("onnx_opset")
    dynamic = cfg.get("onnx_dynamic")
    batch = cfg.get("onnx_batch")
    onnx_contract_ok = (
        backend == "sophon"
        and isinstance(opset, int)
        and opset >= 17
        and dynamic is False
        and batch == 1
    )
    checks["onnx_contract"] = {
        "backend": backend or None,
        "opset": opset,
        "dynamic": dynamic,
        "batch": batch,
        "passed": onnx_contract_ok,
    }
    if not onnx_contract_ok:
        reasons.append("onnx_export_contract_invalid")

    compiler_version = str(
        cfg.get("compiler_version") or cfg.get("tpu_mlir_version") or ""
    ).strip()
    checks["compiler_version_present"] = bool(compiler_version)
    if not compiler_version:
        reasons.append("compiler_version_missing")

    required = list(REQUIRED_PRECISIONS if normalized_mode in {"full", "fp32_fp16"} else ())
    if normalized_mode == "fp32":
        required = ["fp32"]
    elif normalized_mode == "fp16":
        required = ["fp16"]
    elif normalized_mode in {"int8", "int8_only"}:
        required = ["int8"]
    platform_results: dict[str, dict[str, Any]] = {}
    for raw_platform in platforms:
        platform = str(raw_platform or "").strip().lower()
        if not platform:
            continue
        platform_dir = {
            "bm1688": "BM1688",
            "bm1684x": "BM1684X",
            "cv186x": "CV186X",
        }.get(platform, platform.upper())
        missing = [
            precision
            for precision in required
            if not (root / "models" / platform_dir / _expected_bmodel_name(model_stem, precision)).is_file()
        ]
        platform_results[platform] = {
            "required_precisions": required,
            "missing_precisions": missing,
            "passed": not missing,
        }
        if missing:
            reasons.append(f"bmodel_missing:{platform}:{','.join(missing)}")
    checks["platforms"] = platform_results

    calibration_required = normalized_mode in {"int8", "int8_only", "full"}
    calibration_count = _image_count(calibration_dir)
    checks["calibration"] = {
        "required": calibration_required,
        "file_count": calibration_count,
    }
    if calibration_required and calibration_count <= 0:
        reasons.append("calibration_set_missing")

    manifest_ok = isinstance(artifact_manifest, dict) and bool(
        str(artifact_manifest.get("execution_signature") or "").strip()
    )
    checks["artifact_manifest"] = {
        "present": isinstance(artifact_manifest, dict),
        "execution_signature_present": manifest_ok,
    }
    if artifact_manifest is not None and not manifest_ok:
        reasons.append("artifact_manifest_signature_missing")

    checks["fixed_eval"] = {
        "required_for_release": True,
        "present": isinstance(fixed_eval_evidence, dict) and bool(fixed_eval_evidence),
    }
    if not checks["fixed_eval"]["present"]:
        reasons.append("fixed_eval_missing")

    return {
        "passed": not reasons,
        "status": "通过" if not reasons else "阻断",
        "mode": normalized_mode,
        "reasons": reasons,
        "checks": checks,
    }
