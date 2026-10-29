import hashlib
import json
from types import SimpleNamespace

import pytest

from labelu.internal.application.service import model_version as service
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.model_manifest_signature import sign_model_manifest


def test_model_version_manifest_gate_fails_closed_without_production_secret(monkeypatch):
    monkeypatch.setattr(service.settings, "APP_ENV", "production")
    monkeypatch.setattr(service.settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "")
    row = SimpleNamespace(id=7, manifest_json=json.dumps({}), artifacts=[])

    with pytest.raises(LabelUException) as exc_info:
        service._assert_model_version_manifest_ready(row)

    assert "签名密钥" in str(exc_info.value.msg or exc_info.value)


def test_model_version_manifest_gate_rechecks_signed_artifact_file(tmp_path, monkeypatch):
    monkeypatch.setattr(service.settings, "APP_ENV", "production")
    monkeypatch.setattr(service.settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "secret")
    monkeypatch.setattr(service.settings, "YOLO_MODEL_MANIFEST_HMAC_KEY_ID", "k1")
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", tmp_path)
    path = tmp_path / "models" / "model_int8.bmodel"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"bmodel")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = sign_model_manifest(
        {
            "manifest_schema_version": "v1",
            "manifest_id": "model-version:7",
            "model_version_id": 7,
            "execution_signature": "exec-7",
            "artifacts": [],
        },
        secret="secret",
        key_id="k1",
    )
    artifact = SimpleNamespace(
        status="READY",
        relative_path="models/model_int8.bmodel",
        sha256=digest,
        size_bytes=path.stat().st_size,
    )
    row = SimpleNamespace(id=7, manifest_json=json.dumps(manifest), artifacts=[artifact])

    service._assert_model_version_manifest_ready(row)
