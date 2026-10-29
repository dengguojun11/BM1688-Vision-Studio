"""Page-managed Coding Plan selection; every provider request is mocked."""

import pytest

from labelu.internal.application.service import auto_label
from labelu.internal.application.service import vision_llm_chain as vision
from labelu.internal.common import ai_labeling_builtin as builtin
from labelu.internal.common import ai_labeling_runtime as runtime
from labelu.internal.common import model_access_config as store

CODING_BASE = "https://ark.cn-beijing.volces.com/api/coding/v3"


@pytest.fixture
def managed_runtime(monkeypatch):
    configs = []
    state = {"profiles": [], "enabled": ["qwen_plus"]}
    calls = []
    monkeypatch.setattr(store, "load_store", lambda: {"connections": configs})

    def resolve(key):
        return next(
            (
                dict(row)
                for row in configs
                if row["provider_key"] == key
                and row.get("enabled", True)
                and row.get("last_check_status") == "PASSED"
            ),
            None,
        )

    monkeypatch.setattr(vision, "resolve_model_integration", resolve)
    monkeypatch.setattr(auto_label, "resolve_model_integration", resolve)
    monkeypatch.setattr(store, "resolve_model_integration", resolve)
    monkeypatch.setattr(
        vision,
        "has_configured_model_integration",
        lambda key: any(
            row["provider_key"] == key and row.get("enabled", True) for row in configs
        ),
    )
    monkeypatch.setattr(
        vision,
        "load_ai_labeling_runtime",
        lambda: {
            "enabled_providers": state["enabled"],
            "llm_profiles": state["profiles"],
            "annotation_chain_profile_ids": [p["id"] for p in state["profiles"]],
        },
    )
    monkeypatch.setattr(vision, "_download_image", lambda _: (b"image", "image/jpeg"))
    prepared_limits = []

    def prepare_image(_, *, max_side=None):
        prepared_limits.append(max_side)
        return b"image", (10, 10)

    monkeypatch.setattr(vision, "_prepare_image_for_inference", prepare_image)
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_API_KEY", "")
    monkeypatch.setattr(vision.settings, "VISION_QWEN_API_KEY", "")
    monkeypatch.setattr(
        vision,
        "_openai_compatible_infer",
        lambda **kwargs: calls.append(kwargs)
        or {"results": [], "model": kwargs["model"]},
    )
    return configs, state, calls, prepared_limits


def _page_record(**overrides):
    return {
        "id": "page-ark",
        "provider_key": "volcengine",
        "enabled": True,
        "last_check_status": "PASSED",
        "endpoint": CODING_BASE,
        "model": "volcengine/doubao-seed-code",
        "api_key": "offline-key",
        "auth_type": "bearer",
        "timeout_seconds": 75,
        "max_new_tokens": 2048,
        **overrides,
    }


def test_page_volcengine_overrides_old_tagtool_profiles_and_enabled_flags(
    managed_runtime,
):
    configs, state, calls, _ = managed_runtime
    configs.extend(
        [_page_record(), _page_record(provider_key="tagtool", model="qwen-vl")]
    )
    state["profiles"] = [
        {
            "id": "old",
            "model": "old-local",
            "base_url": "http://old/v1",
            "api_key": "old",
        }
    ]

    result = vision.call_vision_llm_chain({"image_url": "http://image/test.jpg"})

    assert result["model"] == "doubao-seed-code"
    assert len(calls) == 1
    assert calls[0]["base_url"] == CODING_BASE
    assert calls[0]["timeout_seconds"] == 75
    assert calls[0]["max_new_tokens"] == 2048
    assert vision.vision_llm_chain_configured() is True


