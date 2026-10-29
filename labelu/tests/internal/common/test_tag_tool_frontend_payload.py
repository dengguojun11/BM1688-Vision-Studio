"""tagTool 多维度拆分：与前端 xVe 索引方式兼容。"""

import json

from labelu.internal.common.tag_tool_frontend_payload import (
    expand_pre_annotation_api_data,
    expand_tag_tool_result_items,
)


def test_expand_tag_tool_result_items_splits_multiple_keys():
    merged = {
        "id": "same-parent",
        "type": "tag",
        "value": {"weather": ["sunny"], "scene": ["urban"]},
    }
    out = expand_tag_tool_result_items([merged])
    assert len(out) == 2
    by_dim = {next(iter(x["value"])): x["value"] for x in out}
    assert by_dim["weather"] == {"weather": ["sunny"]}
    assert by_dim["scene"] == {"scene": ["urban"]}


def test_expand_tag_tool_result_items_single_key_unchanged():
    one = {"type": "tag", "value": {"weather": ["sunny", "cloudy"]}}
    assert expand_tag_tool_result_items([one]) == [one]


def test_expand_pre_annotation_api_data_from_json_string():
    raw = json.dumps(
        {
            "annotations": {
                "tagTool": {
                    "toolName": "tagTool",
                    "result": [
                        {"type": "tag", "value": {"a": ["1"], "b": ["2"]}},
                    ],
                }
            }
        },
        ensure_ascii=False,
    )
    data = expand_pre_annotation_api_data(raw)
    assert isinstance(data, str)
    parsed = json.loads(data)
    items = parsed["annotations"]["tagTool"]["result"]
    assert len(items) == 2
