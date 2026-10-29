"""AI 预标注归一化：tagTool 条目须符合前端 schema（含 type: tag）。"""

import json
from types import SimpleNamespace

from labelu.internal.application.command.auto_label import AutoLabelCommand, BatchAutoLabelCommand
from labelu.internal.application.service.auto_label import (
    _batch_mode_requests_geometry,
    _batch_sample_states,
    _filter_batch_targets,
    _merge_ai_global_tags_into_sample_data,
    _normalize_results,
    _raise_if_ai_global_tags_incomplete,
    _sample_batch_include_geometry,
    _sample_has_formal_geometry,
    _apply_model_runtime_options,
    _build_inference_payload,
)
from labelu.internal.application.service import auto_label
from labelu.internal.common.config import settings
from labelu.internal.application.service.vision_llm_chain import _build_instruction
from labelu.internal.domain.models.sample import SampleState, TaskSample


def test_auto_label_defaults_to_global_only_and_overwrite_existing_annotations():
    single = AutoLabelCommand()
    batch = BatchAutoLabelCommand()

    assert single.include_geometry_tools is False
    assert single.overwrite_existing_annotations is False
    assert batch.include_geometry_tools is False
    assert batch.sample_states is None
    assert batch.overwrite_existing_annotations is True


def test_remote_model_runtime_options_are_forwarded_without_mutating_payload():
    payload = {"image_url": "http://model/image.jpg", "labels": []}
    enriched = _apply_model_runtime_options(
        payload,
        {"model": "qwen-vl", "max_new_tokens": 2048},
    )

    assert payload == {"image_url": "http://model/image.jpg", "labels": []}
    assert enriched["model"] == "qwen-vl"
    assert enriched["max_new_tokens"] == 2048


def test_tested_admin_global_tag_connection_overrides_legacy_local_http(monkeypatch):
    """The worker must use the configured Qwen connection, not an empty .env endpoint."""
    monkeypatch.setattr(settings, "AI_PROVIDER", "local_http")
    monkeypatch.setattr(
        auto_label,
        "resolve_model_integration",
        lambda key: {"provider_key": key, "endpoint": "http://qwen/v1"}
        if key == "tagtool"
        else None,
    )
    assert auto_label._configured_global_tag_vision_available() is True


def test_batch_sample_states_defaults_to_all_annotatable_states_and_filters_invalid_values():
    default_cmd = BatchAutoLabelCommand()
    mixed_cmd = BatchAutoLabelCommand(sample_states=["DONE", "BAD", "INCOMPLETE", "DONE"])

    assert _batch_sample_states(default_cmd) == [
        SampleState.NEW.value,
        SampleState.INCOMPLETE.value,
        SampleState.DONE.value,
    ]
    assert _batch_sample_states(mixed_cmd) == [SampleState.DONE.value, SampleState.INCOMPLETE.value]


def test_batch_sample_states_preserves_new_only_compatibility_flag():
    cmd = BatchAutoLabelCommand(include_done_samples=False)

    assert _batch_sample_states(cmd) == [SampleState.NEW.value]


def test_batch_sample_states_preserves_explicit_new_only_selection():
    cmd = BatchAutoLabelCommand(sample_states=[SampleState.NEW.value])

    assert _batch_sample_states(cmd) == [SampleState.NEW.value]


def test_batch_mode_is_forced_to_tags_only():
    assert _batch_mode_requests_geometry(BatchAutoLabelCommand()) is False
    assert _batch_mode_requests_geometry(BatchAutoLabelCommand(mode="BOX_AND_TAGS")) is False
    assert _batch_mode_requests_geometry(BatchAutoLabelCommand(mode="BOX_AND_TAGS", include_geometry_tools=True)) is True


