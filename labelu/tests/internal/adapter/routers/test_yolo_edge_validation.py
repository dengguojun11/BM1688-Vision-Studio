import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_sample,
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.application.service.yolo_edge_validation_external_cleanup import (
    cleanup_stale_edge_validation_external_attachments,
)
from labelu.internal.application.service.yolo_edge_validation import (
    _build_manual_analysis,
    _build_manual_execution_payload,
    _build_manual_result_summaries,
    _build_manual_validation_dimensions,
    _build_same_batch_pt_bmodel_comparison,
    _run_same_batch_pt_eval,
    _serialize_job_result_json,
)
from labelu.internal.application.service.yolo_edge_validation_pack import (
    edge_validation_job_root,
)


def _write_test_image(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color=color).save(path)


def _prepare_export_and_samples(db: Session, media_root: Path):
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="edge validation task",
                description="description",
                tips="tips",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [{"key": "car", "value": "car"}]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
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
        att1 = crud_attachment.create(
            db,
            TaskAttachment(
                filename="edge_1.png",
                url="/upload/edge_1.png",
                path="upload/edge_1.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        att2 = crud_attachment.create(
            db,
            TaskAttachment(
                filename="edge_2.jpg",
                url="/upload/edge_2.jpg",
                path="upload/edge_2.jpg",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        samples = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
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
                                        "toolName": "rectTool",
                                        "result": [
                                            {
                                                "label": "car",
                                                "x": 10,
                                                "y": 10,
                                                "width": 20,
                                                "height": 20,
                                            }
                                        ],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                ),
                TaskSample(
                    task_id=task.id,
                    file_id=att2.id,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    state=SampleState.DONE.value,
                    annotated_count=0,
                    data=json.dumps({"result": "{}"}, ensure_ascii=False),
                ),
            ],
        )
        task_ref = SimpleNamespace(id=task.id, name=task.name)
        train_job_ref = SimpleNamespace(id=train_job.id)
        export_ref = SimpleNamespace(id=export_row.id)
        sample_refs = [SimpleNamespace(id=sample.id) for sample in samples]
    _write_test_image(media_root / "upload" / "edge_1.png", "red")
    _write_test_image(media_root / "upload" / "edge_2.jpg", "blue")
    return task_ref, train_job_ref, export_ref, sample_refs


def test_yolo_edge_validation_job_create_detail_and_list(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id, samples[1].id],
            "thresholds": {
                "conf_thres": 0.33,
                "nms_thres": 0.72,
                "match_iou_thres": 0.5,
            },
            "threshold_source": "manual",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    assert created["status"] == "PENDING"
    assert created["execution_profile"] == "quick"
    assert created["export_id"] == export_row.id
    assert created["task_id"] == task.id
    assert created["image_count"] == 2
    assert created["labeled_image_count"] == 1
    assert created["unlabeled_image_count"] == 1
    assert created["thresholds"]["threshold_unit"] == "ratio_0_1"
    assert created["thresholds"]["unit"] == "ratio_0_1"
    assert created["thresholds"]["conf_thres"] == 0.33
    assert created["thresholds"]["source"] == "manual"
    assert created["sample_ids"] == [samples[0].id, samples[1].id]
    assert created["result"]["pack"]["copied_count"] == 2

    job_id = created["id"]
    detail_resp = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()["data"]
    assert detail["id"] == job_id
    assert detail["execution_profile"] == "quick"
    assert detail["task_name"] == "edge validation task"
    assert detail["workflow_stage"] == "edge_validation"
    assert detail["edge_result_status"] == "running"
    assert detail["thresholds"]["threshold_unit"] == "ratio_0_1"
    assert detail["report_summary"].startswith("已选 2 张图片")
    assert detail["result"]["pack"]["copied_count"] == 2

    list_resp = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        params={"export_id": export_row.id, "page": 0, "size": 20},
    )
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()
    assert body["meta_data"]["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["execution_profile"] == "quick"
    assert body["data"][0]["id"] == job_id
    assert body["data"][0]["workflow_stage"] == "edge_validation"
    assert body["data"][0]["edge_result_status"] == "running"
    assert body["data"][0]["thresholds"]["threshold_unit"] == "ratio_0_1"
    assert body["data"][0]["thresholds"]["unit"] == "ratio_0_1"
    assert body["data"][0]["thresholds"]["conf_thres"] == 0.33
    assert body["data"][0]["thresholds"]["nms_thres"] == 0.72
    assert body["data"][0]["thresholds"]["match_iou_thres"] == 0.5
    assert body["data"][0]["thresholds"]["source"] == "manual"
    persisted = crud_yolo_edge_validation_job.get(db, job_id)
    persisted_thresholds = json.loads(persisted.thresholds_json)
    assert persisted_thresholds["threshold_unit"] == "ratio_0_1"
    assert persisted_thresholds["unit"] == "ratio_0_1"


def test_yolo_edge_validation_job_preview_is_scoped_to_job(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)
    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id],
            "thresholds": {},
            "threshold_source": "system_default",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    job_id = create_resp.json()["data"]["id"]
    preview = (
        edge_validation_job_root(export_id=export_row.id, job_id=job_id)
        / "server_eval"
        / "bmcv_preview"
        / "result.jpg"
    )
    _write_test_image(preview, "green")

    response = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/preview/result.jpg",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == preview.read_bytes()

    missing = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/preview/missing.jpg",
        headers=testuser_token_headers,
    )
    assert missing.status_code == 404


