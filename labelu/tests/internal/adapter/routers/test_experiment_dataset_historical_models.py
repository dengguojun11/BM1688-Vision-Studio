"""Historical model identity uses isolated pytest SQLite and simulated S3 only."""

import json
import asyncio
from datetime import datetime

import pytest

from labelu.internal.application.service import experiment_dataset_import as service
from labelu.internal.application.service import published_model_catalog
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    DatasetCategory,
)
from labelu.internal.domain.models.auto_label_job import AutoLabelJob
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.experiment_dataset_group import (
    ExperimentDatasetGroup,
)
from labelu.internal.domain.models.published_model_catalog import PublishedModelCatalog
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.tests.internal.adapter.routers import (
    test_experiment_dataset_import as fixtures,
)


@pytest.fixture
def historical_import(db, run_around_tests, monkeypatch, tmp_path):
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 50)
    monkeypatch.setattr(service, "schedule_import_job", lambda job_id: None)
    monkeypatch.setattr(
        service.yolo_preannotate,
        "resolve_published_model",
        lambda db, model_id: {
            "ready": False,
            "model_id": model_id,
            "reason": "no published model mapping found",
        },
    )
    monkeypatch.setattr(
        service,
        "schedule_auto_preannotate_for_new_samples",
        lambda **kwargs: pytest.fail("历史模型无PT不得发起推理"),
    )
    # Valid image fixture with a unique content identity for each object.
    original_image = fixtures._fake_image_bytes
    monkeypatch.setattr(
        fixtures, "_fake_image_bytes", lambda name: original_image(name) + name.encode()
    )
    with begin_transaction(db):
        db.add(
            DatasetCategory(
                category_key="illegalParking", name="非机动车违停", status="active"
            )
        )
        db.add(
            AnnotationLabel(
                label_key="三轮车",
                label_value="nonvehicle_tricycle",
                normalized_label_value="nonvehicle_tricycle",
                color="#aabbcc",
                status="active",
                annotation_kind="bbox",
            )
        )
        db.add(
            PublishedModelCatalog(
                public_model_id="536",
                aicm_model_set_id=536,
                aicm_environment_id="history-test",
                name="三轮车检测",
                display_name="三轮车检测",
                source_type="remote_legacy",
                source_system="aicm",
                labels_json=json.dumps(["nonvehicle_tricycle", "person"]),
            )
        )


def _submit(
    client,
    db,
    monkeypatch,
    *,
    package_id,
    count=50,
    offset=0,
    model_id="536",
    label="nonvehicle_tricycle",
    category="",
):
    names = [f"frame-{index + offset:03d}.jpg" for index in range(count)]
    metadata = {
        "samples": [
            {
                "image": {"file_name": name, "width": 32, "height": 24},
                "target_boxes": [],
            }
            for name in names
        ]
    }
    payload = fixtures._payload(package_id)
    prefix = f"strategy-datasets/history-test/{package_id}/"
    payload["dataset"].update(
        prefix=prefix,
        image_prefix=prefix + "images/",
        metadata_key=prefix + "metadata.json",
        s3_uri=f"s3://acity-artifacts/{prefix}",
        metadata_s3_uri=f"s3://acity-artifacts/{prefix}metadata.json",
    )
    storage = db.info.setdefault(
        "historical_s3_fixture",
        {"listings": {}, "metadata_by_key": {}, "object_bytes_by_key": {}},
    )
    storage["listings"][prefix + "images/"] = [
        prefix + "images/" + name for name in names
    ]
    storage["metadata_by_key"][prefix + "metadata.json"] = metadata
    storage["object_bytes_by_key"].update(
        {prefix + "images/" + name: fixtures._fake_image_bytes(name) for name in names}
    )
    fake_client = fixtures._FakeS3Client(**storage)
    monkeypatch.setattr(service, "_build_s3_client", lambda ds: fake_client)
    payload.update(
        source_type="third_party",
        source_system="strategy_evolution",
        sample_count=count,
        dataset_category_key=category,
    )
    payload["target"]["model_id"] = model_id
    payload["labeling"]["labels"] = [label]
    accepted = client.post("/internal/v1/experiment-datasets", json=payload)
    assert accepted.status_code == 202, accepted.text
    service.run_import_job_now(accepted.json()["data"]["import_job_id"])
    db.expire_all()
    return db.query(ExperimentDatasetImport).filter_by(package_id=package_id).one()


