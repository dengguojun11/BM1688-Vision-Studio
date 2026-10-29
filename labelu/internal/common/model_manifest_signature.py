"""ModelManifest 的签名和制品完整性基础契约。

该模块只负责规范化、签名和本地文件校验；导出、边端和 AICM 发布入口
需要在后续工作包中显式调用它，并把业务对象 ID 与当前数据库记录对账。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from labelu.internal.common.remote_train_hmac import canonical_json_bytes


MODEL_MANIFEST_SCHEMA_VERSION = "v1"
MODEL_MANIFEST_SIGNATURE_FIELD = "manifest_hmac_sha256"
MODEL_MANIFEST_KEY_ID_FIELD = "manifest_hmac_key_id"
MODEL_MANIFEST_ALGORITHM_FIELD = "manifest_signature_algorithm"
MODEL_MANIFEST_ED25519_SIGNATURE_FIELD = "manifest_ed25519_signature"
MODEL_MANIFEST_ED25519_KEY_ID_FIELD = "manifest_ed25519_key_id"
MODEL_MANIFEST_ED25519_ALGORITHM_FIELD = "manifest_ed25519_algorithm"
MODEL_MANIFEST_ED25519_ALGORITHM = "Ed25519"
MODEL_MANIFEST_MUTABLE_FIELDS = frozenset(
    {
        # AICM only returns these after upload; they must not invalidate the
        # server-signed artifact identity.
        "aicm_model_set_id",
        "aicm_published_at",
    }
)


def _canonical_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    body = {
        str(key): value
        for key, value in dict(payload).items()
        if key
        not in {
            MODEL_MANIFEST_SIGNATURE_FIELD,
            MODEL_MANIFEST_KEY_ID_FIELD,
            MODEL_MANIFEST_ALGORITHM_FIELD,
            MODEL_MANIFEST_ED25519_SIGNATURE_FIELD,
            MODEL_MANIFEST_ED25519_KEY_ID_FIELD,
            MODEL_MANIFEST_ED25519_ALGORITHM_FIELD,
            *MODEL_MANIFEST_MUTABLE_FIELDS,
        }
    }
    return canonical_json_bytes(body)


def model_manifest_signature(payload: Mapping[str, Any], secret: str) -> str:
    """Return the HMAC over the immutable manifest body."""

    if not str(secret or "").strip():
        raise ValueError("ModelManifest 签名密钥不能为空")
    return hmac.new(
        str(secret).encode("utf-8"), _canonical_manifest_bytes(payload), hashlib.sha256
    ).hexdigest()


def sign_model_manifest(
    payload: Mapping[str, Any], *, secret: str, key_id: str
) -> dict[str, Any]:
    """Add a versioned HMAC signature without mutating the caller's mapping."""

    if not str(key_id or "").strip():
        raise ValueError("ModelManifest 签名 key_id 不能为空")
    signed = dict(payload)
    signed.setdefault("manifest_schema_version", MODEL_MANIFEST_SCHEMA_VERSION)
    signed[MODEL_MANIFEST_ALGORITHM_FIELD] = "HMAC-SHA256"
    signed[MODEL_MANIFEST_KEY_ID_FIELD] = str(key_id)
    signed[MODEL_MANIFEST_SIGNATURE_FIELD] = model_manifest_signature(
        signed, secret
    )
    return signed


def verify_model_manifest(
    payload: Mapping[str, Any],
    *,
    secret: str,
    expected_key_id: str,
    expected_export_id: int | None = None,
    expected_execution_signature: str | None = None,
    required: bool = True,
) -> tuple[bool, str]:
    """Verify signature and optional object identity, failing closed by default."""

    signature = str(payload.get(MODEL_MANIFEST_SIGNATURE_FIELD) or "").strip()
    if not signature:
        return (False, "manifest_signature_missing") if required else (True, "unsigned_compat")
    if str(payload.get("manifest_schema_version") or "") != MODEL_MANIFEST_SCHEMA_VERSION:
        return False, "manifest_schema_version_invalid"
    if str(payload.get(MODEL_MANIFEST_ALGORITHM_FIELD) or "") != "HMAC-SHA256":
        return False, "manifest_signature_algorithm_invalid"
    if str(payload.get(MODEL_MANIFEST_KEY_ID_FIELD) or "") != str(expected_key_id or ""):
        return False, "manifest_key_id_mismatch"
    if expected_export_id is not None:
        try:
            if int(payload.get("export_id")) != int(expected_export_id):
                return False, "manifest_export_id_mismatch"
        except (TypeError, ValueError):
            return False, "manifest_export_id_missing"
    if expected_execution_signature is not None and str(
        payload.get("execution_signature") or ""
    ) != str(expected_execution_signature):
        return False, "manifest_execution_signature_mismatch"
    if not str(secret or "").strip():
        return False, "manifest_secret_missing"
    expected = model_manifest_signature(payload, secret)
    if not hmac.compare_digest(signature, expected):
        return False, "manifest_signature_invalid"
    return True, "ok"


