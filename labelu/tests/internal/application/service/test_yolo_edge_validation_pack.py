import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

from PIL import Image

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_sample,
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.service.yolo_edge_validation_pack import (
    _class_names_from_training_artifacts,
    edge_validation_pack_committed,
    edge_validation_job_root,
    pack_selected_samples_for_job,
)
import labelu.internal.application.service.yolo_edge_validation_pack as edge_pack
from labelu.internal.application.service.yolo_edge_validation import (
    _assert_edge_result_class_contract,
    _copy_legacy_eval_dataset_into_job,
    create_legacy_edge_ssh_job_for_export,
    run_legacy_edge_ssh_job_sync,
)
from labelu.internal.application.service.yolo_bm1688_export import _job_work_root
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.execution_fencing import claim_execution_lease
from labelu.internal.common.execution_fencing import ExecutionFencingLost
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


def _write_test_image(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), color=color).save(path)


def test_training_artifacts_preserve_model_class_id_order():
    job = SimpleNamespace(
        class_names_json=None,
        dataset_manifest_json=json.dumps(
            {"class_names": ["motorbike", "electricbike", "bicycle"]}
        ),
        metrics_summary=None,
        weights_path=None,
    )
    assert _class_names_from_training_artifacts(job) == [
        "motorbike",
        "electricbike",
        "bicycle",
    ]


def test_edge_result_contract_rejects_signature_mismatch():
    expected = {
        "class_names": ["car"],
        "label_set_signature": "set-signature",
        "class_order_signature": "order-signature",
        "data_yaml_sha256": "yaml-signature",
        "dataset_content_signature": "dataset-signature",
    }
    _assert_edge_result_class_contract(expected, dict(expected))

    mismatched = dict(expected, class_order_signature="wrong-order")
    import pytest

    with pytest.raises(RuntimeError, match="class_order_signature"):
        _assert_edge_result_class_contract(expected, mismatched)


def test_edge_validation_pack_committed_is_strict_ready_gate(tmp_path):
    manifest = tmp_path / "manifest.json"
    row = SimpleNamespace(
        result_json=json.dumps(
            {"pack": {"state": "PREPARING", "manifest_path": str(manifest)}}
        )
    )
    assert edge_validation_pack_committed(row) is False

    manifest.write_text("{}", encoding="utf-8")
    row.result_json = json.dumps(
        {"pack": {"state": "READY", "manifest_path": str(manifest)}}
    )
    assert edge_validation_pack_committed(row) is True

    manifest.write_text(
        json.dumps({"skipped": [{"sample_id": 1, "reason": "image_not_found"}]}),
        encoding="utf-8",
    )
    assert edge_validation_pack_committed(row) is False

    manifest.unlink()
    assert edge_validation_pack_committed(row) is False


def test_edge_validation_pack_gate_distinguishes_legacy_sampling_info_from_blocking_skip(
    tmp_path,
):
    manifest = tmp_path / "manifest.json"
    informational = {
        "copied_count": 2,
        "skipped": [{"label_name": "not_selected.xml", "reason": "image_not_copied"}],
        "blocking_skipped_count": 0,
    }
    informational["manifest_sha256"] = edge_pack._manifest_sha256(informational)
    manifest.write_text(json.dumps(informational), encoding="utf-8")
    row = SimpleNamespace(
        result_json=json.dumps(
            {"pack": {"state": "READY", "manifest_path": str(manifest)}}
        )
    )
    assert edge_validation_pack_committed(row) is True

    blocking = dict(informational, blocking_skipped_count=1)
    blocking["manifest_sha256"] = edge_pack._manifest_sha256(
        {key: value for key, value in blocking.items() if key != "manifest_sha256"}
    )
    manifest.write_text(json.dumps(blocking), encoding="utf-8")
    assert edge_validation_pack_committed(row) is False


