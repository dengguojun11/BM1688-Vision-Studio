from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

from labelu.internal.application.service import (
    annotation_contract,
    dataset as dataset_service,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.label_contract import (
    class_order_signature,
    label_set_signature,
)
from labelu.internal.common.storage import get_storage_backend
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User


def _seed_two_label_task(db):
    user = db.query(User).filter(User.username == "test@example.com").one()
    labels = [
        AnnotationLabel(
            label_key="安全帽",
            label_value="helmet",
            normalized_label_value="helmet",
            annotation_kind="bbox",
            color="#102030",
            status="active",
            version=1,
        ),
        AnnotationLabel(
            label_key="未戴安全帽",
            label_value="no_helmet",
            normalized_label_value="no_helmet",
            annotation_kind="bbox",
            color="#203040",
            status="active",
            version=1,
        ),
    ]
    with begin_transaction(db):
        db.add_all(labels)
        db.flush()
        snapshot = annotation_contract.labels_snapshot(labels)
        dataset = Dataset(
            owner_id=user.id,
            name="标签子集冻结测试数据集",
            status="ACTIVE",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(dataset)
        db.flush()
        dataset_contract = DatasetLabelContract(
            dataset_id=dataset.id,
            revision=1,
            annotation_kind="bbox",
            label_ids_json=json.dumps([row.id for row in labels]),
            labels_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            label_set_signature=label_set_signature([row.id for row in labels]),
            class_order_signature=class_order_signature(
                [row.label_value for row in labels]
            ),
            status="FROZEN",
            created_by=user.id,
            frozen_by=user.id,
        )
        db.add(dataset_contract)
        db.flush()
        dataset.dataset_label_contract_id = dataset_contract.id
        task = Task(
            name="安全帽双标签训练任务",
            # Historical imported tasks may have no canonical UI tool config;
            # the freeze path still accepts DONE samples and must normalize
            # their nested result string.
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.INPROGRESS.value,
            created_by=user.id,
            updated_by=user.id,
            dataset_id=dataset.id,
            dataset_label_contract_id=dataset_contract.id,
        )
        db.add(task)
        db.flush()
        annotation_contract.create_contract(
            db,
            task=task,
            label_ids=[row.id for row in labels],
            created_by=user.id,
            dataset_label_contract_id=dataset_contract.id,
        )
        for index, (filename, boxes) in enumerate(
            (
                ("helmet.jpg", [("helmet", 10, 10)]),
                ("no-helmet.jpg", [("no_helmet", 20, 20)]),
            ),
            start=1,
        ):
            attachment = TaskAttachment(
                filename=filename,
                url=f"/media/{filename}",
                path=f"uploads/{filename}",
                task_id=task.id,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(attachment)
            db.flush()
            result = {
                "rectTool": {
                    "result": [
                        {
                            "label": label,
                            "x": x,
                            "y": y,
                            "width": 30,
                            "height": 20,
                        }
                        for label, x, y in boxes
                    ]
                }
            }
            # Keep the production historical shape: result is a JSON string.
            db.add(
                TaskSample(
                    inner_id=index,
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    annotated_count=1,
                    data=json.dumps({"result": json.dumps(result, ensure_ascii=False)}),
                    state=SampleState.DONE.value,
                )
            )
        db.flush()
    return user, task, dataset, labels


def _read_manifest(version_response):
    text = get_storage_backend().read_text(version_response.manifest_rel_path)
    return json.loads(text)


def test_label_subset_freeze_filters_historical_result_string_and_preserves_source(db):
    user, task, dataset, labels = _seed_two_label_task(db)
    first_sample = (
        db.query(TaskSample)
        .filter(TaskSample.task_id == task.id)
        .order_by(TaskSample.id)
        .first()
    )
    payload = json.loads(first_sample.data)
    result = json.loads(payload["result"])
    result["polygonTool"] = {
        "result": [
            {
                "label": "helmet",
                "points": [{"x": 10, "y": 10}, {"x": 40, "y": 10}, {"x": 40, "y": 30}],
            },
            {
                "label": "no_helmet",
                "points": [{"x": 50, "y": 10}, {"x": 80, "y": 10}, {"x": 80, "y": 30}],
            },
        ]
    }
    payload["result"] = json.dumps(result, ensure_ascii=False)
    first_sample.data = json.dumps(payload, ensure_ascii=False)
    source_data_before = first_sample.data
    db.commit()

    subset = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db,
            task=task,
            current_user=user,
            selected_label_ids=[labels[0].id],
        )
    )
    subset_manifest = _read_manifest(subset)
    assert subset_manifest["selected_label_ids"] == [labels[0].id]
    assert subset_manifest["annotation_contract"]["class_names"] == ["helmet"]
    subset_rects = json.loads(subset_manifest["samples"][0]["annotation"]["result"])[
        "rectTool"
    ]["result"]
    assert [item["label"] for item in subset_rects] == ["helmet"]
    subset_polygons = json.loads(
        subset_manifest["samples"][0]["annotation"]["result"]
    )["polygonTool"]["result"]
    assert [item["label"] for item in subset_polygons] == ["helmet"]

    original_data = (
        db.query(TaskSample)
        .filter(TaskSample.task_id == task.id)
        .order_by(TaskSample.id)
        .first()
        .data
    )
    assert original_data == source_data_before

    full = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db,
            task=task,
            current_user=user,
            selected_label_ids=[],
        )
    )
    full_manifest = _read_manifest(full)
    assert full.id != subset.id
    assert full_manifest["selected_label_ids"] == [labels[0].id, labels[1].id]
    assert full_manifest["annotation_contract"]["class_names"] == [
        "helmet",
        "no_helmet",
    ]

    # A later source-row edit cannot mutate the already frozen subset object.
    sample = (
        db.query(TaskSample)
        .filter(TaskSample.task_id == task.id)
        .order_by(TaskSample.id)
        .first()
    )
    sample.data = json.dumps(
        {"result": json.dumps({"rectTool": {"result": [{"label": "no_helmet"}]}})}
    )
    db.commit()
    changed_subset = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db,
            task=task,
            current_user=user,
            selected_label_ids=[labels[0].id],
        )
    )
    assert changed_subset.id != subset.id
    assert _read_manifest(changed_subset) != subset_manifest
    assert _read_manifest(subset) == subset_manifest


