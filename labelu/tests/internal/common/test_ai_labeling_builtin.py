from labelu.internal.common.ai_labeling_builtin import (
    list_builtin_env_providers,
    resolve_effective_vision_call_plan,
)


def test_resolve_builtin_plan_respects_enabled(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.ai_labeling_builtin._volcengine_key_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.common.ai_labeling_builtin._volcengine_model",
        lambda: "doubao-test",
    )
    monkeypatch.setattr(
        "labelu.internal.common.ai_labeling_builtin.model_access_config.load_store",
        lambda: {"connections": []},
    )
    runtime = {
        "enabled_providers": ["volcengine"],
        "llm_profiles": [],
        "annotation_chain_profile_ids": [],
    }
    providers = list_builtin_env_providers(runtime)
    assert providers[0]["usable"] is True
    assert len(providers) == 1
    plan = resolve_effective_vision_call_plan(runtime)
    assert plan["mode"] == "builtin_env"
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["provider_key"] == "volcengine"