def test_pack_selected_samples_for_job_creates_eval_data_and_manifest(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "car", "value": "car"}]},
            }
        ]
    }

    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="edge pack task",
                description="desc",
                tips="tips",
                config=json.dumps(task_config, ensure_ascii=False),
                created_by=current_user.id,
                updated_by=current_user.id,
                media_type="IMAGE",
            ),
        )
        att1 = crud_attachment.create(
            db,
            TaskAttachment(
                filename="16382_中文 空格.png",
                url="/upload/1.png",
                path="upload/edge-pack/16382_中文 空格.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        att2 = crud_attachment.create(
            db,
            TaskAttachment(
                filename="plain.jpg",
                url="/upload/2.jpg",
                path="upload/edge-pack/plain.jpg",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        sample1 = TaskSample(
            task_id=task.id,
            file_id=att1.id,
            created_by=current_user.id,
            updated_by=current_user.id,
            state=SampleState.DONE.value,
            annotated_count=1,
            data=json.dumps(
                {
                    "result": json.dumps(
                        {
                            "rectTool": {
                                "result": [
                                    {
                                        "label": "car",
                                        "x": 10,
                                        "y": 10,
                                        "width": 20,
                                        "height": 20,
                                    }
                                ]
                            }
                        },
                        ensure_ascii=False,
                    )
                },
                ensure_ascii=False,
            ),
        )
        sample2 = TaskSample(
            task_id=task.id,
            file_id=att2.id,
            created_by=current_user.id,
            updated_by=current_user.id,
            state=SampleState.DONE.value,
            annotated_count=0,
            data=json.dumps({"result": "{}"}, ensure_ascii=False),
        )
        samples = crud_sample.batch(db=db, samples=[sample1, sample2])
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=current_user.id,
            model_weights="yolo11n.pt",
            epochs=10,
            imgsz=640,
            category="default",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=train_job,
            status="COMPLETED",
            weights_path="weights/best.pt",
        )
        train_job.class_names_json = json.dumps(["car"])
        train_job.label_set_signature = "edge-label-set"
        train_job.class_order_signature = "edge-class-order"
        train_job.data_yaml_sha256 = "edge-yaml"
        train_job.dataset_content_signature = "edge-dataset"
        db.add(train_job)
        db.flush()
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")
        job = crud_yolo_edge_validation_job.create(
            db,
            user_id=current_user.id,
            task_id=task.id,
            export_id=export_row.id,
            mode="run_and_eval",
            threshold_source="manual",
            sample_ids_json=json.dumps(
                [samples[0].id, samples[1].id], ensure_ascii=False
            ),
            thresholds_json=json.dumps(
                {
                    "conf_thres": 0.3,
                    "nms_thres": 0.7,
                    "match_iou_thres": 0.5,
                    "source": "manual",
                },
                ensure_ascii=False,
            ),
            ai_thresholds_json=None,
            image_count=2,
            labeled_image_count=1,
            unlabeled_image_count=1,
            report_summary="summary",
        )

    _write_test_image(tmp_path / "upload" / "edge-pack" / "16382_中文 空格.png", "red")
    _write_test_image(tmp_path / "upload" / "edge-pack" / "plain.jpg", "blue")

    packed = pack_selected_samples_for_job(
        db,
        export_id=export_row.id,
        job_id=job.id,
        task_id=task.id,
        mode="run_and_eval",
        sample_ids=[samples[0].id, samples[1].id],
        task_config_json=task.config,
    )

    job_root = edge_validation_job_root(export_id=export_row.id, job_id=job.id)
    images_dir = job_root / "eval_data" / "images" / "test"
    xml_dir = job_root / "eval_data" / "labelsxml" / "test"
    packed_images = sorted(p.name for p in images_dir.iterdir())
    assert packed["copied_count"] == 2
    assert packed["labeled_count"] == 1
    assert packed["unlabeled_count"] == 1
    assert packed_images == ["0000_16382.png", "0001_plain.jpg"]
    assert (xml_dir / "0000_16382.xml").is_file()
    assert not (xml_dir / "0001_plain.xml").exists()

    manifest = json.loads((job_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["copied_count"] == 2
    assert manifest["labeled_count"] == 1
    assert manifest["unlabeled_count"] == 1
    assert manifest["class_contract"] == {
        "class_names": ["car"],
        "label_set_signature": "edge-label-set",
        "class_order_signature": "edge-class-order",
        "data_yaml_sha256": "edge-yaml",
        "dataset_content_signature": "edge-dataset",
    }
    assert manifest["file_mappings"][0]["has_annotation"] is True
    assert manifest["file_mappings"][1]["has_annotation"] is False


def test_pack_selected_samples_for_job_materializes_datasource_sample(
    db, tmp_path, monkeypatch
):
    """第三方 S3 样本先进入本地缓存，再进入不可变边端包。"""
    image_path = tmp_path / "cache" / "problem.jpg"
    _write_test_image(image_path, "orange")
    attachment = SimpleNamespace(
        id=77,
        filename="problem.jpg",
        path="expert-feedback/2026/problem.jpg",
        data_source_id=12,
        sha256="source-sha256",
    )
    sample = SimpleNamespace(
        id=88,
        task_id=3,
        file_id=77,
        file=attachment,
        state=SampleState.DONE.value,
        data=json.dumps({"result": "{}"}),
    )
    monkeypatch.setattr(
        edge_pack.crud_sample,
        "get_by_ids",
        lambda db, sample_ids, task_id=None: [sample],
    )
    monkeypatch.setattr(
        edge_pack,
        "class_contract_for_export",
        lambda db, export_id, fallback_class_names=None: {
            "class_names": [],
            "label_set_signature": None,
            "class_order_signature": None,
            "data_yaml_sha256": None,
            "dataset_content_signature": None,
        },
    )
    monkeypatch.setattr(edge_pack, "resolve_attachment_local_path", lambda row: image_path)

    packed = pack_selected_samples_for_job(
        db,
        export_id=1,
        job_id=2,
        task_id=3,
        mode="run_and_eval",
        sample_ids=[88],
        task_config_json="{}",
        root_override=tmp_path / "pack",
    )

    assert packed["copied_count"] == 1
    manifest = json.loads(
        (tmp_path / "pack" / "manifest.json").read_text(encoding="utf-8")
    )
    mapping = manifest["file_mappings"][0]
    assert mapping["source_kind"] == "sample_datasource"
    assert mapping["data_source_id"] == 12
    assert mapping["source_object_key"] == "expert-feedback/2026/problem.jpg"
    assert mapping["source_sha256"] == "source-sha256"
    assert mapping["source_content_sha256"] == mapping["packed_sha256"]
    assert manifest["manifest_sha256"]

    manifest["file_mappings"][0]["packed_sha256"] = "tampered"
    (tmp_path / "pack" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    row = SimpleNamespace(
        result_json=json.dumps(
            {"pack": {"state": "READY", "manifest_path": str(tmp_path / "pack" / "manifest.json")}}
        )
    )
    assert edge_validation_pack_committed(row) is False


def test_pack_selected_samples_for_job_keeps_samples_from_multiple_tasks(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    task_config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "car", "value": "car"}]},
            }
        ]
    }

    with begin_transaction(db):
        tasks = [
            crud_task.create(
                db=db,
                task=Task(
                    name=f"multi-task-edge-pack-{index}",
                    description="desc",
                    tips="tips",
                    config=json.dumps(task_config),
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    media_type="IMAGE",
                ),
            )
            for index in (1, 2)
        ]
        samples = []
        for index, task in enumerate(tasks, start=1):
            attachment = crud_attachment.create(
                db,
                TaskAttachment(
                    filename=f"task-{index}.jpg",
                    url=f"/upload/task-{index}.jpg",
                    path=f"upload/multi-edge/task-{index}.jpg",
                    task_id=task.id,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            samples.extend(
                crud_sample.batch(
                    db=db,
                    samples=[
                        TaskSample(
                            task_id=task.id,
                            file_id=attachment.id,
                            created_by=current_user.id,
                            updated_by=current_user.id,
                            state=SampleState.DONE.value,
                            annotated_count=1,
                            data=json.dumps(
                                {
                                    "result": json.dumps(
                                        {
                                            "rectTool": {
                                                "result": [
                                                    {
                                                        "label": "car",
                                                        "x": 10,
                                                        "y": 10,
                                                        "width": 20,
                                                        "height": 20,
                                                    }
                                                ]
                                            }
                                        }
                                    )
                                }
                            ),
                        )
                    ],
                )
            )
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=tasks[0].id,
            user_id=current_user.id,
            model_weights="yolo11n.pt",
            epochs=10,
            imgsz=640,
            category="default",
        )
        train_job.class_names_json = json.dumps(["car"])
        train_job.label_set_signature = "edge-label-set"
        train_job.class_order_signature = "edge-class-order"
        db.flush()
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=tasks[0].id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        job = crud_yolo_edge_validation_job.create(
            db,
            user_id=current_user.id,
            task_id=tasks[0].id,
            export_id=export_row.id,
            mode="run_and_eval",
            threshold_source="auto_training_plan",
            sample_ids_json=json.dumps([sample.id for sample in samples]),
            thresholds_json="{}",
            ai_thresholds_json=None,
            image_count=2,
            labeled_image_count=2,
            unlabeled_image_count=0,
            report_summary="multi-task",
        )

    for index in (1, 2):
        _write_test_image(
            tmp_path / "upload" / "multi-edge" / f"task-{index}.jpg",
            "red" if index == 1 else "blue",
        )

    sample_task_ids = {int(sample.id): int(sample.task_id) for sample in samples}
    packed = pack_selected_samples_for_job(
        db,
        export_id=export_row.id,
        job_id=job.id,
        task_id=tasks[0].id,
        mode="run_and_eval",
        sample_ids=[sample.id for sample in samples],
        sample_task_ids=sample_task_ids,
        task_config_json=tasks[0].config,
    )

    assert packed["copied_count"] == 2
    assert packed["task_sample_groups"] == {
        str(tasks[0].id): [samples[0].id],
        str(tasks[1].id): [samples[1].id],
    }
    assert {row["task_id"] for row in packed["file_mappings"]} == {
        tasks[0].id,
        tasks[1].id,
    }


def test_pack_selected_samples_for_job_rejects_task_membership_mismatch(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="edge-pack-membership",
                description="desc",
                tips="tips",
                config="{}",
                created_by=current_user.id,
                updated_by=current_user.id,
                media_type="IMAGE",
            ),
        )
        attachment = crud_attachment.create(
            db,
            TaskAttachment(
                filename="membership.jpg",
                url="/upload/membership.jpg",
                path="upload/membership.jpg",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        sample = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    state=SampleState.DONE.value,
                    annotated_count=0,
                    data='{"result":"{}"}',
                )
            ],
        )[0]
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=current_user.id,
            model_weights="yolo11n.pt",
            epochs=1,
            imgsz=640,
            category="default",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )

    import pytest

    with pytest.raises(ValueError, match="task membership"):
        pack_selected_samples_for_job(
            db,
            export_id=export_row.id,
            job_id=999999,
            task_id=task.id,
            mode="run_only",
            sample_ids=[sample.id],
            sample_task_ids={sample.id: task.id + 999},
            task_config_json=task.config,
        )