def test_full_training_reuses_snapshot_for_none_and_empty_selection(db):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()

    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )
    second = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    third = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert first.id == second.id == third.id
    assert db.query(dataset_service.DatasetVersion).count() == 1
    metadata = json.loads(first.metadata_json)
    assert metadata["selection_mode"] == "FULL"
    assert _read_manifest(first)["selected_label_ids"] == metadata[
        "selected_label_ids"
    ]


def test_reuse_reads_historical_manifest_when_metadata_fields_are_missing(db):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )

    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    row.metadata_json = json.dumps(
        {
            "source": "task_export",
            "source_task_ids": [task.id],
        }
    )
    row.dataset_label_contract_id = None
    row.dataset_contract_revision = None
    row.label_set_signature = None
    row.class_order_signature = None
    db.commit()

    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )
    assert replay.id == first.id
    assert db.query(dataset_service.DatasetVersion).count() == 1


def test_historical_snapshot_without_label_evidence_fails_closed(db, monkeypatch):
    """A legacy row with no label proof must not be treated as a full snapshot."""
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )

    original_metadata = json.loads(first.metadata_json)
    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    row.metadata_json = json.dumps(
        {
            "source": "task_export",
            "source_task_ids": [task.id],
            "source_sample_ids": original_metadata["source_sample_ids"],
            # Keep sample evidence intact so this test isolates the missing
            # label-contract evidence rather than failing on sample hashes.
            "source_annotation_signatures": original_metadata[
                "source_annotation_signatures"
            ],
        }
    )
    row.dataset_label_contract_id = None
    row.dataset_contract_revision = None
    row.label_set_signature = None
    row.class_order_signature = None
    row.labels_snapshot_json = "[]"
    manifest = _read_manifest(first)
    manifest.pop("selected_label_ids", None)
    manifest["annotation_contract"] = {"annotation_kind": "bbox"}
    get_storage_backend().save_bytes(
        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        first.manifest_rel_path,
        content_type="application/json",
    )
    db.commit()

    created = []

    async def fake_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=999)

    monkeypatch.setattr(dataset_service, "create_version_from_tasks", fake_create)
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert replay.id == 999
    assert len(created) == 1


def test_full_training_does_not_reuse_stale_contract_revision(db):
    user, task, dataset, labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    first_contract = (
        db.query(DatasetLabelContract)
        .filter_by(id=dataset.dataset_label_contract_id)
        .one()
    )
    replacement = DatasetLabelContract(
        dataset_id=dataset.id,
        revision=int(first_contract.revision) + 1,
        annotation_kind=first_contract.annotation_kind,
        label_ids_json=first_contract.label_ids_json,
        labels_snapshot_json=first_contract.labels_snapshot_json,
        label_set_signature=first_contract.label_set_signature,
        class_order_signature=first_contract.class_order_signature,
        status="FROZEN",
        created_by=user.id,
        frozen_by=user.id,
    )
    db.add(replacement)
    db.flush()
    dataset.dataset_label_contract_id = replacement.id
    db.commit()

    after_contract_change = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert after_contract_change.id != first.id
    assert after_contract_change.dataset_label_contract_id == replacement.id
    assert after_contract_change.dataset_contract_revision == replacement.revision
    assert set(_read_manifest(after_contract_change)["selected_label_ids"]) == {
        labels[0].id,
        labels[1].id,
    }


