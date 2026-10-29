from __future__ import annotations

from labelu.internal.application.service import ai_labeling_runtime as service
from labelu.internal.common.config import settings


def test_runtime_view_reports_admin_volcengine_and_retired_qwen(monkeypatch):
    monkeypatch.setattr(
        service, "load_ai_labeling_runtime",
        lambda: {"instruction_suffix": "", "enabled_providers": ["volcengine"]},
    )
    monkeypatch.setattr(service, "list_builtin_env_providers", lambda _: [])
    monkeypatch.setattr(
        service, "resolve_effective_vision_call_plan",
        lambda _: {
            "mode": "managed",
            "steps": [{
                "kind": "managed", "provider_key": "volcengine",
                "model": "doubao-seed-2-1-pro-260915", "configured": True,
            }],
            "hint": "",
        },
    )
    monkeypatch.setattr(settings, "VISION_QWEN_API_KEY", "retired-provider-key")
    monkeypatch.setattr(settings, "VISION_QWEN_MODEL_PLUS", "retired-plus")
    monkeypatch.setattr(settings, "VISION_QWEN_MODEL_235B", "retired-235b")
    monkeypatch.setattr(settings, "VISION_VOLCENGINE_API_KEY", "")

    view = service.get_ai_labeling_runtime_view()

    assert view.secrets.volcengine_configured is True
    assert view.models.volcengine_model == "doubao-seed-2-1-pro-260915"
    assert view.secrets.qwen_configured is False
    assert view.models.qwen_plus == ""
    assert view.models.qwen_235b == ""
