from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from labelu.internal.application.service import experiment_dataset_import as service
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.experiment_dataset_group import ExperimentDatasetGroup
from labelu.internal.domain.models.experiment_dataset_import import ExperimentDatasetImport
from labelu.internal.domain.models.user import User


def _payload(package_id: str) -> dict:
    return {
        "schema_version": "v1",
        "package_id": package_id,
        "source_type": "third_party",
        "source_system": "strategy_evolution",
        "dataset_type": "reviewed_false_positive_daily",
        "dataset_date": "2026-10-27",
        "window_start": "2026-10-27 00:00:00",
        "window_end": "2026-10-28 00:00:00",
        "dataset": {
            "s3_uri": "s3://bucket/prefix/",
            "bucket": "bucket",
            "prefix": "prefix/",
            "image_prefix": "prefix/images/",
            "metadata_key": "prefix/metadata.json",
        },
        "labeling": {
            "required": True,
            "media_type": "IMAGE",
            "annotation_type": "bbox",
            "labels": [],
        },
        "target": {"event_type": "helmet", "model_id": ""},
        "sample_count": 1,
        "target_boxes_required": True,
        "dataset_category_key": "",
    }


def _failed_row(db: Session, owner: User, package_id: str = "failed-source") -> ExperimentDatasetImport:
    row = ExperimentDatasetImport(
        package_id=package_id,
        source_type="third_party",
        source_system="strategy_evolution",
        dataset_type="reviewed_false_positive_daily",
        dataset_date="2026-10-27",
        event_type="helmet",
        model_id="",
        target_signature="prefix",
        source_payload_json=json.dumps(_payload(package_id), ensure_ascii=False),
        status=service.IMPORT_STATUS_FAILED,
        formal_task_ids_json="[]",
        declared_sample_count=1,
        last_error="metadata.json 连接超时",
        created_by=owner.id,
        updated_by=owner.id,
    )
    with begin_transaction(db):
        db.add(row)
        db.flush()
    return row


def test_retry_route_requires_system_admin(client, testuser_token_headers):
    response = client.post(
        "/api/v1/evolution/imports/1/retry",
        json={"reason": "再次执行"},
        headers=testuser_token_headers,
    )
    assert response.status_code == 403, response.text


def test_retry_creates_new_attempt_and_repeated_post_is_idempotent(
    client, testadmin_token_headers, db: Session, monkeypatch
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner)
    source_id = int(source.id)

    monkeypatch.setattr(service, "schedule_import_job", lambda _job_id: None)
    monkeypatch.setattr(
        service,
        "_resolve_effective_dataset_category",
        lambda _db, _cmd: (None, None, {}),
    )

    first = client.post(
        f"/api/v1/evolution/imports/{source_id}/retry",
        json={"reason": "修复 S3 配置后再次执行"},
        headers=testadmin_token_headers,
    )
    assert first.status_code == 202, first.text
    first_data = first.json()["data"]
    assert first_data["status"] == service.IMPORT_STATUS_PENDING
    assert first_data["retry_of_import_job_id"] == source_id
    assert first_data["retry_attempt"] == 1
    assert first_data["retry_reason"] == "修复 S3 配置后再次执行"
    assert first_data["package_id"] != "failed-source"
    assert "重新提交" in first_data["message"]

    db.expire_all()
    original = db.query(ExperimentDatasetImport).filter_by(id=source_id).one()
    retry = (
        db.query(ExperimentDatasetImport)
        .filter(ExperimentDatasetImport.id == first_data["import_job_id"])
        .one()
    )
    assert original.status == service.IMPORT_STATUS_FAILED
    assert original.deleted_at is None
    marker = json.loads(retry.source_payload_json)["_labelu_retry"]
    assert marker == {
        "attempt": 1,
        "reason": "修复 S3 配置后再次执行",
        "source_import_job_id": source_id,
    }

    second = client.post(
        f"/api/v1/evolution/imports/{source_id}/retry",
        json={"reason": "重复点击"},
        headers=testadmin_token_headers,
    )
    assert second.status_code == 202, second.text
    second_data = second.json()["data"]
    assert second_data["import_job_id"] == first_data["import_job_id"]
    assert second_data["package_id"] == first_data["package_id"]
    assert "已有重试任务" in second_data["message"]
    assert (
        db.query(ExperimentDatasetImport)
        .filter(ExperimentDatasetImport.id != source_id)
        .count()
        == 1
    )


def test_legacy_failed_source_is_attached_to_retry_group_before_worker_recompute(
    client, testadmin_token_headers, db: Session, monkeypatch
):
    """A pre-group failure keeps its declared count when first retried."""
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner, package_id="legacy-no-group")

    monkeypatch.setattr(service, "schedule_import_job", lambda _job_id: None)
    monkeypatch.setattr(
        service,
        "_resolve_effective_dataset_category",
        lambda _db, _cmd: (None, None, {}),
    )

    first = client.post(
        f"/api/v1/evolution/imports/{source.id}/retry",
        json={"reason": "修复配置后重试"},
        headers=testadmin_token_headers,
    )
    assert first.status_code == 202, first.text
    retry_job_id = first.json()["data"]["import_job_id"]

    db.expire_all()
    source = db.query(ExperimentDatasetImport).filter_by(id=source.id).one()
    group = (
        db.query(ExperimentDatasetGroup)
        .filter(ExperimentDatasetGroup.id == source.aggregation_group_id)
        .one()
    )
    assert source.aggregation_group_id == group.id
    assert group.declared_sample_count == source.declared_sample_count == 1

    def fake_inspect(_db, *, row):
        command = service.CreateExperimentDatasetImportCommand.model_validate(
            json.loads(row.source_payload_json)
        )
        return command, {"samples": []}, 0, 0

    monkeypatch.setattr(service, "_inspect_import_row", fake_inspect)
    service.run_import_job_now(retry_job_id)

    db.expire_all()
    group = db.query(ExperimentDatasetGroup).filter_by(id=group.id).one()
    assert group.status == service.GROUP_STATUS_WAITING_FOR_SAMPLES
    assert group.declared_sample_count == 1