def test_managed_bbox_volcengine_uses_responses_protocol(managed_runtime, monkeypatch):
    configs, _, calls, prepared_limits = managed_runtime
    configs.append(_page_record(capability="bbox_detection"))
    requested_capabilities = []

    def resolve(key, *, capability=None):
        requested_capabilities.append(capability)
        return next(
            (
                dict(row)
                for row in configs
                if row["provider_key"] == key
                and row.get("capability", "vision_reasoning") == capability
                and row.get("enabled", True)
                and row.get("last_check_status") == "PASSED"
            ),
            None,
        )

    monkeypatch.setattr(store, "resolve_model_integration", resolve)
    monkeypatch.setattr(vision, "resolve_model_integration", resolve)

    result = vision.call_vision_llm_chain(
        {"image_url": "http://image/test.jpg"}, provider_key="bbox_detection"
    )

    assert result["model"] == "doubao-seed-code"
    assert requested_capabilities == ["bbox_detection"]
    assert calls[0]["request_protocol"] == "responses"
    assert prepared_limits == [2560]


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": False},
        {"last_check_status": "FAILED"},
        {"last_check_status": "NOT_CHECKED"},
    ],
)
def test_page_unavailable_blocks_all_legacy_fallback(
    managed_runtime, monkeypatch, overrides
):
    configs, state, calls, _ = managed_runtime
    configs.append(_page_record(**overrides))
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_API_KEY", "old-key")
    monkeypatch.setattr(vision.settings, "VISION_QWEN_API_KEY", "old-qwen")
    state["profiles"] = [
        {"id": "old", "model": "local", "base_url": "http://old/v1", "api_key": "old"}
    ]

    assert vision.vision_llm_chain_configured() is False
    with pytest.raises(RuntimeError):
        vision.call_vision_llm_chain({"image_url": "http://image/test.jpg"})
    assert calls == []


def test_auto_label_detects_page_volcengine_in_legacy_local_http_mode(
    managed_runtime, monkeypatch
):
    configs, _, _, _ = managed_runtime
    configs.append(_page_record())
    monkeypatch.setattr(vision.settings, "AI_PROVIDER", "local_http")
    assert auto_label._configured_global_tag_vision_available() is True
    assert auto_label._model_runtime()["provider_key"] == "volcengine"
    auto_label._ensure_ai_backend_configured()


def test_auto_label_disabled_page_cannot_silently_choose_old_runtime(
    managed_runtime, monkeypatch
):
    configs, _, _, _ = managed_runtime
    configs.extend(
        [
            _page_record(enabled=False),
            _page_record(provider_key="tagtool", model="old-model"),
        ]
    )
    monkeypatch.setattr(vision.settings, "AI_PROVIDER", "local_http")
    assert auto_label._configured_global_tag_vision_available() is True
    with pytest.raises(Exception):
        auto_label._ensure_ai_backend_configured()


def test_qwen_env_alone_never_counts_or_calls_as_vision_backend(
    managed_runtime, monkeypatch
):
    _, state, calls, _ = managed_runtime
    state["enabled"] = ["qwen_plus", "qwen_235b"]
    monkeypatch.setattr(vision.settings, "VISION_QWEN_API_KEY", "old-qwen")
    assert vision.vision_llm_chain_configured() is False
    with pytest.raises(RuntimeError):
        vision.call_vision_llm_chain({"image_url": "http://image/test.jpg"})
    assert calls == []


@pytest.mark.parametrize(
    "endpoint",
    ["https://ark.cn-beijing.volces.com/api/v3", "https://other.test/api/coding/v3"],
)
def test_legacy_volcengine_refuses_non_coding_endpoint(
    managed_runtime, monkeypatch, endpoint
):
    _, state, calls, _ = managed_runtime
    state["enabled"] = ["volcengine"]
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_API_KEY", "old-key")
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_BASE_URL", endpoint)
    assert vision.vision_llm_chain_configured() is False
    with pytest.raises(ValueError):
        vision.call_vision_llm_chain({"image_url": "http://image/test.jpg"})
    assert calls == []


def test_qwen_profiles_removed_while_other_custom_model_kept(managed_runtime):
    _, state, calls, _ = managed_runtime
    state["profiles"] = [
        {
            "id": "qwen",
            "model": "qwen3-vl-plus",
            "base_url": "https://proxy.test/v1",
            "api_key": "old",
        },
        {
            "id": "renamed",
            "model": "renamed-model",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "old",
        },
        {
            "id": "local",
            "model": "local-vision",
            "base_url": "http://local/v1",
            "api_key": "test",
        },
    ]
    vision.call_vision_llm_chain({"image_url": "http://image/test.jpg"})
    assert [call["model"] for call in calls] == ["local-vision"]


