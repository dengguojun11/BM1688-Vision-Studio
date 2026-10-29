"""vision_llm_chain：提示词和上游瞬时错误处理。"""

import json
from io import BytesIO

from PIL import Image

from labelu.internal.application.service import vision_llm_chain as vision_service
from labelu.internal.application.service.vision_llm_chain import _build_instruction
from labelu.internal.application.service.vision_llm_chain import (
    _prepare_image_for_inference,
)
from labelu.internal.common.config import settings


def test_prepare_image_for_inference_downscales_only_transient_image(monkeypatch):
    source = BytesIO()
    Image.new("RGB", (3840, 2160), "white").save(source, format="JPEG")
    monkeypatch.setattr(settings, "AI_VISION_MAX_IMAGE_SIDE", 1536)

    prepared, size = _prepare_image_for_inference(source.getvalue())

    assert size == (1536, 864)
    with Image.open(BytesIO(prepared)) as image:
        assert image.size == size


def test_prepare_image_for_inference_accepts_bbox_specific_resolution(monkeypatch):
    source = BytesIO()
    Image.new("RGB", (3840, 2160), "white").save(source, format="JPEG")
    monkeypatch.setattr(settings, "AI_VISION_MAX_IMAGE_SIDE", 1536)

    general_image, general_size = _prepare_image_for_inference(source.getvalue())
    bbox_image, bbox_size = _prepare_image_for_inference(
        source.getvalue(), max_side=2560
    )

    assert general_size == (1536, 864)
    assert bbox_size == (2560, 1440)
    with Image.open(BytesIO(general_image)) as image:
        assert image.size == general_size
    with Image.open(BytesIO(bbox_image)) as image:
        assert image.size == bbox_size


def test_instruction_tag_only_mode_no_rect_example():
    payload = {
        "labels": [],
        "tag_dimensions": [
            {
                "field_key": "weather",
                "name_cn": "天气",
                "options": [{"key": "晴", "value": "sunny"}],
            }
        ],
        "constraints": {"allowed_tools": ["tagTool"], "filter_by_labels": True},
        "prompt": None,
    }
    text = _build_instruction(payload, 640, 480)
    assert "rectTool" not in text
    assert "tag_values" in text
    assert "weather" in text or "field_key" in text


def test_instruction_geometry_mode_keeps_rect_schema():
    payload = {
        "labels": [
            {"name": "cat", "display_name": "猫", "tool": "rectTool", "color": "#f00"}
        ],
        "tag_dimensions": [],
        "constraints": {"allowed_tools": ["rectTool"], "filter_by_labels": True},
        "prompt": None,
    }
    text = _build_instruction(payload, 640, 480)
    assert "rectTool" in text
    assert '"tag_values"' not in text


def test_instruction_geometry_mode_keeps_tag_schema_when_dimensions_exist():
    payload = {
        "labels": [{"name": "person", "display_name": "人员", "tool": "rectTool"}],
        "tag_dimensions": [
            {
                "field_key": "weather",
                "name_cn": "天气",
                "options": [{"key": "晴", "value": "sunny"}],
            }
        ],
        "constraints": {
            "allowed_tools": ["rectTool", "tagTool"],
            "filter_by_labels": True,
        },
        "prompt": None,
    }

    text = _build_instruction(payload, 640, 480)

    assert '"tag_values"' in text
    assert "weather" in text


def test_instruction_geometry_mode_requires_complete_instance_boxes():
    payload = {
        "labels": [
            {
                "name": "person_motorbike",
                "display_name": "骑摩托车人员",
                "tool": "rectTool",
            }
        ],
        "tag_dimensions": [],
        "constraints": {"allowed_tools": ["rectTool"], "filter_by_labels": True},
        "prompt": None,
    }

    text = _build_instruction(payload, 2560, 1440)

    assert "label='person_motorbike'" in text
    assert "display_name='骑摩托车人员'" in text
    assert "本次输入图像 2560 x 1440" in text
    assert "完整可见部分" in text
    assert "逐个实例" in text
    assert "图像边缘" in text
    assert "复查漏检" in text
    assert "x2/y2" in text


def test_instruction_tag_only_mode_omits_bbox_quality_rules():
    payload = {
        "labels": [],
        "tag_dimensions": [
            {
                "field_key": "weather",
                "name_cn": "天气",
                "options": [{"key": "晴", "value": "sunny"}],
            }
        ],
        "constraints": {"allowed_tools": ["tagTool"], "filter_by_labels": True},
        "prompt": None,
    }

    text = _build_instruction(payload, 1536, 864)

    assert "完整可见部分" not in text
    assert "逐个实例" not in text
    assert "复查漏检" not in text


def test_openai_compatible_infer_retries_transient_http_errors(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code: int, body: str):
            self.status_code = status_code
            self.text = body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise vision_service.httpx.HTTPStatusError(
                    "upstream error",
                    request=vision_service.httpx.Request("POST", "http://model.test"),
                    response=vision_service.httpx.Response(self.status_code),
                )

        def json(self):
            return {"choices": [{"message": {"content": '{"results": []}'}}]}

    responses = [
        FakeResponse(500, '{"error":{"message":"<50002> InternalError"}}'),
        FakeResponse(503, "service unavailable"),
        FakeResponse(200, json.dumps({"choices": []})),
    ]
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            calls.append(url)
            return responses.pop(0)

    delays = []
    monkeypatch.setattr(vision_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(vision_service.time, "sleep", delays.append)

    result = vision_service._openai_compatible_infer(
        provider_label="local_vision",
        base_url="http://model.test/v1",
        api_key="test-key",
        model="local-vision",
        instruction="return JSON",
        image_bytes=b"image",
        mime="image/jpeg",
    )

    assert result["results"] == []
    assert len(calls) == 3
    assert delays == [1, 2]
