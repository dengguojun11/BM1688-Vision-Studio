import hashlib
import json
from types import SimpleNamespace

from labelu.internal.application.service import model_optimization
from labelu.internal.domain.models.model_optimization import ModelOptimizationJob


def test_local_optimizer_is_traceable_and_idempotent(db, monkeypatch, tmp_path):
    payload = b"weights"
    relative_path = "models/input.pt"
    input_path = tmp_path / relative_path
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(payload)
    input_sha = hashlib.sha256(payload).hexdigest()

    class Model:
        id = 7
        project_id = 3
        chip_type = "cpu"
        precision = "fp32"
        artifacts = [
            SimpleNamespace(
                id=11,
                artifact_type="pt",
                relative_path=relative_path,
                sha256=input_sha,
                size_bytes=len(payload),
                status="READY",
            )
        ]

    monkeypatch.setattr(
        model_optimization.crud_model_version, "get", lambda _db, _id: Model()
    )
    monkeypatch.setattr(model_optimization.settings, "MEDIA_ROOT", tmp_path)

    def create_artifact(_db, artifact):
        artifact.id = 101
        return artifact

    monkeypatch.setattr(
        model_optimization.crud_model_version, "create_artifact", create_artifact
    )
    first = model_optimization.run_local_sync(
        db,
        project_id=3,
        workflow_id=12,
        model_version_id=7,
        parameters={"quantization": "int8"},
        created_by=1,
        idempotency_key="opt:3:7:1",
    )
    assert first["status"] == "SUCCEEDED"
    assert first["result"]["artifact_created"] is True
    assert first["result"]["execution_mode"] == "local_deterministic"
    assert first["result"]["artifact_type"] == "manifest"
    assert first["result"]["deployable"] is False
    manifest_path = tmp_path / first["result"]["artifact_relative_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["input_artifact"]["sha256"] == input_sha
    assert manifest["deployable"] is False
    replay = model_optimization.run_local_sync(
        db,
        project_id=3,
        workflow_id=12,
        model_version_id=7,
        parameters={"quantization": "int8"},
        created_by=1,
        idempotency_key="opt:3:7:1",
    )
    assert replay["replayed"] is True
    assert replay["job_id"] == first["job_id"]
    assert db.query(ModelOptimizationJob).count() == 1


def test_local_optimizer_rejects_cross_project_model(db, monkeypatch):
    class Model:
        id = 7
        project_id = 99

    monkeypatch.setattr(
        model_optimization.crud_model_version, "get", lambda _db, _id: Model()
    )
    try:
        model_optimization.run_local_sync(
            db,
            project_id=3,
            workflow_id=None,
            model_version_id=7,
            parameters={},
            created_by=1,
            idempotency_key="opt:cross-project",
        )
    except ValueError as exc:
        assert "不属于当前项目" in str(exc)
    else:
        raise AssertionError("cross-project model must be rejected")


def test_local_optimizer_fails_closed_when_input_weight_is_missing(
    db, monkeypatch, tmp_path
):
    class Model:
        id = 7
        project_id = 3
        artifacts = [
            SimpleNamespace(
                id=11,
                artifact_type="pt",
                relative_path="models/missing.pt",
                sha256="0" * 64,
                status="READY",
            )
        ]

    monkeypatch.setattr(
        model_optimization.crud_model_version, "get", lambda _db, _id: Model()
    )
    monkeypatch.setattr(model_optimization.settings, "MEDIA_ROOT", tmp_path)
    try:
        model_optimization.run_local_sync(
            db,
            project_id=3,
            workflow_id=None,
            model_version_id=7,
            parameters={},
            created_by=1,
            idempotency_key="opt:missing-input",
        )
    except ValueError as exc:
        assert "输入制品文件不存在" in str(exc)
    else:
        raise AssertionError("missing input weight must be rejected")


def test_local_optimizer_rejects_input_digest_mismatch(db, monkeypatch, tmp_path):
    relative_path = "models/input.pt"
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b"actual")

    class Model:
        id = 7
        project_id = 3
        artifacts = [
            SimpleNamespace(
                id=11,
                artifact_type="onnx",
                relative_path=relative_path,
                sha256="0" * 64,
                status="READY",
            )
        ]

    monkeypatch.setattr(
        model_optimization.crud_model_version, "get", lambda _db, _id: Model()
    )
    monkeypatch.setattr(model_optimization.settings, "MEDIA_ROOT", tmp_path)
    try:
        model_optimization.run_local_sync(
            db,
            project_id=3,
            workflow_id=None,
            model_version_id=7,
            parameters={},
            created_by=1,
            idempotency_key="opt:digest-mismatch",
        )
    except ValueError as exc:
        assert "SHA256" in str(exc)
    else:
        raise AssertionError("digest mismatch must be rejected")