def test_batch_box_mode_only_includes_geometry_for_new_samples_without_formal_geometry():
    new_without_geometry = TaskSample(
        state=SampleState.NEW.value,
        data=json.dumps({"result": json.dumps({"tagTool": {"toolName": "tagTool", "result": []}})}),
    )
    new_with_geometry = TaskSample(
        state=SampleState.NEW.value,
        data=json.dumps(
            {
                "result": json.dumps(
                    {
                        "rectTool": {
                            "toolName": "rectTool",
                            "result": [{"id": "box-1", "label": "car"}],
                        }
                    }
                )
            }
        ),
    )
    incomplete_without_geometry = TaskSample(state=SampleState.INCOMPLETE.value, data=json.dumps({"result": "{}"}))

    assert _sample_has_formal_geometry(new_without_geometry) is False
    assert _sample_has_formal_geometry(new_with_geometry) is True
    assert _sample_batch_include_geometry(new_without_geometry, True) is True
    assert _sample_batch_include_geometry(new_with_geometry, True) is False
    assert _sample_batch_include_geometry(incomplete_without_geometry, True) is False
    assert _sample_batch_include_geometry(new_without_geometry, False) is False


def test_filter_batch_targets_skips_samples_with_complete_global_tags():
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            }
        ]
    }
    complete = TaskSample(
        id=1,
        state=SampleState.INCOMPLETE.value,
        data=json.dumps(
            {
                "result": json.dumps(
                    {
                        "tagTool": {
                            "toolName": "tagTool",
                            "result": [{"type": "tag", "value": {"weather": ["sunny"]}}],
                        }
                    }
                )
            }
        ),
    )
    missing = TaskSample(
        id=2,
        state=SampleState.INCOMPLETE.value,
        data=json.dumps({"result": json.dumps({"tagTool": {"toolName": "tagTool", "result": []}})}),
    )

    assert _filter_batch_targets([complete, missing], task_config, skip_complete_global_tags=True) == [missing]
    assert _filter_batch_targets([complete, missing], task_config, skip_complete_global_tags=False) == [
        complete,
        missing,
    ]


def test_normalize_tagtool_preannotation_includes_type_tag():
    model_data = {
        "results": [
            {"toolName": "tagTool", "result": {"value": {"weather": ["sunny"]}}},
        ]
    }
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            }
        ]
    }
    out = _normalize_results(model_data, "demo.jpg", task_config)
    items = out["annotations"]["tagTool"]["result"]
    assert len(items) == 1
    assert items[0]["type"] == "tag"
    assert items[0]["value"] == {"weather": ["sunny"]}


def test_normalize_bbox_preannotation_keeps_configured_class_and_geometry():
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            }
        ]
    }
    out = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 1, "y": 2, "width": 30, "height": 40},
                },
                {
                    "toolName": "rectTool",
                    "label": "unknown",
                    "result": {"x": 0, "y": 0, "width": 5, "height": 5},
                },
            ]
        },
        "demo.jpg",
        task_config,
        include_geometry_tools=True,
    )
    items = out["annotations"]["rectTool"]["result"]
    assert len(items) == 1
    assert items[0]["label"] == "person"
    assert items[0]["width"] == 30


def test_bbox_inference_payload_excludes_global_tag_dimensions():
    task = SimpleNamespace(id=1, name="检测任务")
    sample = SimpleNamespace(file=SimpleNamespace(path="/image.jpg"))
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {"key": "天气", "value": "weather", "type": "enum", "options": [{"key": "晴", "value": "sunny"}]},
                    ]
                },
            },
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            },
        ]
    }

    payload = _build_inference_payload(
        sample,
        task,
        task_config,
        filter_by_labels=True,
        include_geometry_tools=True,
        prompt=None,
    )

    assert payload["labels"] == [{"name": "person", "display_name": "人员", "color": None, "tool": "rectTool"}]
    assert payload["constraints"]["allowed_tools"] == ["rectTool"]
    assert payload["tag_dimensions"] == []