def test_history_id_creates_formal_task_without_local_pt_preset_or_category(
    client, db, monkeypatch, historical_import
):
    row = _submit(client, db, monkeypatch, package_id="history-identity-50")
    assert row.status == "COMPLETED", row.last_error
    task_ids = json.loads(row.formal_task_ids_json)
    assert len(task_ids) == 1
    assert row.draft_task_id is None
    task = db.query(Task).filter_by(id=task_ids[0]).one()
    assert task.dataset_category_id is None
    assert task.status == "CONFIGURED"
    config = json.loads(task.config)
    assert "nonvehicle_tricycle" in json.dumps(config)
    assert db.query(TaskSample).filter_by(task_id=task.id).count() == 50
    assert db.query(AutoLabelJob).count() == 0
    group = (
        db.query(ExperimentDatasetGroup).filter_by(id=row.aggregation_group_id).one()
    )
    resolution = json.loads(group.model_resolution_json)
    assert resolution["identity_ready"] is True
    assert resolution["ready"] is False
    assert resolution["preannotation_allowed"] is False
    result = client.get(f"/internal/v1/experiment-datasets/{row.package_id}")
    assert result.status_code == 200
    detail = result.json()["data"]
    assert detail["progress"]["auto_preannotation"]["status"] == "SKIPPED"
    assert "本地没有可用训练权重" in detail["status_detail"]


def test_history_id_still_waits_until_50_distinct_images(
    client, db, monkeypatch, historical_import
):
    row = _submit(
        client, db, monkeypatch, package_id="history-before-threshold", count=49
    )
    assert row.status == "WAITING_FOR_SAMPLES"
    assert db.query(Task).count() == 0
    assert db.query(TaskSample).count() == 0


@pytest.mark.parametrize(
    "model_id,label,reason",
    [
        ("99999", "nonvehicle_tricycle", "no published model mapping found"),
        ("536", "not_a_model_label", "model_labels_do_not_match_requested_labels"),
        ("536", "person", "requested_labels_not_in_label_library"),
    ],
)
def test_unknown_or_incompatible_model_never_creates_formal_task(
    client, db, monkeypatch, historical_import, model_id, label, reason
):
    row = _submit(
        client,
        db,
        monkeypatch,
        package_id=f"history-invalid-{model_id}",
        model_id=model_id,
        label=label,
        category="illegalParking",
    )
    assert row.status == "WAITING_FOR_MODEL", row.last_error
    assert json.loads(row.formal_task_ids_json) == []
    assert row.draft_task_id is not None
    assert db.query(AutoLabelJob).count() == 0
    group = (
        db.query(ExperimentDatasetGroup).filter_by(id=row.aggregation_group_id).one()
    )
    assert json.loads(group.model_resolution_json)["reason"] == reason


def test_local_published_weights_take_priority_over_history_catalog(monkeypatch):
    monkeypatch.setattr(
        service.yolo_preannotate,
        "resolve_published_model",
        lambda db, model_id: {
            "ready": True,
            "model_id": model_id,
            "labels": ["person"],
            "export_id": 8,
        },
    )
    monkeypatch.setattr(
        published_model_catalog,
        "resolve_model",
        lambda *args: pytest.fail("本地发布已匹配，不得转查历史"),
    )
    result = service._resolve_import_model(object(), "603")
    assert result["identity_ready"] is True
    assert result["preannotation_allowed"] is True
    assert result["export_id"] == 8


