"""固定评估集的内容签名。

清单在 Board Agent 和 SSH 包中的目录层级不同，不能直接比较
``manifest_sha256``。这里只使用图片/标签文件的类别、文件名、大小和摘要，
让同一批内容在不同传输布局下仍得到同一个固定集身份。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any


def _file_kind(path: str) -> str:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    if "images" in parts:
        return "image"
    if "labelsxml" in parts:
        return "label_xml"
    if "labels" in parts:
        return "label_yolo"
    return "other"


def fixed_eval_content_signature(manifest: dict[str, Any]) -> str:
    """Return a path-layout-independent digest for a fixed-eval manifest."""
    files = manifest.get("files")
    normalized: list[dict[str, Any]] = []
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").replace("\\", "/")
            digest = str(item.get("sha256") or "").strip().lower()
            if not path or not digest:
                continue
            normalized.append(
                {
                    "kind": _file_kind(path),
                    "name": PurePosixPath(path).name,
                    "size": int(item.get("size") or 0),
                    "sha256": digest,
                }
            )
    normalized.sort(key=lambda item: (item["kind"], item["name"], item["sha256"]))
    payload = {
        "schema_version": "fixed_eval_content.v1",
        "image_count": int(manifest.get("image_count") or 0),
        "labeled_image_count": int(manifest.get("labeled_image_count") or 0),
        "class_names": [str(item) for item in (manifest.get("class_names") or [])],
        "files": normalized,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = ["fixed_eval_content_signature"]
