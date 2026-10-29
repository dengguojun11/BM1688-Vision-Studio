"""Legacy HTTP labeling must enforce the same retired-provider boundaries."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.auto_label import AutoLabelCommand
from labelu.internal.application.service import auto_label as service
from labelu.internal.common import db as common_db
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.task import MediaType


class ReachedNormalization(Exception):
    """Stop successful mocked inference before any annotation write."""


@pytest.fixture
def legacy_http(monkeypatch):
    monkeypatch.setattr(service.settings, "AI_AUTO_LABEL_ENABLED", True)
    monkeypatch.setattr(service.settings, "AI_PROVIDER", "local_http")
    monkeypatch.setattr(
        service.vision_llm_chain, "has_managed_volcengine_config", lambda: False
    )
    monkeypatch.setattr(service, "resolve_model_integration", lambda _: None)
    monkeypatch.setattr(service, "has_configured_model_integration", lambda _: False)
    task = SimpleNamespace(id=1, media_type=MediaType.IMAGE.value, config="{}")
    sample = SimpleNamespace(id=2, task_id=1, file=SimpleNamespace(filename="test.jpg"))
    monkeypatch.setattr(service.crud_task, "get", lambda **kwargs: task)
    monkeypatch.setattr(service.crud_sample, "get", lambda **kwargs: sample)
    monkeypatch.setattr(
        service.crud_pre_annotation, "list_by", lambda **kwargs: ([], 0)
    )
    monkeypatch.setattr(service, "assert_user_has_task_access", lambda *args: None)
    monkeypatch.setattr(service, "resolve_ai_task_config", lambda *args: {})
    monkeypatch.setattr(
        service,
        "_build_model_payload",
        lambda *args: {"image_url": "http://image/test.jpg"},
    )
    monkeypatch.setattr(
        service,
        "_build_inference_payload",
        lambda *args, **kwargs: {"image_url": "http://image/test.jpg"},
    )
    monkeypatch.setattr(service, "biz_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(common_db, "session_scope", lambda: nullcontext(object()))

    def stop_before_writes(**kwargs):
        raise ReachedNormalization

    monkeypatch.setattr(service, "_normalize_results", stop_before_writes)

    def invoke(mode):
        if mode == "manual":
            return asyncio.run(
                service.create(
                    db=object(),
                    task_id=1,
                    sample_id=2,
                    cmd=AutoLabelCommand(),
                    current_user=SimpleNamespace(id=3),
                )
            )
        return service._process_single_sample(
            task_id=1,
            task_config={},
            sample_id=2,
            filter_by_labels=True,
            include_geometry_tools=False,
            overwrite_existing_annotations=False,
            created_by=3,
        )

    return invoke


@pytest.mark.parametrize("mode", ["manual", "worker"])
@pytest.mark.parametrize(
    "endpoint,model",
    [
        ("http://proxy.test/infer", "qwen3-vl-plus"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "renamed-model"),
        (
            "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            "doubao-seed-code",
        ),
        ("http://ark.cn-beijing.volces.com/api/coding/v3", "doubao-seed-code"),
    ],
)
def test_legacy_http_rejects_retired_or_metered_routes_before_client(
    monkeypatch, legacy_http, mode, endpoint, model
):
    monkeypatch.setattr(service.settings, "AI_MODEL_ENDPOINT", endpoint)
    monkeypatch.setattr(service.settings, "AI_MODEL_NAME", model)
    for client in ("Client", "AsyncClient"):
        monkeypatch.setattr(
            service.httpx,
            client,
            lambda **kwargs: pytest.fail("unexpected HTTP client"),
        )

    with pytest.raises(LabelUException if mode == "manual" else ValueError):
        legacy_http(mode)


@pytest.mark.parametrize("mode", ["manual", "worker"])
def test_legacy_http_preserves_custom_service_and_disables_redirects(
    monkeypatch, legacy_http, mode
):
    endpoint = "http://custom.test:18081/api/infer"
    monkeypatch.setattr(service.settings, "AI_MODEL_ENDPOINT", endpoint)
    monkeypatch.setattr(service.settings, "AI_MODEL_NAME", "local-vision")
    calls = {}
    response = SimpleNamespace(
        status_code=200, raise_for_status=lambda: None, json=lambda: {"results": []}
    )

    class Client:
        def __init__(self, **kwargs):
            calls["options"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.update(url=url, payload=kwargs["json"])
            return response

    class AsyncClient(Client):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            return super().post(url, **kwargs)

    monkeypatch.setattr(service.httpx, "Client", Client)
    monkeypatch.setattr(service.httpx, "AsyncClient", AsyncClient)
    with pytest.raises(ReachedNormalization):
        legacy_http(mode)

    assert calls["url"] == endpoint
    assert calls["payload"]["model"] == "local-vision"
    assert calls["options"]["follow_redirects"] is False


@pytest.mark.parametrize("mode", ["manual", "worker"])
def test_blocked_legacy_runtime_never_reuses_environment_endpoint(
    monkeypatch, legacy_http, mode
):
    monkeypatch.setattr(service.settings, "AI_MODEL_ENDPOINT", "http://old.test/infer")
    monkeypatch.setattr(service, "_configured_runtime", lambda _: {"blocked": True})
    monkeypatch.setattr(service, "_ensure_ai_backend_configured", lambda: None)
    for client in ("Client", "AsyncClient"):
        monkeypatch.setattr(
            service.httpx,
            client,
            lambda **kwargs: pytest.fail("unexpected HTTP client"),
        )

    with pytest.raises(LabelUException if mode == "manual" else ValueError):
        legacy_http(mode)


@pytest.mark.parametrize("mode", ["manual", "worker"])
def test_legacy_http_promotes_published_contract_prompt_to_top_level_prompt(
    monkeypatch, legacy_http, mode
):
    """Old local adapters must receive the published contract prompt directly."""

    effective_prompt = "契约标签 person；只输出 rectTool；结果使用 x y width height"
    binding = {
        "id": 41,
        "contract_revision": 4,
        "label_set_signature": "labels",
        "class_order_signature": "order",
        "prompt_sha256": "hash",
        "effective_prompt": effective_prompt,
    }
    monkeypatch.setattr(
        service,
        "_resolve_contract_prompt",
        lambda *args, **kwargs: binding,
    )

    def payload_builder(*args, **kwargs):
        return {
            "image_url": "http://image/test.jpg",
            "prompt": "单次补充",
            "contract_prompt": {"effective_prompt": effective_prompt},
        }

    monkeypatch.setattr(service, "_build_model_payload", payload_builder)
    monkeypatch.setattr(service, "_build_inference_payload", payload_builder)
    endpoint = "http://custom.test:18081/api/infer"
    monkeypatch.setattr(service.settings, "AI_MODEL_ENDPOINT", endpoint)
    monkeypatch.setattr(service.settings, "AI_MODEL_NAME", "local-vision")
    calls = {}
    response = SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {"results": []},
    )

    class Client:
        def __init__(self, **kwargs):
            calls["options"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.update(url=url, payload=kwargs["json"])
            return response

    class AsyncClient(Client):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            return super().post(url, **kwargs)

    monkeypatch.setattr(service.httpx, "Client", Client)
    monkeypatch.setattr(service.httpx, "AsyncClient", AsyncClient)

    with pytest.raises(ReachedNormalization):
        legacy_http(mode)

    assert calls["payload"]["prompt"].startswith(effective_prompt)
    assert "单次补充" in calls["payload"]["prompt"]
    assert calls["payload"]["contract_prompt"]["effective_prompt"] == effective_prompt
