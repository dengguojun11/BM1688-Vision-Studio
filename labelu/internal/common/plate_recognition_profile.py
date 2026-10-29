from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

PLATE_RECOGNITION_PROFILE = "plate_recognition_v1"
PLATE_RECOGNITION_PRESET_SLUG = "plateRecognition"

_PLATE_ATTRIBUTE_NAMES = {
    "plateNumber": "车牌号",
    "color": "车牌颜色",
    "plateType": "车牌类型",
}
_PLATE_COLOR_NAMES = {
    "white": "白色",
    "blue": "蓝色",
    "yellow": "黄色",
    "black": "黑色",
    "green": "绿色",
}
_PLATE_TYPE_NAMES = {
    "whitePlate": "白底黑字车牌",
    "bluePlate": "蓝底白字车牌",
    "greenPlate": "绿底黑字车牌",
    "yellowPlate": "黄底黑字车牌",
    "blackPlate": "黑底白字车牌",
}

DEFAULT_PLATE_RECOGNITION_CONFIG: dict[str, Any] = {
    "rect_label": "doubleEP",
    "polygon_label": "platePoint",
    "attributes": {
        "plateNumber": {"type": "ocr"},
        "color": ["white", "blue", "yellow", "black", "green"],
        "plateType": [
            "whitePlate",
            "bluePlate",
            "greenPlate",
            "yellowPlate",
            "blackPlate",
        ],
    },
}


@dataclass(frozen=True)
class PlateInstance:
    rect: dict[str, Any]
    corners: tuple[tuple[float, float], ...]
    plate_number: str
    color: str
    plate_type: str


