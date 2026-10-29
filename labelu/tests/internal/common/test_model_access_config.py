from __future__ import annotations

from types import SimpleNamespace

import pytest

import labelu.internal.common.model_access_config as store


CODING_ENDPOINT = "https://ark.cn-beijing.volces.com/api/coding/v3"


def _memory_store(monkeypatch):
    store.clear_model_access_cache()
    state = {"version": 1, "connections": []}

    def read(_group):
        return {"connections_store": state}

    def write(_group, values, **_kwargs):
        state.clear()
        state.update(values["connections_store"])
        return True

    monkeypatch.setattr(store, "read_group_values", read)
    monkeypatch.setattr(store, "write_group_values", write)
    monkeypatch.setattr(store, "encrypt_value", lambda value: f"enc:{value}")
    monkeypatch.setattr(store, "decrypt_value", lambda value: str(value).removeprefix("enc:"))
    return state


def test_create_masks_secret_and_does_not_return_plaintext(monkeypatch):
    _memory_store(monkeypatch)
    result = store.create_record(
        name="VLX 服务",
        provider_key="vlx_seek",
        capability="bbox_detection",
        endpoint="http://127.0.0.1:8099/api/infer",
        api_key="top-secret",
        auth_type="api_key",
    )
    assert result["api_key_configured"] is True
    assert result["api_key_masked"] == "******cret"
    assert "api_key" not in result
    assert "api_key_encrypted" not in result


def test_resolution_requires_enabled_and_passed_check(monkeypatch):
    state = _memory_store(monkeypatch)
    state["connections"] = [
        {
            "id": "one",
            "name": "未检查",
            "provider_key": "vlx_seek",
            "capability": "bbox_detection",
            "endpoint": "http://unverified",
            "enabled": True,
            "last_check_status": "NOT_CHECKED",
        },
        {
            "id": "two",
            "name": "已通过",
            "provider_key": "vlx_seek",
            "capability": "bbox_detection",
            "endpoint": "http://verified",
            "enabled": True,
            "is_default": True,
            "last_check_status": "PASSED",
        },
    ]
    assert store.resolve_model_integration("vlx_seek")["id"] == "two"
    assert store.has_configured_model_integration("vlx_seek") is True


def test_validation_accepts_frontend_capability_aliases(monkeypatch):
    _memory_store(monkeypatch)
    for capability in ("global_tag", "bbox_detection", "vision_reasoning"):
        row = store.create_record(
            name=capability,
            provider_key="vision_llm",
            capability=capability,
            endpoint="https://model.example/infer",
        )
        assert row["capability"] == capability


def test_request_protocol_is_persisted_and_validated(monkeypatch):
    _memory_store(monkeypatch)
    row = store.create_record(
        name="18081 图文服务",
        provider_key="tagtool",
        capability="global_tag",
        endpoint="http://192.168.54.11:18081/v1",
        request_protocol="openai_compatible",
    )
    assert row["request_protocol"] == "openai_compatible"
    assert store.get_record(row["id"])["request_protocol"] == "openai_compatible"

    try:
        store.create_record(
            name="错误协议",
            provider_key="tagtool",
            capability="global_tag",
            endpoint="http://model.example",
            request_protocol="unknown",
        )
    except ValueError as exc:
        assert "请求协议" in str(exc)
    else:
        raise AssertionError("invalid request protocol should be rejected")


def test_volcengine_provider_supports_vision_and_bbox_capabilities(monkeypatch):
    _memory_store(monkeypatch)
    row = store.create_record(
        name="火山方舟",
        provider_key="volcengine",
        capability="vision_reasoning",
        endpoint="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="doubao-seed-1-6-vision-250815",
        auth_type="bearer",
    )
    assert row["provider_key"] == "volcengine"
    resolved = store.resolve_model_integration("volcengine", require_tested=False)
    assert resolved is not None
    assert resolved["endpoint"] == "https://ark.cn-beijing.volces.com/api/coding/v3"

    bbox = store.create_record(
        name="火山目标检测",
        provider_key="volcengine",
        capability="bbox_detection",
        endpoint="https://ark.cn-beijing.volces.com/api/coding/v3",
        model="doubao-seed-2-1-pro-260915",
        auth_type="bearer",
    )
    assert bbox["capability"] == "bbox_detection"
    assert bbox["timeout_seconds"] == 120
    assert store.resolve_model_integration("volcengine", capability="bbox_detection", require_tested=False)["id"] == bbox["id"]


def test_api_key_auth_uses_configured_header_and_reader_cache_isolated(monkeypatch):
    _memory_store(monkeypatch)
    row = store.create_record(
        name="Locate",
        provider_key="locateanything",
        capability="bbox_detection",
        endpoint="http://model.example/infer",
        api_key="secret-value",
        auth_type="api_key",
    )
    stored = store.get_record(row["id"])
    assert stored is not None
    assert store.request_headers(stored) == {"X-API-Key": "secret-value"}

    # A patched reader must not observe the previous reader's short-lived
    # cache.  This reproduces the test-order issue seen when configuration
    # tests are run together.
    replacement = {"version": 1, "connections": []}
    monkeypatch.setattr(store, "read_group_values", lambda _group: {"connections_store": replacement})
    assert store.resolve_model_integration("locateanything") is None


