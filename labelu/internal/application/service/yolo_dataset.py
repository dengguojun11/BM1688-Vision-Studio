"""从已完成样本构建 Ultralytics YOLO 数据集目录（images/train|val + labels + data.yaml）。"""

from __future__ import annotations

import json
import random
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image
from loguru import logger

from labelu.internal.common.training_sample import is_explicit_negative_sample
from labelu.internal.common.yolo_detection_geometry import (
    configured_geometry_tools,
    detection_class_names,
    detection_rects_for_result,
    detection_rects_for_sample,
)
from labelu.internal.common.yolo_rect_class_slug import (
    build_rect_label_slug_aliases,
    resolve_rect_label_slug,
)
from labelu.internal.domain.models.sample import SampleState, TaskSample

# 导出行为：不常改动，固定写在代码里，避免 .env 膨胀（与离线工具包 labelsxml 约定对齐）
WRITE_LABELSXML_ON_EXPORT: bool = True
RUN_DATASET_VALIDATION_AFTER_EXPORT: bool = True
DATASET_VALIDATION_STRICT: bool = True
GARBAGE_STREET_PRESET_SLUG = "garbageStreet"
NON_GARBAGE_LABEL = "non_garbage"
GARBAGE_STREET_LABEL_SET = {
    "garbage_street_dump",
    "garbage_street_pack",
    "garbage_street_scattered",
    "object_box",
    "garbage_street_can",
    NON_GARBAGE_LABEL,
    "garbage_bulky",
}


def yolo_job_reports_dir(dataset_root: Path) -> Path:
    """训练作业报告目录：<work>/reports，与 dataset/ 同级，不混入 YOLO 训练数据树。"""
    return dataset_root.resolve().parent / "reports"


def rect_class_names_match(a: list[str], b: list[str]) -> bool:
    """合并训练要求类别名与顺序完全一致。"""
    return a == b


def build_yolo_rect_class_names(
    config: dict[str, Any],
    *,
    preset_label_items: list[Any] | None = None,
) -> list[str]:
    """YOLO 类别仅来自 rectTool；写入 data.yaml 的 names 为英文 slug。"""
    aliases = build_rect_label_slug_aliases(
        preset_label_items=preset_label_items,
        task_config=config,
    )
    seen: dict[str, None] = {}
    order: list[str] = []
    for tool in config.get("tools", []) or []:
        if tool.get("tool") != "rectTool":
            continue
        for attr in tool.get("config", {}).get("attributes", []) or []:
            raw = str(attr.get("value") or attr.get("key") or "").strip()
            if not raw:
                continue
            slug = resolve_rect_label_slug(raw, aliases)
            if slug and slug not in seen:
                seen[slug] = None
                order.append(slug)
    return order


def task_rect_preset_slug(config: dict[str, Any]) -> str:
    return str((config or {}).get("defaultRectPresetId") or "").strip()


def _rect_tool_class_names(config: dict[str, Any]) -> list[str]:
    return build_yolo_rect_class_names(config)


def _matches_garbage_street_label_set(config: dict[str, Any]) -> bool:
    classes = [c for c in _rect_tool_class_names(config) if c]
    return bool(classes) and set(classes) == GARBAGE_STREET_LABEL_SET


def is_garbage_street_background_mode(config: dict[str, Any]) -> bool:
    return task_rect_preset_slug(
        config
    ) == GARBAGE_STREET_PRESET_SLUG or _matches_garbage_street_label_set(config)


def build_training_rect_class_names(
    config: dict[str, Any],
    *,
    preset_label_items: list[Any] | None = None,
) -> list[str]:
    classes = build_yolo_rect_class_names(
        config,
        preset_label_items=preset_label_items,
    )
    return classes


def _rect_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in ((result.get("rectTool") or {}).get("result") or [])
        if isinstance(item, dict)
    ]


def is_background_negative_sample(
    result: dict[str, Any],
    task_config: dict[str, Any],
    *,
    sample_data: dict[str, Any] | None = None,
) -> bool:
    rects = _rect_results(result)
    if is_explicit_negative_sample(sample_data):
        return not rects
    if not is_garbage_street_background_mode(task_config):
        return False
    return not rects


