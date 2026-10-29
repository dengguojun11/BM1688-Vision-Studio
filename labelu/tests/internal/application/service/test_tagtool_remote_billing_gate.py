"""TagTool must share the visual chain's provider and billing boundaries."""

from types import SimpleNamespace

import pytest

from labelu.internal.application.service import tagtool_preannotate as service
from labelu.internal.application.service import vision_llm_chain as vision
from labelu.internal.common.error_code import LabelUException

CODING_BASE = "https://ark.cn-beijing.volces.com/api/coding/v3"


@pytest.fixture(autouse=True)
def no_page_provider_by_default(monkeypatch):
    monkeypatch.setattr(vision, "has_managed_volcengine_config", lambda: False)


@pytest.mark.parametrize(
    "endpoint,model",
    [
        ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-code"),
        ("https://proxy.test/v1", "qwen3-vl-plus"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "renamed-model"),
    ],
)
@pytest.mark.parametrize("protocol", ["multipart", "openai_compatible"])
def test_direct_remote_prediction_rejects_retired_routes_before_http(
    monkeypatch, endpoint, model, protocol
):
    monkeypatch.setattr(
        service.httpx,
        "Client",
        lambda **kwargs: pytest.fail("unexpected client creation"),
    )
    with pytest.raises(ValueError):
        service._predict_remote_image(
            {
                "provider_key": "tagtool",
                "endpoint": endpoint,
                "model": model,
                "request_protocol": protocol,
            },
            image_path=SimpleNamespace(read_bytes=lambda: b"image"),
            sample_name="test.jpg",
            task_id=1,
            allowed_labels={"weather": {"sunny"}},
        )


def test_page_ark_takes_precedence_over_old_tagtool_record(monkeypatch):
    managed = {
        "provider_key": "volcengine",
        "endpoint": CODING_BASE,
        "model": "doubao-seed-code",
    }
    monkeypatch.setattr(vision, "has_managed_volcengine_config", lambda: True)
    monkeypatch.setattr(vision, "_configured_model_for", lambda _: managed)
    monkeypatch.setattr(
        service,
        "resolve_model_integration",
        lambda _: {"model": "qwen-vl", "endpoint": "http://old/v1"},
    )
    runtime = service._remote_tagtool_runtime()
    assert runtime["endpoint"] == CODING_BASE
    assert runtime["model"] == "doubao-seed-code"
    assert runtime["request_protocol"] == "openai_compatible"


def test_disabled_page_ark_blocks_old_tagtool_fallback(monkeypatch):
    monkeypatch.setattr(vision, "has_managed_volcengine_config", lambda: True)
    monkeypatch.setattr(
        vision,
        "_configured_model_for",
        lambda _: {"blocked": True, "provider_key": "volcengine"},
    )
    monkeypatch.setattr(
        service,
        "resolve_model_integration",
        lambda _: {"model": "local", "endpoint": "http://old/v1"},
    )
    with pytest.raises(LabelUException):
        service._remote_tagtool_runtime()


def test_legacy_qwen_tagtool_record_cannot_start_remote_job(monkeypatch):
    monkeypatch.setattr(
        service,
        "resolve_model_integration",
        lambda _: {
            "provider_key": "tagtool",
            "endpoint": "http://proxy/v1",
            "model": "qwen-vl",
        },
    )
    with pytest.raises(LabelUException):
        service._remote_tagtool_runtime()


def test_qwen_named_proxy_is_rejected_even_with_neutral_model_id(monkeypatch):
    monkeypatch.setattr(
        service.httpx,
        "Client",
        lambda **kwargs: pytest.fail("unexpected client creation"),
    )
    with pytest.raises(ValueError):
        service._predict_remote_openai_image(
            {
                "provider_key": "tagtool",
                "name": "Qwen proxy",
                "endpoint": "http://proxy.test/v1",
                "model": "renamed-model",
            },
            image_path=SimpleNamespace(read_bytes=lambda: b"image"),
            allowed_labels={"weather": {"sunny"}},
        )


@pytest.mark.parametrize("protocol", ["multipart", "openai_compatible"])
def test_remote_tagtool_never_follows_model_redirects(monkeypatch, protocol):
    calls = {}

    class Client:
        def __init__(self, **kwargs):
            calls.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            return SimpleNamespace(status_code=302, text="redirect")

    monkeypatch.setattr(service.httpx, "Client", Client)
    with pytest.raises(ValueError, match="302"):
        service._predict_remote_image(
            {
                "endpoint": "http://local.test/v1",
                "model": "local",
                "request_protocol": protocol,
            },
            image_path=SimpleNamespace(read_bytes=lambda: b"image", name="test.jpg"),
            sample_name="test.jpg",
            task_id=1,
            allowed_labels={"weather": {"sunny"}},
        )
    assert calls["follow_redirects"] is False


def test_queued_remote_request_reloads_authoritative_page_ark(monkeypatch):
    managed = {
        "provider_key": "volcengine",
        "endpoint": CODING_BASE,
        "model": "doubao-seed-code",
    }
    monkeypatch.setattr(vision, "has_managed_volcengine_config", lambda: True)
    monkeypatch.setattr(vision, "_configured_model_for", lambda _: managed)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(
                status_code=200,
                text="",
                json=lambda: {
                    "choices": [{"message": {"content": '{"weather":"sunny"}'}}],
                },
            )

    monkeypatch.setattr(service.httpx, "Client", Client)
    result = service._predict_remote_image(
        {"endpoint": "https://dashscope.aliyuncs.com/v1", "model": "qwen-vl"},
        image_path=SimpleNamespace(read_bytes=lambda: b"image", name="test.jpg"),
        sample_name="test.jpg",
        task_id=1,
        allowed_labels={"weather": {"sunny"}},
    )
    assert calls[0][0] == CODING_BASE + "/chat/completions"
    assert calls[0][1]["json"]["model"] == "doubao-seed-code"
    assert result["weather"]["label"] == "sunny"
