"""Offline regression coverage for repeated provider configuration failures."""

import json
from email.utils import formatdate

import httpx
import pytest

from labelu.internal.application.service import vision_llm_chain as vision_service


@pytest.fixture(autouse=True)
def isolated_provider_state(monkeypatch):
    # Do not read administrator configuration or share cooldowns across tests.
    monkeypatch.setattr(vision_service, "load_ai_labeling_runtime", lambda: {})
    getattr(vision_service, "_VISION_PROVIDER_FAILURES", {}).clear()
    yield
    getattr(vision_service, "_VISION_PROVIDER_FAILURES", {}).clear()


def _install_transport(monkeypatch, handler):
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        vision_service.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def _infer(**overrides):
    args = {
        "provider_label": "provider",
        "base_url": "https://model.test/v1",
        "api_key": "offline-test-secret",
        "model": "test-vlm",
        "instruction": "return JSON",
        "image_bytes": b"offline-image",
        "mime": "image/jpeg",
    }
    args.update(overrides)
    return vision_service._openai_compatible_infer(**args)


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "Arrearage"),
        (401, "invalid_api_key"),
        (403, "access_denied"),
        (404, "model_not_found"),
    ],
)
def test_configuration_failure_is_cooled_then_retried(monkeypatch, status, code):
    calls = []
    now = [1000.0]
    monkeypatch.setattr(vision_service.time, "monotonic", lambda: now[0])

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": code}})

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    with pytest.raises(RuntimeError) as cached:
        _infer()

    assert len(calls) == 1
    assert f"HTTP {status}" in str(cached.value)
    assert "offline-test-secret" not in str(cached.value)
    assert "offline-test-secret" not in repr(vision_service._VISION_PROVIDER_FAILURES)
    now[0] += 61
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "changed",
    [
        {"base_url": "https://other.test/v1"},
        {"model": "new-vlm"},
        {"api_key": "new-offline-secret"},
        {"auth_type": "header", "auth_header": "X-Api-Key"},
    ],
)
def test_changed_connection_is_attempted_immediately(monkeypatch, changed):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, json={"error": {"code": "model_not_found"}})

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    with pytest.raises(RuntimeError):
        _infer()
    with pytest.raises(httpx.HTTPStatusError):
        _infer(**changed)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "retry_after,seconds", [(None, 60), ("12", 12), ("9999", 300), ("invalid", 60)]
)
def test_rate_limit_cools_without_repeating_request(monkeypatch, retry_after, seconds):
    calls = []
    now = [1000.0]
    monkeypatch.setattr(vision_service.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        vision_service.time,
        "sleep",
        lambda _: pytest.fail("must fall back immediately"),
    )

    def handler(request):
        calls.append(request)
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        return httpx.Response(
            429, headers=headers, json={"error": {"code": "rate_limit"}}
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    now[0] += seconds - 0.5
    with pytest.raises(RuntimeError):
        _infer()
    assert len(calls) == 1
    now[0] += 1
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    assert len(calls) == 2


def test_rate_limit_accepts_http_date(monkeypatch):
    calls = []
    now = [1000.0]
    monkeypatch.setattr(vision_service.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(vision_service.time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(
        vision_service.time,
        "sleep",
        lambda _: pytest.fail("must fall back immediately"),
    )

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, headers={"Retry-After": formatdate(1700000020, usegmt=True)}
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    now[0] += 19
    with pytest.raises(RuntimeError):
        _infer()
    assert len(calls) == 1
    now[0] += 2
    with pytest.raises(httpx.HTTPStatusError):
        _infer()
    assert len(calls) == 2


def test_request_specific_bad_input_does_not_cool_provider(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"code": "invalid_image"}})

    _install_transport(monkeypatch, handler)
    for image in (b"bad-image", b"another-image"):
        with pytest.raises(httpx.HTTPStatusError):
            _infer(image_bytes=image)
    assert len(calls) == 2


def test_responses_protocol_posts_coding_endpoint_and_parses_output_text(monkeypatch):
    requests = []

    def handler(request):
        import json

        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "doubao-seed-2-1-pro-260915"
        assert body["max_output_tokens"] == 128
        assert body["thinking"] == {"type": "disabled"}
        assert body["text"] == {"format": {"type": "json_object"}}
        assert body["input"][0]["content"][0]["type"] == "input_text"
        assert body["input"][0]["content"][1]["type"] == "input_image"
        assert body["input"][0]["content"][1]["image_url"].startswith(
            "data:image/jpeg;base64,"
        )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"results": [{"toolName": "rectTool"}]}',
                            }
                        ],
                    }
                ]
            },
        )

    _install_transport(monkeypatch, handler)
    result = _infer(
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="doubao-seed-2-1-pro-260915",
        request_protocol="responses",
        max_new_tokens=128,
    )

    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://ark.cn-beijing.volces.com/api/coding/v3/responses"
    )
    assert result["results"] == [{"toolName": "rectTool"}]