def test_yolo_edge_validation_visual_evidence_builds_comparison_and_negative_counts(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)
    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id, samples[1].id],
            "thresholds": {"match_iou_thres": 0.5},
            "threshold_source": "manual",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    job_data = create_resp.json()["data"]
    job_id = job_data["id"]
    pack = job_data["result"].get("pack") or {}
    manifest_path = (
        edge_validation_job_root(export_id=export_row.id, job_id=job_id)
        / "packs"
        / pack["pack_signature"]
        / "manifest.json"
    )
    assert pack["state"] == "READY"
    assert manifest_path.is_file()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    packed_names = [item["packed_name"] for item in manifest["file_mappings"]]
    positive_name = next(name for name in packed_names if name.endswith("edge_1.png"))
    negative_name = next(name for name in packed_names if name.endswith("edge_2.jpg"))
    negative_xml_path = (
        root / "eval_data" / "labelsxml" / "test" / f"{Path(negative_name).stem}.xml"
    )
    negative_xml_path.parent.mkdir(parents=True, exist_ok=True)
    negative_xml_path.write_text("<annotation/>", encoding="utf-8")
    positive_xml = ET.parse(
        root / "eval_data" / "labelsxml" / "test" / f"{Path(positive_name).stem}.xml"
    ).getroot()
    positive_box = positive_xml.find("object/bndbox")
    prediction_coords = [
        float(positive_box.findtext(key)) for key in ("xmin", "ymin", "xmax", "ymax")
    ]
    summary_root = root / "server_eval" / "results_bmodel"
    summary_root.mkdir(parents=True, exist_ok=True)
    (summary_root / "visualization_summary.json").write_text(
        json.dumps(
            {
                "image_count": 2,
                "images": [
                    {
                        "image": f"/edge/eval_data/images/test/{positive_name}",
                        "detections": [
                            {
                                "class_id": 0,
                                "class_name": "car",
                                "score": 0.93,
                                "bbox_xyxy": prediction_coords,
                            }
                        ],
                    },
                    {
                        "image": f"/edge/eval_data/images/test/{negative_name}",
                        "detections": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    contract_resp = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/visual-evidence",
        headers=testuser_token_headers,
    )
    assert contract_resp.status_code == 200, contract_resp.text
    contract = contract_resp.json()["data"]
    assert contract["schema_version"] == "bm1688-visual-evidence-v2"
    assert contract["page_size"] == 12
    assert contract["counts"] == {
        "total_images": 2,
        "positive_images": 1,
        "negative_images": 1,
        "unlabeled_images": 0,
        "matched_images": 1,
        "true_negative_images": 1,
        "false_positive_images": 0,
        "false_negative_images": 0,
        "mismatch_images": 0,
        "tp": 1,
        "fp": 0,
        "fn": 0,
    }
    assert all(not key.startswith("_") for item in contract["items"] for key in item)
    positive_item = next(
        item for item in contract["items"] if item["filename"] == positive_name
    )
    negative_item = next(
        item for item in contract["items"] if item["filename"] == negative_name
    )
    assert positive_item["status"] == "matched"
    assert positive_item["gt_count"] == positive_item["prediction_count"] == 1
    assert negative_item["status"] == "true_negative"

    for kind, asset_key in (
        ("output", "output_asset"),
        ("compare", "comparison_asset"),
    ):
        asset_resp = client.get(
            f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/visual-evidence/{kind}/{positive_item[asset_key]}",
            headers=testuser_token_headers,
        )
        assert asset_resp.status_code == 200, asset_resp.text
        assert asset_resp.headers["content-type"].startswith("image/jpeg")
        assert len(asset_resp.content) > 100

    invalid_kind = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/visual-evidence/raw/{positive_item['output_asset']}",
        headers=testuser_token_headers,
    )
    assert invalid_kind.status_code == 404
    unknown_asset = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/visual-evidence/output/unknown.jpg",
        headers=testuser_token_headers,
    )
    assert unknown_asset.status_code == 404


def test_yolo_edge_validation_job_create_rejects_ambiguous_threshold_units(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)
    invalid_payloads = [
        ("thresholds", {"conf_thres": 35}),
        ("thresholds", {"conf_thres": "35%"}),
        ("thresholds", {"nms_thres": 1.01}),
        ("thresholds", {"match_iou_thres": -0.01}),
        ("thresholds", {"conf_thres": True}),
        ("thresholds", {"unit": "percent", "conf_thres": 0.35}),
        (
            "thresholds",
            {
                "threshold_unit": "ratio_0_1",
                "unit": "percent",
                "conf_thres": 0.35,
            },
        ),
        ("thresholds", {"conf_threshold": 0.35}),
        ("ai_thresholds", {"threshold_unit": "percent", "conf_thres": 0.35}),
        ("ai_thresholds", {"match_iou_threshold": 0.5}),
    ]

    for field_name, threshold_payload in invalid_payloads:
        create_resp = client.post(
            f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
            headers=testuser_token_headers,
            json={
                "export_id": export_row.id,
                "task_id": task.id,
                "mode": "run_only",
                "sample_ids": [samples[0].id],
                field_name: threshold_payload,
            },
        )
        assert create_resp.status_code == 422, create_resp.text
        assert create_resp.json()["err_code"] == 30002

    for non_finite in ("NaN", "Infinity", "-Infinity"):
        raw_payload = {
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id],
            "thresholds": {"conf_thres": "__NON_FINITE__"},
        }
        raw_body = json.dumps(raw_payload).replace('"__NON_FINITE__"', non_finite)
        create_resp = client.post(
            f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
            headers={**testuser_token_headers, "Content-Type": "application/json"},
            content=raw_body,
        )
        assert create_resp.status_code == 422, create_resp.text
        assert create_resp.json()["err_code"] == 30002


def test_yolo_edge_validation_job_create_accepts_full_ratio_domain(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id],
            "thresholds": {
                "conf_thres": 0.8,
                "nms_thres": 0.85,
                "match_iou_thres": 0.9,
            },
            "threshold_source": "third_party",
        },
    )

    assert create_resp.status_code == 201, create_resp.text
    thresholds = create_resp.json()["data"]["thresholds"]
    assert thresholds["threshold_unit"] == "ratio_0_1"
    assert thresholds["conf_thres"] == 0.8
    assert thresholds["nms_thres"] == 0.85
    assert thresholds["match_iou_thres"] == 0.9
    assert thresholds["defaulted_fields"] == []


def test_yolo_edge_validation_job_create_accepts_explicit_null_thresholds(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id],
            "thresholds": {
                "conf_thres": None,
                "nms_thres": None,
                "match_iou_thres": None,
            },
            "threshold_source": "manual",
        },
    )

    assert create_resp.status_code == 201, create_resp.text
    thresholds = create_resp.json()["data"]["thresholds"]
    assert thresholds["conf_thres"] == settings.YOLO_SOP_EVAL_CONF_THRES
    assert thresholds["nms_thres"] == 0.7
    assert thresholds["match_iou_thres"] == settings.YOLO_SOP_EVAL_IOU_THRES
    assert thresholds["requested"] == {
        "conf_thres": None,
        "nms_thres": None,
        "match_iou_thres": None,
    }
    assert thresholds["defaulted_fields"] == [
        "conf_thres",
        "nms_thres",
        "match_iou_thres",
    ]