def test_normalize_bbox_preannotation_rescales_transient_vlm_coordinates():
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            }
        ]
    }
    out = _normalize_results(
        {
            "_geometry_scale": [2, 2],
            "_image_size": [4000, 2000],
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 10, "y": 20, "width": 30, "height": 40},
                }
            ],
        },
        "demo.jpg",
        task_config,
        include_geometry_tools=True,
        image_size=[4000, 2000],
    )
    item = out["annotations"]["rectTool"]["result"][0]
    assert item["x"] == 20
    assert item["y"] == 40
    assert item["width"] == 60
    assert item["height"] == 80


def test_normalize_bbox_accepts_numeric_strings_from_vlm():
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            }
        ]
    }

    out = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": "1", "y": "2.5", "width": "30", "height": "40"},
                }
            ]
        },
        "demo.jpg",
        task_config,
        include_geometry_tools=True,
    )

    item = out["annotations"]["rectTool"]["result"][0]
    assert item["x"] == 1.0
    assert item["y"] == 2.5
    assert item["width"] == 30.0


def test_normalize_bbox_skips_invalid_geometry_and_keeps_valid_results():
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            }
        ]
    }

    out = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 1, "y": None, "width": 30, "height": 40},
                },
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 10, "y": 20, "width": 30, "height": 40},
                },
            ]
        },
        "demo.jpg",
        task_config,
        include_geometry_tools=True,
    )

    items = out["annotations"]["rectTool"]["result"]
    assert len(items) == 1
    assert items[0]["x"] == 10
    assert "Skipped invalid rectTool result" in out["meta"]["warning_message"]
    assert "geometry y must be a number" in out["meta"]["warning_message"]


def test_normalize_bbox_returns_empty_annotations_when_all_geometry_is_invalid():
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            }
        ]
    }

    out = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 0, "y": "unknown", "width": 30, "height": 40},
                },
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": -1, "y": 2, "width": 30, "height": 40},
                },
            ]
        },
        "demo.jpg",
        task_config,
        include_geometry_tools=True,
    )

    assert out["annotations"] == {}
    warning_message = out["meta"]["warning_message"]
    assert warning_message
    assert "geometry y must be a number" in warning_message
    assert "rectTool x/y must be non-negative" in warning_message


def test_normalize_root_tag_values_as_tagtool_result():
    model_data = {
        "results": [],
        "tag_values": {"weather": ["sunny"]},
    }
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            }
        ]
    }

    out = _normalize_results(model_data, "demo.jpg", task_config)
    items = out["annotations"]["tagTool"]["result"]
    merged, changed = _merge_ai_global_tags_into_sample_data({"result": "{}"}, out)

    assert len(items) == 1
    assert items[0]["type"] == "tag"
    assert items[0]["value"] == {"weather": ["sunny"]}
    assert changed is True
    assert json.loads(merged["result"])["tagTool"]["result"][0]["value"] == {"weather": ["sunny"]}


def test_normalize_tagtool_splits_multiple_dimensions():
    model_data = {
        "results": [
            {
                "toolName": "tagTool",
                "result": {"value": {"weather": ["sunny"], "scene": ["urban"]}},
            },
        ]
    }
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                        {
                            "key": "场景",
                            "value": "scene",
                            "type": "enum",
                            "options": [{"key": "城市", "value": "urban"}],
                        },
                    ]
                },
            }
        ]
    }
    out = _normalize_results(model_data, "demo.jpg", task_config)
    items = out["annotations"]["tagTool"]["result"]
    assert len(items) == 2
    dim_keys = {next(iter(x["value"])) for x in items}
    assert dim_keys == {"weather", "scene"}


def test_normalize_strips_geometry_when_include_geometry_tools_false():
    model_data = {
        "results": [
            {
                "toolName": "rectTool",
                "result": {"x": 0, "y": 0, "width": 10, "height": 10},
                "label": "cat",
            },
            {"toolName": "tagTool", "result": {"value": {"weather": ["sunny"]}}},
        ]
    }
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [{"key": "猫", "value": "cat", "color": "#f00"}],
                },
            },
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            },
        ]
    }
    out = _normalize_results(
        model_data,
        "demo.jpg",
        task_config,
        include_geometry_tools=False,
    )
    assert "rectTool" not in out["annotations"]
    assert "tagTool" in out["annotations"]


