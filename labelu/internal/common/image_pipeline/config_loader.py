from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from labelu.internal.common.config import settings

# 导入流水线默认参数（与离线脚本 config.json 中聚类/离群/子场景一致；无需外部文件即可运行）
_BUILTIN_DEFAULTS: dict[str, Any] = {
    "clustering": {"num_clusters": None, "auto_cluster": True},
    "outliers": {"ratio": 0.1},
    "subscenes": {"similarity_threshold": 0.85, "method": "hist"},
    "import_steps": ["cluster", "remove_outliers", "subscenes"],
    "import_outlier_action": "tag",
}

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_pipeline_config() -> dict[str, Any]:
    """始终使用内置默认；仅当设置 IMAGE_PIPELINE_CONFIG_PATH 时才合并外部 JSON 覆盖。"""
    cfg = deepcopy(_BUILTIN_DEFAULTS)
    custom = (settings.IMAGE_PIPELINE_CONFIG_PATH or "").strip()
    if not custom:
        return cfg
    path = Path(custom)
    if not path.is_file():
        return cfg
    with open(path, encoding="utf-8") as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        return cfg
    for key, val in loaded.items():
        if key in ("paths", "rename", "merge", "steps", "clean_empty_dirs"):
            continue
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **val}
        elif key in cfg:
            cfg[key] = val
    if isinstance(loaded.get("import_steps"), list):
        cfg["import_steps"] = loaded["import_steps"]
    if loaded.get("import_outlier_action"):
        cfg["import_outlier_action"] = loaded["import_outlier_action"]
    return cfg


def is_image_upload(content_type: str | None, filename: str) -> bool:
    if content_type and content_type.startswith("image/"):
        return True
    return Path(filename).suffix.lower() in _IMAGE_SUFFIXES
