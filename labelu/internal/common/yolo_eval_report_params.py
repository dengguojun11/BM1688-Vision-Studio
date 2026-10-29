"""从 AI 评估报告正文提取可应用的 YOLO 训练超参（JSON / YAML / 文中键值）。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from labelu.internal.common.yolo_train_hyperparams import (
    ALLOWED_TRAIN_HYPER_KEYS,
    merge_ai_suggested_into_iteration_params,
    merge_iteration_param_dicts,
    unwrap_suggested_train_dict,
)

_JSON_MARKERS = (
    "recommended_train_params",
    '"train"',
    "'train'",
    "suggested_params",
)

_PROSE_KEY_ALIASES = {
    "conf_thres": "conf",
    "iou_thres": "iou",
}

_ITERATION_EXTRA_KEYS = frozenset({"epochs", "imgsz"})


def _extract_json_obj_from_body(text: str) -> Optional[Dict[str, Any]]:
    if not (text or "").strip():
        return None
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
        block = (m.group(1) or "").strip()
        if not block.startswith("{"):
            continue
        low = block.lower()
        if any(k in low for k in _JSON_MARKERS):
            try:
                obj = json.loads(block)
            except json.JSONDecodeError:
                continue
            return obj if isinstance(obj, dict) else None
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        inner = unwrap_suggested_train_dict(obj)
        if isinstance(inner, dict) and set(inner.keys()) & ALLOWED_TRAIN_HYPER_KEYS:
            return obj
    return None


def _parse_yaml_scalar(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    try:
        if "." in s or "e" in low:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _extract_yaml_train_params(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not (text or "").strip():
        return out
    allowed = ALLOWED_TRAIN_HYPER_KEYS | _ITERATION_EXTRA_KEYS
    for m in re.finditer(r"```(?:yaml|yml)\s*([\s\S]*?)```", text, re.IGNORECASE):
        block = m.group(1) or ""
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            mm = re.match(r"^([a-zA-Z_][\w]*)\s*:\s*(.+?)\s*(?:#.*)?$", stripped)
            if not mm:
                continue
            key = mm.group(1)
            if key not in allowed:
                continue
            val = _parse_yaml_scalar(mm.group(2))
            if val is not None and val != "":
                out[key] = val
    return out


def _extract_prose_train_params(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not (text or "").strip():
        return out
    for m in re.finditer(
        r"(conf_thres|iou_thres)\s*=\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    ):
        alias = _PROSE_KEY_ALIASES.get(m.group(1).lower())
        if alias:
            out[alias] = float(m.group(2))
    if re.search(r"--multi-?scale\b", text, re.IGNORECASE):
        out["multi_scale"] = True
    imgsz_m = re.search(
        r"imgsz\s*=\s*\[([^\]]+)\]",
        text,
        re.IGNORECASE,
    )
    if imgsz_m:
        nums: list[int] = []
        for part in imgsz_m.group(1).split(","):
            part = part.strip()
            try:
                nums.append(int(float(part)))
            except ValueError:
                continue
        if nums:
            out["imgsz"] = max(nums)
    cls_m = re.search(
        r"cls_loss(?:_weight)?\s*=\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if cls_m:
        out["cls"] = float(cls_m.group(1))
    return out


def extract_iteration_params_from_report(
    *,
    body: Optional[str] = None,
    suggested_params: Optional[Any] = None,
) -> Dict[str, Any]:
    """合并 suggested_params、JSON 块、YAML 块与正文键值，返回归一化后的迭代参数字典。"""
    layers: list[Dict[str, Any]] = []
    if suggested_params is not None:
        inner = merge_ai_suggested_into_iteration_params(suggested_params)
        if inner:
            layers.append(inner)
    if body:
        json_obj = _extract_json_obj_from_body(body)
        if json_obj:
            layers.append(unwrap_suggested_train_dict(json_obj))
        yaml_part = _extract_yaml_train_params(body)
        if yaml_part:
            layers.append(yaml_part)
        prose_part = _extract_prose_train_params(body)
        if prose_part:
            layers.append(prose_part)
    return merge_iteration_param_dicts(*layers)


def extract_recommended_params_json_for_storage(text: str) -> str | None:
    """入库用：从报告正文提取参数并序列化为 JSON 字符串。"""
    merged = extract_iteration_params_from_report(body=text)
    if not merged:
        return None
    payload = {"recommended_train_params": merged}
    return json.dumps(payload, ensure_ascii=False)[:48000]
