"""任务标注质量：全局 tag + 配置化几何标注 + 几何合法性。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from labelu.internal.common.training_sample import is_explicit_negative_sample
from labelu.internal.domain.models.sample import SampleState, TaskSample

BOUNDS_EPSILON = 1e-6


def _parse_task_config(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def list_required_global_tag_keys(task_config: dict[str, Any]) -> List[str]:
    """任务配置中 tagTool / tagList 定义的全局维度 field_key（每维必选一项）。"""
    keys: List[str] = []
    seen: set[str] = set()
    for tool in task_config.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("tool") != "tagTool":
            continue
        attrs = (tool.get("config") or {}).get("attributes") or []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            fk = str(attr.get("value") or "").strip()
            if not fk or fk in seen:
                continue
            opts = attr.get("options") or []
            if attr.get("type") == "enum" or (isinstance(opts, list) and len(opts) > 0):
                keys.append(fk)
                seen.add(fk)
    if not keys:
        for item in task_config.get("tagList") or []:
            if not isinstance(item, dict):
                continue
            fk = str(item.get("value") or "").strip()
            if fk and fk not in seen:
                keys.append(fk)
                seen.add(fk)
    return keys


def sample_global_tags_complete(
    result: dict[str, Any],
    required_keys: List[str],
    allowed_options: Optional[Dict[str, set[str]]] = None,
) -> Tuple[bool, List[str]]:
    """返回 (是否完整, 缺失的维度 field_key 列表)。"""
    if not required_keys:
        return True, []
    from labelu.internal.application.service.yolo_dataset import (
        extract_scene_attrs_from_result,
    )

    attrs = extract_scene_attrs_from_result(result)
    missing = []
    for key in required_keys:
        value = str(attrs.get(key) or "").strip()
        options = (allowed_options or {}).get(key) or set()
        if not value or (options and value not in options):
            missing.append(key)
    return len(missing) == 0, missing


def _global_tag_options(task_config: dict[str, Any]) -> Dict[str, set[str]]:
    """Return configured values for each global tag dimension."""
    options_by_key: Dict[str, set[str]] = {}
    for tool in task_config.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("tool") != "tagTool":
            continue
        for attr in (tool.get("config") or {}).get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            key = str(attr.get("value") or "").strip()
            if not key:
                continue
            values = {
                str(option.get("value") or "").strip()
                for option in (attr.get("options") or [])
                if isinstance(option, dict) and str(option.get("value") or "").strip()
            }
            if values:
                options_by_key[key] = values
    if options_by_key:
        return options_by_key
    for item in task_config.get("tagList") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("value") or "").strip()
        if not key:
            continue
        values = {
            str(option.get("value") or "").strip()
            for option in (item.get("subSelected") or [])
            if isinstance(option, dict) and str(option.get("value") or "").strip()
        }
        if values:
            options_by_key[key] = values
    return options_by_key


_GEOMETRY_TOOL_NAMES = (
    "rectTool",
    "polygonTool",
    "pointTool",
    "lineTool",
    "cuboidTool",
)


def configured_geometry_tools(task_config: dict[str, Any]) -> List[str]:
    """Return geometry tools enabled by the task, preserving config order."""
    tools: List[str] = []
    seen: set[str] = set()
    for tool in task_config.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("tool") or "").strip()
        if name in _GEOMETRY_TOOL_NAMES and name not in seen:
            tools.append(name)
            seen.add(name)
    return tools


def _geometry_tool_config(
    task_config: dict[str, Any], tool_name: str
) -> dict[str, Any]:
    for tool in task_config.get("tools") or []:
        if isinstance(tool, dict) and tool.get("tool") == tool_name:
            config = tool.get("config")
            return config if isinstance(config, dict) else {}
    return {}


def _geometry_label_names(task_config: dict[str, Any], tool_name: str) -> set[str]:
    config = _geometry_tool_config(task_config, tool_name)
    labels: set[str] = set()
    for attr in config.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        value = str(attr.get("value") or attr.get("key") or "").strip()
        if value:
            labels.add(value)
    return labels


def _positive_int(config: dict[str, Any], key: str) -> int | None:
    value = config.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _geometry_item_has_valid_shape(
    item: Any,
    tool_name: str,
    task_config: dict[str, Any],
) -> bool:
    if not isinstance(item, dict):
        return False
    labels = _geometry_label_names(task_config, tool_name)
    label = str(item.get("label") or "").strip()
    if labels and label not in labels:
        return False
    if tool_name == "rectTool":
        return (_number(item.get("width")) or 0) > BOUNDS_EPSILON and (
            _number(item.get("height")) or 0
        ) > BOUNDS_EPSILON
    if tool_name == "pointTool":
        return _point_xy(item) is not None
    if tool_name in {"polygonTool", "lineTool"}:
        points = item.get("points") or item.get("pointList") or []
        if not isinstance(points, list):
            return False
        min_points = _positive_int(
            _geometry_tool_config(task_config, tool_name), "lowerLimitPointNum"
        )
        max_points = _positive_int(
            _geometry_tool_config(task_config, tool_name), "upperLimitPointNum"
        )
        if min_points and len(points) < min_points:
            return False
        if max_points and len(points) > max_points:
            return False
        return bool(points) and all(_point_xy(point) is not None for point in points)
    if tool_name == "cuboidTool":
        return any(isinstance(item.get(face), dict) for face in ("front", "back"))
    return False


def _has_valid_configured_geometry(
    result: dict[str, Any], task_config: dict[str, Any]
) -> Tuple[bool, Optional[str], Optional[str]]:
    tools = configured_geometry_tools(task_config)
    if not tools:
        return True, None, None
    for tool_name in tools:
        items = _tool_items(result, tool_name)
        if any(
            _geometry_item_has_valid_shape(item, tool_name, task_config)
            for item in items
        ):
            return True, None, tool_name
    return False, "missing geometry annotation", tools[0]


def _has_any_geometry(result: dict[str, Any]) -> bool:
    return any(_tool_items(result, tool_name) for tool_name in _GEOMETRY_TOOL_NAMES)


def _is_valid_negative_sample(
    result: dict[str, Any], task_config: dict[str, Any], sample_data: dict[str, Any]
) -> bool:
    from labelu.internal.application.service.yolo_dataset import (
        is_background_negative_sample,
    )

    if _has_any_geometry(result):
        return False
    if is_explicit_negative_sample(sample_data):
        return True
    return is_background_negative_sample(result, task_config, sample_data=sample_data)


def sample_has_valid_rect(
    result: dict[str, Any], classes: Optional[List[str]] = None
) -> bool:
    """至少一个拉框且 width/height > 0；若配置了 rect 类别则 label 须在类别内。"""
    rects = (result.get("rectTool") or {}).get("result") or []
    if not rects:
        return False
    class_set = set(classes or [])
    require_label = bool(class_set)
    for tool_result in rects:
        if not isinstance(tool_result, dict):
            continue
        try:
            wf = float(
                tool_result.get("width") if tool_result.get("width") is not None else 0
            )
            hf = float(
                tool_result.get("height")
                if tool_result.get("height") is not None
                else 0
            )
        except (TypeError, ValueError):
            continue
        if wf <= 0 or hf <= 0:
            continue
        label = str(tool_result.get("label") or "").strip()
        if require_label:
            if not label or label not in class_set:
                continue
        return True
    return False


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tool_items(result_data: dict[str, Any], *tool_names: str) -> list[Any]:
    items: list[Any] = []
    for tool_name in tool_names:
        block = result_data.get(tool_name)
        if isinstance(block, dict):
            block_items = block.get("result")
        else:
            block_items = block
        if isinstance(block_items, list):
            items.extend(block_items)
    return items


def _point_xy(point: Any) -> tuple[float, float] | None:
    if isinstance(point, dict):
        x = _number(point.get("x"))
        y = _number(point.get("y"))
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        x = _number(point[0])
        y = _number(point[1])
    else:
        return None
    if x is None or y is None:
        return None
    return x, y


def _point_out_of_bounds(x: float, y: float, width: float, height: float) -> bool:
    return (
        x < -BOUNDS_EPSILON
        or y < -BOUNDS_EPSILON
        or x > width + BOUNDS_EPSILON
        or y > height + BOUNDS_EPSILON
    )


def _extract_result_dimensions(
    sample: TaskSample,
    sample_data: dict[str, Any],
    result_data: dict[str, Any],
) -> tuple[float | None, float | None]:
    width = _number(result_data.get("width"))
    height = _number(result_data.get("height"))
    if width and height:
        return width, height

    file_data = (
        sample_data.get("file") if isinstance(sample_data.get("file"), dict) else {}
    )
    width = _number(file_data.get("original_width") or file_data.get("width"))
    height = _number(file_data.get("original_height") or file_data.get("height"))
    if width and height:
        return width, height

    attachment = sample.file
    if not attachment or not getattr(attachment, "path", None):
        return None, None

    try:
        from labelu.internal.common.storage import get_storage_backend

        storage = get_storage_backend()
        if storage.is_remote:
            return None, None

        from PIL import Image, ImageOps

        local_path = storage.get_local_path(attachment.path)
        if not local_path or not local_path.is_file():
            return None, None
        with Image.open(local_path) as image:
            image = ImageOps.exif_transpose(image)
            image_width, image_height = image.size
            return float(image_width), float(image_height)
    except Exception:
        return None, None


def sample_geometry_ok(
    sample: TaskSample,
    result: dict[str, Any],
    outer: Optional[dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """返回 (几何是否正常, 错误原因)。无几何标注时视为正常。"""
    rect_items = _tool_items(result, "rectTool", "rect")
    point_items = _tool_items(result, "pointTool", "point")
    line_items = _tool_items(result, "lineTool", "line")
    polygon_items = _tool_items(result, "polygonTool", "polygon")
    cuboid_items = _tool_items(result, "cuboidTool", "cuboid")
    if not any([rect_items, point_items, line_items, polygon_items, cuboid_items]):
        return True, None

    sample_data = outer if isinstance(outer, dict) else {}
    image_width, image_height = _extract_result_dimensions(sample, sample_data, result)
    if not image_width or not image_height:
        return False, "missing image width or height"

    for item in rect_items:
        if not isinstance(item, dict):
            return False, "invalid rect annotation"
        x = _number(item.get("x"))
        y = _number(item.get("y"))
        width = _number(item.get("width"))
        height = _number(item.get("height"))
        if x is None or y is None or width is None or height is None:
            return False, "invalid rect annotation"
        if width <= BOUNDS_EPSILON or height <= BOUNDS_EPSILON:
            return False, "rect out of bounds"
        if x < -BOUNDS_EPSILON or y < -BOUNDS_EPSILON:
            return False, "rect out of bounds"
        if (
            x + width > image_width + BOUNDS_EPSILON
            or y + height > image_height + BOUNDS_EPSILON
        ):
            return False, "rect out of bounds"

    for item in point_items:
        point = _point_xy(item)
        if point is None:
            return False, "invalid point annotation"
        if _point_out_of_bounds(point[0], point[1], image_width, image_height):
            return False, "point out of bounds"

    for item in [*line_items, *polygon_items]:
        if not isinstance(item, dict):
            return False, "invalid line or polygon annotation"
        points = item.get("points") or item.get("pointList") or []
        if not isinstance(points, list):
            return False, "invalid line or polygon points"
        for raw_point in points:
            point = _point_xy(raw_point)
            if point is None:
                return False, "invalid line or polygon point"
            if _point_out_of_bounds(point[0], point[1], image_width, image_height):
                return False, "line or polygon out of bounds"

    for item in cuboid_items:
        if not isinstance(item, dict):
            return False, "invalid cuboid annotation"
        for face_name in ("front", "back"):
            face = item.get(face_name)
            if not isinstance(face, dict):
                continue
            for raw_point in face.values():
                point = _point_xy(raw_point)
                if point is None:
                    return False, "invalid cuboid point"
                if _point_out_of_bounds(point[0], point[1], image_width, image_height):
                    return False, "cuboid out of bounds"

    return True, None


def audit_one_sample(
    sample: TaskSample,
    task_config: dict[str, Any],
) -> Dict[str, Any]:
    """单样本审计结果。"""
    required_keys = list_required_global_tag_keys(task_config)
    allowed_tag_options = _global_tag_options(task_config)
    geometry_tools = configured_geometry_tools(task_config)
    out: Dict[str, Any] = {
        "sample_id": sample.id,
        "inner_id": sample.inner_id,
        "filename": sample.file.filename if sample.file else None,
        "ok": False,
        "tags_ok": True,
        "geometry_annotation_ok": False,
        "geometry_tool": geometry_tools[0] if geometry_tools else None,
        "rect_ok": False,
        "geometry_ok": True,
        "geometry_error": None,
        "sample_quality_state": SampleState.INCOMPLETE.value,
        "missing_tag_keys": [],
    }
    from labelu.internal.application.service.yolo_dataset import (
        _parse_sample_annotation,
    )

    parsed = _parse_sample_annotation(sample)
    if not parsed:
        out["tags_ok"] = len(required_keys) == 0
        out["missing_tag_keys"] = list(required_keys)
        out["geometry_annotation_ok"] = not geometry_tools
        out["rect_ok"] = out["geometry_annotation_ok"]
        return out
    outer, result = parsed
    tags_ok, missing = sample_global_tags_complete(
        result, required_keys, allowed_options=allowed_tag_options
    )
    geometry_annotation_ok, geometry_annotation_error, geometry_tool = (
        _has_valid_configured_geometry(result, task_config)
    )
    if not geometry_annotation_ok and _is_valid_negative_sample(
        result, task_config, outer
    ):
        geometry_annotation_ok = True
        geometry_annotation_error = None
        geometry_tool = "negative"
    geometry_ok, geometry_error = sample_geometry_ok(sample, result, outer)
    out["tags_ok"] = tags_ok
    out["missing_tag_keys"] = missing
    out["geometry_annotation_ok"] = geometry_annotation_ok
    out["geometry_tool"] = geometry_tool
    # Keep rect_ok as a compatibility alias for older incremental audit data.
    out["rect_ok"] = geometry_annotation_ok
    if geometry_annotation_error and geometry_ok:
        out["geometry_error"] = geometry_annotation_error
    out["geometry_ok"] = geometry_ok
    if geometry_error:
        out["geometry_error"] = geometry_error
    out["ok"] = tags_ok and geometry_annotation_ok and geometry_ok
    if not geometry_ok:
        out["sample_quality_state"] = SampleState.ABNORMAL.value
    elif not tags_ok or not geometry_annotation_ok:
        out["sample_quality_state"] = SampleState.INCOMPLETE.value
    else:
        out["sample_quality_state"] = SampleState.DONE.value
    return out


def audit_task_samples(
    task_config: dict[str, Any], samples: List[TaskSample]
) -> Dict[str, Any]:
    """
    任务级审计：每一张阻塞样本须满足全局 tag + 配置化几何标注。
    training_ready = 有样本且全部样本 ok。
    """
    rows = [audit_one_sample(s, task_config) for s in samples]
    total = len(rows)

    def _blocks_training(row: dict[str, Any]) -> bool:
        return row.get("state") not in (
            SampleState.NEW.value,
            SampleState.SKIPPED.value,
        )

    for row, sample in zip(rows, samples):
        row["state"] = getattr(sample, "state", None)

    blocking_rows = [r for r in rows if _blocks_training(r)]
    passed = sum(1 for r in blocking_rows if r.get("ok"))
    failed_tags = sum(1 for r in rows if not r.get("tags_ok"))
    failed_geometry_annotation = sum(
        1 for r in rows if not r.get("geometry_annotation_ok", r.get("rect_ok"))
    )
    failed_rect = sum(
        1
        for r in rows
        if r.get("geometry_tool") == "rectTool"
        and not r.get("geometry_annotation_ok", r.get("rect_ok"))
    )
    failed_geometry = sum(1 for r in rows if not r.get("geometry_ok"))
    failed_incomplete = sum(
        1 for r in rows if r.get("sample_quality_state") == SampleState.INCOMPLETE.value
    )
    blocking_total = len(blocking_rows)
    training_ready = blocking_total > 0 and passed == blocking_total
    failed_samples = [
        {
            "sample_id": r["sample_id"],
            "filename": r.get("filename"),
            "missing_tag_keys": r.get("missing_tag_keys") or [],
            "geometry_annotation_ok": r.get("geometry_annotation_ok", r.get("rect_ok")),
            "geometry_tool": r.get("geometry_tool"),
            "rect_ok": r.get("rect_ok"),
            "geometry_ok": r.get("geometry_ok"),
            "geometry_error": r.get("geometry_error"),
            "sample_quality_state": r.get("sample_quality_state"),
        }
        for r in rows
        if not r.get("ok") and _blocks_training(r)
    ][:50]
    return {
        "training_ready": training_ready,
        "total_samples": total,
        "passed_samples": passed,
        "failed_samples_count": blocking_total - passed,
        "failed_missing_tags_count": failed_tags,
        "failed_missing_rect_count": failed_rect,
        "failed_missing_geometry_count": failed_geometry_annotation,
        "failed_geometry_count": failed_geometry,
        "failed_incomplete_count": failed_incomplete,
        "failed_samples": failed_samples,
    }


def audit_task_config_and_samples(
    config_raw: Any, samples: List[TaskSample]
) -> Dict[str, Any]:
    return audit_task_samples(_parse_task_config(config_raw), samples)