def test_copy_legacy_eval_dataset_into_job_sanitizes_non_ascii_names(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")

    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="legacy edge pack task",
                description="desc",
                tips="tips",
                config=json.dumps({}, ensure_ascii=False),
                created_by=current_user.id,
                updated_by=current_user.id,
                media_type="IMAGE",
            ),
        )
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=current_user.id,
            model_weights="yolo11n.pt",
            epochs=10,
            imgsz=640,
            category="default",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=train_job,
            status="COMPLETED",
            weights_path="weights/best.pt",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")
        job = crud_yolo_edge_validation_job.create(
            db,
            user_id=current_user.id,
            task_id=task.id,
            export_id=export_row.id,
            mode="run_and_eval",
            threshold_source="system_default",
            sample_ids_json="[]",
            thresholds_json=json.dumps(
                {
                    "conf_thres": 0.25,
                    "nms_thres": 0.7,
                    "match_iou_thres": 0.5,
                    "source": "system_default",
                }
            ),
            ai_thresholds_json=None,
            image_count=0,
            labeled_image_count=0,
            unlabeled_image_count=0,
            report_summary="summary",
        )

    dataset_root = _job_work_root(train_job.id) / "dataset"
    image_src = (
        dataset_root
        / "images"
        / "test"
        / "0056_89663_中文 场景_2026-07-02_154939_frame_0390_1170.0s.jpg"
    )
    xml_src = (
        dataset_root
        / "labelsxml"
        / "test"
        / "0056_89663_中文 场景_2026-07-02_154939_frame_0390_1170.0s.xml"
    )
    _write_test_image(image_src, "purple")
    xml_src.parent.mkdir(parents=True, exist_ok=True)
    xml_src.write_text("<annotation/>", encoding="utf-8")
    (dataset_root / "data.yaml").write_text("names: ['fall']\n", encoding="utf-8")

    packed = _copy_legacy_eval_dataset_into_job(
        export_id=export_row.id,
        job_id=job.id,
        yolo_train_job_id=train_job.id,
        db=db,
    )

    job_root = edge_validation_job_root(export_id=export_row.id, job_id=job.id)
    images_dir = job_root / "eval_data" / "images" / "test"
    xml_dir = job_root / "eval_data" / "labelsxml" / "test"
    packed_images = sorted(p.name for p in images_dir.iterdir())
    packed_xml = sorted(p.name for p in xml_dir.iterdir())

    assert packed["copied_count"] == 1
    assert packed["annotation_file_count"] == 1
    assert packed["positive_image_count"] == 0
    assert packed["negative_image_count"] == 1
    assert packed["unlabeled_image_count"] == 0
    assert packed_images == [
        "0000_0056_89663___2026-07-02_154939_frame_0390_1170.0s.jpg"
    ]
    assert packed_xml == ["0000_0056_89663___2026-07-02_154939_frame_0390_1170.0s.xml"]
    assert all(ord(ch) < 128 for ch in packed_images[0])
    packed_manifest = json.loads(
        (job_root / "manifest.json").read_text(encoding="utf-8")
    )
    assert packed_manifest["labeled_count"] == 1
    assert packed_manifest["positive_image_count"] == 0
    assert packed_manifest["negative_image_count"] == 1
    assert packed_manifest["file_mappings"][0]["has_annotation_file"] is True
    assert packed_manifest["file_mappings"][0]["has_targets"] is False

    reused = create_legacy_edge_ssh_job_for_export(
        db,
        export_id=export_row.id,
        current_user=current_user,
    )
    assert reused.id == job.id
    assert (
        crud_yolo_edge_validation_job.get_latest_active_for_export(
            db, export_id=export_row.id
        ).id
        == job.id
    )

    with begin_transaction(db):
        crud_yolo_edge_validation_job.update(db, job, status="PROCESSING")
    enqueue_calls = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.enqueue_edge_validation_job",
        lambda job_id: enqueue_calls.append(job_id),
    )
    running = run_legacy_edge_ssh_job_sync(
        db,
        export_id=export_row.id,
        current_user=current_user,
    )
    assert running.id == job.id
    assert running.status == "PROCESSING"
    assert enqueue_calls == []


