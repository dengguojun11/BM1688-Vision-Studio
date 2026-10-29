from __future__ import annotations

import pytest

from labelu.internal.application.command.tagtool_preannotate import BatchTagtoolPreannotateCommand
from labelu.internal.application.service import tagtool_preannotate as service
from labelu.internal.application.service.tagtool_preannotate import _parse_remote_predictions


def test_remote_tagtool_command_uses_zero_model_id():
    command = BatchTagtoolPreannotateCommand(model_kind="remote", provider_key="tagtool")
    assert command.model_id == 0
    assert command.provider_key == "tagtool"


def test_imported_tagtool_command_requires_model_id():
    with pytest.raises(ValueError):
        BatchTagtoolPreannotateCommand(model_kind="imported", model_id=0)


def test_parse_remote_predictions_accepts_common_response_shapes():
    parsed = _parse_remote_predictions(
        {
            "data": {
                "attributes": {
                    "weather": {"label": "sunny", "confidence": 0.91},
                    "quality": "clear",
                }
            }
        }
    )
    assert parsed["weather"]["label"] == "sunny"
    assert parsed["weather"]["confidence"] == 0.91
    assert parsed["quality"]["label"] == "clear"


def test_parse_remote_predictions_accepts_task_specific_dimensions():
    parsed = _parse_remote_predictions(
        {"result": {"weather_type": {"label": "sunny", "score": 0.8}}},
        allowed_labels={"weather_type": {"sunny", "rainy"}},
    )
    assert parsed == {"weather_type": {"label": "sunny", "confidence": 0.8}}


def test_openai_global_tag_request_uses_chat_completions_and_configured_headers(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake-jpeg")

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"content": '{"weather_type":{"label":"sunny","confidence":0.93}}'}}]
            }

    calls = {}

    class Client:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, endpoint, **kwargs):
            calls["endpoint"] = endpoint
            calls["request"] = kwargs
            return Response()

    monkeypatch.setattr(service.httpx, "Client", Client)
    result = service._predict_remote_openai_image(
        {
            "endpoint": "http://model.example/v1",
            "model": "local-vision",
            "api_key": "secret",
            "auth_type": "bearer",
            "max_new_tokens": 128,
            "timeout_seconds": 7,
            "verify_tls": False,
        },
        image_path=image_path,
        allowed_labels={"weather_type": {"sunny", "rainy"}},
    )
    assert calls["endpoint"] == "http://model.example/v1/chat/completions"
    assert calls["request"]["headers"]["Authorization"] == "Bearer secret"
    assert calls["kwargs"] == {"timeout": 7.0, "verify": False, "follow_redirects": False}
    assert result["weather_type"]["label"] == "sunny"
