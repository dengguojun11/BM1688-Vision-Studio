"""Normalize configured annotation geometry for YOLO detection training."""

from __future__ import annotations

from typing import Any, Iterable

_GEOMETRY_TOOLS = ("rectTool", "polygonTool", "pointTool", "lineTool", "cuboidTool")


def parse_task_config(config: Any) -> dict[str, Any]:
    if isinstance(config, dict):
        return config
    if isinstance(config, str):
        import json

        try:
            value = json.loads(config)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def configured_geometry_tools(task_config: Any) -> list[str]:
    config = parse_task_config(task_config)
    tools: list[str] = []
    seen: set[str] = set()
    for tool in config.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("tool") or "").strip()
        if name in _GEOMETRY_TOOLS and name not in seen:
            tools.append(name)
            seen.add(name)
    return tools or ["rectTool"]


def _tool_config(task_config: Any, tool_name: str) -> dict[str, Any]:
    config = parse_task_config(task_config)
    for tool in config.get("tools") or []:
        if isinstance(tool, dict) and tool.get("tool") == tool_name:
            value = tool.get("config")
            return value if isinstance(value, dict) else {}
    return {}


def _tool_labels(task_config: Any, tool_name: str) -> set[str]:
    labels: set[str] = set()
    for item in _tool_config(task_config, tool_name).get("attributes") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or item.get("key") or "").strip()
        if value:
            labels.add(value)
    return labels


def _tool_label_aliases(task_config: Any, tool_name: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in _tool_config(task_config, tool_name).get("attributes") or []:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("value") or item.get("key") or "").strip()
        raw_key = str(item.get("key") or "").strip()
        if canonical:
            aliases[canonical] = canonical
        if raw_key and canonical:
            aliases[raw_key] = canonical
    return aliases


def detection_class_names(task_config: Any) -> list[str]:
    """Return configured geometry labels in tool/config order."""
    names: list[str] = []
    seen: set[str] = set()
    for tool_name in configured_geometry_tools(task_config):
        for item in _tool_config(task_config, tool_name).get("attributes") or []:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or item.get("key") or "").strip()
            if value and value not in seen:
                names.append(value)
                seen.add(value)
    return names


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    x = _number(value.get("x"))
    y = _number(value.get("y"))
    if x is None or y is None:
        return None
    return x, y


def _points(item: dict[str, Any]) -> list[tuple[float, float]]:
    raw = item.get("points") or item.get("pointList") or []
    if not isinstance(raw, list):
        return []
    points = [_point(value) for value in raw]
    return [value for value in points if value is not None]


def _configured_point_limit(task_config: Any, tool_name: str, key: str) -> int | None:
    value = _number(_tool_config(task_config, tool_name).get(key))
    if value is None or value <= 0:
        return None
    return int(value)


