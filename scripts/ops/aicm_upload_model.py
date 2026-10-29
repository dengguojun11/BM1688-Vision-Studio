#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动将模型 zip 上传到 AICM（与平台发布页同一套 API）。

用法（在已配置 aicm_publish_config.json 的服务器上）:
  python scripts/ops/aicm_upload_model.py /path/to/model-xxx.zip
  python scripts/ops/aicm_upload_model.py /path/to/model-xxx.zip my_model_name

或指定环境变量:
  AICM_BASE_URL=http://10.10.11.20:8102
  AICM_USERNAME=7g
  AICM_PASSWORD=***

成功时输出: {"data": {}, "error_code": 0, "error_info": ""}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from labelu.internal.common.aicm_publish_config import (  # noqa: E402
    DEFAULT_ALGORITHM_TYPE,
    DEFAULT_CHIP_TYPE,
    load_aicm_publish_config,
    normalize_aicm_base_url,
)
from labelu.internal.application.service.yolo_aicm_publish import (  # noqa: E402
    _aicm_response_ok,
    _aicm_session,
    _aicm_upload_model_name,
    _upload_model_package,
)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/ops/aicm_upload_model.py <model.zip>", file=sys.stderr)
        return 2
    zip_path = Path(sys.argv[1]).resolve()
    if not zip_path.is_file():
        print(f"文件不存在: {zip_path}", file=sys.stderr)
        return 2

    cfg = load_aicm_publish_config()
    base = normalize_aicm_base_url(cfg.get("base_url") or "")
    if not base or not (cfg.get("username") and cfg.get("password")):
        print("请先在 LabelU「模型发布」页保存 AICM 配置，或设置 AICM_* 环境变量", file=sys.stderr)
        return 2

    upload_name = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not upload_name:
        upload_name = _aicm_upload_model_name(zip_path=zip_path)

    sess = _aicm_session(cfg)
    text = _upload_model_package(
        sess,
        base,
        zip_path,
        name=upload_name,
        chip_type=str(cfg.get("chip_type") or DEFAULT_CHIP_TYPE),
        algorithm_type=str(cfg.get("algorithm_type") or DEFAULT_ALGORITHM_TYPE),
        verify=bool(cfg.get("verify_ssl")),
    )
    print(text)
    if _aicm_response_ok(text):
        print("上传成功 (error_code=0)", file=sys.stderr)
        return 0
    print("上传失败", file=sys.stderr)
    try:
        body = json.loads(text)
        if isinstance(body, dict) and body.get("error_info"):
            print(body["error_info"], file=sys.stderr)
    except json.JSONDecodeError:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
