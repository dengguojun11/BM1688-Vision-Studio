from __future__ import annotations

import asyncio

import pytest

from labelu.internal.application.command.model_version import (
    CreateModelQualityEvidenceCommand,
    CreateModelVersionCommand,
)
from labelu.internal.application.service import model_version
from labelu.internal.application.service.training_worker_persistence import (
    persist_training_worker_result,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _algorithm(db, key: str = "model_repo_detection") -> AlgorithmDefinition:
    group = AlgorithmGroup(group_key=f"{key}_group", name="测试算法组", status="ACTIVE")
    db.add(group)
    db.flush()
    row = AlgorithmDefinition(
        algorithm_key=key,
        name="测试目标检测",
        task_type="object_detection",
        algorithm_group_id=group.id,
        training_adapter="yolo_detection",
        evaluation_adapter="yolo_detection",
        supported_chips_json='["BM1688"]',
        status="ACTIVE",
    )
    db.add(row)
    db.flush()
    return row


def _frozen_version(db, user_id: int = 1) -> DatasetVersion:
    dataset = Dataset(
        owner_id=user_id,
        name="模型评估数据集",
        status="ACTIVE",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(dataset)
    db.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version_no=1,
        status="FROZEN",
        manifest_rel_path="manifests/model-v1.json",
        manifest_sha256="a" * 64,
        sample_count=12,
        quality_status="PASS",
        review_status="APPROVED",
        created_by=user_id,
        frozen_by=user_id,
    )
    db.add(version)
    db.flush()
    return version


def _user(db) -> User:
    return db.query(User).filter(User.id == 1).one()


def test_model_version_contract_has_no_project_field():
    assert "project_id" not in CreateModelVersionCommand.__fields__
    with pytest.raises(ValueError):
        CreateModelVersionCommand(
            project_id=1,
            algorithm_id=1,
            dataset_version_id=1,
            display_name="不应接受项目参数",
        )

    from labelu.internal.application.command.model_version import CreateModelArtifactCommand

    with pytest.raises(ValueError):
        CreateModelArtifactCommand(
            artifact_type="rknn",
            relative_path="models/legacy.rknn",
            sha256="a" * 64,
        )


def test_model_repository_uses_dataset_lineage_and_publishes_bm1688_bundle(db):
    algorithm = _algorithm(db)
    version = _frozen_version(db)
    user = _user(db)
    db.commit()

    created = asyncio.run(
        model_version.create(
            db,
            CreateModelVersionCommand(
                algorithm_id=algorithm.id,
                dataset_version_id=version.id,
                display_name="安全帽检测候选模型",
                chip_type="BM1688",
            ),
            user,
        )
    )
    assert "project_id" not in created.dict()
    model_id = created.id

    for precision, digest in (("FP32", "b"), ("FP16", "c"), ("INT8", "d")):
        asyncio.run(
            model_version.add_artifact(
                db,
                model_id,
                model_version.CreateModelArtifactCommand(
                    artifact_type="bmodel",
                    relative_path=f"models/{precision.lower()}.bmodel",
                    sha256=digest * 64,
                    chip_type="BM1688",
                    precision=precision,
                ),
                user,
            )
        )

    # PT is required before the training gate, and ONNX is required before
    # conversion; the three BM1688 artifacts are checked at conversion.
    asyncio.run(
        model_version.add_artifact(
            db,
            model_id,
            model_version.CreateModelArtifactCommand(
                artifact_type="pt",
                relative_path="models/best.pt",
                sha256="e" * 64,
            ),
            user,
        )
    )
    asyncio.run(
        model_version.add_evidence(
            db,
            model_id,
            CreateModelQualityEvidenceCommand(gate_type="training", status="PASS"),
            user,
        )
    )
    asyncio.run(
        model_version.add_evidence(
            db,
            model_id,
            CreateModelQualityEvidenceCommand(
                gate_type="generalization", status="PASS"
            ),
            user,
        )
    )
    with pytest.raises(Exception, match="ONNX"):
        asyncio.run(
            model_version.add_evidence(
                db,
                model_id,
                CreateModelQualityEvidenceCommand(
                    gate_type="conversion", status="PASS"
                ),
                user,
            )
        )
    asyncio.run(
        model_version.add_artifact(
            db,
            model_id,
            model_version.CreateModelArtifactCommand(
                artifact_type="onnx",
                relative_path="models/model.onnx",
                sha256="f" * 64,
            ),
            user,
        )
    )
    converted = asyncio.run(
        model_version.add_evidence(
            db,
            model_id,
            CreateModelQualityEvidenceCommand(gate_type="conversion", status="PASS"),
            user,
        )
    )
    assert converted.quality_gate["conversion"] == "PASS"


def test_worker_materializes_model_without_project_lineage(db):
    algorithm = _algorithm(db, "worker_no_project_detection")
    version = _frozen_version(db)
    task = Task(
        name="Worker 无项目任务",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
        project_id=None,
        dataset_id=version.dataset_id,
    )
    db.add(task)
    db.flush()
    job = YoloTrainJob(
        task_id=task.id,
        project_id=None,
        created_by=1,
        status="PENDING",
        job_type="classification",
    )
    db.add(job)
    db.commit()

    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "worker-no-project-1",
        "adapter_key": "classification",
        "stage": "export",
        "status": "SUCCEEDED",
        "worker_id": "worker-no-project",
        "model_lineage": {
            "algorithm_id": algorithm.id,
            "dataset_version_id": version.id,
            "display_name": "Worker 无项目模型",
        },
        "artifacts": [
            {
                "artifact_type": "onnx",
                "path": "models/worker.onnx",
                "sha256": "b" * 64,
            }
        ],
    }
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job.id, result=result) is True
    row = db.query(YoloTrainJob).filter_by(id=job.id).one()
    assert row.status == "COMPLETED"
    from labelu.internal.domain.models.model_version import ModelVersion

    materialized = db.query(ModelVersion).filter_by(training_job_id=job.id).one()
    assert materialized.algorithm_id == algorithm.id
    assert materialized.dataset_version_id == version.id