def _load_ed25519_private_key(value: str | bytes) -> Ed25519PrivateKey:
    """Load PEM, base64, hex, or raw 32-byte private key material."""

    import base64

    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    text = raw.decode("utf-8", errors="ignore").strip()
    if "\\n" in text and "\n" not in text:
        raw = text.replace("\\n", "\n").encode("utf-8")
        text = raw.decode("utf-8").strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Ed25519 私钥类型无效")
        return key
    candidates = [raw]
    if text:
        try:
            candidates.append(base64.b64decode(text, validate=True))
        except (ValueError, TypeError):
            pass
        try:
            candidates.append(bytes.fromhex(text))
        except ValueError:
            pass
    for candidate in candidates:
        if len(candidate) == 32:
            return Ed25519PrivateKey.from_private_bytes(candidate)
    raise ValueError("Ed25519 私钥必须是 PEM 或 32 字节原始/base64/hex 内容")


def _load_ed25519_public_key(value: str | bytes) -> Ed25519PublicKey:
    """Load PEM, base64, hex, or raw 32-byte public key material."""

    import base64

    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    text = raw.decode("utf-8", errors="ignore").strip()
    if "\\n" in text and "\n" not in text:
        raw = text.replace("\\n", "\n").encode("utf-8")
        text = raw.decode("utf-8").strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Ed25519 公钥类型无效")
        return key
    candidates = [raw]
    if text:
        try:
            candidates.append(base64.b64decode(text, validate=True))
        except (ValueError, TypeError):
            pass
        try:
            candidates.append(bytes.fromhex(text))
        except ValueError:
            pass
    for candidate in candidates:
        if len(candidate) == 32:
            return Ed25519PublicKey.from_public_bytes(candidate)
    raise ValueError("Ed25519 公钥必须是 PEM 或 32 字节原始/base64/hex 内容")


def sign_model_manifest_ed25519(
    payload: Mapping[str, Any], *, private_key: str | bytes, key_id: str
) -> dict[str, Any]:
    """Sign an immutable manifest for devices/external platforms.

    The private key is only accepted by the server-side caller.  The resulting
    envelope carries a key id but never embeds key material.
    """

    if not str(key_id or "").strip():
        raise ValueError("Ed25519 签名 key_id 不能为空")
    signed = dict(payload)
    signed.setdefault("manifest_schema_version", MODEL_MANIFEST_SCHEMA_VERSION)
    signed[MODEL_MANIFEST_ED25519_ALGORITHM_FIELD] = MODEL_MANIFEST_ED25519_ALGORITHM
    signed[MODEL_MANIFEST_ED25519_KEY_ID_FIELD] = str(key_id)
    signed[MODEL_MANIFEST_ED25519_SIGNATURE_FIELD] = _load_ed25519_private_key(
        private_key
    ).sign(_canonical_manifest_bytes(signed)).hex()
    return signed


