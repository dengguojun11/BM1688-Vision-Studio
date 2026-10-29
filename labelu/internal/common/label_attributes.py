from __future__ import annotations

import json
from typing import Any


def decode_label_attributes(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if raw is None or not str(raw).strip():
        return []
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def encode_label_attributes(value: Any) -> str:
    return json.dumps(decode_label_attributes(value), ensure_ascii=False)