def test_responses_output_ignores_reasoning_items_when_message_is_present():
    response = {
        "output": [
            {
                "type": "reasoning",
                "content": [{"type": "output_text", "text": "internal notes"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": '{"results": []}'}],
            },
        ]
    }

    assert vision_service._responses_output_text(response) == '{"results": []}'


def test_responses_output_text_item_is_supported():
    response = {
        "output": [{"type": "output_text", "text": '{"results": []}'}]
    }

    assert vision_service._responses_output_text(response) == '{"results": []}'


def test_responses_output_text_ignores_reasoning_items():
    response = {
        "output": [
            {
                "type": "reasoning",
                "content": [{"type": "output_text", "text": "internal notes"}],
            },
            {"type": "output_text", "text": '{"results": []}'},
        ]
    }

    assert vision_service._responses_output_text(response) == '{"results": []}'


def test_responses_message_top_level_text_is_supported():
    response = {
        "output": [
            {"type": "message", "role": "assistant", "text": '{"results": []}'}
        ]
    }

    assert vision_service._responses_output_text(response) == '{"results": []}'


def test_responses_malformed_json_retries_with_larger_output_budget(monkeypatch):
    requests = []
    malformed = '{"results":[{"toolName":"rectTool","label":"person" "result":{}}]}'

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": malformed}],
                        }
                    ],
                },
            )
        assert body["max_output_tokens"] == 4096
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"results": []}'}],
                    }
                ],
            },
        )

    _install_transport(monkeypatch, handler)
    result = _infer(
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="doubao-seed-2-1-pro-260915",
        request_protocol="responses",
        max_new_tokens=1024,
    )

    assert len(requests) == 2
    assert result["results"] == []


def test_responses_malformed_json_at_maximum_does_not_retry_forever(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"results":[{"label":"person" "result":{}}]}',
                            }
                        ],
                    }
                ],
            },
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(json.JSONDecodeError):
        _infer(
            base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
            model="doubao-seed-2-1-pro-260915",
            request_protocol="responses",
            max_new_tokens=32768,
        )

    assert len(requests) == 1


def test_openai_compatible_malformed_json_retries_with_larger_output_budget(
    monkeypatch,
):
    requests = []
    malformed = '{"results":[{"toolName":"rectTool","label":"person" "result":{}}]}'

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["messages"][0]["content"][1]["type"] == "text"
        if len(requests) == 1:
            assert body["max_tokens"] == 1024
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": malformed}}]},
            )
        assert body["max_tokens"] == 4096
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"results": []}'}}]},
        )

    _install_transport(monkeypatch, handler)
    result = _infer(
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="doubao-seed-2-1-pro-260915",
        request_protocol="openai_compatible",
        max_new_tokens=1024,
    )

    assert len(requests) == 2
    assert result["results"] == []


def test_openai_compatible_retries_without_unsupported_json_constraint(monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "invalid_parameter",
                        "param": "response_format",
                        "message": "response_format is not supported",
                    }
                },
            )
        assert "response_format" not in body
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"results": []}'}}]},
        )

    _install_transport(monkeypatch, handler)
    result = _infer(
        base_url="https://model.test/v1",
        request_protocol="openai_compatible",
        max_new_tokens=1024,
    )

    assert len(requests) == 2
    assert result["results"] == []


