from sqlalchemy.orm import Session

from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.user import User


def _row(
    owner_id: int,
    package_id: str,
    *,
    model_id: str,
    status: str,
    last_error: str | None = None,
) -> ExperimentDatasetImport:
    return ExperimentDatasetImport(
        package_id=package_id,
        source_type="third_party",
        source_system="expert-review",
        source_external_task_id=f"task-{package_id}",
        source_external_package_id=package_id,
        dataset_type="reviewed_false_positive_daily",
        dataset_date="2026-10-26",
        event_type="helmet",
        model_id=model_id,
        target_signature="signature",
        source_payload_json="{}",
        status=status,
        last_error=last_error,
        formal_task_ids_json="[]",
        aggregation_key=f"aggregation-{package_id}",
        created_by=owner_id,
        updated_by=owner_id,
    )


def test_evolution_imports_supports_search_and_server_pagination(
    client, testuser_token_headers: dict[str, str], db: Session
):
    owner = db.query(User).filter(User.username == "test@example.com").first()
    assert owner is not None
    db.add_all(
        [
            _row(owner.id, "pkg-alpha", model_id="model-1", status="PENDING"),
            _row(owner.id, "pkg-beta", model_id="model-2", status="COMPLETED"),
            _row(
                owner.id,
                "pkg-gamma",
                model_id="model-2",
                status="FAILED",
                last_error="metadata.json 连接超时",
            ),
        ]
    )
    db.commit()

    first_page = client.get(
        "/api/v1/evolution/imports",
        params={"page": 0, "size": 2},
        headers=testuser_token_headers,
    )
    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()
    assert first_payload["meta_data"] == {"total": 3, "page": 0, "size": 2}
    assert [row["package_id"] for row in first_payload["data"]] == [
        "pkg-gamma",
        "pkg-beta",
    ]
    failed_row = first_payload["data"][0]
    assert failed_row["last_error"] == "metadata.json 连接超时"
    assert failed_row["status_detail"] == "导入失败：metadata.json 连接超时"

    searched = client.get(
        "/api/v1/evolution/imports",
        params={"q": "model-1", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert searched.status_code == 200, searched.text
    searched_payload = searched.json()
    assert searched_payload["meta_data"]["total"] == 1
    assert searched_payload["data"][0]["package_id"] == "pkg-alpha"
