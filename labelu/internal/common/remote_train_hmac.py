"""远程训练制品的 HMAC-SHA256 签名工具。

签名字段本身不参与摘要，保证 JSON 重写、字段顺序变化不会形成循环依赖。
该模块只处理平台侧文件；节点 Agent 为保持可独立部署，内置了相同的规范。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Mapping

HMAC_ALGORITHM = "HMAC-SHA256"
HMAC_SIGNATURE_FIELD = "hmac_sha256"
HMAC_KEY_ID_FIELD = "hmac_key_id"


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    body = dict(payload)
    body.pop(HMAC_SIGNATURE_FIELD, None)
    body.pop(HMAC_KEY_ID_FIELD, None)
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def json_signature(payload: Mapping[str, Any], secret: str) -> str:
    return hmac.new(
        str(secret).encode("utf-8"), canonical_json_bytes(payload), hashlib.sha256
    ).hexdigest()


def sign_json_payload(
    payload: Mapping[str, Any], *, secret: str, key_id: str = "v1"
) -> dict[str, Any]:
    signed = dict(payload)
    signed[HMAC_KEY_ID_FIELD] = str(key_id or "v1")
    signed[HMAC_SIGNATURE_FIELD] = json_signature(signed, secret)
    return signed


def verify_json_payload(
    payload: Mapping[str, Any],
    *,
    secret: str,
    expected_key_id: str = "",
    required: bool = False,
) -> bool:
    signature = str(payload.get(HMAC_SIGNATURE_FIELD) or "").strip()
    if not signature:
        return not required and not secret
    if not secret:
        return False
    if expected_key_id and str(payload.get(HMAC_KEY_ID_FIELD) or "").strip() != expected_key_id:
        return False
    expected = json_signature(payload, secret)
    return hmac.compare_digest(signature, expected)


def bytes_signature(content: bytes, secret: str) -> str:
    return hmac.new(str(secret).encode("utf-8"), content, hashlib.sha256).hexdigest()


def verify_bytes_signature(
    content: bytes,
    signature: str,
    *,
    secret: str,
    required: bool = False,
) -> bool:
    sig = str(signature or "").strip()
    if not sig:
        return not required and not secret
    if not secret:
        return False
    return hmac.compare_digest(sig, bytes_signature(content, secret))


def sign_json_file(path: Path, *, secret: str, key_id: str = "v1") -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"签名对象必须是 JSON 对象: {path}")
    signed = sign_json_payload(payload, secret=secret, key_id=key_id)
    path.write_text(
        json.dumps(signed, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