def test_builtin_defaults_and_plan_never_include_qwen(managed_runtime, monkeypatch):
    monkeypatch.setattr(vision.settings, "VISION_QWEN_API_KEY", "old-qwen")
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_API_KEY", "test-key")
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_BASE_URL", CODING_BASE)
    assert runtime.DEFAULT_ENABLED == ["volcengine"]
    assert [row["key"] for row in builtin.list_builtin_env_providers({})] == [
        "volcengine"
    ]
    plan = builtin.resolve_effective_vision_call_plan({})
    assert [row["provider_key"] for row in plan["steps"]] == ["volcengine"]


def test_call_plan_uses_page_volcengine_before_profiles(managed_runtime):
    configs, state, _, _ = managed_runtime
    configs.append(_page_record())
    state["profiles"] = [
        {"id": "old", "model": "local", "base_url": "http://old/v1", "api_key": "old"}
    ]
    plan = builtin.resolve_effective_vision_call_plan(
        {
            "llm_profiles": state["profiles"],
            "annotation_chain_profile_ids": ["old"],
        }
    )
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["provider_key"] == "volcengine"
    assert plan["steps"][0]["base_url"] == CODING_BASE


def test_call_plan_blocks_disabled_page_instead_of_previewing_legacy(managed_runtime):
    configs, _, _, _ = managed_runtime
    configs.append(_page_record(enabled=False))
    assert builtin.resolve_effective_vision_call_plan({})["steps"] == []


@pytest.mark.parametrize(
    "base_url,model,label",
    [
        ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-code", "custom"),
        ("https://proxy.test/v1", "qwen3-vl-plus", "custom"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "renamed", "custom"),
    ],
)
def test_actual_request_boundary_rejects_retired_or_metered_routes(
    monkeypatch, base_url, model, label
):
    monkeypatch.setattr(
        vision.httpx,
        "Client",
        lambda **kwargs: pytest.fail("must reject before opening client"),
    )
    with pytest.raises(ValueError):
        vision._openai_compatible_infer(
            provider_label=label,
            base_url=base_url,
            api_key="offline-only",
            model=model,
            instruction="JSON",
            image_bytes=b"test",
            mime="image/jpeg",
        )


def test_legacy_profile_normalizer_removes_qwen_without_losing_other_models():
    assert runtime._normalize_profiles(
        [
            {
                "id": "qwen",
                "name": "old",
                "base_url": "https://proxy.test/v1",
                "model": "qwen-vl",
            },
            {
                "id": "local",
                "name": "local",
                "base_url": "http://local/v1",
                "model": "local-vision",
            },
        ]
    ) == [
        {
            "id": "local",
            "name": "local",
            "base_url": "http://local/v1",
            "model": "local-vision",
            "api_key": "",
        }
    ]


def test_metered_page_record_rejected_even_if_stale_check_passed(managed_runtime):
    configs, _, calls, _ = managed_runtime
    configs.append(_page_record(endpoint="https://ark.cn-beijing.volces.com/api/v3"))
    assert vision.vision_llm_chain_configured() is False
    with pytest.raises(ValueError):
        vision.call_vision_llm_chain({"image_url": "http://image/test.jpg"})
    assert calls == []


def test_qwen_named_model_is_never_shown_as_available_ark_plan(
    managed_runtime, monkeypatch
):
    configs, _, _, _ = managed_runtime
    configs.append(_page_record(model="qwen3-coder"))
    assert vision.vision_llm_chain_configured() is False
    assert builtin.resolve_effective_vision_call_plan({})["steps"] == []
    configs.clear()
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_API_KEY", "test-key")
    monkeypatch.setattr(vision.settings, "VISION_VOLCENGINE_MODEL", "qwen3-coder")
    assert vision.vision_llm_chain_configured() is False
    assert builtin.list_builtin_env_providers({})[0]["usable"] is False