@pytest.mark.parametrize("suffix", ["", "/", "/chat/completions", "/chat/completions/"])
def test_volcengine_coding_endpoint_is_canonicalized(monkeypatch, suffix):
    _memory_store(monkeypatch)
    row = store.create_record(
        provider_key="volcengine",
        capability="vision_reasoning",
        endpoint=CODING_ENDPOINT + suffix,
    )
    assert row["endpoint"] == CODING_ENDPOINT


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ark.cn-beijing.volces.com/api/v3",
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "https://ark.cn-beijing.volces.com",
        "http://ark.cn-beijing.volces.com/api/coding/v3",
        "https://other.example/api/coding/v3",
        "https://ark.cn-beijing.volces.com.evil.example/api/coding/v3",
        "https://name@ark.cn-beijing.volces.com/api/coding/v3",
        "https://ark.cn-beijing.volces.com:443/api/coding/v3",
        CODING_ENDPOINT + "?route=/api/v3",
        CODING_ENDPOINT + "?",
        CODING_ENDPOINT + "#fragment",
        CODING_ENDPOINT + "#",
        CODING_ENDPOINT + "/models",
        CODING_ENDPOINT + "/../v3",
        "https://ark.cn-beijing.volces.com/api/%63oding/v3",
        "https://ark.cn-beijing.volces.com/api/coding/\nv3",
    ],
)
def test_volcengine_rejects_endpoints_outside_coding_plan(monkeypatch, endpoint):
    state = _memory_store(monkeypatch)
    with pytest.raises(ValueError, match="Coding Plan"):
        store.create_record(
            provider_key="volcengine",
            capability="vision_reasoning",
            endpoint=endpoint,
        )
    assert state["connections"] == []


def test_volcengine_update_rejects_pay_as_you_go_endpoint(monkeypatch):
    _memory_store(monkeypatch)
    row = store.create_record(
        provider_key="volcengine",
        capability="vision_reasoning",
        endpoint=CODING_ENDPOINT,
    )
    with pytest.raises(ValueError, match="Coding Plan"):
        store.update_record(row["id"], endpoint="https://ark.cn-beijing.volces.com/api/v3")
    assert store.get_record(row["id"])["endpoint"] == CODING_ENDPOINT


def _legacy_volcengine_record(state, *, endpoint):
    row = {
        "id": "legacy-volcengine",
        "name": "Legacy",
        "provider_key": "volcengine",
        "capability": "vision_reasoning",
        "endpoint": endpoint,
        "model": "model-id-not-verified",
        "enabled": True,
        "last_check_status": "PASSED",
    }
    state["connections"] = [row]
    return row


@pytest.mark.parametrize("require_tested", [True, False])
def test_legacy_pay_as_you_go_record_is_never_resolved(monkeypatch, require_tested):
    state = _memory_store(monkeypatch)
    _legacy_volcengine_record(state, endpoint="https://ark.cn-beijing.volces.com/api/v3")
    assert store.resolve_model_integration("volcengine", require_tested=require_tested) is None
    assert store.has_configured_model_integration("volcengine") is True
    assert store.list_records()[0]["available"] is False


def test_non_volcengine_endpoint_policy_is_unchanged(monkeypatch):
    _memory_store(monkeypatch)
    endpoint = "http://127.0.0.1:9000/v1"
    row = store.create_record(
        provider_key="vision_llm", capability="vision_reasoning", endpoint=endpoint
    )
    assert row["endpoint"] == endpoint


def test_management_list_search_and_pagination_keep_store_records_intact(monkeypatch):
    _memory_store(monkeypatch)
    store.create_record(
        name="生产 VLX",
        provider_key="vlx_seek",
        capability="bbox_detection",
        endpoint="http://model.example/vlx",
        model="vlx-prod",
    )
    store.create_record(
        name="测试标签",
        provider_key="tagtool",
        capability="global_tag",
        endpoint="http://model.example/tag",
        model="tag-test",
    )
    store.create_record(
        name="备用视觉",
        provider_key="vision_llm",
        capability="vision_reasoning",
        endpoint="http://model.example/vision",
        model="vision-backup",
    )

    from labelu.internal.application.service import model_access_config as service

    page, total = service.list_configs_page(q="目标检测", page=0, size=1)
    assert total == 1
    assert [row.name for row in page.configs] == ["生产 VLX"]

    second_page, second_total = service.list_configs_page(page=1, size=1)
    assert second_total == 3
    assert len(second_page.configs) == 1
    # The compatibility store API remains the source of truth and is not
    # replaced by a paginated view.
    assert len(store.list_records()) == 3


