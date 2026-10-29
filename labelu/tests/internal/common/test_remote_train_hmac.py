import importlib.util
import json
from pathlib import Path

import pytest

from labelu.internal.application.service import remote_yolo_train as remote_svc
from labelu.internal.common.remote_train_hmac import (
    HMAC_SIGNATURE_FIELD,
    sign_json_payload,
    verify_json_payload,
)


def _load_agent_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "remote_train" / "agent_worker.py"
    spec = importlib.util.spec_from_file_location("agent_worker_hmac", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_json_signature_round_trip_and_tamper_rejection():
    payload = sign_json_payload({"job_id": 7, "operation_id": "worker:1"}, secret="secret")
    assert verify_json_payload(payload, secret="secret", expected_key_id="v1", required=True)
    payload["job_id"] = 8
    assert not verify_json_payload(payload, secret="secret", expected_key_id="v1", required=True)


def test_local_unsigned_compatibility():
    assert verify_json_payload({"phase": "running"}, secret="", required=False)
    assert not verify_json_payload({"phase": "running"}, secret="", required=True)


def test_production_without_hmac_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(remote_svc.settings, "APP_ENV", "production")
    monkeypatch.setattr(remote_svc.settings, "YOLO_REMOTE_TRAIN_HMAC_SECRET", "")
    with pytest.raises(RuntimeError, match="HMAC"):
        remote_svc._assert_remote_hmac_ready()


def test_prod_alias_also_requires_hmac_secret(monkeypatch):
    monkeypatch.setattr(remote_svc.settings, "APP_ENV", "prod")
    monkeypatch.setattr(remote_svc.settings, "YOLO_REMOTE_TRAIN_HMAC_SECRET", "")
    assert remote_svc._remote_hmac_required() is True
    with pytest.raises(RuntimeError, match="HMAC"):
        remote_svc._assert_remote_hmac_ready()


def test_platform_artifact_tamper_rejected(monkeypatch):
    signed = sign_json_payload(
        {"operation_id": "worker:1", "spec_sha256": "abc"}, secret="secret"
    )
    monkeypatch.setattr(remote_svc.settings, "APP_ENV", "staging")
    monkeypatch.setattr(remote_svc.settings, "YOLO_REMOTE_TRAIN_HMAC_REQUIRED", True)
    monkeypatch.setattr(remote_svc.settings, "YOLO_REMOTE_TRAIN_HMAC_SECRET", "secret")
    assert remote_svc._remote_hmac_metadata_matches(signed)
    signed["spec_sha256"] = "tampered"
    assert not remote_svc._remote_hmac_metadata_matches(signed)


def test_agent_phase_and_weight_are_signed(tmp_path, monkeypatch):
    mod = _load_agent_module()
    monkeypatch.setattr(remote_svc.settings, "APP_ENV", "staging")
    monkeypatch.setattr(remote_svc.settings, "YOLO_REMOTE_TRAIN_HMAC_REQUIRED", True)
    monkeypatch.setattr(remote_svc.settings, "YOLO_REMOTE_TRAIN_HMAC_SECRET", "secret")
    mod._CURRENT_HMAC_SECRET = "secret"
    mod._CURRENT_HMAC_KEY_ID = "v2"
    mod._HMAC_REQUIRED = True
    mod._CURRENT_OPERATION_ID = "worker:2"
    mod._CURRENT_SPEC_SHA256 = "spec"
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    mod.write_phase(job_dir, "running")
    phase = json.loads((job_dir / "phase.json").read_text(encoding="utf-8"))
    assert phase[HMAC_SIGNATURE_FIELD]
    assert phase["hmac_key_id"] == "v2"
    best = job_dir / "best.pt"
    best.write_bytes(b"best-model")
    mod._write_best_signature(best)
    sidecar = json.loads((job_dir / "best.pt.hmac.json").read_text(encoding="utf-8"))
    assert sidecar[HMAC_SIGNATURE_FIELD]
    best.write_bytes(b"tampered-model")
    assert not remote_svc._remote_best_signature_matches(best, best.with_name("best.pt.hmac.json"))