def test_manifest_digest_fallback_returns_existing_auto_frozen_version(db):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    metadata = json.loads(row.metadata_json)
    metadata["source_annotation_signatures"] = {"1": "stale", "2": "stale"}
    row.metadata_json = json.dumps(metadata)
    db.commit()

    # The reuse metadata check deliberately misses, but the immutable manifest
    # digest still identifies the already-frozen auto snapshot.
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    assert replay.id == first.id
    assert db.query(dataset_service.DatasetVersion).count() == 1


def test_training_rejects_nonempty_all_unknown_label_ids(
    client, testuser_token_headers, db
):
    """Unknown non-empty filters must not fall through to full-label training."""
    _user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()

    response = client.post(
        "/api/v1/training/jobs",
        headers=testuser_token_headers,
        json={
            "task_id": task.id,
            "algorithm_key": "yolo_detection",
            "label_ids": [999999],
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["msg"] == "任务标签筛选参数无效，请刷新标签列表"
    assert db.query(dataset_service.DatasetVersion).count() == 0


def test_malformed_current_contract_label_id_fails_closed(db, monkeypatch):
    user, task, dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )

    contract = (
        db.query(DatasetLabelContract)
        .filter_by(id=dataset.dataset_label_contract_id)
        .one()
    )
    contract.labels_snapshot_json = json.dumps([{"label_id": "malformed"}])
    db.commit()

    created = []

    async def fake_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=1001)

    monkeypatch.setattr(dataset_service, "create_version_from_tasks", fake_create)
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert replay.id == 1001
    assert len(created) == 1
    assert first.id != replay.id


def test_malformed_historical_label_snapshot_fails_closed(db, monkeypatch):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    row.labels_snapshot_json = json.dumps([{"label_id": "malformed"}])
    db.commit()

    created = []

    async def fake_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=1002)

    monkeypatch.setattr(dataset_service, "create_version_from_tasks", fake_create)
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert replay.id == 1002
    assert len(created) == 1


def test_malformed_historical_manifest_sample_id_fails_closed(db, monkeypatch):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    metadata = json.loads(row.metadata_json)
    metadata.pop("source_sample_ids", None)
    metadata.pop("source_annotation_signatures", None)
    row.metadata_json = json.dumps(metadata)
    manifest = _read_manifest(first)
    manifest["samples"][0]["sample_id"] = "malformed"
    get_storage_backend().save_bytes(
        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        first.manifest_rel_path,
        content_type="application/json",
    )
    db.commit()

    created = []

    async def fake_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=1003)

    monkeypatch.setattr(dataset_service, "create_version_from_tasks", fake_create)
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert replay.id == 1003
    assert len(created) == 1


def test_historical_metadata_accepts_canonical_annotation_hashes(db, monkeypatch):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    metadata = json.loads(row.metadata_json)
    canonical_hashes = {}
    for sample in db.query(TaskSample).filter_by(task_id=task.id).all():
        try:
            annotation = json.loads(sample.data or "null")
        except (TypeError, ValueError, json.JSONDecodeError):
            annotation = sample.data
        canonical_hashes[str(sample.id)] = dataset_service.canonical_annotation_sha256(
            annotation
        )
    metadata["source_annotation_signatures"] = canonical_hashes
    row.metadata_json = json.dumps(metadata)
    db.commit()

    created = []

    async def fail_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=1004)

    monkeypatch.setattr(dataset_service, "create_version_from_tasks", fail_create)
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert replay.id == first.id
    assert created == []


def test_historical_manifest_accepts_raw_annotation_hashes(db, monkeypatch):
    user, task, _dataset, _labels = _seed_two_label_task(db)
    db.commit()
    first = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=[]
        )
    )
    row = db.query(dataset_service.DatasetVersion).filter_by(id=first.id).one()
    metadata = json.loads(row.metadata_json)
    metadata.pop("source_annotation_signatures", None)
    row.metadata_json = json.dumps(metadata)
    manifest = _read_manifest(first)
    for item in manifest["samples"]:
        sample = db.query(TaskSample).filter_by(id=int(item["sample_id"])).one()
        item["source_annotation_sha256"] = hashlib.sha256(
            str(sample.data or "").encode("utf-8")
        ).hexdigest()
    get_storage_backend().save_bytes(
        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        first.manifest_rel_path,
        content_type="application/json",
    )
    db.commit()

    created = []

    async def fail_create(*args, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=1005)

    monkeypatch.setattr(dataset_service, "create_version_from_tasks", fail_create)
    replay = asyncio.run(
        dataset_service.ensure_training_version_for_task(
            db, task=task, current_user=user, selected_label_ids=None
        )
    )

    assert replay.id == first.id
    assert created == []
