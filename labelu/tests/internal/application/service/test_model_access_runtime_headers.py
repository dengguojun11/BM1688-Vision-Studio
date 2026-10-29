from __future__ import annotations


def test_vlx_and_rex_use_configured_auth_headers(monkeypatch):
    from labelu.internal.application.service import vlx_seek_preannotate as runtime

    rows = {
        "vlx_seek": {"id": "vlx", "api_key": "vlx-secret", "auth_type": "api_key", "auth_header": "X-VLX-Key"},
        "rex_omni": {"id": "rex", "api_key": "rex-secret", "auth_type": "bearer", "auth_header": "Authorization"},
    }
    monkeypatch.setattr(runtime, "resolve_model_integration", lambda key: rows.get(key))
    monkeypatch.setattr(runtime, "request_headers", lambda row: {row["auth_header"]: row["api_key"]} if row else {})

    assert runtime._provider_auth_headers("vlx") == {"X-VLX-Key": "vlx-secret"}
    assert runtime._provider_auth_headers("rex") == {"Authorization": "rex-secret"}


def test_tagtool_api_key_uses_x_api_key_default(monkeypatch):
    from labelu.internal.application.service import tagtool_preannotate as runtime

    monkeypatch.setattr(
        runtime,
        "request_headers",
        lambda row: {"X-API-Key": row["api_key"]},
    )
    assert runtime._auth_headers(
        {"api_key": "tag-secret", "auth_type": "api_key", "auth_header": "Authorization"}
    ) == {"X-API-Key": "tag-secret"}


def test_locate_payload_carries_configured_model_and_output_limit(monkeypatch):
    from labelu.internal.application.service import locateanything_preannotate as runtime

    monkeypatch.setattr(
        runtime,
        "_model_runtime",
        lambda: (True, "http://locate/infer", 30, "locate-v2", False, 2048),
    )
    payload = runtime._build_la_model_payload(
        image_url="http://image/1.jpg",
        task_id=7,
        task_name="示例任务",
        task_config={"tools": []},
        filter_by_labels=False,
    )
    assert payload["model"] == "locate-v2"
    assert payload["max_new_tokens"] == 2048


def test_health_candidates_include_service_level_api_health():
    from labelu.internal.application.service import model_access_config as service

    assert service._health_candidates("https://model.example/api/rex/infer")[:4] == [
        "https://model.example/api/rex/infer/health",
        "https://model.example/api/rex/health",
        "https://model.example/api/health",
        "https://model.example/health",
    ]