def test_openai_compatible_malformed_json_at_maximum_does_not_retry_forever(
    monkeypatch,
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"results":[{"label":"person" "result":{}}]}'
                        }
                    }
                ]
            },
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(json.JSONDecodeError):
        _infer(
            base_url="https://model.test/v1",
            request_protocol="openai_compatible",
            max_new_tokens=32768,
        )

    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["length", "max_output_tokens"])
def test_responses_length_incomplete_retries_with_larger_output_budget(
    monkeypatch, reason
):
    requests = []

    def handler(request):
        import json

        requests.append(request)
        body = json.loads(request.content)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "status": "incomplete",
                    "incomplete_details": {"reason": reason},
                    "output": [],
                },
            )
        assert body["max_output_tokens"] == 4096
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"results": []}',
                            }
                        ],
                    }
                ],
            },
        )

    _install_transport(monkeypatch, handler)
    result = _infer(
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="doubao-seed-2-1-pro-260915",
        request_protocol="responses",
        max_new_tokens=1024,
    )

    assert len(requests) == 2
    assert result["results"] == []


@pytest.mark.parametrize("reason", ["length", "max_output_tokens"])
def test_responses_length_incomplete_at_maximum_returns_actionable_error(
    monkeypatch, reason
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": reason},
                "output": [],
            },
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="提高最大输出长度"):
        _infer(
            base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
            model="doubao-seed-2-1-pro-260915",
            request_protocol="responses",
            max_new_tokens=32768,
        )

    assert len(requests) == 1


def test_responses_transport_error_is_not_replayed(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("offline timeout", request=request)

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.ReadTimeout):
        _infer(
            base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
            request_protocol="responses",
        )

    assert len(requests) == 1


def _stub_chain_inputs(monkeypatch, profiles):
    monkeypatch.setattr(
        vision_service, "_download_image", lambda _: (b"image", "image/jpeg")
    )
    monkeypatch.setattr(
        vision_service, "_prepare_image_for_inference", lambda _: (b"image", (10, 10))
    )
    monkeypatch.setattr(vision_service, "_configured_model_for", lambda _: None)
    monkeypatch.setattr(vision_service, "resolve_model_integration", lambda _: None)
    monkeypatch.setattr(
        vision_service, "has_configured_model_integration", lambda _: False
    )
    monkeypatch.setattr(
        vision_service,
        "load_ai_labeling_runtime",
        lambda: {
            "llm_profiles": profiles,
            "annotation_chain_profile_ids": [profile["id"] for profile in profiles],
        },
    )


def test_cooled_provider_still_falls_back_to_healthy_model(monkeypatch):
    profiles = [
        {
            "id": model,
            "name": model,
            "model": model,
            "api_key": "test-key",
            "base_url": "https://model.test/v1",
        }
        for model in ("unavailable", "healthy")
    ]
    _stub_chain_inputs(monkeypatch, profiles)
    calls = []

    def handler(request):
        import json

        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "unavailable":
            return httpx.Response(404, json={"error": {"code": "model_not_found"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"results": []}'}}]}
        )

    _install_transport(monkeypatch, handler)
    for _ in range(2):
        result = vision_service.call_vision_llm_chain(
            {"image_url": "https://image.test/test.jpg"}
        )
        assert result["results"] == []
    assert calls == ["unavailable", "healthy", "healthy"]


def test_all_cooled_providers_still_report_failure(monkeypatch):
    _stub_chain_inputs(
        monkeypatch,
        [
            {
                "id": "bad",
                "model": "bad",
                "api_key": "test-key",
                "base_url": "https://model.test/v1",
            }
        ],
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, json={"error": {"code": "model_not_found"}})

    _install_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        vision_service.call_vision_llm_chain(
            {"image_url": "https://image.test/test.jpg"}
        )
    with pytest.raises(RuntimeError, match="HTTP 404"):
        vision_service.call_vision_llm_chain(
            {"image_url": "https://image.test/test.jpg"}
        )
    assert len(calls) == 1


def test_image_download_failure_is_not_cached(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            vision_service.call_vision_llm_chain(
                {"image_url": "https://image.test/missing.jpg"}
            )
    assert len(calls) == 2