def test_normalize_records_overwrite_existing_annotations_meta():
    model_data = {
        "results": [
            {"toolName": "tagTool", "result": {"value": {"weather": ["sunny"]}}},
        ]
    }
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            }
        ]
    }

    out = _normalize_results(
        model_data,
        "demo.jpg",
        task_config,
        overwrite_existing_annotations=True,
    )

    assert out["meta"]["overwrite_existing_annotations"] is True
    assert out["meta"]["include_geometry_tools"] is False


def test_merge_ai_global_tags_into_sample_data_preserves_geometry():
    sample_data = {
        "file": {"filename": "demo.jpg"},
        "result": (
            '{"width": 100, "height": 80, "rectTool": {"toolName": "rectTool", '
            '"result": [{"id": "box-1", "label": "car"}]}, '
            '"tagTool": {"toolName": "tagTool", "result": [{"type": "tag", "value": {"weather": ["rainy"]}}]}}'
        ),
    }
    ai_payload = {
        "annotations": {
            "tagTool": {
                "toolName": "tagTool",
                "result": [{"type": "tag", "value": {"weather": ["sunny"], "scene": ["urban"]}}],
            },
            "rectTool": {
                "toolName": "rectTool",
                "result": [{"id": "ai-box", "label": "bus"}],
            },
        }
    }

    merged, changed = _merge_ai_global_tags_into_sample_data(sample_data, ai_payload)

    assert changed is True
    result = json.loads(merged["result"])
    assert result["rectTool"]["result"] == [{"id": "box-1", "label": "car"}]
    assert [item["value"] for item in result["tagTool"]["result"]] == [
        {"weather": ["sunny"]},
        {"scene": ["urban"]},
    ]


def test_merge_ai_global_tags_returns_unchanged_without_tagtool():
    sample_data = {"result": {"rectTool": {"toolName": "rectTool", "result": [{"id": "box-1"}]}}}
    ai_payload = {
        "annotations": {
            "rectTool": {"toolName": "rectTool", "result": [{"id": "ai-box"}]},
        }
    }

    merged, changed = _merge_ai_global_tags_into_sample_data(sample_data, ai_payload)

    assert changed is False
    assert merged == sample_data


def test_vision_llm_tag_only_prompt_requires_all_global_dimensions():
    instruction = _build_instruction(
        {
            "labels": [],
            "tag_dimensions": [
                {
                    "field_key": "weather",
                    "name_cn": "天气",
                    "options": [{"key": "晴", "value": "sunny"}],
                },
                {
                    "field_key": "scene",
                    "name_cn": "场景",
                    "options": [{"key": "城市", "value": "urban"}],
                },
            ],
            "constraints": {"allowed_tools": ["tagTool"], "filter_by_labels": True},
        },
        100,
        80,
        include_runtime_suffix=False,
    )

    assert "必须为上面列出的每一个 field_key 都输出一个合法 value_en" in instruction
    assert "不能返回空 tag_values" in instruction
    assert "可省略该键" not in instruction


def test_raise_if_ai_global_tags_incomplete_reports_missing_dimensions():
    task_config = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                        {
                            "key": "场景",
                            "value": "scene",
                            "type": "enum",
                            "options": [{"key": "城市", "value": "urban"}],
                        },
                    ]
                },
            }
        ]
    }
    sample = TaskSample(
        state=SampleState.INCOMPLETE.value,
        data=json.dumps(
            {
                "result": json.dumps(
                    {
                        "tagTool": {
                            "toolName": "tagTool",
                            "result": [{"type": "tag", "value": {"weather": ["sunny"]}}],
                        }
                    }
                )
            }
        ),
    )

    try:
        _raise_if_ai_global_tags_incomplete(sample, task_config)
    except ValueError as exc:
        assert "missing dimensions: scene" in str(exc)
    else:
        raise AssertionError("expected incomplete AI global tags to fail the sample")