def test_legacy_pack_failure_is_persisted_and_not_active(db, monkeypatch):
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="legacy edge pack failure",
                description="desc",
                tips="tips",
                config="{}",
                created_by=current_user.id,
                updated_by=current_user.id,
                media_type="IMAGE",
            ),
        )
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=current_user.id,
            model_weights="yolo11n.pt",
            epochs=1,
            imgsz=640,
            category="default",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")

    def fail_pack(**kwargs):
        raise RuntimeError("pack build failed")

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._copy_legacy_eval_dataset_into_job",
        fail_pack,
    )
    import pytest

    with pytest.raises(RuntimeError, match="pack build failed"):
        create_legacy_edge_ssh_job_for_export(
            db,
            export_id=export_row.id,
            current_user=current_user,
        )

    failed = (
        db.query(YoloEdgeValidationJob)
        .filter(YoloEdgeValidationJob.export_id == export_row.id)
        .one()
    )
    assert failed.status == "FAILED"
    assert json.loads(failed.result_json)["pack"]["state"] == "FAILED"
    assert crud_yolo_edge_validation_job.get_latest_active_for_export(
        db, export_id=export_row.id
    ) is None


def test_stale_export_execution_token_cannot_create_legacy_edge_job(db):
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="stale edge execution gate",
                description="desc",
                tips="tips",
                config="{}",
                created_by=current_user.id,
                updated_by=current_user.id,
                media_type="IMAGE",
            ),
        )
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=current_user.id,
            model_weights="yolo11n.pt",
            epochs=1,
            imgsz=640,
            category="default",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")

    stale_token = claim_execution_lease(
        db,
        YoloBm1688Export,
        export_row.id,
        owner="stale-edge-worker",
        lease_seconds=1,
    )
    assert stale_token is not None
    current_token = claim_execution_lease(
        db,
        YoloBm1688Export,
        export_row.id,
        owner="current-edge-worker",
        now=datetime.now() + timedelta(seconds=2),
    )
    assert current_token is not None

    monitor = SimpleNamespace(token=stale_token, ensure_current=lambda: None)
    import pytest

    with pytest.raises(ExecutionFencingLost, match="边端验证任务创建前导出执行租约已失效"):
        create_legacy_edge_ssh_job_for_export(
            db,
            export_id=export_row.id,
            current_user=current_user,
            execution_monitor=monitor,
        )

    assert (
        db.query(YoloEdgeValidationJob)
        .filter(YoloEdgeValidationJob.export_id == export_row.id)
        .count()
        == 0
    )