def test_history_multiple_packages_accumulate_then_append_to_same_task(
    client, db, monkeypatch, historical_import
):
    first = _submit(client, db, monkeypatch, package_id="history-batch-20", count=20)
    assert first.status == "WAITING_FOR_SAMPLES"
    second = _submit(
        client, db, monkeypatch, package_id="history-batch-30", count=30, offset=20
    )
    assert second.status == "COMPLETED", second.last_error
    task_id = json.loads(second.formal_task_ids_json)[0]
    assert db.query(TaskSample).filter_by(task_id=task_id).count() == 50
    third = _submit(
        client, db, monkeypatch, package_id="history-batch-3", count=3, offset=50
    )
    assert third.status == "COMPLETED", third.last_error
    assert json.loads(third.formal_task_ids_json) == [task_id]
    assert db.query(Task).count() == 1
    assert db.query(TaskSample).filter_by(task_id=task_id).count() == 53


def test_history_task_reuses_unique_existing_dataset_contract(
    client, db, monkeypatch, historical_import
):
    owner = db.query(User).first()
    label = db.query(AnnotationLabel).filter_by(label_value="nonvehicle_tricycle").one()
    with begin_transaction(db):
        dataset = Dataset(
            name="已有三轮车数据集",
            owner_id=owner.id,
            created_by=owner.id,
            updated_by=owner.id,
        )
        db.add(dataset)
        db.flush()
        contract = DatasetLabelContract(
            dataset_id=dataset.id,
            revision=1,
            label_ids_json=json.dumps([label.id]),
            labels_snapshot_json=json.dumps(
                [
                    {
                        "id": label.id,
                        "label_key": label.label_key,
                        "label_value": label.label_value,
                        "color": label.color,
                    }
                ]
            ),
            label_set_signature="test-label-signature",
            class_order_signature="test-order-signature",
            status="FROZEN",
        )
        db.add(contract)
        db.flush()
        dataset.dataset_label_contract_id = contract.id
    row = _submit(client, db, monkeypatch, package_id="history-existing-contract")
    assert row.status == "COMPLETED", row.last_error
    task = db.query(Task).filter_by(id=json.loads(row.formal_task_ids_json)[0]).one()
    assert task.dataset_id == dataset.id
    assert task.dataset_label_contract_id == contract.id


def test_historical_model_does_not_bypass_invalid_explicit_category(
    client, db, historical_import
):
    payload = fixtures._payload("history-invalid-category")
    payload["dataset_category_key"] = "does-not-exist"
    payload["target"]["model_id"] = "536"
    payload["labeling"]["labels"] = ["nonvehicle_tricycle"]
    response = client.post("/internal/v1/experiment-datasets", json=payload)
    assert response.status_code == 400
    assert "未知或已停用的数据集分类" in response.text
    assert db.query(ExperimentDatasetImport).count() == 0
    assert db.query(Task).count() == 0


def test_local_published_model_keeps_configured_auto_preannotation(
    client, db, monkeypatch, historical_import
):
    calls = []
    monkeypatch.setattr(
        service.yolo_preannotate,
        "resolve_published_model",
        lambda db, model_id: {
            "ready": True,
            "model_id": model_id,
            "labels": ["nonvehicle_tricycle"],
            "export_id": 1,
        },
    )
    monkeypatch.setattr(
        service,
        "schedule_auto_preannotate_for_new_samples",
        lambda **kwargs: calls.append(kwargs),
    )
    row = _submit(client, db, monkeypatch, package_id="local-published-50")
    assert row.status == "COMPLETED", row.last_error
    assert json.loads(row.formal_task_ids_json)
    assert len(calls) == 1
    assert calls[0]["requested_model_id"] == "536"
    assert len(calls[0]["sample_ids"]) == 50
    assert not json.loads(row.metadata_json)["_labelu_auto_preannotation"][
        "skipped_reason"
    ]


