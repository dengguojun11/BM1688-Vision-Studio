import json

from labelu.internal.common.business_log import (
    sanitize_mapping,
    should_log_http_payload,
    summarize_for_log,
    summarize_http_payload,
)


def test_sanitize_mapping_redacts_secrets():
    data = {"username": "alice", "password": "secret123", "task_id": 9}
    sanitized = sanitize_mapping(data)
    assert sanitized["username"] == "alice"
    assert sanitized["password"] == "***"
    assert sanitized["task_id"] == 9


def test_should_log_http_payload_only_for_api(monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "LOG_HTTP_BODY", True)
    assert should_log_http_payload("/api/v1/tasks/1/samples") is True
    assert should_log_http_payload("/static/app.js") is False
    monkeypatch.setattr(config.settings, "LOG_HTTP_BODY", False)
    assert should_log_http_payload("/api/v1/tasks/1/samples") is False


def test_summarize_http_payload_skips_huge_body():
    raw = json.dumps({"data": {"id": 1}}).encode()
    text = summarize_http_payload(raw, content_type="application/json", max_capture_bytes=10)
    assert text == f"<body len={len(raw)} omitted>"


def test_summarize_http_payload_json_response():
    raw = json.dumps({"data": {"id": 1}, "msg": "ok"}).encode()
    text = summarize_http_payload(raw, content_type="application/json")
    assert '"id": 1' in text or "id" in text


def test_summarize_http_payload_binary():
    assert summarize_http_payload(b"\xff\xd8\x00", content_type="image/jpeg") == "<binary len=3>"


def test_summarize_for_log_truncates_long_text():
    text = summarize_for_log({"payload": "x" * 1000}, max_len=50)
    assert text.endswith("…")
    assert len(text) <= 51
