import json
from datetime import datetime

import pytest

from labelu.internal.application.service import third_party_calls as service
from labelu.internal.common.third_party_call_audit import (
    classify_third_party_call,
    extract_third_party_identifiers,
)
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.third_party_call_audit import ThirdPartyCallAudit


def test_classifies_external_routes_and_skips_authenticated_operator_reads():
    target = classify_third_party_call(
        path="/internal/v1/strategy-evolution/model-trainings",
        method="POST",
    )
    assert target is not None
    assert target.operation == "model_capability"
    assert (
        classify_third_party_call(
            path="/internal/v1/strategy-evolution/tasks",
            method="GET",
            authorization="Bearer operator-token",
        )
        is None
    )
    public_create = classify_third_party_call(
        path="/api/v1/strategy-tasks",
        method="POST",
    )
    assert public_create is not None
    assert public_create.operation == "strategy_evolution_task"


def test_extracts_only_business_identifiers_from_request_body():
    body = json.dumps(
        {
            "schema_version": "v2",
            "source_system": "Experiment_Platform",
            "external_task_id": "task-42",
            "package_id": "pkg-42",
            "project_id": 7,
            "secret": "must-not-be-stored",
        }
    ).encode()
    result = extract_third_party_identifiers(
        body,
        default_source_system="strategy_evolution",
    )
    assert result == {
        "source_system": "experiment_platform",
        "external_task_id": "task-42",
        "external_package_id": "pkg-42",
        "schema_version": "v2",
        "project_id": 7,
    }


def test_extracts_project_identifier_without_storing_other_payload_fields():
    body = json.dumps(
        {
            "projectId": "17",
            "external_task_id": "task-project-17",
            "secret": "must-not-be-stored",
        }
    ).encode()

    result = extract_third_party_identifiers(
        body,
        default_source_system="strategy_evolution",
    )

    assert result["project_id"] == 17
    assert "secret" not in result


