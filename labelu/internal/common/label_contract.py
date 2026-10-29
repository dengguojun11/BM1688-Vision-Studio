from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def annotation_sha256(value: Any) -> str:
    return sha256_text(canonical_json(value))


def label_set_signature(label_ids: Iterable[int]) -> str:
    ordered = sorted({int(value) for value in label_ids})
    return sha256_text(canonical_json(ordered))


def class_order_signature(class_names: Iterable[str]) -> str:
    ordered = [str(value).strip() for value in class_names]
    return sha256_text(canonical_json(ordered))