@dataclass(frozen=True)
class PlateSampleAudit:
    kind: str
    instances: tuple[PlateInstance, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.kind in {"positive", "negative"}


def normalize_token(value: Any) -> str:
    return str(value or "").strip()


def task_config_has_plate_recognition_marker(raw: Any) -> bool:
    expected = {
        PLATE_RECOGNITION_PRESET_SLUG,
        DEFAULT_PLATE_RECOGNITION_CONFIG["rect_label"],
        "doebleEP",
        DEFAULT_PLATE_RECOGNITION_CONFIG["polygon_label"],
    }
    if isinstance(raw, dict):
        return any(
            task_config_has_plate_recognition_marker(key)
            or task_config_has_plate_recognition_marker(value)
            for key, value in raw.items()
        )
    if isinstance(raw, list):
        return any(task_config_has_plate_recognition_marker(item) for item in raw)
    return isinstance(raw, str) and raw.strip() in expected


def parse_profile_config(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        parsed: Any = copy.deepcopy(DEFAULT_PLATE_RECOGNITION_CONFIG)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("training_profile_config must be valid JSON") from exc
    else:
        parsed = copy.deepcopy(raw)
    if not isinstance(parsed, dict):
        raise ValueError("training_profile_config must be an object")

    rect_label = normalize_token(parsed.get("rect_label"))
    polygon_label = normalize_token(parsed.get("polygon_label"))
    attributes = parsed.get("attributes")
    if not rect_label or not polygon_label or not isinstance(attributes, dict):
        raise ValueError(
            "plate profile requires rect_label, polygon_label and attributes"
        )

    plate_number = attributes.get("plateNumber")
    if (
        not isinstance(plate_number, dict)
        or normalize_token(plate_number.get("type")) != "ocr"
    ):
        raise ValueError("plateNumber must be configured as OCR")

    normalized_attrs: dict[str, Any] = {"plateNumber": {"type": "ocr"}}
    for key in ("color", "plateType"):
        values = attributes.get(key)
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a non-empty list")
        normalized_values = []
        for value in values:
            token = normalize_token(value)
            if token and token not in normalized_values:
                normalized_values.append(token)
        if not normalized_values:
            raise ValueError(f"{key} must be a non-empty list")
        normalized_attrs[key] = normalized_values

    return {
        "rect_label": rect_label,
        "polygon_label": polygon_label,
        "attributes": normalized_attrs,
    }


def dump_profile_config(raw: Any) -> str:
    return json.dumps(
        parse_profile_config(raw), ensure_ascii=False, separators=(",", ":")
    )


def _tool_config(task_config: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    aliases = {tool_name}
    if tool_name.endswith("Tool"):
        aliases.add(tool_name[:-4])
    for tool in task_config.get("tools") or []:
        if (
            not isinstance(tool, dict)
            or normalize_token(tool.get("tool")) not in aliases
        ):
            continue
        config = tool.get("config")
        return config if isinstance(config, dict) else {}
    return None


def _iter_attribute_definitions(raw: Any) -> Iterable[dict[str, Any]]:
    if isinstance(raw, dict):
        yield raw
        for value in raw.values():
            if isinstance(value, (dict, list)):
                yield from _iter_attribute_definitions(value)
    elif isinstance(raw, list):
        for item in raw:
            yield from _iter_attribute_definitions(item)


def _config_tokens(config: dict[str, Any] | None) -> set[str]:
    if not isinstance(config, dict):
        return set()
    tokens: set[str] = set()
    for item in _iter_attribute_definitions(config.get("attributes") or []):
        for key in ("value", "key", "field", "name"):
            token = normalize_token(item.get(key))
            if token:
                tokens.add(token)
    return tokens


def task_config_matches_profile(task_config: Any, profile_config: Any = None) -> bool:
    if isinstance(task_config, str):
        try:
            task_config = json.loads(task_config)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    if not isinstance(task_config, dict):
        return False
    profile = parse_profile_config(profile_config)
    rect_tokens = _config_tokens(_tool_config(task_config, "rectTool"))
    polygon_tokens = _config_tokens(_tool_config(task_config, "polygonTool"))
    if profile["rect_label"] not in rect_tokens:
        return False
    if profile["polygon_label"] not in polygon_tokens:
        return False
    return all(key in rect_tokens for key in profile["attributes"])


def build_plate_recognition_rect_attribute(
    profile_config: Any = None,
    *,
    label_key: str = "双层车牌",
    color: str = "#ff6600",
) -> dict[str, Any]:
    """Build the full LabelU rect attribute required by the plate profile."""
    profile = parse_profile_config(profile_config)
    color_values = profile["attributes"]["color"]
    plate_type_values = profile["attributes"]["plateType"]
    return {
        "key": label_key,
        "value": profile["rect_label"],
        "color": color,
        "attributes": [
            {
                "key": _PLATE_ATTRIBUTE_NAMES["plateNumber"],
                "value": "plateNumber",
                "type": "string",
                "required": True,
                "maxLength": 1000,
                "stringType": "text",
                "defaultValue": "",
                "regexp": "",
            },
            {
                "key": _PLATE_ATTRIBUTE_NAMES["color"],
                "value": "color",
                "type": "enum",
                "required": True,
                "options": [
                    {"key": _PLATE_COLOR_NAMES.get(value, value), "value": value}
                    for value in color_values
                ],
            },
            {
                "key": _PLATE_ATTRIBUTE_NAMES["plateType"],
                "value": "plateType",
                "type": "enum",
                "required": True,
                "options": [
                    {"key": _PLATE_TYPE_NAMES.get(value, value), "value": value}
                    for value in plate_type_values
                ],
            },
        ],
    }


def normalize_plate_recognition_task_config(
    task_config: Any,
    profile_config: Any = None,
    *,
    label_key: str = "双层车牌",
    color: str = "#ff6600",
    polygon_key: str = "关键点",
) -> dict[str, Any]:
    """Return a plate-profile task config without discarding unrelated tools."""
    profile = parse_profile_config(profile_config)
    cfg = copy.deepcopy(task_config) if isinstance(task_config, dict) else {}
    tools = cfg.get("tools")
    if not isinstance(tools, list):
        tools = []
        cfg["tools"] = tools

    def tool_entry(tool_name: str) -> dict[str, Any]:
        for item in tools:
            if (
                isinstance(item, dict)
                and normalize_token(item.get("tool")) == tool_name
            ):
                return item
        item = {"tool": tool_name, "config": {}}
        tools.append(item)
        return item

    rect = tool_entry("rectTool")
    rect_config = rect.get("config") if isinstance(rect.get("config"), dict) else {}
    rect_config = copy.deepcopy(rect_config)
    for key, value in {
        "isShowCursor": False,
        "showConfirm": False,
        "skipWhileNoDependencies": False,
        "drawOutsideTarget": False,
        "copyBackwardResult": False,
        "minWidth": 1,
        "minHeight": 1,
        "attributeConfigurable": True,
        "textConfigurable": True,
        "textCheckType": 4,
        "customFormat": "",
    }.items():
        rect_config.setdefault(key, value)
    rect_config["attributes"] = [
        build_plate_recognition_rect_attribute(
            profile,
            label_key=label_key,
            color=color,
        )
    ]
    rect["config"] = rect_config

    polygon = tool_entry("polygonTool")
    polygon_config = (
        copy.deepcopy(polygon.get("config"))
        if isinstance(polygon.get("config"), dict)
        else {}
    )
    polygon_config.setdefault("attributeConfigurable", True)
    polygon_config.setdefault("lineType", 1)
    polygon_config.setdefault("lowerLimitPointNum", 5)
    polygon_config.setdefault("upperLimitPointNum", 6)
    polygon_config["attributes"] = [
        {
            "key": polygon_key,
            "value": profile["polygon_label"],
            "color": color,
        }
    ]
    polygon["config"] = polygon_config

    cfg["defaultRectPresetId"] = PLATE_RECOGNITION_PRESET_SLUG
    cfg.setdefault("commonAttributeConfigurable", True)
    return cfg


def normalize_plate_recognition_sample_data(
    sample_data: Any,
    profile_config: Any = None,
) -> tuple[Any, dict[str, int]]:
    """Normalize known historical plate tokens while preserving annotations."""
    stats = {
        "rect_labels": 0,
        "polygon_labels": 0,
        "attribute_keys": 0,
        "attribute_values": 0,
    }
    if not isinstance(sample_data, dict):
        return sample_data, stats

    normalized = copy.deepcopy(sample_data)
    result_raw = normalized.get("result")
    result = result_raw
    if isinstance(result_raw, str):
        try:
            result = json.loads(result_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return normalized, stats
    if not isinstance(result, dict):
        return normalized, stats

    profile = parse_profile_config(profile_config)
    rect_label = profile["rect_label"]
    polygon_label = profile["polygon_label"]
    canonical_attr_keys = set(profile["attributes"])

    for rect in _tool_items(result, "rectTool"):
        raw_label = str(rect.get("label") or "")
        stripped_label = raw_label.strip()
        if stripped_label in {rect_label, "doebleEP"} and raw_label != rect_label:
            rect["label"] = rect_label
            stats["rect_labels"] += 1

        attrs = rect.get("attributes")
        if not isinstance(attrs, dict):
            continue
        rebuilt: dict[str, Any] = {}
        for raw_key, raw_value in attrs.items():
            stripped_key = str(raw_key).strip()
            target_key = (
                stripped_key if stripped_key in canonical_attr_keys else raw_key
            )
            if target_key != raw_key:
                stats["attribute_keys"] += 1
            value = raw_value
            if target_key in {"color", "plateType"}:
                if isinstance(value, list):
                    cleaned = [
                        item.strip() if isinstance(item, str) else item
                        for item in value
                    ]
                    if cleaned != value:
                        stats["attribute_values"] += 1
                    value = cleaned
                elif isinstance(value, str):
                    cleaned = value.strip()
                    if cleaned != value:
                        stats["attribute_values"] += 1
                    value = cleaned
            if target_key not in rebuilt or raw_key == target_key:
                rebuilt[target_key] = value
        rect["attributes"] = rebuilt

    for polygon in _tool_items(result, "polygonTool"):
        raw_label = str(polygon.get("label") or "")
        if raw_label.strip() == polygon_label and raw_label != polygon_label:
            polygon["label"] = polygon_label
            stats["polygon_labels"] += 1

    if any(stats.values()):
        if isinstance(result_raw, str):
            normalized["result"] = json.dumps(result, ensure_ascii=False)
        else:
            normalized["result"] = result
    return normalized, stats


def _tool_items(result: dict[str, Any], tool_name: str) -> list[dict[str, Any]]:
    aliases = [tool_name]
    if tool_name.endswith("Tool"):
        aliases.append(tool_name[:-4])
    rows: list[dict[str, Any]] = []
    for alias in aliases:
        block = result.get(alias)
        items = block.get("result") if isinstance(block, dict) else block
        if isinstance(items, list):
            rows.extend(item for item in items if isinstance(item, dict))
    return rows


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _rect_bounds(rect: dict[str, Any]) -> tuple[float, float, float, float] | None:
    x = _number(rect.get("x"))
    y = _number(rect.get("y"))
    width = _number(rect.get("width"))
    height = _number(rect.get("height"))
    if (
        x is None
        or y is None
        or width is None
        or height is None
        or width <= 0
        or height <= 0
    ):
        return None
    return x, y, x + width, y + height


def _point(raw: Any) -> tuple[float, float] | None:
    if isinstance(raw, dict):
        x, y = _number(raw.get("x")), _number(raw.get("y"))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        x, y = _number(raw[0]), _number(raw[1])
    else:
        return None
    if x is None or y is None:
        return None
    return x, y


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def normalize_four_corners(
    raw_points: Any,
    *,
    rect_bounds: tuple[float, float, float, float] | None = None,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw_points, list):
        raise ValueError("polygon points must be a list")
    points = [_point(raw) for raw in raw_points]
    if any(point is None for point in points):
        raise ValueError("polygon contains an invalid point")
    normalized = [point for point in points if point is not None]

    if len(normalized) == 5:
        if rect_bounds:
            x1, y1, x2, y2 = rect_bounds
            close_tolerance = max(10.0, math.hypot(x2 - x1, y2 - y1) * 0.03)
        else:
            close_tolerance = 10.0
        if _distance(normalized[0], normalized[-1]) <= close_tolerance:
            normalized.pop()
    if len(normalized) != 4:
        raise ValueError(
            "plate polygon must contain four corners plus an optional closing point"
        )
    if len({(round(x, 6), round(y, 6)) for x, y in normalized}) != 4:
        raise ValueError("plate polygon corners must be unique")

    center_x = sum(point[0] for point in normalized) / 4.0
    center_y = sum(point[1] for point in normalized) / 4.0
    ordered = sorted(
        normalized,
        key=lambda point: math.atan2(point[1] - center_y, point[0] - center_x),
    )
    top_left_index = min(
        range(4), key=lambda index: ordered[index][0] + ordered[index][1]
    )
    ordered = ordered[top_left_index:] + ordered[:top_left_index]
    if ordered[1][0] < ordered[-1][0]:
        ordered = [ordered[0], ordered[3], ordered[2], ordered[1]]
    return tuple(ordered)


def _attribute_value(rect: dict[str, Any], key: str) -> str:
    key = normalize_token(key)
    sources = []
    for field_name in ("attributes", "attribute", "value", "result"):
        value = rect.get(field_name)
        if isinstance(value, dict):
            sources.append(value)
    sources.append(rect)
    for source in sources:
        for raw_key, raw_value in source.items():
            if normalize_token(raw_key) != key:
                continue
            if isinstance(raw_value, list):
                raw_value = next(
                    (item for item in raw_value if normalize_token(item)), ""
                )
            elif isinstance(raw_value, dict):
                raw_value = raw_value.get("value") or raw_value.get("text") or ""
            return normalize_token(raw_value)
    return ""


def _centroid(points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _contains(
    bounds: tuple[float, float, float, float], point: tuple[float, float]
) -> bool:
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def audit_plate_sample(result: Any, profile_config: Any = None) -> PlateSampleAudit:
    if not isinstance(result, dict):
        return PlateSampleAudit(
            kind="invalid", errors=("annotation result is not an object",)
        )
    profile = parse_profile_config(profile_config)
    rect_label = profile["rect_label"]
    polygon_label = profile["polygon_label"]
    rects = [
        row
        for row in _tool_items(result, "rectTool")
        if normalize_token(row.get("label")) == rect_label
    ]
    polygons = [
        row
        for row in _tool_items(result, "polygonTool")
        if normalize_token(row.get("label")) == polygon_label
    ]
    if not rects and not polygons:
        return PlateSampleAudit(kind="negative")
    if not rects or not polygons:
        return PlateSampleAudit(
            kind="invalid",
            errors=("positive plate sample requires both rectangle and polygon",),
        )

    rect_entries: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
    for rect in rects:
        bounds = _rect_bounds(rect)
        if bounds is None:
            return PlateSampleAudit(kind="invalid", errors=("invalid plate rectangle",))
        rect_entries.append((rect, bounds))

    instances: list[PlateInstance] = []
    sample_errors: list[str] = []
    used_rect_indexes: set[int] = set()
    for polygon in polygons:
        try:
            rough_points = tuple(
                point
                for point in (_point(raw) for raw in (polygon.get("points") or []))
                if point is not None
            )
            if len(rough_points) < 4:
                raise ValueError("plate polygon has fewer than four points")
            center = _centroid(rough_points[:4])
        except ValueError as exc:
            return PlateSampleAudit(kind="invalid", errors=(str(exc),))
        candidates = [
            (index, rect, bounds)
            for index, (rect, bounds) in enumerate(rect_entries)
            if index not in used_rect_indexes and _contains(bounds, center)
        ]
        if not candidates:
            return PlateSampleAudit(
                kind="invalid",
                errors=(
                    "plate polygon does not belong to exactly one unused rectangle",
                ),
            )
        index, rect, bounds = min(
            candidates,
            key=lambda item: (item[2][2] - item[2][0]) * (item[2][3] - item[2][1]),
        )
        used_rect_indexes.add(index)
        try:
            corners = normalize_four_corners(
                polygon.get("points") or [], rect_bounds=bounds
            )
        except ValueError as exc:
            return PlateSampleAudit(kind="invalid", errors=(str(exc),))

        plate_number = _attribute_value(rect, "plateNumber")
        color = _attribute_value(rect, "color")
        plate_type = _attribute_value(rect, "plateType")
        missing = [
            key
            for key, value in (
                ("plateNumber", plate_number),
                ("color", color),
                ("plateType", plate_type),
            )
            if not value
        ]
        if missing:
            sample_errors.append(
                f"plate rectangle missing attributes: {', '.join(missing)}"
            )
        if color and color not in profile["attributes"]["color"]:
            sample_errors.append(f"unsupported color: {color}")
        if plate_type and plate_type not in profile["attributes"]["plateType"]:
            sample_errors.append(f"unsupported plateType: {plate_type}")
        instances.append(
            PlateInstance(
                rect=dict(rect),
                corners=corners,
                plate_number=plate_number,
                color=color,
                plate_type=plate_type,
            )
        )

    if len(used_rect_indexes) != len(rect_entries):
        return PlateSampleAudit(
            kind="invalid",
            errors=("one or more plate rectangles have no matching polygon",),
        )
    return PlateSampleAudit(
        kind="positive",
        instances=tuple(instances),
        errors=tuple(sample_errors),
    )
