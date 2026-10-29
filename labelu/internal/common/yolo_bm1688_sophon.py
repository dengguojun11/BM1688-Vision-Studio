"""对齐《常规训练文档步骤》BM1688 量化：训练集校准、模型 stem 与 qtable 一致。"""
from __future__ import annotations

import hashlib
import random
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.yolo_bm1688_calibration import (
    calibration_options_from_cfg,
    count_calibration_images,
    count_train_images,
    load_class_names,
    sample_calibration_images,
    write_calibration_zip,
)

# Ultralytics 权重文件名 → TPU-MLIR / sophon-demo MODEL_NAME（脚本内 bmodel 前缀）
_WEIGHT_STEM_RE = re.compile(
    r"^(yolo(?:v)?11[nmslx])(?:\.pt)?$",
    re.IGNORECASE,
)


def ultralytics_weights_to_sophon_stem(model_weights: Optional[str]) -> str:
    """
    yolo11n.pt → yolov11n；yolo11s.pt → yolov11s（与 bm1688_onnx_to_bmodel.sh 的 MODEL_NAME 一致）。
    无法识别时回退 DEFAULT_BMODEL_BASENAME。
    """
    raw = (model_weights or "").strip()
    base = Path(raw).name.lower().removesuffix(".pt")
    m = _WEIGHT_STEM_RE.match(base)
    if m:
        token = m.group(1).lower()
        if token.startswith("yolo11") and not token.startswith("yolov"):
            return "yolov" + token[4:]
        return token
    if base.startswith("yolov11"):
        return base[:8] if len(base) >= 8 else base
    return cfg_io.DEFAULT_BMODEL_BASENAME


def onnx_filename_for_stem(stem: str) -> str:
    s = (stem or "").strip().lower()
    if s.startswith("yolov11"):
        size = s.replace("yolov11", "", 1) or "n"
        return f"yolo11{size}.onnx"
    return cfg_io.DEFAULT_ONNX_FILENAME


def qtable_filename_for_stem(stem: str) -> str:
    s = (stem or cfg_io.DEFAULT_BMODEL_BASENAME).strip() or cfg_io.DEFAULT_BMODEL_BASENAME
    return f"{s}_qtable"


def sigmoid_f16_qtable_line(fpfwd_outputs: str) -> str:
    """
    qtable 中检测头所需的 Sigmoid F16 行。

    若缺失该行，/model.23/Sigmoid 会被 INT8 量化，边端推理分数趋近 0 → 无检测框。
    """
    fpfwd = (fpfwd_outputs or "").strip()
    if "/model." in fpfwd:
        layer = fpfwd.split("/model.", 1)[1].split("/", 1)[0]
    else:
        layer = "23"
    return f"/model.{layer}/Sigmoid_output_0_Sigmoid F16"


def ensure_sigmoid_f16_in_qtable(
    qtable_path: Path,
    fpfwd_outputs: str,
) -> bool:
    """若 qtable 缺少 Sigmoid F16 行则追加（幂等）。"""
    if not qtable_path.is_file():
        return False
    want = sigmoid_f16_qtable_line(fpfwd_outputs).split()[0]
    text = qtable_path.read_text(encoding="utf-8", errors="replace")
    if want in text:
        return False
    with qtable_path.open("a", encoding="utf-8") as f:
        f.write(sigmoid_f16_qtable_line(fpfwd_outputs) + "\n")
    logger.info("appended missing Sigmoid F16 to qtable {}", qtable_path.name)
    return True


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_reused_yolov11s_qtable(qtable_path: Path, stem: str) -> bool:
    """检测 yolov11n 等 stem 是否误用了与内置 yolov11s 模板相同的 qtable。"""
    stem = (stem or "").strip().lower()
    if stem == "yolov11s" or not qtable_path.is_file():
        return False
    ref = cfg_io.default_qtable_path()
    if not ref.is_file():
        return False
    try:
        return file_md5(qtable_path) == file_md5(ref)
    except OSError:
        return False


