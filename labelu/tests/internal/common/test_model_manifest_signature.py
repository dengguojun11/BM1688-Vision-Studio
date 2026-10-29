from labelu.internal.common.model_manifest_signature import (
    sign_model_manifest_ed25519,
    sign_model_manifest,
    verify_manifest_artifacts,
    verify_model_manifest_ed25519,
    verify_model_manifest,
)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _manifest():
    return {
        "manifest_id": "manifest-11",
        "manifest_schema_version": "v1",
        "export_id": 11,
        "execution_signature": "execution-11",
        "profile_digest": "profile-11",
        "artifacts": [],
    }


def test_model_manifest_signature_accepts_aicm_post_publish_fields():
    signed = sign_model_manifest(_manifest(), secret="manifest-secret", key_id="k1")
    signed["aicm_model_set_id"] = "534"
    signed["aicm_published_at"] = "2026-09-20T10:00:00Z"

    assert verify_model_manifest(
        signed,
        secret="manifest-secret",
        expected_key_id="k1",
        expected_export_id=11,
        expected_execution_signature="execution-11",
    ) == (True, "ok")


def test_model_manifest_signature_rejects_wrong_key_or_field_tamper():
    signed = sign_model_manifest(_manifest(), secret="manifest-secret", key_id="k1")
    signed["profile_digest"] = "tampered"

    assert verify_model_manifest(
        signed,
        secret="manifest-secret",
        expected_key_id="k1",
    ) == (False, "manifest_signature_invalid")
    assert verify_model_manifest(
        sign_model_manifest(_manifest(), secret="manifest-secret", key_id="k1"),
        secret="wrong-secret",
        expected_key_id="k1",
    ) == (False, "manifest_signature_invalid")


def test_ed25519_manifest_binds_identity_and_rejects_tamper():
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    signed = sign_model_manifest_ed25519(
        {**_manifest(), "artifact_execution_signature": "execution-11"},
        private_key=private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
        key_id="edge-k1",
    )
    assert verify_model_manifest_ed25519(
        signed,
        public_key=public_pem,
        expected_key_id="edge-k1",
        expected_export_id=11,
        expected_profile_digest="profile-11",
        expected_execution_signature="execution-11",
    ) == (True, "ok")
    signed["profile_digest"] = "tampered"
    assert verify_model_manifest_ed25519(
        signed, public_key=public_pem, expected_key_id="edge-k1"
    ) == (False, "edge_manifest_signature_invalid")


def test_ed25519_manifest_requires_signature_and_key_id():
    assert verify_model_manifest_ed25519(
        _manifest(), public_key="bad", expected_key_id="edge-k1"
    ) == (False, "edge_manifest_signature_missing")


def test_ed25519_manifest_rejects_execution_generation_mismatch():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    signed = sign_model_manifest_ed25519(
        {**_manifest(), "execution_generation": "gen-a"},
        private_key=private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
        key_id="edge-k1",
    )
    assert verify_model_manifest_ed25519(
        signed,
        public_key=public,
        expected_key_id="edge-k1",
        expected_execution_generation="gen-b",
    ) == (False, "edge_manifest_execution_generation_mismatch")


def test_model_manifest_signature_rejects_identity_mismatch():
    signed = sign_model_manifest(_manifest(), secret="manifest-secret", key_id="k1")
    assert verify_model_manifest(
        signed,
        secret="manifest-secret",
        expected_key_id="k1",
        expected_export_id=12,
    ) == (False, "manifest_export_id_mismatch")


def test_manifest_artifacts_detect_file_change_and_require_three_precisions(tmp_path):
    paths = []
    artifacts = []
    for precision in ("fp32", "fp16", "int8"):
        path = tmp_path / f"model_{precision}.bmodel"
        path.write_bytes(precision.encode("ascii"))
        import hashlib

        paths.append(path)
        artifacts.append(
            {
                "role": "model_bmodel",
                "precision": precision.upper(),
                "relative_path": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {"artifacts": artifacts}
    assert verify_manifest_artifacts(manifest, root=tmp_path) == (True, [])
    paths[0].write_bytes(b"changed")
    ok, reasons = verify_manifest_artifacts(manifest, root=tmp_path)
    assert ok is False
    assert "artifact_0_sha256_mismatch" in reasons


def test_manifest_artifacts_reject_missing_precision(tmp_path):
    path = tmp_path / "model_int8.bmodel"
    path.write_bytes(b"int8")
    import hashlib

    ok, reasons = verify_manifest_artifacts(
        {
            "artifacts": [
                {
                    "role": "model_bmodel",
                    "relative_path": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ]
        },
        root=tmp_path,
    )
    assert ok is False
    assert "bmodel_precision_missing:FP16,FP32" in reasons


def test_manifest_artifacts_reject_path_escape_and_invalid_size(tmp_path):
    outside = tmp_path.parent / "outside-model.bmodel"
    outside.write_bytes(b"outside")
    ok, reasons = verify_manifest_artifacts(
        {
            "artifacts": [
                {
                    "role": "model_bmodel",
                    "relative_path": "../outside-model.bmodel",
                    "bytes": "not-a-number",
                    "sha256": "wrong",
                }
            ]
        },
        root=tmp_path,
    )
    assert ok is False
    assert "artifact_0_outside_root" in reasons
