from __future__ import annotations

from types import SimpleNamespace

from labelu.internal.application.service import auto_training_plan as service
from labelu.internal.common.config import settings
from labelu.internal.domain.models.auto_training_governance import (
    QualityPolicyApproval,
    TrainingEvaluationDataset,
)
from labelu.internal.domain.models.user import User


def test_evaluation_dataset_list_forwards_search_and_pagination(
    client, testadmin_token_headers, monkeypatch
):
    captured = {}

    def fake_list(db, *, user, page, size, q):
        captured.update(page=page, size=size, q=q)
        return [{"id": 9, "name": "needle"}], 17

    monkeypatch.setattr(service, "list_evaluation_datasets", fake_list)
    response = client.get(
        f"{settings.API_V1_STR}/training-evaluation-datasets" "?q=needle&page=2&size=5",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    assert captured == {"page": 2, "size": 5, "q": "needle"}
    body = response.json()
    assert body["data"] == [{"id": 9, "name": "needle"}]
    assert body["meta_data"] == {"total": 17, "page": 2, "size": 1}


def test_quality_approval_list_supports_history_and_workspace_views(
    client, testadmin_token_headers, monkeypatch
):
    captured = []

    def fake_history(db, *, user, page, size, q):
        captured.append(("history", page, size, q))
        return [{"id": 3, "policy_name": "needle"}], 8

    def fake_workspace(db, *, user, page, size, q):
        captured.append(("workspace", page, size, q))
        return [{"policy_name": "needle", "approval_history": []}], 4

    monkeypatch.setattr(service, "list_quality_approvals", fake_history)
    monkeypatch.setattr(service, "list_quality_approval_workspace", fake_workspace)

    history = client.get(
        f"{settings.API_V1_STR}/quality-policy-approvals" "?q=needle&page=1&size=10",
        headers=testadmin_token_headers,
    )
    workspace = client.get(
        f"{settings.API_V1_STR}/quality-policy-approvals"
        "?view=workspace&q=needle&page=2&size=3",
        headers=testadmin_token_headers,
    )

    assert history.status_code == 200, history.text
    assert workspace.status_code == 200, workspace.text
    assert captured == [
        ("history", 1, 10, "needle"),
        ("workspace", 2, 3, "needle"),
    ]
    assert history.json()["meta_data"] == {"total": 8, "page": 1, "size": 1}
    assert workspace.json()["meta_data"] == {"total": 4, "page": 2, "size": 1}


def test_evaluation_dataset_service_filters_and_slices_in_sql(db):
    user = db.query(User).filter(User.username == "test@example.com").one()
    db.add_all(
        [
            TrainingEvaluationDataset(
                owner_id=user.id,
                name="needle-validation",
                dataset_kind="VALIDATION",
                series_type="yolo_detection",
                dataset_category_id=1,
                label_set_signature="a" * 64,
                version_no=1,
                status="APPROVED",
                manifest_rel_path="eval/needle.json",
                manifest_sha256="1" * 64,
                sample_count=4,
                grouping_policy_json="{}",
                created_by=user.id,
            ),
            TrainingEvaluationDataset(
                owner_id=user.id,
                name="other-validation",
                dataset_kind="VALIDATION",
                series_type="yolo_detection",
                dataset_category_id=1,
                label_set_signature="b" * 64,
                version_no=1,
                status="DRAFT",
                manifest_rel_path="eval/other.json",
                manifest_sha256="2" * 64,
                sample_count=2,
                grouping_policy_json="{}",
                created_by=user.id,
            ),
        ]
    )
    db.flush()

    rows, total = service.list_evaluation_datasets(
        db, user=user, page=0, size=1, q="needle"
    )
    assert total == 1
    assert [row["name"] for row in rows] == ["needle-validation"]


def test_quality_approval_service_filters_and_slices_in_sql(db):
    user = db.query(User).filter(User.username == "test@example.com").one()
    db.add_all(
        [
            QualityPolicyApproval(
                policy_name="needle-policy",
                policy_version=2,
                policy_signature="a" * 64,
                scope_signature="b" * 64,
                status="PENDING_APPROVAL",
                policy_snapshot_json="{}",
                submitted_by=user.id,
                submission_reason="needle reason",
            ),
            QualityPolicyApproval(
                policy_name="other-policy",
                policy_version=1,
                policy_signature="c" * 64,
                scope_signature="d" * 64,
                status="APPROVED",
                policy_snapshot_json="{}",
                submitted_by=user.id,
            ),
        ]
    )
    db.flush()

    rows, total = service.list_quality_approvals(
        db, user=user, page=0, size=1, q="needle"
    )
    assert total == 1
    assert [row["policy_name"] for row in rows] == ["needle-policy"]


def test_quality_approval_workspace_keeps_unsubmitted_policies(monkeypatch, db):
    user = db.query(User).filter(User.username == "test@example.com").one()
    from labelu.internal.application.service import yolo_quality_policy

    monkeypatch.setattr(
        yolo_quality_policy,
        "list_quality_policies_bundle",
        lambda: SimpleNamespace(
            policies=[
                {
                    "policy_name": "submitted-policy",
                    "scene_name": "Scene A",
                    "model_family": "yolo",
                    "annotation_kind": "bbox",
                    "version": 1,
                },
                {
                    "policy_name": "unsubmitted-policy",
                    "scene_name": "Scene B",
                    "model_family": "yolo",
                    "annotation_kind": "bbox",
                    "version": 1,
                },
            ]
        ),
    )
    db.add(
        QualityPolicyApproval(
            policy_name="submitted-policy",
            policy_version=1,
            policy_signature="e" * 64,
            scope_signature="f" * 64,
            status="APPROVED",
            policy_snapshot_json="{}",
            submitted_by=user.id,
            approved_by=user.id,
        )
    )
    db.flush()

    rows, total = service.list_quality_approval_workspace(
        db, user=user, page=0, size=20, q="unsubmitted"
    )
    assert total == 1
    assert rows[0]["policy_name"] == "unsubmitted-policy"
    assert rows[0]["approval_history"] == []
