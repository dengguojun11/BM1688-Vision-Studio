from datetime import datetime

from labelu.internal.domain.models.third_party_call_audit import ThirdPartyCallAudit
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)


def test_third_party_dashboard_requires_admin(
    client,
    testuser_token_headers,
    testadmin_token_headers,
):
    denied = client.get(
        "/api/v1/third-party-calls/summary",
        headers=testuser_token_headers,
    )
    assert denied.status_code == 403

    allowed = client.get(
        "/api/v1/third-party-calls/summary",
        headers=testadmin_token_headers,
    )
    assert allowed.status_code == 200
    assert "total_chains" in allowed.json()["data"]


def test_middleware_audits_rejected_third_party_request(client, db):
    response = client.post(
        "/internal/v1/strategy-evolution/tasks",
        json={
            "schema_version": "v2",
            "source_system": "integration-test",
            "external_task_id": "audit-invalid-payload",
            "project_id": 9999,
        },
    )
    assert response.status_code == 422

    db.expire_all()
    row = (
        db.query(ThirdPartyCallAudit)
        .filter(ThirdPartyCallAudit.external_task_id == "audit-invalid-payload")
        .one()
    )
    assert row.source_system == "integration-test"
    # Third-party imports are global in一期；历史审计表仍保留可空
    # project_id，但新请求不能再伪造项目归属。
    assert row.project_id is None
    assert row.operation == "strategy_evolution_task"
    assert row.response_status == 422
    assert row.outcome == "rejected"
    assert row.request_sha256
    assert "audit-invalid-payload" not in (row.error_summary or "")


def test_middleware_resolves_project_from_package_when_task_id_is_unknown(client, db):
    db.add(
        StrategyTaskIntegration(
            external_task_id="registered-task",
            source_package_id="registered-package",
            task_name="registered",
            task_type="model_capability",
            source_system="strategy_evolution",
            schema_version="v2",
            dataset_s3_uri="s3://bucket/registered",
            dataset_image_prefix="images/",
            metadata_key="metadata.json",
            bucket="bucket",
            prefix="registered",
            import_status="COMPLETED",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    db.commit()

    response = client.post(
        "/internal/v1/strategy-evolution/tasks",
        json={
            "schema_version": "v2",
            "source_system": "integration-test",
            "external_task_id": "unknown-task",
            "package_id": "registered-package",
        },
    )
    assert response.status_code == 422

    db.expire_all()
    row = (
        db.query(ThirdPartyCallAudit)
        .filter(ThirdPartyCallAudit.external_task_id == "unknown-task")
        .one()
    )
    assert row.external_package_id == "registered-package"
    assert row.project_id is None