def test_catalog_sync_wakes_draft_and_promotes_same_task_preserving_work(
    client, db, monkeypatch, historical_import
):
    catalog = db.query(PublishedModelCatalog).filter_by(public_model_id="536").one()
    with begin_transaction(db):
        catalog.deleted_at = datetime.now()
    row = _submit(
        client,
        db,
        monkeypatch,
        package_id="history-draft-promotion",
        category="illegalParking",
    )
    assert row.status == "WAITING_FOR_MODEL"
    original_task_id = row.draft_task_id
    sample = db.query(TaskSample).filter_by(task_id=original_task_id).first()
    manual_data = json.dumps(
        {
            "result": {
                "rectTool": {
                    "result": [{"id": "manual-box", "label": "nonvehicle_tricycle"}]
                }
            }
        }
    )
    with begin_transaction(db):
        sample.data = manual_data
        sample.state = "DONE"
        catalog.deleted_at = None
        assert (
            service.wake_waiting_imports(db, status="WAITING_FOR_MODEL", model_id="536")
            == 1
        )
    attachment_ids = {
        item[0]
        for item in db.query(TaskAttachment.id)
        .filter_by(task_id=original_task_id)
        .all()
    }
    service.run_import_job_now(row.id)
    db.expire_all()
    assert row.status == "COMPLETED", row.last_error
    assert json.loads(row.formal_task_ids_json) == [original_task_id]
    assert row.draft_task_id is None
    assert db.query(Task).count() == 1
    assert db.query(TaskSample).filter_by(task_id=original_task_id).count() == 50
    assert db.query(TaskSample).filter_by(id=sample.id).one().data == manual_data
    assert db.query(TaskSample).filter_by(id=sample.id).one().state == "DONE"
    assert {
        item[0]
        for item in db.query(TaskAttachment.id)
        .filter_by(task_id=original_task_id)
        .all()
    } == attachment_ids
    task = db.query(Task).filter_by(id=original_task_id).one()
    assert task.status == "CONFIGURED"
    assert "草稿" not in task.name
    context = json.loads(task.config)["third_party_context"]
    assert context["model_id"] == "536"
    assert context["catalog_id"] == catalog.id
    assert context["aggregation_group_id"] == row.aggregation_group_id


def test_catalog_sync_preserves_incompatible_operator_label_configuration(
    client, db, monkeypatch, historical_import
):
    catalog = db.query(PublishedModelCatalog).filter_by(public_model_id="536").one()
    with begin_transaction(db):
        catalog.deleted_at = datetime.now()
    row = _submit(
        client,
        db,
        monkeypatch,
        package_id="history-draft-conflict",
        category="illegalParking",
    )
    task = db.query(Task).filter_by(id=row.draft_task_id).one()
    with begin_transaction(db):
        other_label = AnnotationLabel(
            label_key="人员",
            label_value="person",
            normalized_label_value="person",
            color="#bbaa22",
            status="active",
            annotation_kind="bbox",
        )
        db.add(other_label)
        db.flush()
    owner = db.query(User).filter_by(id=task.created_by).one()
    asyncio.run(
        service.annotation_taxonomy_service.apply_labels_to_task(
            db, task.id, [other_label.id], owner
        )
    )
    original_config = task.config
    with begin_transaction(db):
        catalog.deleted_at = None
        service.wake_waiting_imports(db, status="WAITING_FOR_MODEL", model_id="536")
    service.run_import_job_now(row.id)
    db.expire_all()
    assert row.status == "WAITING_FOR_MODEL"
    assert row.last_error == "existing_draft_label_contract_conflict"
    assert row.draft_task_id == task.id
    assert json.loads(row.formal_task_ids_json) == []
    assert db.query(Task).count() == 1
    assert task.config == original_config
    assert db.query(TaskSample).filter_by(task_id=task.id).count() == 50