def _dataset(package_id: str) -> ExperimentDatasetImport:
    return ExperimentDatasetImport(
        package_id=package_id,
        source_type="third_party",
        source_system="experiment_platform",
        dataset_type="threshold_experiment",
        dataset_date="2026-08-14",
        event_type="garbageStreet",
        model_id="537",
        target_signature="business-type",
        source_payload_json="{}",
        status="WAITING_FOR_SAMPLES",
        formal_task_ids_json="[]",
        declared_sample_count=12,
        valid_sample_count=12,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def test_unified_chain_list_calculates_next_action_and_business_type(db):
    strategy = StrategyTaskIntegration(
        external_task_id="strategy-waiting-model",
        task_name="model optimization",
        task_type="model_capability",
        source_system="strategy_evolution",
        schema_version="v2",
        dataset_s3_uri="s3://bucket/dataset",
        dataset_image_prefix="images/",
        metadata_key="metadata.json",
        bucket="bucket",
        prefix="dataset",
        import_status="COMPLETED",
        platform_status="waiting_for_model",
        outbound_callback_status="pending",
        target_json=json.dumps({"model_id": "537"}),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add_all([strategy, _dataset("dataset-waiting-samples")])
    db.commit()

    result = service.list_chains(
        db,
        source_system=None,
        record_type=None,
        business_type=None,
        health=None,
        status=None,
        keyword=None,
        page=0,
        size=20,
    )
    rows = {item["record_id"]: item for item in result["items"]}

    assert rows["strategy-waiting-model"]["health"] == "waiting"
    assert rows["strategy-waiting-model"]["business_type"] == "model_optimization"
    assert rows["dataset-waiting-samples"]["business_type"] == "dataset_creation"
    assert rows["dataset-waiting-samples"]["next_action_owner"]


def test_chain_list_can_filter_by_business_type(db):
    db.add(_dataset("dataset-business-type"))
    db.commit()

    result = service.list_chains(
        db,
        source_system=None,
        record_type=None,
        business_type="dataset_creation",
        health=None,
        status=None,
        keyword=None,
        page=0,
        size=20,
    )

    assert [item["record_id"] for item in result["items"]] == [
        "dataset-business-type"
    ]
    assert result["items"][0]["business_type_label"] == "\u4efb\u52a1\u6570\u636e\u96c6\u521b\u5efa"


def test_audit_list_can_filter_by_business_type(db):
    now = datetime.now()
    for request_id, operation in (
        ("audit-dataset", "experiment_dataset_import"),
        ("audit-model", "model_capability"),
    ):
        db.add(
            ThirdPartyCallAudit(
                project_id=1,
                request_id=request_id,
                source_system="experiment_platform",
                operation=operation,
                request_method="POST",
                request_path="/internal/v1/test",
                request_sha256="0" * 64,
                response_status=202,
                outcome="accepted",
                duration_ms=8,
                received_at=now,
            )
        )
    db.commit()

    result = service.list_audit_events(
        db,
        source_system=None,
        operation=None,
        business_type="model_optimization",
        outcome=None,
        keyword=None,
        hours=24,
        page=0,
        size=20,
    )

    assert [item["request_id"] for item in result["items"]] == ["audit-model"]
    assert result["items"][0]["business_type"] == "model_optimization"


def test_chain_and_audit_lists_filter_by_project(db):
    pytest.skip("一期已取消项目维度；历史项目隔离契约仅保留作二期兼容参考")
    now = datetime.now()
    db.add_all(
        [
            StrategyTaskIntegration(
                project_id=11,
                external_task_id="strategy-project-11",
                task_name="project scoped strategy",
                task_type="model_capability",
                source_system="strategy_evolution",
                schema_version="v2",
                dataset_s3_uri="s3://bucket/dataset",
                dataset_image_prefix="images/",
                metadata_key="metadata.json",
                bucket="bucket",
                prefix="dataset",
                import_status="COMPLETED",
                platform_status="succeeded",
                created_at=now,
                updated_at=now,
            ),
            ThirdPartyCallAudit(
                project_id=11,
                request_id="audit-project-11",
                source_system="strategy_evolution",
                operation="model_capability",
                request_method="POST",
                request_path="/internal/v1/strategy-evolution/model-trainings",
                request_sha256="1" * 64,
                response_status=202,
                outcome="accepted",
                received_at=now,
            ),
            ThirdPartyCallAudit(
                project_id=12,
                request_id="audit-project-12",
                source_system="strategy_evolution",
                operation="model_capability",
                request_method="POST",
                request_path="/internal/v1/strategy-evolution/model-trainings",
                request_sha256="2" * 64,
                response_status=202,
                outcome="accepted",
                received_at=now,
            ),
        ]
    )
    db.commit()

    chains = service.list_chains(
        db,
        source_system=None,
        record_type=None,
        business_type=None,
        health=None,
        status=None,
        keyword=None,
        page=0,
        size=20,
        project_id=11,
    )
    audits = service.list_audit_events(
        db,
        source_system=None,
        operation=None,
        business_type=None,
        outcome=None,
        keyword=None,
        hours=24,
        page=0,
        size=20,
        project_id=11,
    )

    assert [item["record_id"] for item in chains["items"]] == [
        "strategy-project-11"
    ]
    assert [item["request_id"] for item in audits["items"]] == [
        "audit-project-11"
    ]


def test_summary_includes_business_and_call_health(db):
    db.add(
        ThirdPartyCallAudit(
            project_id=1,
            request_id="audit-summary-1",
            source_system="strategy_evolution",
            operation="experiment_dataset_import",
            request_method="POST",
            request_path="/internal/v1/experiment-datasets",
            request_sha256="0" * 64,
            request_bytes=24,
            response_status=422,
            outcome="rejected",
            duration_ms=8,
            received_at=datetime.now(),
        )
    )
    db.commit()

    result = service.summary(db)

    assert result["calls_today"] == 1
    assert result["call_failures_today"] == 1
    assert "strategy_evolution" in result["source_systems"]


def test_project_filter_isolates_chains_audits_summary_and_detail(db):
    pytest.skip("一期已取消项目维度；历史项目隔离契约仅保留作二期兼容参考")
    now = datetime.now()
    strategy_a = StrategyTaskIntegration(
        project_id=101,
        external_task_id="strategy-project-a",
        task_name="project a optimization",
        task_type="model_capability",
        source_system="strategy_evolution",
        schema_version="v2",
        dataset_s3_uri="s3://bucket/a",
        dataset_image_prefix="images/",
        metadata_key="metadata.json",
        bucket="bucket",
        prefix="a",
        import_status="COMPLETED",
        platform_status="succeeded",
        target_json="{}",
        created_at=now,
        updated_at=now,
    )
    strategy_b = StrategyTaskIntegration(
        project_id=202,
        external_task_id="strategy-project-b",
        task_name="project b optimization",
        task_type="model_capability",
        source_system="strategy_evolution",
        schema_version="v2",
        dataset_s3_uri="s3://bucket/b",
        dataset_image_prefix="images/",
        metadata_key="metadata.json",
        bucket="bucket",
        prefix="b",
        import_status="COMPLETED",
        platform_status="failed",
        target_json="{}",
        created_at=now,
        updated_at=now,
    )
    audit_a = ThirdPartyCallAudit(
        project_id=101,
        request_id="audit-project-a",
        source_system="strategy_evolution",
        operation="model_capability",
        request_method="POST",
        request_path="/internal/v1/strategy-evolution/model-trainings",
        external_task_id="strategy-project-a",
        request_sha256="1" * 64,
        response_status=202,
        outcome="accepted",
        duration_ms=8,
        received_at=now,
    )
    audit_b = ThirdPartyCallAudit(
        project_id=202,
        request_id="audit-project-b",
        source_system="strategy_evolution",
        operation="model_capability",
        request_method="POST",
        request_path="/internal/v1/strategy-evolution/model-trainings",
        external_task_id="strategy-project-b",
        request_sha256="2" * 64,
        response_status=500,
        outcome="failed",
        duration_ms=9,
        received_at=now,
    )
    db.add_all([strategy_a, strategy_b, audit_a, audit_b])
    db.commit()

    chains = service.list_chains(
        db,
        source_system=None,
        record_type=None,
        business_type=None,
        health=None,
        status=None,
        keyword=None,
        page=0,
        size=20,
        project_id=101,
    )
    audits = service.list_audit_events(
        db,
        source_system=None,
        operation=None,
        business_type=None,
        outcome=None,
        keyword=None,
        hours=24,
        page=0,
        size=20,
        project_id=101,
    )
    summary = service.summary(db, project_id=101)
    detail = service.get_chain_detail(
        db,
        record_type="strategy_task",
        record_id="strategy-project-a",
        project_id=101,
    )

    assert [item["record_id"] for item in chains["items"]] == [
        "strategy-project-a"
    ]
    assert [item["request_id"] for item in audits["items"]] == ["audit-project-a"]
    assert summary["total_chains"] == 1
    assert summary["calls_today"] == 1
    assert summary["call_failures_today"] == 0
    assert detail is not None
    assert [item["request_id"] for item in detail["call_events"]] == [
        "audit-project-a"
    ]
    assert (
        service.get_chain_detail(
            db,
            record_type="strategy_task",
            record_id="strategy-project-b",
            project_id=101,
        )
        is None
    )


def test_failed_business_chain_remains_blocked_when_callback_is_pending():
    row = StrategyTaskIntegration(
        platform_status="failed",
        outbound_callback_status="pending",
    )

    health, owner, next_action = service._strategy_next_action(row)

    assert health == "blocked"
    assert owner
    assert next_action
