"""AICM 发布环境的 MySQL 加密存储。"""

import json

from labelu.internal.common.aicm_publish_config import (
    config_is_complete,
    config_missing_fields,
    create_environment_record,
    delete_environment_record,
    load_aicm_publish_config,
    load_environments_store,
    mask_password,
    normalize_aicm_base_url,
    repair_stored_config_if_needed,
    save_aicm_publish_config,
    set_active_environment_id,
    update_environment_record,
)


def test_normalize_aicm_base_url_strips_api_path():
    assert normalize_aicm_base_url("http://192.168.54.169:8102/v2/login/") == (
        "http://192.168.54.169:8102"
    )
    assert normalize_aicm_base_url("http://192.168.54.169:8102/v2/model-set") == (
        "http://192.168.54.169:8102"
    )
    assert normalize_aicm_base_url("192.168.54.169:8102") == (
        "http://192.168.54.169:8102"
    )


def test_mask_password():
    assert mask_password("") == (False, "")
    present, masked = mask_password("secret123")
    assert present is True
    assert masked.endswith("123")
    assert "secret" not in masked


def test_environment_password_is_encrypted_in_mysql_store():
    rec = create_environment_record(
        name="生产 AICM",
        base_url="http://192.168.1.10:8102/",
        username="admin",
        password="super-secret-pass-1234",
        set_active=True,
    )

    store = load_environments_store()
    stored = store["environments"][0]
    payload = json.dumps(store, ensure_ascii=False)
    assert rec["id"] == stored["id"]
    assert stored["password_encrypted"]
    assert stored["password_source_type"] == "mysql:encrypted"
    assert stored["password_is_configured"] is True
    assert "super-secret-pass-1234" not in payload
    assert stored["password_masked_hint"].endswith("1234")

    cfg = load_aicm_publish_config(environment_id=rec["id"])
    assert cfg["base_url"] == "http://192.168.1.10:8102"
    assert cfg["username"] == "admin"
    assert cfg["password"] == "super-secret-pass-1234"
    assert config_is_complete(cfg) is True


def test_update_password_keep_replace_and_clear():
    rec = create_environment_record(
        name="测试环境",
        base_url="http://10.0.0.1:8102",
        username="u1",
        password="first-password",
    )
    before = load_environments_store()["environments"][0]["password_encrypted"]

    update_environment_record(
        rec["id"],
        username="u2",
        password_secret_action="keep",
    )
    kept = load_environments_store()["environments"][0]
    assert kept["password_encrypted"] == before
    assert load_aicm_publish_config()["password"] == "first-password"

    update_environment_record(
        rec["id"],
        password_secret_action="set",
        password="second-password",
    )
    replaced = load_environments_store()["environments"][0]
    assert replaced["password_encrypted"] != before
    assert load_aicm_publish_config()["password"] == "second-password"

    update_environment_record(rec["id"], password_secret_action="clear")
    cleared = load_environments_store()["environments"][0]
    assert "password_encrypted" not in cleared
    assert cleared["password_is_configured"] is False
    assert load_aicm_publish_config()["password"] == ""


def test_environment_name_defaults_to_endpoint():
    rec = create_environment_record(
        name="",
        base_url="http://10.0.0.8:8102",
        username="admin",
        password="secret",
    )
    assert rec["name"] == "AICM 10.0.0.8:8102"


def test_environments_crud_and_active_selection():
    first = create_environment_record(
        name="环境1",
        base_url="http://10.0.0.1:8102",
        username="u1",
        password="secret-1",
        set_active=True,
    )
    second = create_environment_record(
        name="环境2",
        base_url="http://10.0.0.2:8102",
        username="u2",
        password="secret-2",
        set_active=False,
    )
    assert load_environments_store()["active_environment_id"] == first["id"]

    set_active_environment_id(second["id"])
    assert load_aicm_publish_config()["base_url"] == "http://10.0.0.2:8102"

    delete_environment_record(second["id"])
    store = load_environments_store()
    assert len(store["environments"]) == 1
    assert store["active_environment_id"] == first["id"]


def test_save_compat_api_uses_encrypted_mysql_password():
    save_aicm_publish_config(
        base_url="http://10.0.0.9:8102",
        username="admin",
        password_secret_action="set",
        password="db-secret",
    )
    assert load_aicm_publish_config()["password"] == "db-secret"
    payload = json.dumps(load_environments_store(), ensure_ascii=False)
    assert "db-secret" not in payload

    save_aicm_publish_config(username="admin2", password_secret_action="keep")
    cfg = load_aicm_publish_config()
    assert cfg["username"] == "admin2"
    assert cfg["password"] == "db-secret"


def test_config_missing_fields():
    assert config_missing_fields(
        {"base_url": "http://x", "username": "", "password": "p"}
    ) == ["username"]
    assert config_is_complete(
        {"base_url": "http://x", "username": "u", "password": "p"}
    )
    assert not config_is_complete(
        {"base_url": "http://x", "username": "u", "password": ""}
    )


def test_repair_normalizes_mysql_record(monkeypatch):
    from labelu.internal.common import aicm_publish_config as mod

    store = {
        "version": 1,
        "active_environment_id": "env_test",
        "environments": [
            {
                "id": "env_test",
                "base_url": "http://10.0.0.1:8102/v2/login",
                "password_env_name": "LEGACY_AICM_PASSWORD",
                "password_masked_hint": "********1234",
            }
        ],
    }
    written = []
    monkeypatch.setattr(
        mod,
        "read_group_values",
        lambda _group_key: {"environments_store": store},
    )
    monkeypatch.setattr(mod, "_write_environments_store", written.append)

    assert repair_stored_config_if_needed() is True
    repaired = written[0]["environments"][0]
    assert repaired["base_url"] == "http://10.0.0.1:8102"
    assert "password_env_name" not in repaired
    assert repaired["password_masked_hint"] == ""
    assert "password_encrypted" not in repaired


def test_module_has_no_legacy_json_store_api():
    from labelu.internal.common import aicm_publish_config as mod

    assert not hasattr(mod, "load_environments_store_legacy")
    assert not hasattr(mod, "config_file_path")
    assert not hasattr(mod, "environments_file_path")