def collect_rect_labels_from_samples(samples: list[TaskSample]) -> set[str]:
    """从样本标注中收集实际使用的拉框 label 文本。"""
    labels: set[str] = set()
    for sample in samples:
        parsed = _parse_sample_annotation(sample)
        if not parsed:
            continue
        _, result = parsed
        for tool_result in (result.get("rectTool") or {}).get("result") or []:
            lb = (tool_result.get("label") or "").strip()
            if lb:
                labels.add(lb)
    return labels


def _rect_xywh(tool_result: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(tool_result, dict):
        return None
    try:
        x = float(tool_result.get("x", 0))
        y = float(tool_result.get("y", 0))
        w = float(tool_result.get("width", 0))
        h = float(tool_result.get("height", 0))
    except (TypeError, ValueError):
        return None
    return x, y, w, h


def _rect_is_valid_for_export(
    tool_result: Any,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> bool:
    parsed = _rect_xywh(tool_result)
    if parsed is None:
        return False
    x, y, w, h = parsed
    if w <= 0 or h <= 0:
        return False
    if image_width is None or image_height is None:
        return True
    if image_width <= 0 or image_height <= 0:
        return False
    if x < 0 or y < 0:
        return False
    if x + w > image_width or y + h > image_height:
        return False
    return True


def _sample_has_usable_rect(
    result: dict[str, Any],
    classes: list[str],
    *,
    label_aliases: dict[str, str] | None = None,
    task_config: dict[str, Any] | None = None,
) -> bool:
    if task_config is not None:
        return bool(
            detection_rects_for_result(
                result,
                task_config,
                classes,
                aliases=label_aliases,
            )
        )
    aliases = label_aliases or {}
    for tool_result in (result.get("rectTool") or {}).get("result") or []:
        raw_label = tool_result.get("label") or ""
        label = (
            resolve_rect_label_slug(str(raw_label), aliases) or str(raw_label).strip()
        )
        if label in classes and _rect_is_valid_for_export(tool_result):
            return True
    return False


def validate_rect_config_compatible(
    reference_classes: list[str], other_config: dict[str, Any], *, other_task_id: int
) -> None:
    other_classes = build_yolo_rect_class_names(other_config)
    if not other_classes:
        raise ValueError(f"任务 #{other_task_id} 的 rectTool 未配置类别")
    if (
        set(reference_classes) == set(other_classes)
        and reference_classes != other_classes
    ):
        raise ValueError(
            f"任务 #{other_task_id} 的拉框类别与主任务相同但顺序不同。"
            f"主任务={reference_classes}，该任务={other_classes}。"
            "请在各任务应用同一拉框预设并保持顺序一致。"
        )
    if not rect_class_names_match(reference_classes, other_classes):
        raise ValueError(
            f"任务 #{other_task_id} 的检测类别与主任务不一致。"
            f"主任务={reference_classes}，该任务={other_classes}。"
            "合并训练要求 rectTool 类别名与顺序完全相同（与 tagTool/任务名无关）。"
        )


def validate_merge_tasks_sample_labels(
    reference_classes: list[str],
    *,
    per_task_samples: dict[int, list[TaskSample]],
    primary_task_id: int,
    task_config: dict[str, Any] | None = None,
) -> None:
    """合并训练：各任务 DONE 样本中的拉框 label 必须落在主任务类别内，且副任务须有可训练样本。"""
    ref_set = set(reference_classes)
    for tid, samples in per_task_samples.items():
        ann_labels = collect_rect_labels_from_samples(samples)
        unknown = sorted(ann_labels - ref_set)
        if unknown:
            raise ValueError(
                f"任务 #{tid} 的标注含主任务未配置的拉框类别 {unknown}；"
                f"主任务类别为 {reference_classes}"
            )
        usable_n = 0
        for sample in samples:
            if sample.state != SampleState.DONE.value or not sample.file:
                continue
            parsed = _parse_sample_annotation(sample)
            if not parsed:
                continue
            outer, result = parsed
            if _sample_has_usable_rect(
                result,
                reference_classes,
                task_config=task_config,
            ) or (
                is_background_negative_sample(
                    result,
                    task_config or {},
                    sample_data=outer,
                )
            ):
                usable_n += 1
        if usable_n == 0:
            raise ValueError(
                f"任务 #{tid} 没有与主任务类别匹配的已完成拉框标注，无法参与合并训练"
            )


def validate_yolo_training_prerequisites(
    task_config: dict[str, Any], samples: list[TaskSample]
) -> None:
    """创建训练作业前校验，避免线程内失败才看到报错。"""
    classes = build_training_rect_class_names(task_config) or detection_class_names(
        task_config
    )
    if not classes:
        raise ValueError(
            "任务配置中 rectTool 没有类别（attributes 为空）。"
            "请在任务里配置拉框标签或应用拉框预设写入类别后再训练。"
        )

    usable: list[TaskSample] = []
    positive_count = 0
    for s in samples:
        if s.state != SampleState.DONE.value or not s.file:
            continue
        parsed = _parse_sample_annotation(s)
        if not parsed:
            continue
        outer, result = parsed
        has_positive_rect = _sample_has_usable_rect(
            result,
            classes,
            task_config=task_config,
        )
        if not has_positive_rect and not is_background_negative_sample(
            result,
            task_config,
            sample_data=outer,
        ):
            continue
        usable.append(s)
        if has_positive_rect:
            positive_count += 1

    if positive_count == 0:
        raise ValueError(
            "训练数据至少需要 1 张含有效矩形框的正样本；负样本只能作为补充。"
        )

    if len(usable) < 2:
        raise ValueError(
            "至少需要 2 个「已完成」样本，且矩形框标签须在任务配置的类别列表中；"
            "仅有预标注请在标注页提交为 DONE。"
        )


def _parse_sample_annotation(
    sample: TaskSample,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not sample.data:
        return None
    try:
        outer = json.loads(sample.data)
        # Newly uploaded samples may only contain file metadata and have no
        # annotation result yet.  Treat any non-object payload as an empty
        # annotation instead of dereferencing ``None`` below; the automatic
        # preannotation worker needs to inspect these samples before calling
        # the configured global-tag model.
        if not isinstance(outer, dict):
            return None
        result_raw = outer.get("result")
        if result_raw is None:
            return None
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
        if not isinstance(result, dict):
            return None
        return outer, result
    except (json.JSONDecodeError, TypeError):
        return None


def extract_scene_attrs_from_result(result: dict[str, Any]) -> dict[str, str]:
    """从 tagTool 提取场景/全局维度（与离线工具包 labelu_tags 语义一致）。"""
    attrs: dict[str, str] = {}
    tag_block = result.get("tagTool") or {}
    for item in tag_block.get("result") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        for key, raw in value.items():
            if isinstance(raw, list) and raw:
                attrs[str(key)] = str(raw[0])
            elif raw not in (None, ""):
                attrs[str(key)] = str(raw)
    return attrs


def extract_scene_attrs_from_outer(outer: dict[str, Any]) -> dict[str, str]:
    """图像流水线 scene_id 等元数据（若有）。"""
    attrs: dict[str, str] = {}
    pipe = outer.get("image_pipeline")
    if isinstance(pipe, dict):
        sid = pipe.get("scene_id")
        if sid is not None and str(sid).strip():
            attrs["scene_id"] = str(sid).strip()
    return attrs


def merge_scene_attrs(outer: dict[str, Any], result: dict[str, Any]) -> dict[str, str]:
    merged = extract_scene_attrs_from_outer(outer)
    merged.update(extract_scene_attrs_from_result(result))
    return merged


def _safe_xml_tag(name: str) -> str:
    s = re.sub(r"[^\w]", "_", (name or "").strip())
    return s or "tag"


def write_voc_xml_with_scene(
    path: Path,
    *,
    image_name: str,
    width: int,
    height: int,
    class_names: list[str],
    yolo_lines: list[str],
    scene_attrs: dict[str, str] | None = None,
) -> None:
    """写入带 extra_annotations 的 VOC XML，与固定 test 场景统计约定一致。"""
    root = ET.Element("annotation")
    ET.SubElement(root, "filename").text = image_name
    size = ET.SubElement(root, "size")
    ET.SubElement(size, "width").text = str(width)
    ET.SubElement(size, "height").text = str(height)
    ET.SubElement(size, "depth").text = "3"
    if scene_attrs:
        extra = ET.SubElement(root, "extra_annotations")
        for key, value in sorted(scene_attrs.items()):
            ET.SubElement(extra, _safe_xml_tag(key)).text = str(value)
    for line in yolo_lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls_id = int(float(parts[0]))
            xc, yc, nw, nh = (
                float(parts[1]),
                float(parts[2]),
                float(parts[3]),
                float(parts[4]),
            )
        except ValueError:
            continue
        if cls_id < 0 or cls_id >= len(class_names):
            continue
        x1 = (xc - nw / 2) * width
        y1 = (yc - nh / 2) * height
        x2 = (xc + nw / 2) * width
        y2 = (yc + nh / 2) * height
        obj = ET.SubElement(root, "object")
        ET.SubElement(obj, "name").text = class_names[cls_id]
        bnd = ET.SubElement(obj, "bndbox")
        ET.SubElement(bnd, "xmin").text = str(int(round(max(0, x1))))
        ET.SubElement(bnd, "ymin").text = str(int(round(max(0, y1))))
        ET.SubElement(bnd, "xmax").text = str(int(round(min(width, x2))))
        ET.SubElement(bnd, "ymax").text = str(int(round(min(height, y2))))
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_dataset_yaml_files(
    dataset_root: Path,
    classes: list[str],
    *,
    include_test: bool,
) -> None:
    yaml_names = "\n".join(f"  {i}: {name}" for i, name in enumerate(classes))
    yaml_lines = [
        f"path: {dataset_root.resolve().as_posix()}",
        "train: images/train",
        "val: images/val",
    ]
    if include_test:
        yaml_lines.append("test: images/test")
    yaml_lines.extend([f"nc: {len(classes)}", "names:", yaml_names, ""])
    content = "\n".join(yaml_lines)
    (dataset_root / "data.yaml").write_text(content, encoding="utf-8")
    (dataset_root / "class_names.txt").write_text(
        "\n".join(classes) + ("\n" if classes else ""),
        encoding="utf-8",
    )
    # 仅保留 data.yaml（Ultralytics 训练用）；test 划分已写在 data.yaml 的 test: 行


def _rect_lines_for_sample(
    result: dict[str, Any],
    classes: list[str],
    image_width: int,
    image_height: int,
    *,
    label_aliases: dict[str, str] | None = None,
    task_config: dict[str, Any] | None = None,
) -> tuple[list[str], int]:
    if task_config is not None:
        lines: list[str] = []
        # ``detection_rects_for_result`` intentionally drops malformed
        # geometry. Keep an export statistic for operators by counting the
        # rectTool entries that are otherwise class-eligible but invalid.
        skipped_invalid = 0
        if "rectTool" in configured_geometry_tools(task_config):
            for item in (result.get("rectTool") or {}).get("result") or []:
                if not isinstance(item, dict):
                    continue
                label = resolve_rect_label_slug(
                    str(item.get("label") or ""), label_aliases or {}
                ) or str(item.get("label") or "").strip()
                if label in classes and not _rect_is_valid_for_export(
                    item,
                    image_width=image_width,
                    image_height=image_height,
                ):
                    skipped_invalid += 1
        rects = detection_rects_for_result(
            result,
            task_config,
            classes,
            aliases=label_aliases,
            image_width=image_width,
            image_height=image_height,
        )
        for rect in rects:
            x, y, w, h = (
                float(rect["x"]),
                float(rect["y"]),
                float(rect["width"]),
                float(rect["height"]),
            )
            cid = classes.index(str(rect["label"]))
            lines.append(
                f"{cid} {(x + w / 2) / image_width:.6f} "
                f"{(y + h / 2) / image_height:.6f} "
                f"{w / image_width:.6f} {h / image_height:.6f}"
            )
        return lines, skipped_invalid
    lines: list[str] = []
    skipped_invalid = 0
    aliases = label_aliases or {}
    rect_block = result.get("rectTool") or {}
    for tool_result in rect_block.get("result", []) or []:
        raw_label = tool_result.get("label") or ""
        label = (
            resolve_rect_label_slug(str(raw_label), aliases) or str(raw_label).strip()
        )
        if label not in classes:
            continue
        parsed = _rect_xywh(tool_result)
        if parsed is None:
            skipped_invalid += 1
            continue
        x, y, w, h = parsed
        if not _rect_is_valid_for_export(
            tool_result,
            image_width=image_width,
            image_height=image_height,
        ):
            skipped_invalid += 1
            continue
        xc = (x + w / 2) / image_width
        yc = (y + h / 2) / image_height
        nw = w / image_width
        nh = h / image_height
        cid = classes.index(label)
        lines.append(f"{cid} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}")
    return lines, skipped_invalid


def _split_samples_with_negative_coverage(
    usable: list[tuple[TaskSample, bool]],
    *,
    n_train: int,
    n_val: int,
    n_test: int,
    seed: int,
) -> tuple[list[TaskSample], list[TaskSample], list[TaskSample]]:
    """Keep positive coverage first, then spread confirmed negatives across splits."""
    rng = random.Random(seed)
    positives = [sample for sample, is_negative in usable if not is_negative]
    negatives = [sample for sample, is_negative in usable if is_negative]
    rng.shuffle(positives)
    rng.shuffle(negatives)

    capacities = {"train": n_train, "val": n_val, "test": n_test}
    assigned: dict[str, list[TaskSample]] = {key: [] for key in capacities}
    priority = [key for key in ("train", "val", "test") if capacities[key] > 0]

    def reserve_one_per_split(group: list[TaskSample]) -> None:
        for split in priority:
            if not group:
                return
            if len(assigned[split]) >= capacities[split]:
                continue
            assigned[split].append(group.pop())

    # Validation remains meaningful only when positive targets are represented.
    reserve_one_per_split(positives)
    # When capacity allows, confirmed negatives reach train/val/test instead of
    # being left to a purely random split.
    reserve_one_per_split(negatives)

    remaining = [*positives, *negatives]
    rng.shuffle(remaining)
    for split in priority:
        free = capacities[split] - len(assigned[split])
        if free > 0:
            assigned[split].extend(remaining[:free])
            del remaining[:free]
        rng.shuffle(assigned[split])

    if remaining:
        raise ValueError("样本划分容量计算异常")
    return assigned["train"], assigned["val"], assigned["test"]


def prepare_yolo_dataset(
    *,
    task_config: dict[str, Any],
    samples: list[TaskSample],
    media_root: Path,
    dataset_root: Path,
    val_ratio: float = 0.2,
    test_ratio: float = 0.0,
    seed: int = 42,
    preset_label_items: list[Any] | None = None,
    forced_splits: dict[int, str] | None = None,
    selected_class_names: list[str] | None = None,
) -> tuple[int, int, int, list[str], dict[int, int]]:
    """
    将 DONE 且含 rectTool 标注的样本写入 dataset_root（train / val / 可选 test）。
    返回 (train_count, val_count, test_count, class_names, 各 task_id 实际导出张数)。
    """
    classes = [str(item).strip() for item in (selected_class_names or []) if str(item).strip()]
    if not classes:
        classes = build_training_rect_class_names(
            task_config, preset_label_items=preset_label_items
        )
    if not classes:
        classes = detection_class_names(task_config)
    if not classes:
        raise ValueError(
            "任务配置中 rectTool 没有任何英文类别 slug（请检查拉框预设 label_value）"
        )

    label_aliases = build_rect_label_slug_aliases(
        preset_label_items=preset_label_items,
        task_config=task_config,
    )

    for generated_dir in ("images", "labels", "labelsxml"):
        target = dataset_root / generated_dir
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    for generated_file in ("data.yaml", "class_names.txt"):
        (dataset_root / generated_file).unlink(missing_ok=True)

    usable: list[tuple[TaskSample, bool]] = []
    for s in samples:
        if s.state != SampleState.DONE.value or not s.file:
            continue
        parsed = _parse_sample_annotation(s)
        if not parsed:
            continue
        outer, result = parsed
        is_background = is_background_negative_sample(
            result,
            task_config,
            sample_data=outer,
        )
        if (
            not _sample_has_usable_rect(
                result,
                classes,
                label_aliases=label_aliases,
                task_config=task_config,
            )
            and not is_background
        ):
            continue
        usable.append((s, is_background))

    test_ratio = max(0.0, min(float(test_ratio), 0.5))
    val_ratio = max(0.0, min(float(val_ratio), 0.5))
    min_usable = 3 if test_ratio > 0 or forced_splits else 2
    if len(usable) < min_usable:
        raise ValueError(
            f"至少需要 {min_usable} 张已完成的有效正样本或明确负样本"
            + ("（启用 test 划分时）" if test_ratio > 0 else "")
            + "，才能划分 train/val"
            + ("/test" if test_ratio > 0 else "")
        )
    if not any(not is_negative for _, is_negative in usable):
        raise ValueError(
            "训练数据至少需要 1 张含有效矩形框的正样本；负样本只能作为补充。"
        )

    if forced_splits is not None:
        unknown = {
            str(split)
            for split in forced_splits.values()
            if str(split) not in {"train", "val", "test"}
        }
        if unknown:
            raise ValueError(f"冻结数据包含未知 split: {sorted(unknown)}")
        missing = [
            sample.id for sample, _negative in usable if sample.id not in forced_splits
        ]
        if missing:
            raise ValueError(f"冻结数据缺少 split: sample_ids={missing[:10]}")
        train_samples = [
            sample for sample, _ in usable if forced_splits[sample.id] == "train"
        ]
        val_samples = [
            sample for sample, _ in usable if forced_splits[sample.id] == "val"
        ]
        test_samples = [
            sample for sample, _ in usable if forced_splits[sample.id] == "test"
        ]
        if not train_samples or not val_samples:
            raise ValueError("冻结数据必须同时包含 train 和 val 样本")
    else:
        n = len(usable)
        n_test = max(1, int(round(n * test_ratio))) if test_ratio > 0 else 0
        n_val = max(1, int(round(n * val_ratio)))
        if n_test + n_val >= n:
            n_test = min(n_test, max(0, n - 2))
            n_val = min(n_val, max(1, n - n_test - 1))
        n_train = n - n_val - n_test
        if n_train < 1:
            raise ValueError(
                "样本过少：请降低 YOLO_TRAIN_VAL_RATIO / YOLO_TRAIN_TEST_RATIO"
            )

        train_samples, val_samples, test_samples = (
            _split_samples_with_negative_coverage(
                usable,
                n_train=n_train,
                n_val=n_val,
                n_test=n_test,
                seed=seed,
            )
        )

    write_labelsxml = WRITE_LABELSXML_ON_EXPORT
    for sub in (
        "images/train",
        "images/val",
        "labels/train",
        "labels/val",
        "images/test",
        "labels/test",
    ):
        (dataset_root / sub).mkdir(parents=True, exist_ok=True)
    if write_labelsxml:
        for sub in ("train", "val", "test"):
            (dataset_root / "labelsxml" / sub).mkdir(parents=True, exist_ok=True)

    per_task_exported: dict[int, int] = {}
    export_stats = {
        "skipped_missing_file": 0,
        "datasource_materialization_failed": 0,
        "skipped_no_annotation": 0,
        "skipped_empty_boxes": 0,
        "skipped_invalid_boxes": 0,
        "with_scene_attrs": 0,
    }
    sample_composition = {
        "positive_images": 0,
        "negative_images": 0,
        "negative_ratio": 0.0,
        "splits": {
            split: {"positive_images": 0, "negative_images": 0, "total_images": 0}
            for split in ("train", "val", "test")
        },
    }

    def write_split(split: str, subset: list[TaskSample]) -> int:
        count = 0
        for sample in subset:
            from labelu.internal.application.service.attachment import (
                resolve_attachment_local_path,
            )

            src = resolve_attachment_local_path(sample.file)
            if getattr(sample.file, "data_source_id", None) and src is None:
                export_stats["datasource_materialization_failed"] += 1
                raise ValueError(
                    "第三方数据源图片物化失败，已停止训练数据导出："
                    f"attachment_id={getattr(sample.file, 'id', None)}, "
                    f"object_key={getattr(sample.file, 'path', None)}"
                )
            if src is None:
                src = media_root / sample.file.path.lstrip("/\\")
            if not src.is_file():
                logger.warning("skip missing image: {}", src)
                export_stats["skipped_missing_file"] += 1
                continue
            parsed = _parse_sample_annotation(sample)
            if not parsed:
                export_stats["skipped_no_annotation"] += 1
                continue
            outer, result = parsed
            stem = f"{sample.id}_{Path(sample.file.filename).stem}"
            suffix = Path(sample.file.filename).suffix or ".jpg"
            dst_img = dataset_root / "images" / split / f"{stem}{suffix}"
            shutil.copy2(src, dst_img)

            rotate = result.get("rotate") or 0
            with Image.open(dst_img) as img:
                if rotate:
                    img = img.rotate(rotate, expand=True)
                    img.save(dst_img)
            with Image.open(dst_img) as img_final:
                image_width, image_height = img_final.size

            is_background = is_background_negative_sample(
                result,
                task_config,
                sample_data=outer,
            )
            lines, skipped_invalid = _rect_lines_for_sample(
                result,
                classes,
                image_width,
                image_height,
                label_aliases=label_aliases,
                task_config=task_config,
            )
            export_stats["skipped_invalid_boxes"] += skipped_invalid
            if not lines and not is_background:
                dst_img.unlink(missing_ok=True)
                export_stats["skipped_empty_boxes"] += 1
                continue
            label_path = dataset_root / "labels" / split / f"{stem}.txt"
            label_text = "\n".join(lines)
            if lines:
                label_text += "\n"
            label_path.write_text(label_text, encoding="utf-8")
            scene_attrs = merge_scene_attrs(outer, result)
            if scene_attrs:
                export_stats["with_scene_attrs"] += 1
            if write_labelsxml:
                write_voc_xml_with_scene(
                    dataset_root / "labelsxml" / split / f"{stem}.xml",
                    image_name=dst_img.name,
                    width=image_width,
                    height=image_height,
                    class_names=classes,
                    yolo_lines=lines,
                    scene_attrs=scene_attrs or None,
                )
            tid = int(sample.task_id)
            per_task_exported[tid] = per_task_exported.get(tid, 0) + 1
            composition_key = "negative_images" if is_background else "positive_images"
            sample_composition[composition_key] += 1
            sample_composition["splits"][split][composition_key] += 1
            sample_composition["splits"][split]["total_images"] += 1
            count += 1
        return count

    train_n = write_split("train", train_samples)
    val_n = write_split("val", val_samples)
    test_n = write_split("test", test_samples) if test_samples else 0
    if train_n < 1 or val_n < 1:
        raise ValueError(
            "有效标注样本不足：训练集或验证集为空（请检查图片路径与矩形标注）"
        )
    if test_samples and test_n < 1:
        raise ValueError("test 划分已启用但测试集为空（请检查图片路径与矩形标注）")

    exported_total = int(sample_composition["positive_images"]) + int(
        sample_composition["negative_images"]
    )
    if exported_total:
        sample_composition["negative_ratio"] = round(
            int(sample_composition["negative_images"]) / exported_total,
            6,
        )

    _write_dataset_yaml_files(dataset_root, classes, include_test=test_n > 0)

    report_dir = yolo_job_reports_dir(dataset_root)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "export_summary.json").write_text(
        json.dumps(
            {
                "train": train_n,
                "val": val_n,
                "test": test_n,
                "classes": classes,
                "write_labelsxml": write_labelsxml,
                "per_task_exported": per_task_exported,
                "sample_composition": sample_composition,
                "stats": export_stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return train_n, val_n, test_n, classes, per_task_exported


def count_class_instances_in_dataset(
    dataset_root: Path,
    class_names: list[str],
) -> list[dict[str, Any]]:
    """
    统计 YOLO labels/{train|val|test} 中各类别矩形框实例数和包含该类别的图片数。
    返回顺序与 class_names / 拉框预设一致。
    """
    nc = len(class_names)
    if nc == 0:
        return []
    split_counts: dict[str, list[int]] = {
        split: [0] * nc for split in ("train", "val", "test")
    }
    split_image_counts: dict[str, list[int]] = {
        split: [0] * nc for split in ("train", "val", "test")
    }
    for split, counters in split_counts.items():
        label_dir = dataset_root / "labels" / split
        if not label_dir.is_dir():
            continue
        for txt_path in label_dir.glob("*.txt"):
            try:
                text = txt_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            image_classes: set[int] = set()
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                try:
                    cid = int(float(parts[0]))
                except (IndexError, ValueError):
                    continue
                if 0 <= cid < nc:
                    counters[cid] += 1
                    image_classes.add(cid)
            for cid in image_classes:
                split_image_counts[split][cid] += 1
    return [
        {
            "index": i,
            "label": class_names[i],
            "train_instances": split_counts["train"][i],
            "val_instances": split_counts["val"][i],
            "test_instances": split_counts["test"][i],
            "train_images": split_image_counts["train"][i],
            "val_images": split_image_counts["val"][i],
            "test_images": split_image_counts["test"][i],
        }
        for i in range(nc)
    ]