def test_yolo_edge_validation_job_create_with_external_attachments(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        external_attachment = crud_attachment.create(
            db,
            TaskAttachment(
                filename="manual_external.png",
                url="/upload/manual_external.png",
                path="upload/manual_external.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    external_attachment_id = int(external_attachment.id)
    _write_test_image(tmp_path / "upload" / "manual_external.png", "green")

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id],
            "attachment_ids": [external_attachment_id],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    assert created["image_count"] == 2
    assert created["labeled_image_count"] == 1
    assert created["unlabeled_image_count"] == 1
    assert created["sample_ids"] == [samples[0].id]
    assert created["attachment_ids"] == [external_attachment_id]
    assert len(created["external_attachments"]) == 1
    assert created["external_attachments"][0]["id"] == external_attachment_id
    assert created["result"]["pack"]["copied_count"] == 2


def test_yolo_edge_validation_job_create_restores_missing_external_attachment_from_snapshot(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, _ = _prepare_export_and_samples(db, tmp_path)
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        external_attachment = crud_attachment.create(
            db,
            TaskAttachment(
                filename="restore_external.png",
                url="/upload/restore_external.png",
                path="upload/restore_external.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    external_attachment_id = int(external_attachment.id)
    source_path = tmp_path / "upload" / "restore_external.png"
    _write_test_image(source_path, "yellow")

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [],
            "attachment_ids": [external_attachment_id],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    detail = create_resp.json()["data"]
    snapshot = detail["external_attachments"][0]

    with begin_transaction(db):
        crud_attachment.delete(db=db, attachment_ids=[external_attachment_id])
    source_path.unlink(missing_ok=True)

    recreated_path = (
        tmp_path
        / "upload"
        / str(task.id)
        / "edge_validation_external"
        / "restore_history_source.png"
    )
    _write_test_image(recreated_path, "yellow")
    snapshot["url"] = (
        f"http://testserver/api/v1/tasks/attachment/upload/{task.id}/edge_validation_external/restore_history_source.png"
    )
    snapshot["stream_url"] = snapshot["url"].replace("/attachment/", "/partial/")
    snapshot["annotation_display_url"] = snapshot["url"].replace(
        "/attachment/", "/annotation-display/"
    )
    snapshot["path"] = (
        f"upload/{task.id}/edge_validation_external/restore_history_source.png"
    )

    replay_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [],
            "attachment_ids": [external_attachment_id],
            "external_attachments": [snapshot],
        },
    )
    assert replay_resp.status_code == 201, replay_resp.text
    replay_data = replay_resp.json()["data"]
    assert replay_data["image_count"] == 1
    assert replay_data["result"]["pack"]["copied_count"] == 1
    assert len(replay_data["external_attachments"]) == 1
    restored = replay_data["external_attachments"][0]
    assert restored["id"] != external_attachment_id


def test_yolo_edge_validation_job_replay_still_restores_after_external_cleanup(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_EDGE_VALIDATION_EXTERNAL_RETENTION_HOURS", 1)
    task, _, export_row, _ = _prepare_export_and_samples(db, tmp_path)
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        external_attachment = crud_attachment.create(
            db,
            TaskAttachment(
                filename="cleanup_restore_external.png",
                url="/upload/cleanup_restore_external.png",
                path=f"upload/{task.id}/edge_validation_external/cleanup_restore_external.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    attachment_id = int(external_attachment.id)
    source_path = (
        tmp_path
        / "upload"
        / str(task.id)
        / "edge_validation_external"
        / "cleanup_restore_external.png"
    )
    _write_test_image(source_path, "orange")

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [],
            "attachment_ids": [attachment_id],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    detail = create_resp.json()["data"]
    snapshot = detail["external_attachments"][0]
    assert snapshot.get("pack_image_path")
    assert not Path(snapshot["pack_image_path"]).is_absolute()
    assert (Path(settings.MEDIA_ROOT) / snapshot["pack_image_path"]).is_file()

    with begin_transaction(db):
        crud_attachment.delete(db=db, attachment_ids=[attachment_id])

    import datetime as _dt

    with begin_transaction(db):
        raw = (
            db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).first()
        )
        raw.deleted_at = _dt.datetime.now() - _dt.timedelta(hours=2)

    summary = cleanup_stale_edge_validation_external_attachments()
    assert summary["soft_deleted_rows_removed"] == 1
    assert not source_path.exists()

    replay_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [],
            "attachment_ids": [attachment_id],
            "external_attachments": [snapshot],
        },
    )
    assert replay_resp.status_code == 201, replay_resp.text
    replay_data = replay_resp.json()["data"]
    assert replay_data["result"]["pack"]["copied_count"] == 1
    assert len(replay_data["external_attachments"]) == 1
    restored_snapshot = replay_data["external_attachments"][0]
    assert "edge_validation_external" in str(restored_snapshot.get("url") or "")


def test_yolo_edge_validation_recommend_thresholds(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        external_attachment = crud_attachment.create(
            db,
            TaskAttachment(
                filename="recommend_external.png",
                url="/upload/recommend_external.png",
                path="upload/recommend_external.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    external_attachment_id = int(external_attachment.id)
    _write_test_image(tmp_path / "upload" / "recommend_external.png", "green")

    import labelu.internal.application.service.yolo_edge_threshold_recommendation as svc

    captured = {}

    monkeypatch.setattr(
        svc,
        "_build_provider_specs",
        lambda: [("qwen_plus", "https://example.com", "k", "m")],
    )

    def _fake_chat(**kwargs):
        captured["images_count"] = len(kwargs.get("images_jpeg") or [])
        captured["user_text"] = kwargs.get("user_text") or ""
        return json.dumps(
            {
                "recommended_thresholds": {
                    "conf_thres": 0.29,
                    "nms_thres": 0.66,
                    "match_iou_thres": 0.54,
                    "reason": "本次样本中有已标注图片，建议适度放宽召回。",
                    "confidence_level": "high",
                }
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(svc, "chat_multimodal_markdown", _fake_chat)

    resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/recommend-thresholds",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id, samples[1].id],
            "attachment_ids": [external_attachment_id],
        },
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"]
    assert row["fallback_used"] is False
    assert row["recommended_thresholds"]["conf_thres"] == 0.29
    assert row["recommended_thresholds"]["provider_key"] == "qwen_plus"
    assert row["recommended_thresholds"]["confidence_level"] == "high"
    assert captured["images_count"] == 3
    assert '"external_attachment_count": 1' in captured["user_text"]
    assert (
        f'"attachment_ids": [\n    {external_attachment_id}\n  ]'
        in captured["user_text"]
    )


def test_yolo_edge_validation_recommend_thresholds_falls_back_when_ai_output_invalid(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    import labelu.internal.application.service.yolo_edge_threshold_recommendation as svc

    monkeypatch.setattr(
        svc,
        "_build_provider_specs",
        lambda: [("volcengine", "https://example.com", "k", "m")],
    )
    monkeypatch.setattr(svc, "chat_multimodal_markdown", lambda **kwargs: "not-json")

    resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/recommend-thresholds",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[1].id],
        },
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"]
    assert row["fallback_used"] is True
    assert row["recommended_thresholds"]["source"] == "system_fallback"
    assert row["recommended_thresholds"]["provider_key"] == "volcengine"


def test_yolo_edge_validation_recommend_thresholds_with_only_external_attachments(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, _ = _prepare_export_and_samples(db, tmp_path)
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        external_attachment = crud_attachment.create(
            db,
            TaskAttachment(
                filename="only_external.png",
                url="/upload/only_external.png",
                path="upload/only_external.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    external_attachment_id = int(external_attachment.id)
    _write_test_image(tmp_path / "upload" / "only_external.png", "yellow")

    import labelu.internal.application.service.yolo_edge_threshold_recommendation as svc

    monkeypatch.setattr(
        svc,
        "_build_provider_specs",
        lambda: [("qwen_plus", "https://example.com", "k", "m")],
    )
    monkeypatch.setattr(
        svc,
        "chat_multimodal_markdown",
        lambda **kwargs: json.dumps(
            {
                "recommended_thresholds": {
                    "conf_thres": 0.25,
                    "nms_thres": 0.6,
                    "match_iou_thres": 0.67,
                    "reason": "仅基于外部图片做运行可用性判断",
                    "confidence_level": "medium",
                }
            },
            ensure_ascii=False,
        ),
    )

    resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/recommend-thresholds",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "attachment_ids": [external_attachment_id],
        },
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"]
    assert row["fallback_used"] is False
    assert row["recommended_thresholds"]["conf_thres"] == 0.25
    assert row["recommended_thresholds"]["match_iou_thres"] == 0.5
    assert "无标注" in (row["recommended_thresholds"]["reason"] or "")


def test_yolo_edge_validation_job_create_rejects_invalid_sample_ids(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id, 999999],
        },
    )
    assert create_resp.status_code == 400
    assert create_resp.json()["err_code"] == 30003


def test_yolo_edge_validation_job_create_rejects_too_many_samples(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_EDGE_VALIDATION_MAX_IMAGES", 1)
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id, samples[1].id],
        },
    )
    assert create_resp.status_code == 400
    assert create_resp.json()["err_code"] == 30003


def test_yolo_edge_validation_job_run_starts_background_execution(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_only",
            "sample_ids": [samples[0].id, samples[1].id],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    assert created["thresholds"]["threshold_unit"] == "ratio_0_1"
    job_id = created["id"]

    import labelu.internal.application.service.yolo_edge_validation as svc

    started = {}

    def _fake_thread(target=None, args=(), name=None, daemon=None):
        started["target"] = target
        started["args"] = args
        started["name"] = name
        started["daemon"] = daemon
        return SimpleNamespace(start=lambda: target(*args))

    def _fake_run(job_id_value: int):
        row = svc.crud_yolo_edge_validation_job.get(db, job_id_value)
        with begin_transaction(db):
            svc.crud_yolo_edge_validation_job.update(
                db,
                row,
                status="PARTIAL_COMPLETED",
                report_summary="run_only finished",
                completed_at=row.created_at,
                result_json=json.dumps(
                    {
                        **(json.loads(row.result_json) if row.result_json else {}),
                        "execution": {"status": "ok", "mode": "run_only"},
                    },
                    ensure_ascii=False,
                ),
            )

    monkeypatch.setattr(svc.threading, "Thread", _fake_thread)
    monkeypatch.setattr(svc, "_run_manual_job_sync", _fake_run)
    monkeypatch.setattr(svc.edge_ssh_validation, "_edge_ssh_configured", lambda: True)

    run_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/run",
        headers=testuser_token_headers,
    )
    assert run_resp.status_code == 202, run_resp.text
    body = run_resp.json()["data"]
    assert body["id"] == job_id
    assert started["args"] == (job_id,)
    assert started["daemon"] is True

    detail_resp = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()["data"]
    assert detail["status"] == "PARTIAL_COMPLETED"
    assert detail["result"]["execution"]["status"] == "ok"


def test_yolo_edge_validation_job_run_rejects_processing_job(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id, samples[1].id],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    assert created["thresholds"]["threshold_unit"] == "ratio_0_1"
    job_id = created["id"]

    row = crud_yolo_edge_validation_job.get(db, job_id)
    with begin_transaction(db):
        crud_yolo_edge_validation_job.update(db, row, status="PROCESSING")

    import labelu.internal.application.service.yolo_edge_validation as svc

    monkeypatch.setattr(svc.edge_ssh_validation, "_edge_ssh_configured", lambda: True)

    run_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/run",
        headers=testuser_token_headers,
    )
    assert run_resp.status_code == 409
    assert run_resp.json()["err_code"] == 57032


def test_yolo_edge_validation_job_detail_includes_processing_progress(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id, samples[1].id],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    job_id = create_resp.json()["data"]["id"]

    row = crud_yolo_edge_validation_job.get(db, job_id)
    with begin_transaction(db):
        crud_yolo_edge_validation_job.update(
            db,
            row,
            status="PROCESSING",
            report_summary="正在上传样本到边端",
            progress_json=json.dumps(
                {
                    "stage": "upload",
                    "message": "样本包已生成，正在上传到边端设备",
                    "detail": {
                        "remote_host": "10.10.11.26",
                        "remote_job_dir": "/data/job-901",
                    },
                    "updated_at": "2026-07-04T10:11:12",
                    "history": [
                        {
                            "stage": "prepare",
                            "message": "任务已进入边端执行队列",
                            "detail": {"job_id": job_id},
                            "at": "2026-07-04T10:10:00",
                        },
                        {
                            "stage": "upload",
                            "message": "样本包已生成，正在上传到边端设备",
                            "detail": {"remote_host": "10.10.11.26"},
                            "at": "2026-07-04T10:11:12",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        )

    detail_resp = client.get(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}",
        headers=testuser_token_headers,
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()["data"]
    assert detail["status"] == "PROCESSING"
    assert detail["display_status"] == "RUNNING"
    assert detail["workflow_stage"] == "edge_validation"
    assert detail["edge_result_status"] == "running"
    assert detail["progress"]["stage"] == "upload"
    assert detail["progress"]["message"] == "样本包已生成，正在上传到边端设备"
    assert detail["progress"]["detail"]["remote_host"] is None
    assert detail["progress"]["detail"]["remote_job_dir"] is None
    assert detail["progress"]["updated_at"] == "2026-07-04T10:11:12"
    assert len(detail["progress"]["history"]) == 2
    assert detail["progress"]["history"][-1]["stage"] == "upload"
    assert detail["progress"]["history"][-1]["detail"]["remote_host"] is None


def test_build_manual_result_summaries_extracts_prediction_and_eval_fields() -> None:
    data = _build_manual_result_summaries(
        summary={
            "status": "ok",
            "remote_exit_code": 0,
            "files_pulled": 16,
            "remote_host": "192.168.1.8",
        },
        ingest={
            "board_report": {"passed": True},
            "bmcv_infer": {
                "detection_summary": {
                    "image_count": 4,
                    "with_boxes": 3,
                    "total_boxes": 9,
                }
            },
            "bmodel_sop": {
                "status": "ok",
                "metrics": {
                    "map50": 0.82,
                    "map50_95": 0.57,
                    "precision": 0.78,
                    "recall": 0.73,
                    "images": 3,
                },
            },
        },
    )
    assert data["execution_summary"]["status"] == "ok"
    assert data["execution_summary"]["has_bmodel_eval"] is True
    assert data["prediction_summary"]["avg_boxes_per_image"] == 2.25
    assert data["eval_summary"]["map50"] == 0.82


def test_generic_same_batch_pt_eval_uses_edge_threshold_contract(
    tmp_path, monkeypatch
) -> None:
    from labelu.internal.application.service import yolo_sop_model_evaluation as sop

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_BM1688_EXPORT_SUBDIR", "exports")
    weights = (
        tmp_path
        / "yolo_train"
        / "job-308"
        / "plate_detection"
        / "plate_detection_best.pt"
    )
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"pt")
    job_root = edge_validation_job_root(export_id=165, job_id=12600)
    images = job_root / "eval_data" / "images" / "test"
    labels = job_root / "eval_data" / "labelsxml" / "test"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    _write_test_image(images / "0000_plate.jpg", "red")
    (labels / "0000_plate.xml").write_text("<annotation />", encoding="utf-8")
    staging = job_root / "edge_ssh_staging"
    staging.mkdir(parents=True)
    (staging / "run_config.json").write_text(
        json.dumps(
            {
                "imgsz": 640,
                "conf_thres": 0.35,
                "nms_thres": 0.7,
                "match_iou_thres": 0.5,
                "threshold_unit": "ratio_0_1",
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_run(_script, *, env_extra, timeout_sec):
        captured.update(env_extra)
        save_dir = Path(env_extra["EVAL_SAVE_DIR"])
        (save_dir / "eval_summary.json").write_text(
            json.dumps(
                {
                    "map50": 0.992,
                    "map50_95": 0.88,
                    "precision": 0.99,
                    "recall": 0.98,
                    "images": 1,
                }
            ),
            encoding="utf-8",
        )
        (save_dir / "evaluation_report.md").write_text("# PT", encoding="utf-8")
        return True, "ok"

    monkeypatch.setattr(sop, "_run_eval_script", fake_run)
    result = _run_same_batch_pt_eval(
        job_id=12600,
        export_id=165,
        train_job=SimpleNamespace(
            job_type="yolo_detection",
            weights_path=str(weights),
            component_artifacts_json="{}",
            imgsz=640,
        ),
        manifest={
            "copied_count": 1,
            "labeled_count": 1,
            "unlabeled_count": 0,
            "class_names": ["doubleEP"],
            "file_mappings": [
                {
                    "sample_id": 1001,
                    "task_id": 171,
                    "packed_name": "0000_plate.jpg",
                    "has_annotation": True,
                }
            ],
            "class_contract": {"class_order_signature": "class-signature"},
        },
    )

    assert result["status"] == "ok"
    assert result["metrics"]["map50"] == 0.992
    assert result["dataset_evidence"]["image_count"] == 1
    assert result["dataset_evidence"]["pack_signature"]
    assert captured["EVAL_MODEL_TASK"] == "auto"
    assert captured["EVAL_NMS_THRES"] == "0.7"
    assert captured["EVAL_MATCH_IOU_THRES"] == "0.5"
    assert result["eval_params"]["preprocessing_contract"] == {
        "resize": "letterbox",
        "pad_value": 114,
        "color_order": "RGB",
        "scale": 1.0 / 255.0,
    }


def test_generic_same_batch_pt_eval_reads_staged_bundle_dataset(
    tmp_path, monkeypatch
) -> None:
    from labelu.internal.application.service import yolo_sop_model_evaluation as sop

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_BM1688_EXPORT_SUBDIR", "exports")
    weights = tmp_path / "yolo_train" / "job-1" / "train" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"pt")
    job_root = edge_validation_job_root(export_id=1, job_id=2)
    images = job_root / "edge_ssh_staging" / "eval_data" / "images" / "test"
    labels = job_root / "edge_ssh_staging" / "eval_data" / "labelsxml" / "test"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    _write_test_image(images / "0000.jpg", "red")
    (labels / "0000.xml").write_text("<annotation />", encoding="utf-8")
    (job_root / "edge_ssh_staging" / "run_config.json").write_text(
        json.dumps({"imgsz": 640, "conf_thres": 0.35, "nms_thres": 0.7, "match_iou_thres": 0.5}),
        encoding="utf-8",
    )

    def fake_run(_script, *, env_extra, timeout_sec):
        save_dir = Path(env_extra["EVAL_SAVE_DIR"])
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "eval_summary.json").write_text(json.dumps({"map50": 0.8, "images": 1}), encoding="utf-8")
        return True, "ok"

    monkeypatch.setattr(sop, "_run_eval_script", fake_run)
    result = _run_same_batch_pt_eval(
        job_id=2,
        export_id=1,
        train_job=SimpleNamespace(job_type="yolo_detection", weights_path=str(weights), component_artifacts_json="{}", imgsz=640),
        manifest={"copied_count": 1, "labeled_count": 1, "file_mappings": []},
    )

    assert result["status"] == "ok"
    assert result["metrics"]["map50"] == 0.8


def test_same_batch_comparison_targets_fp16_and_rejects_count_mismatch() -> None:
    ingest = {
        "bmodel_sop": {
            "models": {
                "fp16": {
                    "metrics": {
                        "map50": 0.98,
                        "images": 50,
                        "model_type": "plate_detection",
                    }
                }
            }
        }
    }
    pt_eval = {
        "metrics": {"map50": 0.985, "images": 50},
        "dataset_evidence": {"pack_signature": "same-pack"},
        "eval_params": {"imgsz": 640},
    }

    comparison = _build_same_batch_pt_bmodel_comparison(pt_eval=pt_eval, ingest=ingest)
    assert comparison["status"] == "ok"
    assert comparison["precision"] == "fp16"
    assert comparison["same_batch"] is True
    assert "FP16 BModel" in comparison["detail"]

    pt_eval["metrics"]["images"] = 49
    mismatch = _build_same_batch_pt_bmodel_comparison(pt_eval=pt_eval, ingest=ingest)
    assert mismatch["status"] == "skip"
    assert mismatch["same_batch"] is False
    assert mismatch["reason_code"] == "evaluation_image_count_mismatch"
    assert mismatch["detail"] == (
        "PT 与 FP16 的评估图片数不一致（49 vs 50），不能判定部署转换精度损失"
    )


def test_same_batch_comparison_reports_every_precision_and_int8_vs_fp32() -> None:
    ingest = {
        "bmodel_sop": {
            "models": {
                "fp32": {"metrics": {"map50": 0.91, "images": 50}},
                "fp16": {"metrics": {"map50": 0.905, "images": 50}},
                "int8": {"metrics": {"map50": 0.89, "images": 50}},
            }
        }
    }
    pt_eval = {
        "metrics": {"map50": 0.92, "images": 50},
        "dataset_evidence": {"pack_signature": "same-pack"},
        "eval_params": {
            "imgsz": 640,
            "conf_thres": 0.35,
            "nms_thres": 0.7,
            "match_iou_thres": 0.5,
            "preprocessing_contract": {"resize": "letterbox"},
        },
    }

    comparison = _build_same_batch_pt_bmodel_comparison(pt_eval=pt_eval, ingest=ingest)

    assert comparison["primary_precision"] == "int8"
    assert set(comparison["by_precision"]) == {"fp32", "fp16", "int8"}
    assert all(item["same_batch"] for item in comparison["by_precision"].values())
    assert comparison["int8_vs_fp32"]["same_batch"] is True
    assert comparison["int8_vs_fp32"]["fp32_map50"] == 0.91
    assert comparison["int8_vs_fp32"]["int8_map50"] == 0.89
    assert comparison["int8_vs_fp32"]["drop_map50"] == 0.02


def test_manual_validation_dimensions_keep_runtime_and_precision_independent() -> None:
    dimensions = _build_manual_validation_dimensions(
        mode="run_and_eval",
        manifest={
            "labeled_count": 50,
            "class_names": ["common", "rare"],
            "class_coverage": {
                "strategy": "class_coverage_then_uniform",
                "expected_classes": ["common", "rare"],
                "covered_classes": ["common"],
                "missing_classes": ["rare"],
                "selected_distribution": {"common": 12},
            },
        },
        summary={
            "remote_exit_code": 0,
            "environment": {"after": {"environment_ready": True}},
        },
        ingest={
            "bmcv_infer": {"ok": True},
            "board_report": {"skipped": False},
        },
        result_summaries={
            "prediction_summary": {
                "image_count": 50,
                "with_boxes": 40,
                "total_boxes": 72,
            },
            "eval_models": {"int8": {"model_tag": "int8", "map50": 0.81, "images": 50}},
        },
    )

    assert dimensions["environment"]["status"] == "pass"
    assert dimensions["inference"]["status"] == "pass"
    assert dimensions["detection_output"]["status"] == "detected"
    assert dimensions["precision_evaluation"]["status"] == "computed"
    assert dimensions["precision_evaluation"]["ground_truth_source"] == (
        "existing_training_annotations"
    )
    assert dimensions["class_coverage"]["status"] == "partial"
    assert dimensions["class_coverage"]["missing_classes"] == ["rare"]


def test_manual_validation_dimensions_run_only_does_not_require_annotations() -> None:
    dimensions = _build_manual_validation_dimensions(
        mode="run_only",
        manifest={"labeled_count": 0, "unlabeled_count": 12},
        summary={"remote_exit_code": 0},
        ingest={"bmcv_infer": {"ok": True}},
        result_summaries={
            "prediction_summary": {
                "image_count": 12,
                "with_boxes": 4,
                "total_boxes": 7,
            },
            "eval_models": {"int8": {"model_tag": "int8", "map50": 0.81, "images": 50}},
        },
    )

    assert dimensions["inference"]["status"] == "pass"
    assert dimensions["detection_output"]["status"] == "detected"
    assert dimensions["precision_evaluation"]["status"] == "not_requested"
    assert dimensions["precision_evaluation"]["reason"] == "run_only"


def test_manual_validation_dimensions_do_not_call_quality_gate_exit_inference_failure() -> None:
    dimensions = _build_manual_validation_dimensions(
        mode="run_and_eval",
        manifest={"labeled_count": 10, "class_names": ["car"]},
        summary={"remote_exit_code": 1},
        ingest={
            "bmodel_sop": {
                "status": "fail",
                "usability_gate": {"status": "fail", "reasons": ["fixed_eval_labels_missing"]},
            },
            "board_report": {"passed": True, "skipped": False},
        },
        result_summaries={
            "eval_models": {
                tag: {"status": "ok", "images": 10, "map50": 0.75}
                for tag in ("fp32", "fp16", "int8")
            }
        },
    )

    assert dimensions["inference"]["status"] == "pass"


def test_build_manual_result_summaries_uses_plate_runner_detection_metrics() -> None:
    data = _build_manual_result_summaries(
        summary={"status": "ok", "remote_exit_code": 0},
        ingest={
            "bmcv_infer": {
                "detection_summary": {
                    "image_count": 351,
                    "with_boxes": 0,
                    "total_boxes": 0,
                }
            },
            "bmodel_sop": {
                "status": "ok",
                "models": {
                    "fp16": {
                        "status": "ok",
                        "backend": "plate_detection_bm1688",
                        "metrics": {
                            "runtime_ok": True,
                            "objects_detected": True,
                            "model_type": "plate_detection",
                            "input_image_count": 351,
                            "result_image_count": 351,
                            "images_with_detections": 351,
                            "total_detections": 438,
                            "bmrt_test": {"return_code": 0, "elapsed_seconds": 1.058},
                            "inference": {"return_code": 0, "elapsed_seconds": 67.553},
                            "rendered_images": {"status": "ok", "count": 351},
                        },
                    }
                },
            },
        },
    )

    assert data["prediction_summary"] == {
        "image_count": 351,
        "with_boxes": 351,
        "total_boxes": 438,
        "avg_boxes_per_image": 1.2479,
    }
    fp16 = data["eval_models"]["fp16"]
    assert fp16["runtime_ok"] is True
    assert fp16["model_type"] == "plate_detection"
    assert fp16["total_detections"] == 438
    assert fp16["rendered_images"]["count"] == 351


def test_build_manual_result_summaries_preserves_performance_observability() -> None:
    runtime_metrics = {
        "warmup_count_completed": 50,
        "repeat_count_completed": 1000,
        "p99_inference_ms": 8.4,
    }
    resource_usage = {
        "sample_count": 20,
        "cpu_usage": {"avg": 31.2, "max": 48.0},
    }
    data = _build_manual_result_summaries(
        summary={"status": "ok"},
        ingest={
            "bmodel_sop": {
                "status": "ok",
                "metrics": {
                    "map50": 0.82,
                    "runtime_metrics": runtime_metrics,
                    "resource_usage": resource_usage,
                    "environment": {"device_serial": "BM1688-1"},
                    "stability": {"enabled": False},
                    "performance_artifacts": {
                        "latency_samples": "performance/latency_samples.jsonl.gz"
                    },
                },
                "models": {
                    "int8": {
                        "status": "ok",
                        "metrics": {
                            "map50": 0.82,
                            "runtime_metrics": runtime_metrics,
                            "resource_usage": resource_usage,
                            "environment": {"device_serial": "BM1688-1"},
                            "stability": {"enabled": False},
                            "performance_artifacts": {
                                "latency_samples": "performance/latency_samples.jsonl.gz"
                            },
                        },
                    }
                },
            }
        },
    )

    assert data["eval_summary"]["runtime_metrics"]["p99_inference_ms"] == 8.4
    assert (
        data["eval_models"]["int8"]["runtime_metrics"]["repeat_count_completed"] == 1000
    )
    assert data["eval_models"]["int8"]["environment"]["device_serial"] == "BM1688-1"


def test_build_manual_result_summaries_preserves_plate_recognition_business_fields() -> (
    None
):
    data = _build_manual_result_summaries(
        summary={"status": "ok", "remote_exit_code": 0},
        ingest={
            "bmodel_sop": {
                "status": "ok",
                "model_tag": "fp16",
                "model_filename": "plate_recognition_fp16_1b.bmodel",
                "backend": "plate_recognition_bm1688",
                "metrics": {
                    "status": "completed",
                    "runtime_ok": True,
                    "model_type": "plate_recognition",
                    "input_image_count": 351,
                    "result_image_count": 351,
                    "images_with_detections": 351,
                    "total_detections": 437,
                    "plate_numbers": ["A12345", "A12345", "B67890"],
                    "plate_type_counts": {"greenPlate": 300},
                    "color_counts": {"green": 310},
                    "inference": {"elapsed_seconds": 78.653},
                },
            }
        },
    )

    plate = data["plate_recognition_eval"]
    assert plate["runtime_ok"] is True
    assert plate["model_tag"] == "fp16"
    assert plate["input_image_count"] == 351
    assert plate["total_detections"] == 437
    assert plate["recognized_plate_count"] == 3
    assert plate["unique_plate_count"] == 2
    assert plate["plate_number_samples"] == ["A12345", "B67890"]
    assert plate["inference"]["elapsed_seconds"] == 78.653


def test_build_manual_analysis_reads_class_rows_from_manual_results(
    tmp_path, monkeypatch
) -> None:
    export_root = tmp_path / "yolo_bm1688_exports" / "49"
    server_eval = export_root / "server_eval" / "results_cloud"
    manual_eval = export_root / "server_eval" / "results_bmodel"
    server_eval.mkdir(parents=True, exist_ok=True)
    manual_eval.mkdir(parents=True, exist_ok=True)

    pt_report = """## 类别指标

| 类别 | 标签数 | TP | FP | FN | 精确率 | 召回率 | F1 | AP50 |
|------|--------|----|----|----|--------|--------|----|------|
| car | 10 | 8 | 1 | 2 | 0.8889 | 0.8000 | 0.8421 | 0.9000 |
"""
    bm_report = """## 类别指标

| 类别 | 标签数 | TP | FP | FN | 精确率 | 召回率 | F1 | AP50 |
|------|--------|----|----|----|--------|--------|----|------|
| car | 10 | 7 | 1 | 3 | 0.8750 | 0.7000 | 0.7778 | 0.8200 |
"""
    (server_eval / "evaluation_report.md").write_text(pt_report, encoding="utf-8")
    (manual_eval / "evaluation_report.md").write_text(bm_report, encoding="utf-8")

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._export_dir_for_row",
        lambda export_id: export_root,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation._manual_report_root",
        lambda job_id, export_id: export_root / "server_eval",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.edge_ssh_validation.load_server_sop_eval_summary",
        lambda _root: {
            "pt": {
                "metrics": {
                    "map50": 0.90,
                    "map50_95": 0.80,
                    "precision": 0.88,
                    "recall": 0.80,
                    "images": 10,
                },
                "results_dir": str(server_eval),
            }
        },
    )

    analysis = _build_manual_analysis(
        job_id=4,
        export_id=49,
        ingest={
            "bmodel_sop": {
                "status": "ok",
                "metrics": {
                    "map50": 0.82,
                    "map50_95": 0.72,
                    "precision": 0.87,
                    "recall": 0.70,
                    "images": 10,
                },
            }
        },
    )

    assert analysis is not None
    assert analysis["class_drops"]
    assert analysis["class_drops"][0]["name"] == "car"
    assert analysis["class_drops"][0]["pt_ap50"] == 0.9
    assert analysis["class_drops"][0]["bmodel_ap50"] == 0.82


def test_build_manual_execution_payload_omits_raw_ingest_and_keeps_artifact_paths(
    tmp_path, monkeypatch
) -> None:
    import labelu.internal.application.service.yolo_edge_validation as svc

    output_root = tmp_path / "job" / "output"
    report_root = tmp_path / "job" / "server_eval"
    output_root.mkdir(parents=True, exist_ok=True)
    (report_root / "results_bmodel").mkdir(parents=True, exist_ok=True)
    (output_root / "board_report.json").write_text("{}", encoding="utf-8")
    (output_root / "bmcv_infer_summary.json").write_text("{}", encoding="utf-8")
    (output_root / "bmodel_sop_eval.json").write_text("{}", encoding="utf-8")
    (report_root / "results_bmodel" / "evaluation_report.md").write_text(
        "# ok", encoding="utf-8"
    )

    monkeypatch.setattr(
        svc, "_manual_output_root", lambda job_id, export_id: output_root
    )
    monkeypatch.setattr(
        svc, "_manual_report_root", lambda job_id, export_id: report_root
    )

    payload = _build_manual_execution_payload(
        job_id=5,
        export_id=49,
        summary={
            "status": "ok",
            "remote_host": "10.10.11.26",
            "remote_stdout_tail": "x" * 5000,
            "remote_stderr_tail": "y" * 3000,
            "ingest_warnings": ["warn-1", "warn-2"],
            "ingest": {
                "board_report": {"huge": "value"},
                "bmcv_infer": {"huge": "value"},
                "bmodel_sop": {"huge": "value"},
                "runner_summary": {
                    "passed": True,
                    "validation_profile": "plate_detection",
                },
            },
        },
    )

    assert "ingest" not in payload
    assert len(payload["remote_stdout_tail"]) == 1200
    assert len(payload["remote_stderr_tail"]) == 800
    assert payload["artifacts"]["board_report"].endswith("board_report.json")
    assert payload["artifacts"]["bmodel_report"].endswith("evaluation_report.md")
    assert payload["runner_summary"]["passed"] is True


def test_serialize_job_result_json_truncates_large_analysis_payload() -> None:
    large_rows = [
        {"name": f"cls-{idx}", "delta": idx / 1000.0, "note": "z" * 400}
        for idx in range(200)
    ]
    payload = {
        "execution": {
            "status": "ok",
            "remote_stdout_tail": "a" * 10000,
            "remote_stderr_tail": "b" * 6000,
            "artifacts": {"board_report": "/tmp/board_report.json"},
        },
        "analysis": {
            "class_drops": large_rows,
        },
    }

    serialized = _serialize_job_result_json(payload)
    decoded = json.loads(serialized)

    assert len(serialized.encode("utf-8")) < 60000
    assert decoded["analysis"]["class_drop_truncated"] is True
    assert decoded["analysis"]["class_drop_total"] >= 25
    assert len(decoded["analysis"]["class_drops"]) == 25
    assert "note" not in decoded["analysis"]["class_drops"][0]


def test_build_manual_execution_payload_omits_raw_ingest_and_keeps_artifact_paths(
    tmp_path, monkeypatch
) -> None:
    import labelu.internal.application.service.yolo_edge_validation as svc

    output_root = tmp_path / "job" / "output"
    report_root = tmp_path / "job" / "server_eval"
    output_root.mkdir(parents=True, exist_ok=True)
    (report_root / "results_bmodel").mkdir(parents=True, exist_ok=True)
    (output_root / "board_report.json").write_text("{}", encoding="utf-8")
    (output_root / "bmcv_infer_summary.json").write_text("{}", encoding="utf-8")
    (output_root / "bmodel_sop_eval.json").write_text("{}", encoding="utf-8")
    (report_root / "results_bmodel" / "evaluation_report.md").write_text(
        "# ok", encoding="utf-8"
    )

    monkeypatch.setattr(
        svc, "_manual_output_root", lambda job_id, export_id: output_root
    )
    monkeypatch.setattr(
        svc, "_manual_report_root", lambda job_id, export_id: report_root
    )

    payload = _build_manual_execution_payload(
        job_id=5,
        export_id=49,
        summary={
            "status": "ok",
            "remote_host": "10.10.11.26",
            "remote_stdout_tail": "x" * 5000,
            "remote_stderr_tail": "y" * 3000,
            "ingest_warnings": ["warn-1", "warn-2"],
            "ingest": {
                "board_report": {"huge": "value"},
                "bmcv_infer": {"huge": "value"},
                "bmodel_sop": {"huge": "value"},
            },
        },
    )

    assert "ingest" not in payload
    assert len(payload["remote_stdout_tail"]) == 1200
    assert len(payload["remote_stderr_tail"]) == 800
    assert payload["artifacts"]["board_report"].endswith("board_report.json")
    assert payload["artifacts"]["bmodel_report"].endswith("evaluation_report.md")


def test_serialize_job_result_json_truncates_large_analysis_payload() -> None:
    large_rows = [
        {"name": f"cls-{idx}", "delta": idx / 1000.0, "note": "z" * 400}
        for idx in range(200)
    ]
    payload = {
        "execution": {
            "status": "ok",
            "remote_stdout_tail": "a" * 10000,
            "remote_stderr_tail": "b" * 6000,
            "artifacts": {"board_report": "/tmp/board_report.json"},
        },
        "analysis": {
            "class_drops": large_rows,
        },
    }

    serialized = _serialize_job_result_json(payload)
    decoded = json.loads(serialized)

    assert len(serialized.encode("utf-8")) < 60000
    assert decoded["analysis"]["class_drop_truncated"] is True
    assert decoded["analysis"]["class_drop_total"] >= 25
    assert len(decoded["analysis"]["class_drops"]) == 25
    assert "note" not in decoded["analysis"]["class_drops"][0]


def test_serialize_keeps_normal_class_drops_with_large_artifacts() -> None:
    class_drops = [
        {
            "name": f"cls-{idx}",
            "labels": idx + 1,
            "pt_ap50": 0.9,
            "bmodel_ap50": 0.8,
            "ap50_delta": -0.1,
            "severity": "high",
        }
        for idx in range(11)
    ]
    payload = {
        "execution": {
            "status": "ok",
            "artifacts": {"verbose_manifest": "x" * 70000},
        },
        "analysis": {"class_drops": class_drops},
    }

    serialized = _serialize_job_result_json(payload)
    decoded = json.loads(serialized)

    assert len(serialized.encode("utf-8")) < 60000
    assert decoded["analysis"]["class_drop_total"] == 11
    assert decoded["analysis"]["class_drops"] == class_drops
    assert "class_drop_truncated" not in decoded["analysis"]


def test_yolo_edge_validation_job_refine_thresholds(
    client: TestClient, testuser_token_headers: dict, db: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    task, _, export_row, samples = _prepare_export_and_samples(db, tmp_path)

    create_resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs",
        headers=testuser_token_headers,
        json={
            "export_id": export_row.id,
            "task_id": task.id,
            "mode": "run_and_eval",
            "sample_ids": [samples[0].id, samples[1].id],
            "thresholds": {
                "conf_thres": 0.31,
                "nms_thres": 0.69,
                "match_iou_thres": 0.5,
            },
            "threshold_source": "ai_recommended",
            "ai_thresholds": {
                "conf_thres": 0.31,
                "nms_thres": 0.69,
                "match_iou_thres": 0.5,
                "source": "ai_recommended",
            },
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()["data"]
    assert created["thresholds"]["threshold_unit"] == "ratio_0_1"
    assert created["ai_thresholds"]["threshold_unit"] == "ratio_0_1"
    job_id = created["id"]

    row = crud_yolo_edge_validation_job.get(db, job_id)
    result_json = json.loads(row.result_json or "{}")
    result_json["execution"] = {"status": "ok"}
    result_json["execution_summary"] = {
        "status": "ok",
        "remote_exit_code": 0,
        "files_pulled": 12,
        "has_board_report": True,
        "has_bmcv_infer": True,
        "has_bmodel_eval": True,
    }
    result_json["prediction_summary"] = {
        "image_count": 2,
        "with_boxes": 2,
        "total_boxes": 4,
        "avg_boxes_per_image": 2.0,
    }
    result_json["eval_summary"] = {
        "status": "ok",
        "map50": 0.79,
        "map50_95": 0.52,
        "precision": 0.74,
        "recall": 0.71,
        "images": 1,
    }
    with begin_transaction(db):
        crud_yolo_edge_validation_job.update(
            db,
            row,
            status="COMPLETED",
            result_json=json.dumps(result_json, ensure_ascii=False),
        )

    import labelu.internal.application.service.yolo_edge_threshold_refinement as refine_svc

    monkeypatch.setattr(
        refine_svc,
        "_build_provider_specs",
        lambda: [("qwen_plus", "http://x", "k", "m")],
    )
    monkeypatch.setattr(
        refine_svc,
        "_load_refinement_images",
        lambda db, task_id, request_payload, max_images: [],
    )
    monkeypatch.setattr(
        refine_svc,
        "request_edge_threshold_recommendation",
        lambda **kwargs: SimpleNamespace(
            recommended_thresholds=refine_svc.EdgeThresholdsResponse(
                conf_thres=0.28,
                nms_thres=0.65,
                match_iou_thres=0.52,
                source="ai_recommended",
                reason="根据本次实测结果建议下调 conf 与 nms",
                confidence_level="high",
                provider_key="qwen_plus",
            ),
            fallback_used=False,
        ),
    )

    resp = client.post(
        f"{settings.API_V1_STR}/yolo-edge-validation/jobs/{job_id}/refine-thresholds",
        headers=testuser_token_headers,
        json={
            "max_images": 4,
            "include_visual_evidence": False,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["stage"] == "post_validation_refine"
    assert data["base_thresholds"]["conf_thres"] == 0.31
    assert data["refined_thresholds"]["source"] == "ai_refined"
    assert data["delta_summary"]["conf_delta"] == -0.03
