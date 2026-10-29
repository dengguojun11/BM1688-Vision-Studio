"""拉框类别：界面可用中文 label_key，写入 YOLO/AICM 文件须为英文 label_value（slug）。"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

_YOLO_CLASS_SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def looks_like_yolo_class_slug(text: str) -> bool:
    """是否为 YOLO/AICM 可用的英文类别 slug。"""
    s = str(text or "").strip()
    return bool(s and _YOLO_CLASS_SLUG_RE.match(s))


def _iter_preset_label_items(preset_label_items: Optional[Iterable[Any]]) -> List[Any]:
    if not preset_label_items:
        return []
    return list(preset_label_items)


def build_rect_label_slug_aliases(
    *,
    preset_label_items: Optional[Iterable[Any]] = None,
    task_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """
    构建「展示名 / 旧别名 → 英文 slug」映射。
    来源：预设库 label_key/label_value、任务 rectTool attributes 的 key/value。
    """
    aliases: Dict[str, str] = {}
    for item in _iter_preset_label_items(preset_label_items):
        val = str(getattr(item, "label_value", None) or "").strip()
        key = str(getattr(item, "label_key", None) or "").strip()
        if isinstance(item, dict):
            val = str(item.get("label_value") or "").strip()
            key = str(item.get("label_key") or "").strip()
        if val:
            aliases[val] = val
        if key and val:
            aliases[key] = val
        elif key and looks_like_yolo_class_slug(key):
            aliases[key] = key

    cfg = task_config if isinstance(task_config, dict) else {}
    for tool in cfg.get("tools", []) or []:
        if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
            continue
        config = tool.get("config") if isinstance(tool.get("config"), dict) else {}
        for attr in config.get("attributes", []) or []:
            if not isinstance(attr, dict):
                continue
            value = str(attr.get("value") or "").strip()
            key = str(attr.get("key") or "").strip()
            if value:
                aliases[value] = value if looks_like_yolo_class_slug(value) else aliases.get(value, value)
            if key and value and looks_like_yolo_class_slug(value):
                aliases[key] = value
            elif key and looks_like_yolo_class_slug(key):
                aliases[key] = key
    return aliases


def resolve_rect_label_slug(raw: str, aliases: Mapping[str, str]) -> str:
    """将展示名或别名解析为英文 slug；无法解析时返回空字符串。"""
    key = str(raw or "").strip()
    if not key:
        return ""
    mapped = aliases.get(key)
    if mapped and looks_like_yolo_class_slug(mapped):
        return mapped
    if looks_like_yolo_class_slug(key):
        return key
    return ""


def normalize_rect_class_names_to_slugs(
    names: List[str],
    aliases: Mapping[str, str],
) -> tuple[List[str], List[str]]:
    """
    按顺序将类别名规范为英文 slug。
    返回 (slugs, unresolved_names)。
    """
    slugs: List[str] = []
    unresolved: List[str] = []
    for name in names:
        raw = str(name or "").strip()
        if not raw:
            continue
        slug = resolve_rect_label_slug(raw, aliases)
        if slug:
            slugs.append(slug)
        else:
            unresolved.append(raw)
    return slugs, unresolved