def discover_qtable_path(
    stem: str,
    file_cfg: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """按 stem 查找 qtable；优先配置与环境，再 sophon-demo / 内置资源。"""
    stem = (stem or "").strip() or cfg_io.DEFAULT_BMODEL_BASENAME
    want_name = qtable_filename_for_stem(stem)
    cfg = file_cfg or cfg_io.load_bm1688_compiler_config()

    candidates: list[Path] = []
    qt_cfg = str(cfg.get("quantize_table_path") or "").strip()
    if qt_cfg:
        candidates.append(Path(qt_cfg))
    env_qt = (settings.YOLO_BM1688_QTABLE_PATH or "").strip()
    if env_qt:
        candidates.append(Path(env_qt))

    det = Path((cfg.get("sophon_det_root") or settings.SOPHON_DET_ROOT or "").strip())
    if det.is_dir():
        for sub in ("models/onnx", "onnx"):
            candidates.append(det / sub / want_name)
            candidates.append(det / sub / cfg_io.DEFAULT_QTABLE_FILENAME)

    bundled = cfg_io.default_qtable_path()
    if bundled.is_file():
        candidates.append(bundled)

    seen: set[str] = set()
    ordered: list[Path] = []
    for p in candidates:
        key = str(p)
        if key not in seen:
            seen.add(key)
            ordered.append(p)

    for p in ordered:
        if p.is_file() and stem in p.name.lower():
            resolved = p.resolve()
            if is_reused_yolov11s_qtable(resolved, stem):
                logger.error(
                    "拒绝使用与 yolov11s 模板 md5 相同的 {}（stem={}）；"
                    "请通过 Docker search_qtable 为本模型生成专属 qtable",
                    resolved.name,
                    stem,
                )
                return None
            return resolved
    return None


def prepare_calibration_from_dataset(
    dataset_root: Path,
    dest_dir: Path,
    *,
    count: Optional[int] = None,
    seed: Optional[int] = None,
    clear_output: bool = True,
    file_cfg: Optional[dict[str, Any]] = None,
) -> Path:
    """
    从训练作业 dataset 抽取 INT8 校准集（对齐文档 sample_calibration_images.py）。
    """
    cfg = file_cfg or cfg_io.load_bm1688_compiler_config()
    train_n = count_train_images(dataset_root)
    opts = calibration_options_from_cfg(cfg, train_count=train_n)
    n = int(count if count is not None else opts["count"])
    split_seed = int(
        seed if seed is not None else (opts["seed"] or settings.YOLO_TRAIN_SPLIT_SEED)
    )
    classes = load_class_names(dataset_root)
    return sample_calibration_images(
        dataset_root,
        dest_dir,
        class_names=classes or None,
        count=n,
        seed=split_seed,
        clear_output=clear_output,
        balance_scene=bool(opts["balance_scene"]),
        filename_prefixes=list(opts["filename_prefixes"]),
        infer_lighting_opencv=bool(opts["infer_lighting_opencv"]),
    )


def resolve_export_calibration_dir(
    *,
    export_root: Path,
    train_job_work_root: Optional[Path],
    file_cfg: Optional[dict[str, Any]] = None,
) -> tuple[str, str]:
    """
    返回 (calibration_dir, source_note)。
    优先本作业训练集按文档 3.5 抽样；否则用编译配置里的全局目录。
    """
    cfg = file_cfg or cfg_io.load_bm1688_compiler_config()
    dest = export_root / "calibration"

    if settings.YOLO_BM1688_USE_JOB_CALIBRATION and train_job_work_root:
        dataset_root = train_job_work_root / "dataset"
        train_img = dataset_root / "images" / "train"
        if train_img.is_dir():
            try:
                path = prepare_calibration_from_dataset(
                    dataset_root,
                    dest,
                    clear_output=True,
                    file_cfg=cfg,
                )
                train_n = count_train_images(dataset_root)
                picked_n = count_calibration_images(path)
                opts = calibration_options_from_cfg(cfg, train_count=train_n)
                zip_path = export_root / "calib.zip"
                try:
                    write_calibration_zip(path, zip_path)
                    zip_note = f" zip={zip_path.name}"
                except OSError as zexc:
                    logger.warning("calib.zip 生成失败: {}", zexc)
                    zip_note = ""
                return (
                    str(path),
                    (
                        f"job_balanced_calibration({path}) train={train_n} "
                        f"target={opts['count']} picked={picked_n} "
                        f"mode={opts['count_mode']}{zip_note}"
                    ),
                )
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("作业校准集生成失败，回退全局目录: {}", exc)

    cali = str(cfg.get("calibration_dataset_dir") or "").strip()
    if not cali:
        cali = (settings.YOLO_BM1688_CALIBRATION_DATASET_DIR or "").strip()
    if cali and Path(cali).is_dir():
        return cali, f"configured({cali})"
    return "", "missing"
