"""LocateAnything 标框链路服务层测试。"""

import asyncio

import pytest

from labelu.internal.application.service import locateanything_preannotate as svc
from labelu.internal.adapter.persistence import crud_pre_annotation
from labelu.internal.common.error_code import ErrorCode, LabelUException


def test_build_la_payload_only_includes_rect_labels():
    task_cfg = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人", "value": "person"}]},
            },
            {
                "tool": "polygonTool",
                "config": {"attributes": [{"key": "车", "value": "car"}]},
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
                        }
                    ]
                },
            },
        ]
    }

    payload = svc._build_la_model_payload(
        image_url="http://labelu.local/media/a.jpg",
        task_id=7,
        task_name="demo",
        task_config=task_cfg,
        filter_by_labels=True,
    )

    assert payload["image_url"] == "http://labelu.local/media/a.jpg"
    assert payload["labels"] == [
        {"name": "person", "display_name": "人", "color": None, "tool": "rectTool"}
    ]
    assert payload["constraints"]["allowed_tools"] == ["rectTool"]
    assert payload["constraints"]["filter_by_labels"] is True
    assert payload["prompt"] is None


def test_build_la_payload_uses_consistent_pool_contract_and_business_queries():
    task_cfg = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {"key": "水果摊", "value": "streetVendor_fruit"},
                        {"key": "其他摊贩", "value": "streetVendor_other"},
                        {"key": "篮子", "value": "object_basket"},
                    ]
                },
            }
        ],
        "consistent_pool": {
            "dataset_category_key": "streetVendorOccupation",
            "class_names": ["streetVendor_fruit", "streetVendor_other"],
        },
    }

    payload = svc._build_la_model_payload(
        image_url="http://labelu.local/media/stall.jpg",
        task_id=247,
        task_name="占道经营",
        task_config=task_cfg,
        filter_by_labels=True,
    )

    assert [(item["name"], item["display_name"]) for item in payload["labels"]] == [
        ("streetVendor_fruit", "售卖水果或榴莲的路边水果摊位，不是餐饮摊"),
        ("streetVendor_other", "不属于蔬菜摊、餐饮摊、衣服摊、水果摊的其他路边占道摊位"),
    ]


def test_build_la_preannotation_prefers_specific_street_vendor_over_other():
    task_cfg = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {"key": "水果摊", "value": "streetVendor_fruit"},
                        {"key": "其他摊贩", "value": "streetVendor_other"},
                        {"key": "篮子", "value": "object_basket"},
                    ]
                },
            }
        ],
        "consistent_pool": {
            "dataset_category_key": "streetVendorOccupation",
            "class_names": ["streetVendor_fruit", "streetVendor_other"],
        },
    }
    model_response = {
        "model": "LocateAnything-3B",
        "results": [
            {
                "toolName": "rectTool",
                "label": "streetVendor_fruit",
                "result": {"x": 0, "y": 640, "width": 800, "height": 440},
            },
            {
                "toolName": "rectTool",
                "label": "streetVendor_other",
                "result": {"x": 5, "y": 645, "width": 790, "height": 430},
            },
            {
                "toolName": "rectTool",
                "label": "object_basket",
                "result": {"x": 600, "y": 850, "width": 50, "height": 50},
            },
        ],
    }

    payload = svc._build_la_preannotation_payload(
        sample_name="stall.jpg",
        task_config=task_cfg,
        model_response=model_response,
    )

    rects = payload["annotations"]["rectTool"]["result"]
    assert [rect["label"] for rect in rects] == ["streetVendor_fruit"]
    assert payload["meta"]["target_labels"] == ["streetVendor_fruit", "streetVendor_other"]
    assert payload["meta"]["model_queries"][0].startswith("售卖水果或榴莲")


def test_build_la_preannotation_payload_has_distinct_source_type():
    task_cfg = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人", "value": "person"}]},
            }
        ]
    }
    model_response = {
        "model": "LocateAnything-3B",
        "results": [
            {
                "tool": "rectTool",
                "label": "person",
                "score": 0.91,
                "result": {"x": 10, "y": 20, "width": 30, "height": 40},
            }
        ],
    }

    payload = svc._build_la_preannotation_payload(
        sample_name="a.jpg",
        task_config=task_cfg,
        model_response=model_response,
    )

    assert payload["sample_name"] == "a.jpg"
    assert payload["meta"]["source_type"] == svc.LA_PRE_META
    assert payload["meta"]["model"] == "LocateAnything-3B"
    rects = payload["annotations"]["rectTool"]["result"]
    assert rects[0]["label"] == "person"
    assert rects[0]["attributes"]["score"] == "0.91"


def test_la_preannotation_is_canvas_generated_candidate_source():
    assert svc.LA_PRE_META in crud_pre_annotation.GENERATED_PRE_ANNOTATION_SOURCE_TYPES


def test_create_batch_job_rejects_when_la_disabled(monkeypatch):
    monkeypatch.setattr(svc.settings, "LA_AUTO_LABEL_ENABLED", False)

    with pytest.raises(LabelUException) as exc:
        asyncio.run(
            svc.create_batch_job(
                db=None,
                task_id=1,
                cmd=object(),
                current_user=object(),
            )
        )

    assert exc.value.code == ErrorCode.CODE_56000_AUTO_LABEL_DISABLED.value[0]
    assert exc.value.msg == "LA 标注已暂时停用"
