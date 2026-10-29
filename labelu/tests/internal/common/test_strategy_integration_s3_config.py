from labelu.internal.common.strategy_integration_s3_config import (
    _restore_store,
    normalize_endpoint,
)


def test_normalize_endpoint_accepts_ip_and_adds_http():
    assert normalize_endpoint("192.168.54.190:29100") == "http://192.168.54.190:29100"
    assert normalize_endpoint("http://192.168.54.190:29100/") == "http://192.168.54.190:29100"


def test_normalize_endpoint_rejects_unsafe_or_invalid_values():
    assert normalize_endpoint("") == ""
    assert normalize_endpoint("ftp://192.168.54.190:29100") == ""
    assert normalize_endpoint("http://user:pass@192.168.54.190:29100") == ""
    assert normalize_endpoint("http://192.168.54.190:not-a-port") == ""


def test_restore_store_keeps_one_valid_active_connection():
    store = _restore_store(
        {
            "active_connection_id": "missing",
            "connections": [
                {"id": "bad", "name": "bad", "endpoint": "ftp://bad"},
                {"id": "good", "name": "生产", "endpoint": "10.10.11.28:29100"},
            ],
        },
        fallback={"version": 1, "active_connection_id": "", "connections": []},
    )
    assert store["active_connection_id"] == "good"
    assert len(store["connections"]) == 1
    assert store["connections"][0]["endpoint"] == "http://10.10.11.28:29100"
