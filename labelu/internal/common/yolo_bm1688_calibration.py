"""
BM1688 INT8 校准集抽样（对齐《常规训练文档》3.5 与 sample_calibration_images.py）。

在平台训练导出的 dataset（images/train + labelsxml/train + class_names.txt）上执行，
不依赖外部脚本路径；TPU-MLIR 仅消费输出目录中的图片。
"""
from __future__ import annotations

import json
import random
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# 文档建议 100～1000；默认 128；实际张数优先按 train 规模自适应（见 compute_adaptive_calibration_count）
CALIBRATION_COUNT_DEFAULT = 128
CALIBRATION_COUNT_MIN = 1
CALIBRATION_COUNT_MAX = 1000
CALIBRATION_ADAPTIVE_RATIO = 0.05
CALIBRATION_ADAPTIVE_FLOOR = 128
CALIBRATION_ADAPTIVE_CEILING = 1000

DEFAULT_BALANCE_SCENE = True
DEFAULT_INFER_LIGHTING_OPENCV = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
SCENE_ATTR_KEYS = ("sceneType", "lighting", "weather", "quality", "angle", "source")


def _parse_prefixes(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    return [p.strip() for p in text.split(",") if p.strip()]


def count_train_images(dataset_root: Path) -> int:
    train_img = dataset_root / "images" / "train"
    if not train_img.is_dir():
        return 0
    return sum(
        1 for p in train_img.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def compute_adaptive_calibration_count(train_count: int) -> int:
    """
    随训练集规模自适应：min(1000, max(128, int(train*0.05)))，再不超过 train_count。
    """
    if train_count <= 0:
        return CALIBRATION_COUNT_DEFAULT
    n = min(
        CALIBRATION_ADAPTIVE_CEILING,
        max(CALIBRATION_ADAPTIVE_FLOOR, int(train_count * CALIBRATION_ADAPTIVE_RATIO)),
    )
    return max(CALIBRATION_COUNT_MIN, min(n, train_count))


def resolve_calibration_count(train_count: int, file_cfg: Optional[dict[str, Any]]) -> int:
    """
    adaptive（默认）：按 train 规模公式；fixed：使用 cali_input_num（仍不超过 train_count）。
    cali_input_num 在 adaptive 下仅作上限（>0 时 min(adaptive, cap)）。
    """
    cfg = file_cfg or {}
    mode = str(cfg.get("calibration_count_mode") or "adaptive").strip().lower()
    if mode == "fixed":
        manual = int(cfg.get("cali_input_num") or CALIBRATION_COUNT_DEFAULT)
        manual = max(CALIBRATION_COUNT_MIN, min(CALIBRATION_COUNT_MAX, manual))
        if train_count > 0:
            return max(1, min(manual, train_count))
        return manual
    adaptive = compute_adaptive_calibration_count(train_count)
    cap = int(cfg.get("cali_input_num") or 0)
    if cap > 0:
        cap = max(CALIBRATION_COUNT_MIN, min(CALIBRATION_COUNT_MAX, cap))
        adaptive = min(adaptive, cap)
    if train_count > 0:
        return max(1, min(adaptive, train_count))
    return adaptive


def count_calibration_images(calib_dir: Path) -> int:
    calib_dir = Path(calib_dir)
    if not calib_dir.is_dir():
        return 0
    return sum(
        1 for p in calib_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def calibration_options_from_cfg(
    file_cfg: Optional[dict[str, Any]],
    *,
    train_count: int = 0,
) -> dict[str, Any]:
    """从 bm1688_compiler_config.json 读取校准抽样参数（不新增 .env）。"""
    cfg = file_cfg or {}
    count = resolve_calibration_count(train_count, cfg)
    return {
        "count": count,
        "train_count": train_count,
        "count_mode": str(cfg.get("calibration_count_mode") or "adaptive"),
        "balance_scene": bool(cfg.get("calibration_balance_scene", DEFAULT_BALANCE_SCENE)),
        "filename_prefixes": _parse_prefixes(cfg.get("calibration_filename_prefix")),
        "infer_lighting_opencv": bool(
            cfg.get("calibration_infer_lighting_opencv", DEFAULT_INFER_LIGHTING_OPENCV)
        ),
        "seed": int(cfg.get("calibration_seed") or 0),
    }


def load_class_names(dataset_root: Path) -> list[str]:
    cn = dataset_root / "class_names.txt"
    if cn.is_file():
        names = []
        for line in cn.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)
        if names:
            return names
    data_yaml = dataset_root / "data.yaml"
    if not data_yaml.is_file():
        return []
    names = []
    in_names = False
    for line in data_yaml.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("names:"):
            in_names = True
            continue
        if in_names:
            m = re.match(r"^\s*(\d+)\s*:\s*(.+)$", line)
            if m:
                names.append(m.group(2).strip().strip("'\""))
            elif line.strip() and not line.strip().startswith("#"):
                in_names = False
    return names


def _parse_int(root, path: str, default: int = 0) -> int:
    text = root.findtext(path)
    try:
        return int(float(text)) if text is not None else default
    except ValueError:
        return default


def _read_box(obj) -> Optional[tuple[int, int, int, int]]:
    box = obj.find("bndbox")
    if box is None:
        return None
    return (
        _parse_int(box, "xmin"),
        _parse_int(box, "ymin"),
        _parse_int(box, "xmax"),
        _parse_int(box, "ymax"),
    )


def _object_size_bin(box: tuple[int, int, int, int], image_w: int, image_h: int) -> str:
    if image_w <= 0 or image_h <= 0:
        return "unknown"
    xmin, ymin, xmax, ymax = box
    area_ratio = max(0, xmax - xmin) * max(0, ymax - ymin) / float(image_w * image_h)
    if area_ratio < 0.0004:
        return "tiny"
    if area_ratio < 0.0025:
        return "small"
    if area_ratio < 0.04:
        return "medium"
    return "large"


def _read_extra_attrs(root) -> list[str]:
    """兼容平台 VOC（子节点）与历史 JSON 文本两种 extra_annotations。"""
    attrs: list[str] = []
    extra = root.find("extra_annotations")
    if extra is not None and len(extra):
        for child in extra:
            text = (child.text or "").strip()
            if text:
                attrs.append(f"{child.tag}:{text}")
        return attrs

    text = (root.findtext("extra_annotations") or "").strip()
    if not text:
        return attrs
    try:
        extra_items = json.loads(text)
    except json.JSONDecodeError:
        return attrs
    if not isinstance(extra_items, list):
        return attrs
    for item in extra_items:
        if not isinstance(item, dict):
            continue
        for result in item.get("result") or []:
            if not isinstance(result, dict):
                continue
            value = result.get("value")
            if not isinstance(value, dict):
                continue
            for key, values in value.items():
                if not isinstance(values, list):
                    values = [values]
                for attr_value in values:
                    if attr_value:
                        attrs.append(f"{key}:{attr_value}")
    return attrs


def _has_attr(attrs: list[str], key: str) -> bool:
    prefix = f"{key}:"
    return any(a.startswith(prefix) for a in attrs)


def _filename_matches_prefix(filename: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    name = Path(filename).name
    return any(name.startswith(prefix) for prefix in prefixes)


def _augment_attrs(
    labels: list[str],
    size_bins: list[str],
    attrs: list[str],
    *,
    lighting_hint: Optional[str] = None,
) -> list[str]:
    attrs = list(attrs)
    if not _has_attr(attrs, "sceneType"):
        attrs.append("sceneType:unknown")
    if not _has_attr(attrs, "lighting"):
        attrs.append(f"lighting:{lighting_hint or 'unknown'}")
    for key in SCENE_ATTR_KEYS:
        if not _has_attr(attrs, key):
            attrs.append(f"{key}:unknown")
    for size_bin in sorted(set(size_bins)):
        attrs.append(f"targetSize:{size_bin}")
    if any(s in ("tiny", "small") for s in size_bins):
        attrs.append("targetDistance:far")
    if "medium" in size_bins:
        attrs.append("targetDistance:middle")
    if "large" in size_bins:
        attrs.append("targetDistance:near")
    n = len(labels)
    if n <= 1:
        attrs.append("targetCount:single")
    elif n <= 5:
        attrs.append("targetCount:few")
    else:
        attrs.append("targetCount:dense")
    attrs.append("classDiversity:multi" if len(set(labels)) > 1 else "classDiversity:single")
    return attrs


def _balance_attrs_for_sample(attrs: list[str], balance_scene: bool) -> list[str]:
    keys = ["lighting", "targetSize", "targetDistance", "targetCount", "classDiversity"]
    if balance_scene:
        keys.insert(0, "sceneType")
    selected = []
    for key in keys:
        selected.extend(a for a in attrs if a.startswith(f"{key}:"))
    return selected


def _class_factor_tokens(labels: list[str], attrs: list[str], balance_scene: bool) -> list[str]:
    factor_attrs = _balance_attrs_for_sample(attrs, balance_scene)
    tokens = []
    for label in set(labels):
        for attr in factor_attrs:
            tokens.append(f"{label}|{attr}")
    return tokens


def _read_xml_labels(xml_path: Path) -> tuple[str, list[str], list[str], list[str]]:
    import xml.etree.ElementTree as ET

    root = ET.parse(xml_path).getroot()
    filename = root.findtext("filename") or f"{xml_path.stem}.jpg"
    image_w = _parse_int(root, "size/width")
    image_h = _parse_int(root, "size/height")
    attrs = _read_extra_attrs(root)
    labels: list[str] = []
    size_bins: list[str] = []
    for obj in root.findall("object"):
        name = obj.findtext("name")
        if not name:
            continue
        labels.append(name.strip())
        box = _read_box(obj)
        size_bins.append(
            _object_size_bin(box, image_w, image_h) if box is not None else "unknown"
        )
    return filename, labels, size_bins, attrs


def _find_image(image_dir: Path, filename: str) -> Optional[Path]:
    image_path = image_dir / filename
    if image_path.is_file():
        return image_path
    stem = Path(filename).stem
    for ext in IMAGE_EXTS:
        candidate = image_dir / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _replace_attr(attrs: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}:"
    replaced = False
    updated = []
    for attr in attrs:
        if attr.startswith(prefix):
            if not replaced:
                updated.append(f"{key}:{value}")
                replaced = True
            continue
        updated.append(attr)
    if not replaced:
        updated.append(f"{key}:{value}")
    return updated


def _refresh_balance_fields(sample: dict[str, Any], balance_scene: bool) -> None:
    sample["attr_set"] = set(sample["attrs"])
    sample["balance_attrs"] = _balance_attrs_for_sample(sample["attrs"], balance_scene)
    sample["balance_attr_set"] = set(sample["balance_attrs"])
    sample["factor_tokens"] = _class_factor_tokens(
        sample["labels"], sample["attrs"], balance_scene
    )
    sample["factor_token_set"] = set(sample["factor_tokens"])


def _infer_lighting_opencv(image_path: Path) -> str:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return "unknown"
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        return "unknown"
    img = cv2.resize(img, (320, 320), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2].astype(np.float32)
    mean_v = float(v.mean())
    p90_v = float(np.percentile(v, 90))
    dark_ratio = float((v < 50).mean())
    bright_ratio = float((v > 220).mean())
    if mean_v >= 115 and dark_ratio < 0.45:
        return "day"
    if mean_v <= 70 or dark_ratio > 0.58:
        return "nightWithLight" if bright_ratio > 0.015 or p90_v > 160 else "nightNoLight"
    return "dusk"


def _collect_samples(
    image_dir: Path,
    xml_dir: Path,
    classes: list[str],
    *,
    filename_prefixes: list[str],
    balance_scene: bool,
) -> tuple[list[dict[str, Any]], int]:
    class_set = set(classes)
    samples: list[dict[str, Any]] = []
    skipped_prefix = 0
    for xml_path in sorted(xml_dir.glob("*.xml")):
        filename, labels, size_bins, attrs = _read_xml_labels(xml_path)
        if not _filename_matches_prefix(filename, filename_prefixes):
            skipped_prefix += 1
            continue
        pairs = [
            (label, size_bin)
            for label, size_bin in zip(labels, size_bins)
            if label in class_set
        ]
        labels = [p[0] for p in pairs]
        size_bins = [p[1] for p in pairs]
        if not labels:
            continue
        image_path = _find_image(image_dir, filename)
        if image_path is None:
            continue
        need_lighting = not _has_attr(attrs, "lighting")
        attrs = _augment_attrs(labels, size_bins, attrs)
        balance_attrs = _balance_attrs_for_sample(attrs, balance_scene)
        factor_tokens = _class_factor_tokens(labels, attrs, balance_scene)
        samples.append(
            {
                "image": image_path,
                "xml": xml_path,
                "labels": labels,
                "label_set": set(labels),
                "size_bins": size_bins,
                "size_bin_set": set(size_bins),
                "attrs": attrs,
                "balance_attrs": balance_attrs,
                "balance_attr_set": set(balance_attrs),
                "factor_tokens": factor_tokens,
                "factor_token_set": set(factor_tokens),
                "need_lighting_infer": need_lighting,
            }
        )
    return samples, skipped_prefix


def _sample_balanced(
    samples: list[dict[str, Any]],
    classes: list[str],
    count: int,
    seed: int,
    *,
    balance_scene: bool,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    candidates = list(samples)
    rng.shuffle(candidates)

    selected: list[dict[str, Any]] = []
    selected_paths: set[Path] = set()
    class_image_counts: Counter = Counter()
    class_image_total: Counter = Counter()
    size_counts: Counter = Counter()
    size_total: Counter = Counter()
    attr_counts: Counter = Counter()
    attr_total: Counter = Counter()
    factor_counts: Counter = Counter()
    factor_total: Counter = Counter()

    for sample in candidates:
        class_image_total.update(sample["label_set"])
        size_total.update(sample["size_bins"])
        attr_total.update(sample["balance_attrs"])
        factor_total.update(sample["factor_tokens"])

    active_classes = [c for c in classes if class_image_total[c] > 0]
    if not active_classes:
        return []

    class_image_target = count / len(active_classes)

    while candidates and len(selected) < count:
        best_index = None
        best_score = None
        for idx, sample in enumerate(candidates):
            if sample["image"] in selected_paths:
                continue
            score = 0.0
            for label in sample["label_set"]:
                if class_image_total[label] == 0:
                    continue
                deficit = max(0.0, class_image_target - class_image_counts[label])
                score += 20.0 * deficit / max(class_image_target, 1.0)
                score += 8.0 / (1 + class_image_counts[label])
            for token in sample["factor_token_set"]:
                if factor_total[token] == 0:
                    continue
                score += 2.0 / (1 + factor_counts[token])
            for size_bin in sample["size_bin_set"]:
                if size_total[size_bin] == 0:
                    continue
                score += 0.8 / (1 + size_counts[size_bin])
            for attr in sample["balance_attr_set"]:
                if attr_total[attr] == 0:
                    continue
                score += 0.25 / (1 + attr_counts[attr])
            under = sum(
                1 for label in sample["label_set"] if class_image_counts[label] < class_image_target
            )
            score += under * 1.0
            if best_score is None or score > best_score:
                best_score = score
                best_index = idx
        if best_index is None:
            break
        sample = candidates.pop(best_index)
        selected.append(sample)
        selected_paths.add(sample["image"])
        class_image_counts.update(sample["label_set"])
        size_counts.update(sample["size_bins"])
        attr_counts.update(sample["balance_attrs"])
        factor_counts.update(sample["factor_tokens"])

    return selected


def _infer_lighting_for_selected(
    selected: list[dict[str, Any]],
    *,
    balance_scene: bool,
) -> int:
    inferred = 0
    for sample in selected:
        if not sample.get("need_lighting_infer"):
            continue
        lighting = _infer_lighting_opencv(sample["image"])
        sample["attrs"] = _replace_attr(sample["attrs"], "lighting", lighting)
        _refresh_balance_fields(sample, balance_scene)
        inferred += 1
    return inferred


def _fallback_random_sample(
    image_dir: Path,
    dest_dir: Path,
    *,
    count: int,
    seed: int,
    clear_output: bool,
) -> Path:
    images = sorted(p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise ValueError(f"训练集目录无可用图片: {image_dir}")
    n = max(1, min(count, len(images)))
    picked = random.Random(seed).sample(images, n)
    if clear_output and dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in picked:
        shutil.copy2(src, dest_dir / src.name)
    logger.warning(
        "BM1688 calibration: labelsxml 不可用，已回退为 train 随机抽样 {} 张",
        len(picked),
    )
    return dest_dir.resolve()


def sample_calibration_images(
    dataset_root: Path,
    dest_dir: Path,
    *,
    class_names: Optional[list[str]] = None,
    count: int = CALIBRATION_COUNT_DEFAULT,
    seed: int = 0,
    clear_output: bool = True,
    balance_scene: bool = DEFAULT_BALANCE_SCENE,
    filename_prefixes: Optional[list[str]] = None,
    infer_lighting_opencv: bool = DEFAULT_INFER_LIGHTING_OPENCV,
) -> Path:
    """
    从 dataset_root 的 train 划分抽取校准图片到 dest_dir。
    优先 labelsxml/train + 类别均衡；无 XML 时回退随机抽样。
    """
    dataset_root = dataset_root.resolve()
    image_dir = dataset_root / "images" / "train"
    xml_dir = dataset_root / "labelsxml" / "train"
    if not image_dir.is_dir():
        raise FileNotFoundError(f"训练集图片目录不存在: {image_dir}")

    classes = class_names or load_class_names(dataset_root)
    if not classes:
        raise ValueError(
            f"未找到类别列表（class_names.txt / data.yaml）: {dataset_root}"
        )

    prefixes = filename_prefixes or []
    count = max(CALIBRATION_COUNT_MIN, min(CALIBRATION_COUNT_MAX, int(count)))

    if not xml_dir.is_dir() or not any(xml_dir.glob("*.xml")):
        return _fallback_random_sample(
            image_dir, dest_dir, count=count, seed=seed, clear_output=clear_output
        )

    samples, skipped_prefix = _collect_samples(
        image_dir,
        xml_dir,
        classes,
        filename_prefixes=prefixes,
        balance_scene=balance_scene,
    )
    if not samples:
        raise ValueError(
            "校准集抽样：labelsxml/train 无可用样本（检查类别、filename_prefix 或标注）"
        )

    target = min(count, len(samples))
    if infer_lighting_opencv:
        pool_size = min(len(samples), max(target * 4, min(512, len(samples))))
        if pool_size < len(samples):
            pre = _sample_balanced(
                samples, classes, pool_size, seed, balance_scene=balance_scene
            )
            samples = pre
        _infer_lighting_for_selected(samples, balance_scene=balance_scene)

    selected = _sample_balanced(
        samples, classes, target, seed, balance_scene=balance_scene
    )
    if not selected:
        raise ValueError("校准集均衡抽样未选出任何图片")

    if clear_output and dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied_names: list[str] = []
    for sample in selected:
        dst = dest_dir / sample["image"].name
        shutil.copy2(sample["image"], dst)
        copied_names.append(dst.name)

    report = {
        "method": "balanced_labelsxml",
        "selected_images": len(selected),
        "requested_count": count,
        "effective_count": len(selected),
        "balance_scene": balance_scene,
        "filename_prefixes": prefixes,
        "skipped_by_prefix": skipped_prefix,
        "infer_lighting_opencv": infer_lighting_opencv,
        "classes": classes,
        "images": copied_names,
    }
    (dest_dir / "sample_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest_dir / "selected_images.txt").write_text(
        "\n".join(copied_names) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "BM1688 calibration: balanced {} / {} from {} (balance_scene={} prefixes={})",
        len(selected),
        len(samples),
        image_dir,
        balance_scene,
        prefixes or "none",
    )
    return dest_dir.resolve()


def write_calibration_zip(calib_dir: Path, zip_path: Path) -> Path:
    """对齐文档：在上级目录生成 calib.zip（仅包含图片文件）。"""
    calib_dir = calib_dir.resolve()
    zip_path = zip_path.resolve()
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(calib_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                zf.write(p, arcname=f"calib/{p.name}")
    logger.info("BM1688 calibration zip: {}", zip_path)
    return zip_path