def _bbox_from_points(
    points: Iterable[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    values = list(points)
    if not values:
        return None
    xs = [point[0] for point in values]
    ys = [point[1] for point in values]
    x = min(xs)
    y = min(ys)
    width = max(xs) - x
    height = max(ys) - y
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _item_bbox(
    item: Any, tool_name: str, task_config: Any
) -> tuple[float, float, float, float] | None:
    if not isinstance(item, dict):
        return None
    if tool_name == "rectTool":
        values = tuple(_number(item.get(key)) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            return None
        x, y, width, height = values
        if width <= 0 or height <= 0:
            return None
        return x, y, width, height
    if tool_name in {"polygonTool", "lineTool"}:
        points = _points(item)
        minimum = _configured_point_limit(task_config, tool_name, "lowerLimitPointNum")
        maximum = _configured_point_limit(task_config, tool_name, "upperLimitPointNum")
        if minimum and len(points) < minimum:
            return None
        if maximum and len(points) > maximum:
            return None
        return _bbox_from_points(points)
    if tool_name == "pointTool":
        point = _point(item)
        if point is None:
            return None
        return point[0], point[1], 1.0, 1.0
    if tool_name == "cuboidTool":
        points: list[tuple[float, float]] = []
        for face in ("front", "back", "left", "right", "top", "bottom"):
            value = item.get(face)
            if isinstance(value, dict):
                points.extend(_points(value))
                point = _point(value)
                if point is not None:
                    points.append(point)
        return _bbox_from_points(points)
    return None


def detection_rects_for_result(
    result: dict[str, Any],
    task_config: Any,
    class_names: Iterable[str],
    *,
    aliases: dict[str, str] | None = None,
    image_width: float | None = None,
    image_height: float | None = None,
) -> list[dict[str, Any]]:
    """Return valid detection rectangles from the configured geometry tools."""
    classes = {str(value).strip() for value in class_names if str(value).strip()}
    aliases = aliases or {}
    rects: list[dict[str, Any]] = []
    for tool_name in configured_geometry_tools(task_config):
        configured_labels = _tool_labels(task_config, tool_name)
        tool_aliases = _tool_label_aliases(task_config, tool_name)
        for item in (result.get(tool_name) or {}).get("result") or []:
            if not isinstance(item, dict):
                continue
            raw_label = str(item.get("label") or "").strip()
            label = aliases.get(raw_label, tool_aliases.get(raw_label, raw_label))
            if not label or (classes and label not in classes):
                continue
            if (
                configured_labels
                and raw_label not in configured_labels
                and label not in configured_labels
            ):
                continue
            bbox = _item_bbox(item, tool_name, task_config)
            if bbox is None:
                continue
            x, y, width, height = bbox
            # Annotation coordinates are often produced by scaling canvas
            # coordinates and may exceed an image edge by a tiny float error.
            boundary_tolerance = 1e-6 * max(
                1.0,
                float(image_width or 0.0),
                float(image_height or 0.0),
            )
            if x < -boundary_tolerance or y < -boundary_tolerance:
                continue
            if image_width and x + width > image_width + boundary_tolerance:
                continue
            if image_height and y + height > image_height + boundary_tolerance:
                continue
            if image_width:
                x = max(0.0, min(x, float(image_width)))
                width = min(width, float(image_width) - x)
            if image_height:
                y = max(0.0, min(y, float(image_height)))
                height = min(height, float(image_height) - y)
            if width <= 0 or height <= 0:
                continue
            normalized = dict(item)
            normalized.update(
                {"label": label, "x": x, "y": y, "width": width, "height": height}
            )
            normalized["source_tool"] = tool_name
            rects.append(normalized)
    return rects


def detection_rects_for_sample(
    outer: dict[str, Any],
    result: dict[str, Any],
    task_config: Any,
    class_names: Iterable[str],
    *,
    aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    file_data = outer.get("file") if isinstance(outer.get("file"), dict) else {}
    image_width = _number(
        result.get("width") or file_data.get("original_width") or file_data.get("width")
    )
    image_height = _number(
        result.get("height")
        or file_data.get("original_height")
        or file_data.get("height")
    )
    return detection_rects_for_result(
        result,
        task_config,
        class_names,
        aliases=aliases,
        image_width=image_width,
        image_height=image_height,
    )


def count_detection_samples(
    samples: Iterable[Any],
    task_config: Any,
    class_names: Iterable[str],
    *,
    parse_annotation,
    is_negative,
    aliases: dict[str, str] | None = None,
) -> tuple[int, int, int]:
    """Return (included images, positive images, instances)."""
    included = positive = instances = 0
    for sample in samples:
        parsed = parse_annotation(sample)
        if not parsed:
            continue
        outer, result = parsed
        rects = detection_rects_for_sample(
            outer, result, task_config, class_names, aliases=aliases
        )
        if rects:
            included += 1
            positive += 1
            instances += len(rects)
        elif is_negative(outer):
            included += 1
    return included, positive, instances
