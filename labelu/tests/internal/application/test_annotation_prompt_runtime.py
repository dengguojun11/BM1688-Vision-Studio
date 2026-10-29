from io import BytesIO

from PIL import Image

from labelu.internal.application.service import vision_llm_chain as vision

CODING_BASE = "https://ark.cn-beijing.volces.com/api/coding/v3"


def test_published_contract_prompt_is_sent_to_managed_vision_model(monkeypatch):
    source = BytesIO()
    Image.new("RGB", (1280, 960), "white").save(source, format="JPEG")
    captured = {}

    monkeypatch.setattr(
        vision,
        "_download_image",
        lambda _url: (source.getvalue(), "image/jpeg"),
    )
    monkeypatch.setattr(vision.settings, "AI_BBOX_VISION_MAX_IMAGE_SIDE", 640)

    def fake_infer(**kwargs):
        captured.update(kwargs)
        return {"results": [], "model": kwargs["model"]}

    monkeypatch.setattr(vision, "_openai_compatible_infer", fake_infer)
    monkeypatch.setattr(
        vision,
        "_configured_model_for",
        lambda *_args, **_kwargs: {
            "provider_key": "volcengine",
            "name": "managed bbox model",
            "endpoint": CODING_BASE,
            "model": "doubao-seed-vision",
            "api_key": "test-key",
            "auth_type": "bearer",
            "auth_header": "Authorization",
            "verify_tls": False,
            "timeout_seconds": 30,
            "max_new_tokens": 1024,
            "blocked": False,
        },
    )
    monkeypatch.setattr(vision, "has_configured_model_integration", lambda *_: True)

    payload = {
        "image_url": "http://image.test/person.jpg",
        "prompt": "只作为附加说明",
        "labels": [{"name": "legacy_label", "tool": "rectTool"}],
        "constraints": {"allowed_tools": ["rectTool"]},
        "contract_prompt": {
            "id": 17,
            "effective_prompt": (
                "契约标签 person；只能使用 rectTool；输出 results；字段 x y width height；"
                "图像尺寸 {{image_width}}x{{image_height}}"
            ),
        },
    }

    result = vision.call_vision_llm_chain(payload, provider_key="bbox_detection")

    assert result["results"] == []
    assert "契约标签 person" in captured["instruction"]
    assert "legacy_label" not in captured["instruction"]
    assert "图像尺寸 640x480" in captured["instruction"]
    assert "只作为附加说明" in captured["instruction"]
    assert captured["request_protocol"] == "responses"
    assert result["_image_size"] == [1280, 960]
    assert result["_geometry_scale"] == [2.0, 2.0]
