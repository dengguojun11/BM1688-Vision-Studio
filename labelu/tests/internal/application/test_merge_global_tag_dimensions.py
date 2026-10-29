"""merge_global_dimensions_into_cfg：占位 tagTool 属性应被数据库枚举定义覆盖，以便 AI 拿到 tag_dimensions。"""
from unittest.mock import MagicMock

from labelu.internal.application.service.annotation_taxonomy import merge_global_dimensions_into_cfg
from labelu.internal.application.service.auto_label import _extract_tool_configs


def _mock_dim(field_key: str, name_cn: str, options: list[tuple[str, str]]):
    d = MagicMock()
    d.field_key = field_key
    d.name_cn = name_cn
    d.sort_order = 0
    d.id = 1
    d.options = []
    for i, (nc, ve) in enumerate(options):
        o = MagicMock()
        o.name_cn = nc
        o.value_en = ve
        o.sort_order = i
        o.id = i + 1
        d.options.append(o)
    return d


def test_merge_upgrades_weak_tagtool_attr():
    cfg = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {"key": "天气", "value": "weather"},
                    ]
                },
            }
        ]
    }
    dim = _mock_dim("weather", "天气", [("晴", "sunny"), ("阴", "cloudy")])
    merge_global_dimensions_into_cfg(cfg, [dim])
    attrs = cfg["tools"][0]["config"]["attributes"]
    assert len(attrs) == 1
    assert attrs[0].get("type") == "enum"
    assert len(attrs[0].get("options") or []) == 2


def test_extract_tool_configs_accepts_options_without_enum_type():
    cfg = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            }
        ]
    }
    _, _, tag_dims = _extract_tool_configs(cfg)
    assert len(tag_dims) == 1
    assert tag_dims[0]["field_key"] == "weather"
