from types import SimpleNamespace

import pytest

from labelu.internal.application.service import yolo_train_evaluation as svc
from labelu.internal.application.service import vision_llm_chain as vision_chain


@pytest.fixture(autouse=True)
def isolated_provider_configuration(monkeypatch):
    monkeypatch.setattr(vision_chain, "resolve_model_integration", lambda _key: None)
    monkeypatch.setattr(vision_chain, "has_managed_volcengine_config", lambda: False)
    monkeypatch.setattr(vision_chain, "has_configured_model_integration", lambda _key: False)
    monkeypatch.setattr(svc.settings, "VISION_VOLCENGINE_API_KEY", "")
    monkeypatch.setattr(svc.settings, "YOLO_EVAL_DISABLED_PROVIDERS", "")


def _row(*, report_id: int, body=None, error_message=None):
    return (
        SimpleNamespace(
            id=report_id,
            yolo_train_job_id=401,
            provider_key="volcengine",
            report_kind="full_analysis",
            body=body,
            error_message=error_message,
            created_at=None,
        ),
        SimpleNamespace(id=401),
        SimpleNamespace(id=88, name="garbage-task"),
    )


def test_build_provider_specs_rejects_retired_qwen_profile_chain(monkeypatch):
    monkeypatch.setattr(
        svc,
        "load_ai_labeling_runtime",
        lambda: {
            "enabled_providers": [],
            "annotation_chain_profile_ids": ["local-qwen"],
            "llm_profiles": [
                {
                    "id": "local-qwen",
                    "name": "内置千问",
                    "base_url": "http://llm.example/v1",
                    "model": "qwen-vl",
                    "api_key": "profile-key",
                }
            ],
        },
    )
    monkeypatch.setattr(svc.settings, "VISION_QWEN_API_KEY", "")
    monkeypatch.setattr(svc.settings, "VISION_VOLCENGINE_API_KEY", "")

    assert svc._build_provider_specs() == []


def test_build_provider_specs_prefers_system_model_access_connection(monkeypatch):
    monkeypatch.setattr(
        vision_chain,
        "resolve_model_integration",
        lambda key: {
            "provider_key": "volcengine",
            "name": "火山 Coding Plan",
            "endpoint": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
            "api_key": "configured-key",
            "model": "doubao-seed-2.1-pro",
        }
        if key == "volcengine"
        else None,
    )
    monkeypatch.setattr(
        svc,
        "load_ai_labeling_runtime",
        lambda: {
            "enabled_providers": [],
            "annotation_chain_profile_ids": ["legacy"],
            "llm_profiles": [],
        },
    )

    assert svc._build_provider_specs() == [
        (
            "volcengine",
            "https://ark.cn-beijing.volces.com/api/coding/v3",
            "configured-key",
            "doubao-seed-2.1-pro",
        )
    ]


def test_build_provider_specs_does_not_fallback_when_system_connection_is_unhealthy(monkeypatch):
    monkeypatch.setattr(vision_chain, "has_configured_model_integration", lambda key: key == "vision_llm")
    monkeypatch.setattr(
        svc,
        "load_ai_labeling_runtime",
        lambda: {
            "enabled_providers": [],
            "annotation_chain_profile_ids": ["legacy"],
            "llm_profiles": [
                {
                    "id": "legacy",
                    "name": "旧配置",
                    "base_url": "http://legacy.example/v1",
                    "model": "old-model",
                    "api_key": "old-key",
                }
            ],
        },
    )

    assert svc._build_provider_specs() == []


def test_build_provider_specs_disabled_managed_ark_blocks_environment(monkeypatch):
    monkeypatch.setattr(vision_chain, "has_managed_volcengine_config", lambda: True)
    monkeypatch.setattr(svc.settings, "VISION_VOLCENGINE_API_KEY", "old-env-key")
    monkeypatch.setattr(svc.settings, "VISION_VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
    monkeypatch.setattr(svc.settings, "VISION_VOLCENGINE_MODEL", "doubao-seed-2.1-pro")

    assert svc._build_provider_specs() == []


@pytest.mark.parametrize(
    "base_url,model,name",
    [
        ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-2.1-pro", "volcengine"),
        ("http://proxy.example/v1", "qwen-vl", "custom"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "aliased-model", "custom"),
        ("http://proxy.example/v1", "aliased-model", "内置千问"),
    ],
)
def test_build_provider_specs_rejects_unsafe_managed_connections(monkeypatch, base_url, model, name):
    monkeypatch.setattr(svc, "_configured_model_for", lambda _key: {
        "provider_key": "vision_llm", "name": name,
        "endpoint": base_url, "model": model, "api_key": "test-key",
    })

    assert svc._build_provider_specs() == []


def test_build_provider_specs_keeps_non_qwen_profile_compatibility(monkeypatch):
    monkeypatch.setattr(svc, "load_ai_labeling_runtime", lambda: {
        "annotation_chain_profile_ids": ["local-vision"],
        "llm_profiles": [{"id": "local-vision", "name": "视觉服务", "base_url": "http://vision.example/v1", "api_key": "test-key", "model": "vision-model"}],
    })

    assert svc._build_provider_specs() == [("视觉服务", "http://vision.example/v1", "test-key", "vision-model")]


def test_build_provider_specs_keeps_managed_unauthenticated_service(monkeypatch):
    monkeypatch.setattr(svc, "_configured_model_for", lambda _key: {
        "provider_key": "vision_llm", "name": "本地视觉服务", "auth_type": "none",
        "endpoint": "http://localhost:18081/v1", "model": "vision-model", "api_key": "",
    })

    assert svc._build_provider_specs() == [("本地视觉服务", "http://localhost:18081/v1", "", "vision-model")]


def test_list_eval_reports_global_returns_structured_summary_for_body(monkeypatch):
    monkeypatch.setattr(svc.yolo_train_service, "_task_ids_for_rect_preset_filter", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        svc.crud_yolo_eval_report,
        "list_for_user",
        lambda *args, **kwargs: (
            [_row(report_id=1, body="## 结论\n整体达标，建议继续优化召回率。")],
            1,
        ),
    )

    rows, total = svc.list_eval_reports_global(
        object(),
        SimpleNamespace(id=7),
        report_kind="full_analysis",
    )

    assert total == 1
    assert rows[0].summary_level == "optimize"
    assert rows[0].summary_brief


def test_list_eval_reports_global_returns_structured_summary_for_error(monkeypatch):
    monkeypatch.setattr(svc.yolo_train_service, "_task_ids_for_rect_preset_filter", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        svc.crud_yolo_eval_report,
        "list_for_user",
        lambda *args, **kwargs: (
            [_row(report_id=2, error_message="provider timeout")],
            1,
        ),
    )

    rows, total = svc.list_eval_reports_global(
        object(),
        SimpleNamespace(id=7),
        report_kind="full_analysis",
    )

    assert total == 1
    assert rows[0].summary_level == "abnormal"
    assert "timeout" in (rows[0].summary_brief or "")


def test_list_eval_reports_global_returns_structured_summary_for_empty_body(monkeypatch):
    monkeypatch.setattr(svc.yolo_train_service, "_task_ids_for_rect_preset_filter", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        svc.crud_yolo_eval_report,
        "list_for_user",
        lambda *args, **kwargs: (
            [_row(report_id=3, body="   ")],
            1,
        ),
    )

    rows, total = svc.list_eval_reports_global(
        object(),
        SimpleNamespace(id=7),
        report_kind="full_analysis",
    )

    assert total == 1
    assert rows[0].summary_level == "unknown"
    assert rows[0].summary_brief