def test_retry_reuses_a_completed_attempt_instead_of_creating_a_duplicate(
    client, testadmin_token_headers, db: Session, monkeypatch
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner, package_id="failed-completed-retry")

    monkeypatch.setattr(service, "schedule_import_job", lambda _job_id: None)
    monkeypatch.setattr(
        service,
        "_resolve_effective_dataset_category",
        lambda _db, _cmd: (None, None, {}),
    )
    first = client.post(
        f"/api/v1/evolution/imports/{source.id}/retry",
        headers=testadmin_token_headers,
    )
    assert first.status_code == 202, first.text
    first_data = first.json()["data"]
    retry_row = (
        db.query(ExperimentDatasetImport)
        .filter_by(id=first_data["import_job_id"])
        .one()
    )
    with begin_transaction(db):
        retry_row.status = service.IMPORT_STATUS_COMPLETED
        db.add(retry_row)

    second = client.post(
        f"/api/v1/evolution/imports/{source.id}/retry",
        headers=testadmin_token_headers,
    )
    assert second.status_code == 202, second.text
    second_data = second.json()["data"]
    assert second_data["import_job_id"] == first_data["import_job_id"]
    assert second_data["package_id"] == first_data["package_id"]
    assert second_data["status"] == service.IMPORT_STATUS_COMPLETED
    assert "无需重复执行" in second_data["message"]
    assert (
        db.query(ExperimentDatasetImport)
        .filter(ExperimentDatasetImport.id != source.id)
        .count()
        == 1
    )


def test_archive_rejects_a_source_with_an_active_retry(
    client, testadmin_token_headers, db: Session, monkeypatch
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner, package_id="failed-with-active-retry")

    monkeypatch.setattr(service, "schedule_import_job", lambda _job_id: None)
    monkeypatch.setattr(
        service,
        "_resolve_effective_dataset_category",
        lambda _db, _cmd: (None, None, {}),
    )
    retry = client.post(
        f"/api/v1/evolution/imports/{source.id}/retry",
        headers=testadmin_token_headers,
    )
    assert retry.status_code == 202, retry.text

    archived = client.delete(
        f"/api/v1/evolution/imports/{source.id}",
        headers=testadmin_token_headers,
    )
    assert archived.status_code == 409, archived.text
    assert "已有重试任务" in archived.text


def test_retry_skips_package_ids_of_archived_attempts(
    client, testadmin_token_headers, db: Session, monkeypatch
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner, package_id="failed-archived-retry")

    monkeypatch.setattr(service, "schedule_import_job", lambda _job_id: None)
    monkeypatch.setattr(
        service,
        "_resolve_effective_dataset_category",
        lambda _db, _cmd: (None, None, {}),
    )
    first = client.post(
        f"/api/v1/evolution/imports/{source.id}/retry",
        headers=testadmin_token_headers,
    )
    assert first.status_code == 202, first.text
    first_data = first.json()["data"]

    retry_row = (
        db.query(ExperimentDatasetImport)
        .filter_by(id=first_data["import_job_id"])
        .one()
    )
    with begin_transaction(db):
        retry_row.status = service.IMPORT_STATUS_FAILED
        retry_row.deleted_at = datetime.now()
        db.add(retry_row)

    second = client.post(
        f"/api/v1/evolution/imports/{source.id}/retry",
        headers=testadmin_token_headers,
    )
    assert second.status_code == 202, second.text
    second_data = second.json()["data"]
    assert second_data["retry_attempt"] == 2
    assert second_data["package_id"] != first_data["package_id"]


def test_archive_failed_import_is_soft_delete_and_rejects_created_task(
    client, testadmin_token_headers, db: Session
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner, package_id="failed-archive")
    response = client.delete(
        f"/api/v1/evolution/imports/{source.id}",
        headers=testadmin_token_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["archived"] is True
    assert "已归档" in response.json()["data"]["message"]
    db.expire_all()
    archived_row = db.query(ExperimentDatasetImport).filter_by(id=source.id).one()
    assert archived_row.deleted_at is not None
    admin = db.query(User).filter(User.username == "admin@123.com").one()
    assert archived_row.updated_by == admin.id

    created = _failed_row(db, owner, package_id="failed-created")
    with begin_transaction(db):
        created.formal_task_ids_json = "[999]"
        db.add(created)
    rejected = client.delete(
        f"/api/v1/evolution/imports/{created.id}",
        headers=testadmin_token_headers,
    )
    assert rejected.status_code == 409, rejected.text
    assert "已创建任务" in rejected.text


def test_repeated_terminal_failed_notification_explains_new_package_requirement(
    client, db: Session
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    source = _failed_row(db, owner, package_id="failed-notify")
    response = client.post(
        "/internal/v1/experiment-datasets",
        json=_payload(source.package_id),
    )
    assert response.status_code == 202, response.text
    assert "已有失败记录" in response.json()["data"]["message"]
    assert "换 package_id" in response.json()["data"]["message"]
