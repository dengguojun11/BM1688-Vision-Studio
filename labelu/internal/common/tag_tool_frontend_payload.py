"""tagTool 预标注与前端全局侧栏 xVe 的兼容。

前端对每个 tag 条目只用 ``Object.keys(item.value)[0]`` 映射到对应维度；
多维度必须拆成多条 result，每条 ``value`` 仅含一个键。"""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

TAG_TOOL_NAME = "tagTool"


def expand_tag_tool_result_items(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not items:
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vp = item.get("value")
        if not isinstance(vp, dict) or len(vp) <= 1:
            out.append(item)
            continue
        base = {k: v for k, v in item.items() if k != "value"}
        for k, vals in vp.items():
            sk = str(k)
            if isinstance(vals, list):
                norm_vals = [str(x) for x in vals if x is not None and str(x).strip()]
            elif vals is not None and str(vals).strip():
                norm_vals = [str(vals)]
            else:
                norm_vals = []
            out.append({**base, "id": str(uuid.uuid4()), "value": {sk: norm_vals}})
    return out


def expand_tag_tool_in_annotations(annotations: dict[str, Any] | None) -> None:
    if not annotations or not isinstance(annotations, dict):
        return
    block = annotations.get(TAG_TOOL_NAME)
    if not isinstance(block, dict):
        return
    res = block.get("result")
    if not isinstance(res, list):
        return
    block["result"] = expand_tag_tool_result_items(res)


def expand_pre_annotation_api_data(raw: Any) -> Any:
    """列出/获取预标注时在响应体中展开 tagTool（兼容库内仍为合并结构的旧数据）。

    预标注的 ``data`` 在库中存为 **JSON 文本**；接口历史上也一直返回字符串。
    前端会对该字段做 ``JSON.parse``，因此这里必须仍返回 **字符串**，不能改成嵌套对象。
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        payload = copy.deepcopy(raw)
        ann = payload.get("annotations")
        if isinstance(ann, dict):
            expand_tag_tool_in_annotations(ann)
        return json.dumps(payload, ensure_ascii=False)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return raw
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return raw
        if isinstance(payload, dict):
            ann = payload.get("annotations")
            if isinstance(ann, dict):
                expand_tag_tool_in_annotations(ann)
            return json.dumps(payload, ensure_ascii=False)
        return raw
    return raw