def test_unsafe_volcengine_connection_test_makes_no_http_request(monkeypatch):
    from labelu.internal.application.service import model_access_config as service

    state = _memory_store(monkeypatch)
    row = _legacy_volcengine_record(
        state, endpoint="https://ark.cn-beijing.volces.com/api/v3"
    )
    constructed_clients = []

    def client(*args, **kwargs):
        constructed_clients.append(kwargs)
        raise AssertionError("unsafe endpoint must be rejected before HTTP")

    monkeypatch.setattr(service.httpx, "Client", client)
    result = service.test_connection(row["id"])

    assert result.ok is False
    assert result.endpoint_reachable is False
    assert result.config.last_check_status == "FAILED"
    assert "Coding Plan" in result.message
    assert constructed_clients == []


@pytest.mark.parametrize("response_status", [200, 302])
def test_volcengine_connection_probe_stays_on_coding_plan(monkeypatch, response_status):
    from labelu.internal.application.service import model_access_config as service

    state = _memory_store(monkeypatch)
    row = _legacy_volcengine_record(state, endpoint=CODING_ENDPOINT + "/chat/completions")
    client_options = []
    requested_urls = []

    class FakeClient:
        def __init__(self, **kwargs):
            client_options.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            requested_urls.append(url)
            return SimpleNamespace(status_code=response_status)

    monkeypatch.setattr(service.httpx, "Client", FakeClient)
    result = service.test_connection(row["id"])

    assert client_options[0]["follow_redirects"] is False
    assert requested_urls == [CODING_ENDPOINT + "/models"]
    assert result.ok is (response_status == 200)
    if response_status == 200:
        assert "未验证" in result.message
        assert "模型" in result.message and "图像" in result.message


def _tested_connection(monkeypatch):
    _memory_store(monkeypatch)
    row = store.create_record(
        name="Tested connection",
        provider_key="vision_llm",
        capability="vision_reasoning",
        endpoint="https://model.example/v1",
        model="verified-model",
        api_key="verified-key",
        auth_type="bearer",
        auth_header="Authorization",
        request_protocol="openai_compatible",
        verify_tls=True,
        max_new_tokens=1024,
    )
    return store.record_check(row["id"], status="PASSED", message="verified")


@pytest.mark.parametrize(
    "change",
    [
        {"provider_key": "tagtool"},
        {"capability": "global_tag"},
        {"endpoint": "https://new-model.example/v1"},
        {"model": "unverified-model"},
        {"api_key": "new-key"},
        {"api_key": ""},
        {"auth_type": "header"},
        {"auth_header": "X-Model-Key"},
        {"request_protocol": "auto"},
        {"verify_tls": False},
        {"max_new_tokens": 2048},
    ],
)
def test_connection_changes_require_new_check(monkeypatch, change):
    row = _tested_connection(monkeypatch)

    updated = store.update_record(row["id"], **change)

    assert updated["last_check_status"] == "NOT_CHECKED"
    assert updated["last_check_message"] == ""
    assert updated["last_check_at"] is None
    assert updated["available"] is False
    assert store.resolve_model_integration(updated["provider_key"]) is None


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Renamed connection"},
        {"is_default": True},
        {"enabled": False},
        {"timeout_seconds": 600},
        {"model": " verified-model "},
        {"api_key": " verified-key "},
        {"api_key": None},
        {"endpoint": "https://model.example/v1"},
        {"auth_type": "BEARER"},
        {"auth_header": "Authorization"},
        {"request_protocol": "openai_compatible"},
        {"verify_tls": True},
        {"max_new_tokens": 1024},
    ],
)
def test_management_and_identical_updates_keep_previous_check(monkeypatch, change):
    row = _tested_connection(monkeypatch)

    updated = store.update_record(row["id"], **change)

    assert updated["last_check_status"] == "PASSED"
    assert updated["last_check_message"] == row["last_check_message"]
    assert updated["last_check_at"] == row["last_check_at"]


def test_secret_decryption_failure_invalidates_rewritten_key(monkeypatch):
    row = _tested_connection(monkeypatch)

    def cannot_decrypt(_value):
        raise ValueError("cannot decrypt stored key")

    monkeypatch.setattr(store, "decrypt_value", cannot_decrypt)

    updated = store.update_record(row["id"], api_key="verified-key")

    assert updated["last_check_status"] == "NOT_CHECKED"
    assert updated["available"] is False


def test_equivalent_coding_endpoint_keeps_previous_check(monkeypatch):
    _memory_store(monkeypatch)
    row = store.create_record(
        provider_key="volcengine", capability="vision_reasoning", endpoint=CODING_ENDPOINT,
    )
    store.record_check(row["id"], status="PASSED", message="verified")

    updated = store.update_record(row["id"], endpoint=CODING_ENDPOINT + "/chat/completions")

    assert updated["last_check_status"] == "PASSED"
    assert updated["endpoint"] == CODING_ENDPOINT


def test_legacy_unsafe_endpoint_can_be_corrected_and_requires_check(monkeypatch):
    state = _memory_store(monkeypatch)
    row = _legacy_volcengine_record(
        state, endpoint="https://ark.cn-beijing.volces.com/api/v3"
    )

    updated = store.update_record(row["id"], endpoint=CODING_ENDPOINT)

    assert updated["endpoint"] == CODING_ENDPOINT
    assert updated["last_check_status"] == "NOT_CHECKED"
    assert updated["available"] is False