def test_copy_legacy_eval_dataset_uniformly_limits_images(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    monkeypatch.setattr(settings, "YOLO_EDGE_VALIDATION_MAX_IMAGES", 3)
    train_job_id = 8801
    dataset_root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{train_job_id}"
        / "dataset"
    )
    for index in range(7):
        image_path = dataset_root / "images" / "test" / f"image-{index}.jpg"
        label_path = dataset_root / "labelsxml" / "test" / f"image-{index}.xml"
        _write_test_image(image_path, "green")
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text("<annotation/>", encoding="utf-8")

    packed = _copy_legacy_eval_dataset_into_job(
        export_id=881,
        job_id=882,
        yolo_train_job_id=train_job_id,
    )

    assert packed["source_image_count"] == 7
    assert packed["selected_image_count"] == 3
    assert packed["truncated_image_count"] == 4
    job_root = edge_validation_job_root(export_id=881, job_id=882)
    images = sorted(
        path.name for path in (job_root / "eval_data" / "images" / "test").iterdir()
    )
    assert images == ["0000_image-0.jpg", "0001_image-3.jpg", "0002_image-6.jpg"]
    manifest = json.loads((job_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_image_count"] == 7
    assert manifest["selected_image_count"] == 3
    assert manifest["truncated_image_count"] == 4


def test_copy_legacy_eval_dataset_prioritizes_rare_class_coverage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    monkeypatch.setattr(settings, "YOLO_EDGE_VALIDATION_MAX_IMAGES", 3)
    train_job_id = 8802
    dataset_root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{train_job_id}"
        / "dataset"
    )
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "data.yaml").write_text(
        "names:\n  0: common\n  1: rare_a\n  2: rare_b\n",
        encoding="utf-8",
    )
    labels = [
        "common",
        "common",
        "rare_a",
        "common",
        "common",
        "rare_b",
        "common",
        "common",
    ]
    for index, label in enumerate(labels):
        image_path = dataset_root / "images" / "test" / f"image-{index}.jpg"
        label_path = dataset_root / "labelsxml" / "test" / f"image-{index}.xml"
        _write_test_image(image_path, "green")
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text(
            f"<annotation><object><name>{label}</name></object></annotation>",
            encoding="utf-8",
        )

    packed = _copy_legacy_eval_dataset_into_job(
        export_id=883,
        job_id=884,
        yolo_train_job_id=train_job_id,
    )

    job_root = edge_validation_job_root(export_id=883, job_id=884)
    manifest = json.loads((job_root / "manifest.json").read_text(encoding="utf-8"))
    selected_originals = {item["original_name"] for item in manifest["file_mappings"]}
    assert selected_originals == {"image-0.jpg", "image-2.jpg", "image-5.jpg"}
    assert packed["sampling"]["strategy"] == "class_coverage_then_uniform"
    assert manifest["class_coverage"]["coverage_complete"] is True
    assert manifest["class_coverage"]["covered_class_count"] == 3
    assert manifest["class_coverage"]["missing_classes"] == []


def test_copy_legacy_eval_dataset_falls_back_to_plate_pose_dataset(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    train_job_id = 257
    plate_root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{train_job_id}"
        / "plate_dataset"
        / "pose"
    )
    image_src = plate_root / "images" / "test" / "plate scene.jpg"
    label_src = plate_root / "labels" / "test" / "plate scene.txt"
    _write_test_image(image_src, "green")
    label_src.parent.mkdir(parents=True, exist_ok=True)
    label_src.write_text("0 0.5 0.5 0.4 0.2\n", encoding="utf-8")

    packed = _copy_legacy_eval_dataset_into_job(
        export_id=146,
        job_id=91,
        yolo_train_job_id=train_job_id,
    )

    job_root = edge_validation_job_root(export_id=146, job_id=91)
    assert packed["copied_count"] == 1
    assert packed["labeled_count"] == 1
    assert packed["legacy_dataset_kind"] == "plate_pose_dataset"
    assert (
        job_root / "eval_data" / "images" / "test" / "0000_plate_scene.jpg"
    ).is_file()
    assert (
        job_root / "eval_data" / "labels" / "test" / "0000_plate_scene.txt"
    ).is_file()
