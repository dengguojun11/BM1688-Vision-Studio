"""AICM 模型发布配置，以 MySQL 治理表作为唯一数据源。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from loguru import logger

from labelu.internal.common.crypto import decrypt_value, encrypt_value
from labelu.internal.common.system_config_provider import (
    read_group_values,
    write_group_values,
)

DEFAULT_CHIP_TYPE = "SOPHGO,BM1688,NPU"
DEFAULT_ALGORITHM_TYPE = "Detection"
DEFAULT_MODEL_ALG_TYPE = "yoloV11"
DEFAULT_MODEL_FUNCTION = "targetDetetct"
PASSWORD_SOURCE_ENCRYPTED = "mysql:encrypted"


def normalize_aicm_base_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    u = u.rstrip("/")
    strip_suffixes = ("/v2/login", "/v2/model-set", "/v2/model_set", "/v2")
    changed = True
    while changed:
        changed = False
        low = u.lower()
        for suffix in strip_suffixes:
            if low.endswith(suffix):
                u = u[: -len(suffix)].rstrip("/")
                changed = True
                break
    return u


def _default_config() -> dict[str, Any]:
    return {
        "base_url": "",
        "username": "",
        "password": "",
        "chip_type": DEFAULT_CHIP_TYPE,
        "algorithm_type": DEFAULT_ALGORITHM_TYPE,
        "verify_ssl": False,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_environments_store() -> dict[str, Any]:
    return {"version": 1, "active_environment_id": "", "environments": []}


def _sanitize_masked_hint(value: str | None) -> str:
    return str(value or "").strip()[:64]


def _mask_password_hint(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return ("*" * (len(raw) - 4)) + raw[-4:]


def _password_secret_meta(
    rec: dict[str, Any],
) -> dict[str, Any]:
    encrypted = str(rec.get("password_encrypted") or "").strip()
    has_explicit_masked_hint = (
        "password_masked_hint" in rec or "password_masked" in rec
    )
    masked_hint = _sanitize_masked_hint(
        (
            rec.get("password_masked_hint")
            if "password_masked_hint" in rec
            else rec.get("password_masked")
        )
        if has_explicit_masked_hint
        else _sanitize_masked_hint(rec.get("password_masked_hint"))
    ) if encrypted else ""
    updated_at = str(rec.get("password_updated_at") or "").strip() or None
    meta = {
        "password_source_type": PASSWORD_SOURCE_ENCRYPTED if encrypted else "",
        "password_masked": masked_hint,
        "password_masked_hint": masked_hint,
        "password_is_configured": bool(encrypted),
        "password_updated_at": updated_at,
    }
    if encrypted:
        meta["password_encrypted"] = encrypted
    return meta


def _password_from_record(rec: dict[str, Any]) -> str:
    encrypted = str(rec.get("password_encrypted") or "").strip()
    if not encrypted:
        return ""
    try:
        return decrypt_value(encrypted).strip()[:256]
    except Exception:
        logger.exception(
            "aicm publish environment password decrypt failed id={}",
            rec.get("id"),
        )
        return ""


def _strip_runtime_password(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(rec)
    out.pop("password", None)
    out["password_masked"] = _sanitize_masked_hint(
        out.get("password_masked_hint") or out.get("password_masked")
    )
    out["password_masked_hint"] = out["password_masked"]
    return out


def _env_record_to_cfg(rec: dict[str, Any]) -> dict[str, Any]:
    secret_meta = _password_secret_meta(rec)
    out = dict(_default_config())
    out["base_url"] = normalize_aicm_base_url(str(rec.get("base_url") or ""))[:500]
    out["username"] = str(rec.get("username") or "").strip()[:128]
    out["password"] = _password_from_record({**rec, **secret_meta})
    chip = str(rec.get("chip_type") or DEFAULT_CHIP_TYPE).strip()
    out["chip_type"] = chip[:256] if chip else DEFAULT_CHIP_TYPE
    alg = str(rec.get("algorithm_type") or DEFAULT_ALGORITHM_TYPE).strip()
    out["algorithm_type"] = alg[:64] if alg else DEFAULT_ALGORITHM_TYPE
    out["verify_ssl"] = bool(rec.get("verify_ssl"))
    return out


def _normalize_store_for_db(store: dict[str, Any]) -> dict[str, Any]:
    envs: list[dict[str, Any]] = []
    for rec in list(store.get("environments") or []):
        if not isinstance(rec, dict):
            continue
        envs.append(_strip_runtime_password({**rec, **_password_secret_meta(rec)}))
    return {
        "version": 1,
        "active_environment_id": str(store.get("active_environment_id") or "").strip(),
        "environments": envs,
    }


def _store_to_db_items(store: dict[str, Any]) -> dict[str, Any]:
    return {"environments_store": _normalize_store_for_db(store)}


def _restore_store_from_db(
    raw_store: Any,
) -> dict[str, Any]:
    if not isinstance(raw_store, dict):
        return _empty_environments_store()
    envs: list[dict[str, Any]] = []
    for rec in list(raw_store.get("environments") or []):
        if not isinstance(rec, dict):
            continue
        envs.append(
            {
                "id": str(rec.get("id") or f"env_{uuid.uuid4().hex[:12]}"),
                "name": str(rec.get("name") or "未命名环境").strip()[:64] or "未命名环境",
                "base_url": normalize_aicm_base_url(str(rec.get("base_url") or ""))[:500],
                "username": str(rec.get("username") or "").strip()[:128],
                "chip_type": (
                    str(rec.get("chip_type") or DEFAULT_CHIP_TYPE).strip()[:256]
                    or DEFAULT_CHIP_TYPE
                ),
                "algorithm_type": (
                    str(rec.get("algorithm_type") or DEFAULT_ALGORITHM_TYPE).strip()[:64]
                    or DEFAULT_ALGORITHM_TYPE
                ),
                "verify_ssl": bool(rec.get("verify_ssl")),
                "created_at": str(rec.get("created_at") or _utc_now_iso()),
                "updated_at": str(rec.get("updated_at") or _utc_now_iso()),
                **_password_secret_meta(rec),
            }
        )
    return {
        "version": 1,
        "active_environment_id": str(raw_store.get("active_environment_id") or "").strip(),
        "environments": envs,
    }


def _active_environment_record(store: dict[str, Any], environment_id: str | None = None) -> dict[str, Any] | None:
    eid = (environment_id or "").strip() or str(store.get("active_environment_id") or "").strip()
    if not eid:
        return None
    for rec in list(store.get("environments") or []):
        if str(rec.get("id") or "") == eid:
            return rec
    return None


def load_environments_store() -> dict[str, Any]:
    db_store = read_group_values("aicm_publish_config").get("environments_store")
    restored = _restore_store_from_db(db_store)
    if not restored.get("active_environment_id") and restored.get("environments"):
        restored["active_environment_id"] = str(restored["environments"][0]["id"])
    return restored


def _write_environments_store(store: dict[str, Any]) -> None:
    payload = {
        "version": 1,
        "active_environment_id": str(store.get("active_environment_id") or "").strip(),
        "environments": list(store.get("environments") or []),
    }
    ok = write_group_values(
        "aicm_publish_config",
        _store_to_db_items(payload),
        value_source="db",
        change_reason="save aicm environments store",
    )
    if not ok:
        raise RuntimeError("AICM 发布环境写入 MySQL 失败")


def list_environment_records() -> List[dict[str, Any]]:
    store = load_environments_store()
    return list(store.get("environments") or [])


def find_environment_record(environment_id: str) -> dict[str, Any] | None:
    eid = (environment_id or "").strip()
    if not eid:
        return None
    for rec in list_environment_records():
        if str(rec.get("id") or "") == eid:
            return rec
    return None


def get_active_environment_id() -> str:
    return str(load_environments_store().get("active_environment_id") or "").strip()


def set_active_environment_id(environment_id: str) -> dict[str, Any]:
    eid = (environment_id or "").strip()
    store = load_environments_store()
    if not find_environment_record(eid):
        raise ValueError(f"发布环境不存在: {eid}")
    store["active_environment_id"] = eid
    _write_environments_store(store)
    return store


def _apply_password_secret_change(
    rec: dict[str, Any],
    *,
    action: str = "keep",
    password: Optional[str] = None,
) -> dict[str, Any]:
    if action == "keep":
        return rec
    if action == "clear":
        rec["password_source_type"] = ""
        rec.pop("password_env_name", None)
        rec["password_masked"] = ""
        rec["password_masked_hint"] = ""
        rec["password_is_configured"] = False
        rec["password_updated_at"] = _utc_now_iso()
        rec.pop("password", None)
        rec.pop("password_encrypted", None)
        return rec

    inline_password = str(password or "").strip()
    if not inline_password:
        raise ValueError("保存发布环境时必须填写 AICM 密码")
    rec["password_source_type"] = PASSWORD_SOURCE_ENCRYPTED
    rec.pop("password_env_name", None)
    rec["password_encrypted"] = encrypt_value(inline_password)
    rec["password_masked"] = _mask_password_hint(inline_password)
    rec["password_masked_hint"] = rec["password_masked"]
    rec["password_is_configured"] = True
    rec["password_updated_at"] = _utc_now_iso()
    rec.pop("password", None)
    return rec


def create_environment_record(
    *,
    name: str,
    base_url: str,
    username: str,
    password: str,
    chip_type: Optional[str] = None,
    algorithm_type: Optional[str] = None,
    verify_ssl: bool = False,
    set_active: bool = True,
) -> dict[str, Any]:
    now = _utc_now_iso()
    rec = {
        "id": f"env_{uuid.uuid4().hex[:12]}",
        "name": (
            (name or "").strip()
            or f"AICM {normalize_aicm_base_url(base_url).split('://')[-1]}"
        )[:64],
        "base_url": normalize_aicm_base_url(base_url)[:500],
        "username": (username or "").strip()[:128],
        "chip_type": (chip_type or DEFAULT_CHIP_TYPE).strip()[:256] or DEFAULT_CHIP_TYPE,
        "algorithm_type": (algorithm_type or DEFAULT_ALGORITHM_TYPE).strip()[:64]
        or DEFAULT_ALGORITHM_TYPE,
        "verify_ssl": bool(verify_ssl),
        "created_at": now,
        "updated_at": now,
    }
    _apply_password_secret_change(
        rec,
        action="set",
        password=password,
    )
    store = load_environments_store()
    envs = list(store.get("environments") or [])
    envs.append(rec)
    store["environments"] = envs
    if set_active or not str(store.get("active_environment_id") or "").strip():
        store["active_environment_id"] = rec["id"]
    _write_environments_store(store)
    return rec


def update_environment_record(
    environment_id: str,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    username: Optional[str] = None,
    password_secret_action: str = "keep",
    password: Optional[str] = None,
    chip_type: Optional[str] = None,
    algorithm_type: Optional[str] = None,
    verify_ssl: Optional[bool] = None,
) -> dict[str, Any]:
    store = load_environments_store()
    envs: list = list(store.get("environments") or [])
    found: dict[str, Any] | None = None
    for i, rec in enumerate(envs):
        if str(rec.get("id") or "") != environment_id:
            continue
        if name is not None:
            nm = str(name).strip()[:64]
            if nm:
                rec["name"] = nm
        if base_url is not None:
            bu = normalize_aicm_base_url(str(base_url))
            if bu:
                rec["base_url"] = bu[:500]
        if username is not None:
            un = str(username).strip()
            if un:
                rec["username"] = un[:128]
        _apply_password_secret_change(
            rec,
            action=password_secret_action,
            password=password,
        )
        if chip_type is not None:
            ct = str(chip_type).strip()
            rec["chip_type"] = ct[:256] if ct else DEFAULT_CHIP_TYPE
        if algorithm_type is not None:
            at = str(algorithm_type).strip()
            rec["algorithm_type"] = at[:64] if at else DEFAULT_ALGORITHM_TYPE
        if verify_ssl is not None:
            rec["verify_ssl"] = bool(verify_ssl)
        rec["updated_at"] = _utc_now_iso()
        envs[i] = rec
        found = rec
        break
    if not found:
        raise ValueError(f"发布环境不存在: {environment_id}")
    store["environments"] = envs
    _write_environments_store(store)
    return found


def delete_environment_record(environment_id: str) -> None:
    store = load_environments_store()
    envs = [e for e in (store.get("environments") or []) if str(e.get("id") or "") != environment_id]
    if len(envs) == len(store.get("environments") or []):
        raise ValueError(f"发布环境不存在: {environment_id}")
    store["environments"] = envs
    active = str(store.get("active_environment_id") or "").strip()
    if active == environment_id:
        store["active_environment_id"] = str(envs[0]["id"]) if envs else ""
    _write_environments_store(store)


def load_aicm_publish_config(environment_id: Optional[str] = None) -> dict[str, Any]:
    store = load_environments_store()
    rec = _active_environment_record(store, environment_id)
    if rec:
        return _env_record_to_cfg(rec)
    return dict(_default_config())


def save_aicm_publish_config(
    *,
    base_url: Optional[str] = None,
    username: Optional[str] = None,
    password_secret_action: str = "keep",
    password: Optional[str] = None,
    chip_type: Optional[str] = None,
    algorithm_type: Optional[str] = None,
    verify_ssl: Optional[bool] = None,
    environment_id: Optional[str] = None,
) -> dict[str, Any]:
    eid = (environment_id or "").strip() or get_active_environment_id()
    store = load_environments_store()
    if not eid:
        current = load_aicm_publish_config()
        if base_url is not None and str(base_url).strip():
            current["base_url"] = normalize_aicm_base_url(str(base_url))[:500]
        if username is not None and str(username).strip():
            current["username"] = str(username).strip()[:128]
        if chip_type is not None:
            ct = str(chip_type).strip()
            current["chip_type"] = ct[:256] if ct else DEFAULT_CHIP_TYPE
        if algorithm_type is not None:
            at = str(algorithm_type).strip()
            current["algorithm_type"] = at[:64] if at else DEFAULT_ALGORITHM_TYPE
        if verify_ssl is not None:
            current["verify_ssl"] = bool(verify_ssl)
        rec = create_environment_record(
            name="默认环境",
            base_url=current["base_url"],
            username=current["username"],
            password=str(password or ""),
            chip_type=current["chip_type"],
            algorithm_type=current["algorithm_type"],
            verify_ssl=current["verify_ssl"],
            set_active=True,
        )
        logger.info("aicm publish environment created id={}", rec["id"])
        return load_aicm_publish_config(environment_id=rec["id"])
    update_environment_record(
        eid,
        name=None,
        base_url=base_url,
        username=username,
        password_secret_action=password_secret_action,
        password=password,
        chip_type=chip_type,
        algorithm_type=algorithm_type,
        verify_ssl=verify_ssl,
    )
    logger.info("aicm publish environment saved to mysql id={}", eid)
    return load_aicm_publish_config(environment_id=eid)


def config_is_complete(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg if cfg is not None else load_aicm_publish_config()
    return bool(
        str(c.get("base_url") or "").strip()
        and str(c.get("username") or "").strip()
        and str(c.get("password") or "").strip()
    )


def config_missing_fields(cfg: dict[str, Any] | None = None) -> list[str]:
    c = cfg if cfg is not None else load_aicm_publish_config()
    missing: list[str] = []
    if not str(c.get("base_url") or "").strip():
        missing.append("base_url")
    if not str(c.get("username") or "").strip():
        missing.append("username")
    if not str(c.get("password") or "").strip():
        missing.append("password")
    return missing


def apply_publish_config_overrides(
    overrides: dict[str, Any] | None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    if overrides is None:
        return load_aicm_publish_config()
    cur = load_aicm_publish_config()
    base_url = overrides.get("base_url")
    if base_url is not None and str(base_url).strip():
        cur["base_url"] = normalize_aicm_base_url(str(base_url))[:500]
    username = overrides.get("username")
    if username is not None and str(username).strip():
        cur["username"] = str(username).strip()[:128]
    chip_type = overrides.get("chip_type")
    if chip_type is not None and str(chip_type).strip():
        cur["chip_type"] = str(chip_type).strip()[:256]
    algorithm_type = overrides.get("algorithm_type")
    if algorithm_type is not None and str(algorithm_type).strip():
        cur["algorithm_type"] = str(algorithm_type).strip()[:64]
    if overrides.get("verify_ssl") is not None:
        cur["verify_ssl"] = bool(overrides.get("verify_ssl"))
    if persist:
        save_aicm_publish_config(
            base_url=cur.get("base_url"),
            username=cur.get("username"),
            password_secret_action=str(overrides.get("password_secret_action") or "keep"),
            password=overrides.get("password"),
            chip_type=cur.get("chip_type"),
            algorithm_type=cur.get("algorithm_type"),
            verify_ssl=cur.get("verify_ssl"),
        )
    return cur


def repair_stored_config_if_needed() -> bool:
    raw_store = read_group_values("aicm_publish_config").get("environments_store")
    store = _restore_store_from_db(raw_store)
    changed = not isinstance(raw_store, dict)
    if not store.get("active_environment_id") and store.get("environments"):
        store["active_environment_id"] = str(store["environments"][0]["id"])
        changed = True
    for rec in list(store.get("environments") or []):
        old = str(rec.get("base_url") or "").strip()
        new = normalize_aicm_base_url(old)
        if new and new != old:
            rec["base_url"] = new
            rec["updated_at"] = _utc_now_iso()
            changed = True
    normalized = _normalize_store_for_db(store)
    if raw_store != normalized:
        changed = True
    if changed:
        _write_environments_store(normalized)
        logger.warning("aicm environments mysql record normalized")
    return changed


def mask_password(password: str) -> Tuple[bool, str]:
    if not password or not str(password).strip():
        return False, ""
    value = str(password).strip()
    if len(value) <= 4:
        return True, "********"
    return True, "*" * 12 + value[-4:]