def verify_model_manifest_ed25519(
    payload: Mapping[str, Any],
    *,
    public_key: str | bytes,
    expected_key_id: str,
    expected_export_id: int | None = None,
    expected_model_version_id: int | None = None,
    expected_profile_digest: str | None = None,
    expected_execution_signature: str | None = None,
    expected_execution_generation: str | None = None,
    required: bool = True,
) -> tuple[bool, str]:
    """Verify a device-facing manifest and its identity bindings."""

    signature = str(payload.get(MODEL_MANIFEST_ED25519_SIGNATURE_FIELD) or "").strip()
    if not signature:
        return (False, "edge_manifest_signature_missing") if required else (True, "unsigned_compat")
    if str(payload.get(MODEL_MANIFEST_ED25519_ALGORITHM_FIELD) or "") != MODEL_MANIFEST_ED25519_ALGORITHM:
        return False, "edge_manifest_signature_algorithm_invalid"
    if str(payload.get(MODEL_MANIFEST_ED25519_KEY_ID_FIELD) or "") != str(expected_key_id or ""):
        return False, "edge_manifest_key_id_mismatch"
    for field, expected, reason in (
        ("export_id", expected_export_id, "edge_manifest_export_id_mismatch"),
        ("model_version_id", expected_model_version_id, "edge_manifest_model_version_id_mismatch"),
    ):
        if expected is not None:
            try:
                if int(payload.get(field)) != int(expected):
                    return False, reason
            except (TypeError, ValueError):
                return False, reason.replace("mismatch", "missing")
    if expected_profile_digest is not None and str(payload.get("profile_digest") or "") != str(expected_profile_digest):
        return False, "edge_manifest_profile_digest_mismatch"
    if expected_execution_signature is not None and str(payload.get("artifact_execution_signature") or payload.get("execution_signature") or "") != str(expected_execution_signature):
        return False, "edge_manifest_execution_signature_mismatch"
    if expected_execution_generation is not None and str(payload.get("execution_generation") or "") != str(expected_execution_generation):
        return False, "edge_manifest_execution_generation_mismatch"
    try:
        signature_bytes = bytes.fromhex(signature)
        _load_ed25519_public_key(public_key).verify(signature_bytes, _canonical_manifest_bytes(payload))
    except Exception:
        return False, "edge_manifest_signature_invalid"
    return True, "ok"


def verify_manifest_artifacts(
    manifest: Mapping[str, Any], *, root: Path | str | None = None
) -> tuple[bool, list[str]]:
    """Re-hash listed files and optionally require all three BM1688 precisions."""

    base = Path(root).resolve() if root else None
    reasons: list[str] = []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return False, ["manifest_artifacts_missing"]
    precisions: set[str] = set()
    for index, item in enumerate(artifacts):
        if not isinstance(item, Mapping):
            reasons.append(f"artifact_{index}_invalid")
            continue
        relative = str(item.get("relative_path") or "").strip()
        path_value = str(item.get("path") or "").strip()
        if base and relative:
            path = (base / relative).resolve()
            try:
                path.relative_to(base)
            except ValueError:
                reasons.append(f"artifact_{index}_outside_root")
                continue
        else:
            path = Path(path_value)
        if not path.is_file():
            reasons.append(f"artifact_{index}_missing")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(item.get("sha256") or ""):
            reasons.append(f"artifact_{index}_sha256_mismatch")
        if item.get("bytes") is not None:
            try:
                expected_bytes = int(item["bytes"])
            except (TypeError, ValueError):
                reasons.append(f"artifact_{index}_size_invalid")
            else:
                if path.stat().st_size != expected_bytes:
                    reasons.append(f"artifact_{index}_size_mismatch")
        if item.get("role") == "model_bmodel":
            declared_precision = str(item.get("precision") or "").strip().upper()
            if declared_precision in {"FP32", "FP16", "INT8"}:
                precisions.add(declared_precision)
            else:
                # Compatibility fallback for old manifests that encoded the
                # precision only in the filename. New manifests must declare it.
                name = path.name.lower()
                for precision in ("fp32", "fp16", "int8"):
                    if precision in name:
                        precisions.add(precision.upper())
    missing = {"FP32", "FP16", "INT8"} - precisions
    if missing:
        reasons.append("bmodel_precision_missing:" + ",".join(sorted(missing)))
    return not reasons, reasons


__all__ = [
    "MODEL_MANIFEST_SCHEMA_VERSION",
    "MODEL_MANIFEST_SIGNATURE_FIELD",
    "MODEL_MANIFEST_KEY_ID_FIELD",
    "MODEL_MANIFEST_ED25519_SIGNATURE_FIELD",
    "MODEL_MANIFEST_ED25519_KEY_ID_FIELD",
    "MODEL_MANIFEST_ED25519_ALGORITHM_FIELD",
    "sign_model_manifest",
    "verify_model_manifest",
    "sign_model_manifest_ed25519",
    "verify_model_manifest_ed25519",
    "verify_manifest_artifacts",
]
