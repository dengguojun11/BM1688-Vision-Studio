"""BM1688 导出页与 /api/v1/yolo-bm1688-exports API 测试。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from labelu.internal.application.response.board_agent import (
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.bm1688_compiler import (
    Bm1688CompilerConfigResponse,
)
from labelu.internal.application.response.edge_ssh_config import EdgeSshConfigResponse
from labelu.internal.application.response.edge_ssh_config import EdgeSshProbeResponse
from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.service.yolo_edge_validation_pack import (
    edge_validation_job_root,
)
from labelu.internal.application.service import (
    yolo_bm1688_export as bm1688_export_service,
)
from labelu.internal.adapter.routers import yolo_train as yolo_train_router
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


@pytest.fixture
def yolo_train_enabled(monkeypatch):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)


@pytest.fixture(autouse=True)
def allow_test_user_global_mutations(monkeypatch):
    """Keep legacy router tests focused while the production route requires admin."""
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "test@example.com")


def test_edge_agent_busy_error_maps_to_conflict():
    exc = yolo_train_router.edge_agent_bootstrap_service.EdgeAgentBootstrapError(
        "边端设备正在执行任务 #88，暂不能重新引导 Agent",
        code="device_busy",
        detail={"job_id": 88},
    )

    mapped = yolo_train_router._edge_agent_bootstrap_exception(exc)

    assert mapped.status_code == 409
    assert "#88" in mapped.detail


@pytest.mark.parametrize(
    "code",
    [
        "device_mutation_lock_unavailable",
        "bootstrap_state_persist_failed",
        "channel_state_persist_failed",
    ],
)
def test_edge_agent_lock_and_persistence_errors_map_to_service_unavailable(code):
    exc = yolo_train_router.edge_agent_bootstrap_service.EdgeAgentBootstrapError(
        "边端设备状态暂时不可用",
        code=code,
    )

    mapped = yolo_train_router._edge_agent_bootstrap_exception(exc)

    assert mapped.status_code == 503


def test_failed_current_edge_run_cannot_reuse_residual_pass_metrics():
    row = SimpleNamespace(
        status="COMPLETED",
        quality_report=json.dumps(
            {
                "sop_eval": {
                    "pt": {"metrics": {"map50": 0.82}},
                    "bmodel": {"metrics": {"map50": 0.81}},
                    "comparison_pt_bmodel": {
                        "status": "ok",
                        "verdict": "pass",
                        "criteria_version": "map50-absolute-drop-v2",
                        "drop_map50": 0.01,
                    },
                }
            }
        ),
    )
    failed_edge = BoardValidationStatusResponse(
        validation_id=8,
        export_id=8,
        status="FAILED",
        edge_result_status="pass",
        error_message="current edge execution failed",
        report={
            "result": {
                "eval_summary": {"map50": 0.81},
                "comparison_pt_bmodel": {"status": "ok"},
            }
        },
    )

    assert bm1688_export_service._effective_pt_bmodel_comparison(row, failed_edge) == {}
    assert bm1688_export_service._edge_int8_eval_label(row, failed_edge) == "不可评估"
    assert bm1688_export_service._edge_result_status(row, failed_edge) == "fail"
    assert (
        bm1688_export_service._verdict_code(row, "达标", failed_edge) == "edge_failed"
    )


def test_partial_current_edge_run_never_becomes_release_ready():
    row = SimpleNamespace(status="COMPLETED", quality_report=None)
    partial_edge = BoardValidationStatusResponse(
        validation_id=9,
        export_id=8,
        status="PARTIAL_COMPLETED",
        edge_result_status="pass",
        report_summary="only logs returned",
        report={"result": {"eval_summary": {"map50": 0.81}}},
    )

    assert (
        bm1688_export_service._edge_int8_eval_label(row, partial_edge) == "证据不完整"
    )
    assert (
        bm1688_export_service._edge_result_status(row, partial_edge) == "result_pending"
    )
    assert (
        bm1688_export_service._verdict_code(row, "达标", partial_edge) == "edge_pending"
    )


def test_completed_plate_detection_runtime_without_precision_requires_review():
    row = SimpleNamespace(status="COMPLETED", quality_report=None)
    completed_edge = BoardValidationStatusResponse(
        validation_id=150,
        export_id=148,
        status="COMPLETED",
        edge_result_status="pass",
        report={
            "source": "manual_edge_validation",
            "result": {
                "prediction_summary": {
                    "image_count": 351,
                    "with_boxes": 351,
                    "total_boxes": 438,
                    "avg_boxes_per_image": 1.2479,
                },
                "eval_models": {
                    "fp16": {
                        "status": "ok",
                        "backend": "plate_detection_bm1688",
                        "runtime_ok": True,
                        "model_type": "plate_detection",
                        "input_image_count": 351,
                        "result_image_count": 351,
                        "images_with_detections": 351,
                        "total_detections": 438,
                    }
                },
            },
        },
    )

    assert bm1688_export_service._edge_int8_eval_label(row, completed_edge) == "需复核"
    assert bm1688_export_service._edge_result_status(row, completed_edge) == "review"
    assert (
        bm1688_export_service._verdict_code(row, "达标", completed_edge)
        == "review_required"
    )


def test_completed_plate_detection_with_valid_precision_comparison_can_pass():
    row = SimpleNamespace(
        status="COMPLETED",
        quality_report=json.dumps(
            {
                "sop_eval": {
                    "pt": {"metrics": {"map50": 0.99}},
                    "bmodel": {"metrics": {"map50": 0.98}},
                    "comparison_pt_bmodel": {
                        "status": "ok",
                        "verdict": "pass",
                        "criteria_version": "map50-absolute-drop-v2",
                    },
                }
            }
        ),
    )
    completed_edge = BoardValidationStatusResponse(
        validation_id=152,
        export_id=150,
        status="COMPLETED",
        edge_result_status="pass",
        report={
            "result": {
                "eval_models": {
                    "fp16": {
                        "status": "ok",
                        "backend": "plate_detection_bm1688",
                        "runtime_ok": True,
                        "model_type": "plate_detection",
                        "input_image_count": 50,
                        "result_image_count": 50,
                    }
                }
            }
        },
    )

    assert bm1688_export_service._edge_int8_eval_label(row, completed_edge) == "通过"
    assert bm1688_export_service._edge_result_status(row, completed_edge) == "pass"


def test_incomplete_plate_detection_runtime_result_requires_review():
    row = SimpleNamespace(status="COMPLETED", quality_report=None)
    completed_edge = BoardValidationStatusResponse(
        validation_id=151,
        export_id=149,
        status="COMPLETED",
        edge_result_status="pass",
        report={
            "result": {
                "eval_models": {
                    "fp16": {
                        "status": "ok",
                        "backend": "plate_detection_bm1688",
                        "runtime_ok": True,
                        "input_image_count": 351,
                        "result_image_count": 350,
                    }
                }
            }
        },
    )

    assert bm1688_export_service._edge_int8_eval_label(row, completed_edge) == "需复核"
    assert bm1688_export_service._edge_result_status(row, completed_edge) == "review"


def test_plate_detection_compatibility_summary_separates_component_scope():
    row = SimpleNamespace(status="COMPLETED", quality_report=None)
    completed_edge = BoardValidationStatusResponse(
        validation_id=162,
        export_id=162,
        status="COMPLETED",
        edge_result_status="pass",
        report={
            "result": {
                "execution": {
                    "runner_summary": {
                        "checks": {"generic_sophon_demo": {"status": "not_applicable"}}
                    }
                },
                "eval_models": {
                    "fp16": {
                        "status": "ok",
                        "backend": "plate_detection_bm1688",
                        "runtime_ok": True,
                        "model_type": "plate_detection",
                        "input_image_count": 50,
                        "result_image_count": 50,
                        "images_with_detections": 50,
                        "total_detections": 64,
                        "bmrt_test": {"return_code": 0, "elapsed_seconds": 0.535},
                        "inference": {"return_code": 0, "elapsed_seconds": 13.336},
                        "precision_eval_status": "computed",
                        "precision_eval_type": "bbox",
                        "map50": 0.9876,
                        "map50_95": 0.8765,
                        "precision": 0.98,
                        "recall": 0.97,
                        "f1": 0.9749,
                        "images": 50,
                    }
                },
            }
        },
    )

    summary = bm1688_export_service._plate_compatibility_summary(
        row,
        completed_edge,
        {
            "manifest_version": "plate_detection_v1",
            "job_type": "plate_detection",
            "network_names": ["plate_detection"],
        },
        training_recipe_key="plate_recognition_v1",
        bmodel_names=["plate_detection_fp16_1b.bmodel"],
    )

    assert summary is not None
    assert summary["verdict"] == "component_compatible"
    assert summary["model_scope"] == "plate_detection_component"
    assert summary["generic_validation_status"] == "not_applicable"
    assert summary["specialized_runtime_status"] == "pass"
    assert summary["precision_variants"] == ["fp16"]
    assert summary["missing_components"] == ["ocr", "color", "plate_type"]
    assert summary["total_detections"] == 64
    assert summary["precision_evaluation"] == {
        "status": "computed",
        "type": "bbox",
        "map50": 0.9876,
        "map50_95": 0.8765,
        "precision": 0.98,
        "recall": 0.97,
        "f1": 0.9749,
        "images": 50,
        "reason": None,
    }


def test_board_validation_execution_device_host_supports_manual_and_legacy_reports():
    from labelu.internal.application.service import board_agent

    assert (
        board_agent.execution_device_host_from_report(
            {"result": {"execution_summary": {"remote_host": "10.10.11.90"}}}
        )
        == "10.10.11.90"
    )
    assert (
        board_agent.execution_device_host_from_report(
            {"ssh_summary": {"remote_host": "10.10.11.87"}}
        )
        == "10.10.11.87"
    )


def test_manual_edge_validation_status_uses_persisted_execution_host(monkeypatch):
    from labelu.internal.application.service import yolo_aicm_publish

    row = SimpleNamespace(
        id=12600,
        export_id=165,
        status="COMPLETED",
        mode="run_and_eval",
        edge_host="10.10.11.90",
        result_json=json.dumps({"execution_summary": {"status": "ok"}}),
        progress_json=None,
        thresholds_json=None,
        ai_thresholds_json=None,
        image_count=50,
        labeled_image_count=50,
        unlabeled_image_count=0,
        report_summary="边端验证执行完成",
        error_message=None,
        completed_at=None,
        updated_at=None,
    )
    monkeypatch.setattr(
        crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda db, export_id: row,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack.load_manual_edge_board_report",
        lambda **kwargs: None,
    )

    status_response = yolo_aicm_publish._manual_edge_validation_status_for_export(
        SimpleNamespace(), export_id=165
    )

    assert status_response is not None
    assert status_response.execution_device_host == "10.10.11.90"


def test_bm1688_exports_html_page(client: TestClient):
    """独立页：/yolo-bm1688-exports（与 main.py FileResponse 一致）。"""
    r = client.get("/yolo-bm1688-exports")
    assert r.status_code == 200
    text = r.text
    assert "BM1688" in text
    assert "yolo-bm1688-exports/compiler-settings" in text or "/api/v1" in text
    # UI 契约：编译、验证、配置字段
    for needle in (
        "btnDetailRecompile",
        "btnDetailVerify",
        "btnVerifyConfig",
        "tpuMlirPython",
        "detailVerifyReport",
        "detailCompileLog",
        "detailPipeline",
        "detailPipelineSteps",
        "verify-config",
        "/recompile",
        "Sophon 验证",
        "detailCompatibilityPanel",
        "BM1688 兼容性检查",
        "专用车牌推理",
    ):
        assert needle in text, f"missing UI element: {needle}"


def test_compiler_settings_requires_yolo_train(
    client: TestClient, testuser_token_headers
):
    if settings.YOLO_TRAIN_ENABLED:
        pytest.skip("本用例验证未开启 YOLO 时返回 400")
    r = client.get(
        "/api/v1/yolo-bm1688-exports/compiler-settings",
        headers=testuser_token_headers,
    )
    assert r.status_code == 400


def test_compiler_settings_get_and_patch(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    cfg_file = tmp_path / "bm1688_compiler_config.json"
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_compiler_config.config_file_path",
        lambda: cfg_file,
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.ensure_bm1688_compiler_ready",
        lambda **_: None,
    )

    r = client.get(
        "/api/v1/yolo-bm1688-exports/compiler-settings",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["quantize"] in ("full", "fp16", "f32", "int8")
    assert "bmodel_basename" in body["data"]

    r2 = client.patch(
        "/api/v1/yolo-bm1688-exports/compiler-settings",
        headers=testuser_token_headers,
        json={
            "tpu_mlir_root": "/opt/tpu-mlir",
            "auto_compile": True,
            "quantize": "full",
            "compile_platforms": "bm1688",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["tpu_mlir_root"] == "/opt/tpu-mlir"


def test_patch_custom_compile_command_requires_superuser(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    cfg_file = tmp_path / "bm1688_compiler_config.json"
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_compiler_config.config_file_path",
        lambda: cfg_file,
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.ensure_bm1688_compiler_ready",
        lambda **_: None,
    )
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "other@admin.com")

    r = client.patch(
        "/api/v1/yolo-bm1688-exports/compiler-settings",
        headers=testuser_token_headers,
        json={"custom_compile_command": "echo unsafe"},
    )
    assert r.status_code == 403


def test_edge_ssh_settings_get_not_422(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    """静态路径 edge-ssh-settings 不得被 /{export_id} 捕获（否则 422）。"""
    r = client.get(
        "/api/v1/yolo-bm1688-exports/edge-ssh-settings",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "host" in data
    assert "password_configured" in data


def test_edge_ssh_probe_endpoint_returns_structured_result(
    client: TestClient, testuser_token_headers, yolo_train_enabled, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_config.probe_edge_ssh_config",
        lambda persist=True: EdgeSshProbeResponse(
            status="fail",
            stage="auth",
            code="auth_failed",
            message="SSH 认证失败",
            resolved_host="10.10.11.26",
            resolved_user="linaro",
            remote_base="/data/labelu_edge/jobs",
            password_source="mysql:encrypted",
            checked_at="2026-07-06T00:00:00+00:00",
        ),
    )
    r = client.post(
        "/api/v1/yolo-bm1688-exports/edge-ssh-settings/probe",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "fail"
    assert data["code"] == "auth_failed"
    assert data["resolved_host"] == "10.10.11.26"


def test_edge_devices_inline_password_create_and_probe(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
    queued_environment_checks = []
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.start_environment_check",
        lambda *args, **_kwargs: queued_environment_checks.append(args[0]),
    )

    created = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-inline-box",
            "host": "10.10.11.30",
            "user": "linaro",
            "password": "inline-secret-9999",
            "set_active": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    assert body["password_source_type"] == "mysql:encrypted"
    assert body["password_configured"] is True
    assert body["password_runtime_available"] is True
    assert "password_env_name" not in body
    assert body["password_masked_hint"].endswith("9999")

    device_id = body["id"]
    with monkeypatch.context() as m:
        m.setattr(
            "labelu.internal.application.service.edge_device_registry.probe_edge_ssh_target",
            lambda cfg: {
                "status": "ok",
                "stage": "remote_base",
                "code": "ok",
                "message": "ok",
                "checked_at": "2026-07-09T02:00:00+00:00",
            },
        )
        probed = client.post(
            f"/api/v1/edge-devices/{device_id}/probe",
            headers=testuser_token_headers,
        )
    assert probed.status_code == 200, probed.text
    assert probed.json()["data"]["status"] == "ok"
    assert queued_environment_checks == [device_id, device_id]

    updated = client.patch(
        f"/api/v1/edge-devices/{device_id}",
        headers=testuser_token_headers,
        json={"name": "edge-inline-box-updated", "password_secret_action": "keep"},
    )
    assert updated.status_code == 200, updated.text
    assert queued_environment_checks == [device_id, device_id, device_id]


def test_system_scan_queues_configured_devices_without_page(
    monkeypatch, yolo_train_enabled
):
    from labelu.internal.application.service import edge_device_registry as service

    records = [
        {"id": "edge-ready", "environment_status": "ready"},
        {"id": "edge-pending", "environment_status": "unknown"},
        {"id": "edge-incomplete", "environment_status": "unknown"},
    ]
    queued = []
    monkeypatch.setattr(service.registry_io, "list_device_records", lambda: records)
    monkeypatch.setattr(
        service,
        "_has_complete_runtime_config",
        lambda record: record["id"] != "edge-incomplete",
    )
    monkeypatch.setattr(
        service,
        "start_environment_check",
        lambda device_id, *, provision: queued.append((device_id, provision)),
    )

    count = service.scan_and_start_configured_environment_checks(
        provision=True,
        only_pending=False,
    )

    assert count == 2
    assert queued == [("edge-ready", True), ("edge-pending", True)]


def test_system_environment_check_bootstrap_starts_once(
    monkeypatch, yolo_train_enabled
):
    from labelu.internal.application.service import edge_device_registry as service

    scans = []
    started_threads = []
    monkeypatch.setattr(service, "_SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED", False)
    monkeypatch.setattr(
        service,
        "scan_and_start_configured_environment_checks",
        lambda **kwargs: scans.append(kwargs) or 2,
    )

    class _Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started_threads.append(self.name)
            self.target()

    monkeypatch.setattr(service, "threading", SimpleNamespace(Thread=_Thread))

    service.schedule_system_environment_checks()
    service.schedule_system_environment_checks()

    assert started_threads == ["edge-environment-system-bootstrap"]
    assert scans == [{"provision": True, "only_pending": False}]


def test_edge_devices_crud_and_active_switch_api(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

    empty = client.get("/api/v1/edge-devices", headers=testuser_token_headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["data"]["active_device_id"] == ""
    assert empty.json()["data"]["devices"] == []

    create1 = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-box-01",
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-1111",
            "set_active": True,
        },
    )
    assert create1.status_code == 201, create1.text
    dev1 = create1.json()["data"]
    assert dev1["name"] == "edge-box-01"
    assert dev1["is_active"] is True

    create2 = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-box-02",
            "host": "10.10.11.27",
            "user": "linaro",
            "password": "secret-2222",
            "set_active": False,
        },
    )
    assert create2.status_code == 201, create2.text
    dev2 = create2.json()["data"]
    assert dev2["is_active"] is False

    listed = client.get("/api/v1/edge-devices", headers=testuser_token_headers)
    assert listed.status_code == 200, listed.text
    listed_data = listed.json()["data"]
    assert listed_data["active_device_id"] == dev1["id"]
    assert len(listed_data["devices"]) == 2
    assert listed_data["total"] == 2

    searched = client.get(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        params={"q": "EDGE-BOX-02", "page": 0, "size": 1},
    )
    assert searched.status_code == 200, searched.text
    searched_data = searched.json()["data"]
    assert searched_data["total"] == 1
    assert searched_data["page"] == 0
    assert searched_data["size"] == 1
    assert [item["id"] for item in searched_data["devices"]] == [dev2["id"]]

    second_page = client.get(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        params={"page": 1, "size": 1},
    )
    assert second_page.status_code == 200, second_page.text
    second_page_data = second_page.json()["data"]
    assert second_page_data["total"] == 2
    assert second_page_data["page"] == 1
    assert second_page_data["size"] == 1
    assert len(second_page_data["devices"]) == 1

    disabled = client.get(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        params={"status": "disabled", "page": 0, "size": 10},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["total"] == 0

    switched = client.put(
        "/api/v1/edge-devices/active",
        headers=testuser_token_headers,
        json={"device_id": dev2["id"]},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["data"]["id"] == dev2["id"]
    assert switched.json()["data"]["is_active"] is True

    legacy = client.get(
        "/api/v1/yolo-bm1688-exports/edge-ssh-settings",
        headers=testuser_token_headers,
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["data"]["host"] == "10.10.11.27"

    deleted = client.delete(
        f"/api/v1/edge-devices/{dev1['id']}",
        headers=testuser_token_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert [item["id"] for item in deleted.json()["data"]["devices"]] == [dev2["id"]]


def test_edge_devices_probe_updates_device_status(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

    created = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-probe-box",
            "host": "10.10.11.29",
            "user": "linaro",
            "password": "secret-3333",
            "set_active": True,
        },
    )
    assert created.status_code == 201, created.text
    device_id = created.json()["data"]["id"]

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.probe_edge_ssh_target",
        lambda _cfg=None: {
            "status": "fail",
            "stage": "auth",
            "code": "auth_failed",
            "message": "SSH 认证失败",
            "resolved_host": "10.10.11.29",
            "resolved_user": "linaro",
            "remote_base": "/data/labelu_edge/jobs",
            "password_source": "env:EDGE_BOX3_PASSWORD",
            "checked_at": "2026-07-08T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.start_environment_check",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failed SSH probe must not queue environment check")
        ),
    )

    probe = client.post(
        f"/api/v1/edge-devices/{device_id}/probe",
        headers=testuser_token_headers,
    )
    assert probe.status_code == 200, probe.text
    assert probe.json()["data"]["code"] == "auth_failed"

    detail = client.get(
        f"/api/v1/edge-devices/{device_id}",
        headers=testuser_token_headers,
    )
    assert detail.status_code == 200, detail.text
    device = detail.json()["data"]
    assert device["last_probe_status"] == "fail"
    assert device["last_probe_code"] == "auth_failed"
    assert device["last_probe_checked_at"] == "2026-07-08T00:00:00+00:00"


def test_edge_device_environment_check_persists_ready_state_without_provision(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.start_environment_check",
        lambda *_args, **_kwargs: None,
    )
    created = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-ready-box",
            "host": "10.10.11.31",
            "user": "linaro",
            "password": "inline-secret-1111",
            "set_active": True,
        },
    )
    assert created.status_code == 201, created.text
    device_id = created.json()["data"]["id"]

    class _Client:
        def close(self):
            return None

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.ssh_connect",
        lambda **_kwargs: _Client(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.probe_edge_ssh_target",
        lambda _cfg=None: {
            "status": "ok",
            "stage": "remote_base",
            "code": "ok",
            "message": "ok",
            "checked_at": "2026-07-09T03:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.edge_runtime_provision.probe_edge_runtime",
        lambda *_args, **_kwargs: {
            "environment_ready": True,
            "architecture": "aarch64",
            "architecture_ok": True,
            "bm1688_device_ok": True,
            "libsophon_ok": True,
            "docker_ok": True,
            "docker_image_ok": True,
            "container_ok": True,
            "python_runtime_ok": True,
            "sophon_demo_ok": True,
            "validator_ok": True,
            "fatal_reasons": [],
            "repairable_reasons": [],
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.edge_runtime_provision.ensure_edge_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ready environment must not provision")
        ),
    )

    from labelu.internal.application.service import edge_device_registry as service

    service._run_environment_check_sync(device_id, provision=True)
    detail = client.get(
        f"/api/v1/edge-devices/{device_id}", headers=testuser_token_headers
    )
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["environment_status"] == "ready"
    assert data["environment_report"]["architecture"] == "aarch64"


def test_edge_device_environment_check_provisions_repairable_runtime(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.start_environment_check",
        lambda *_args, **_kwargs: None,
    )
    created = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-provision-box",
            "host": "10.10.11.32",
            "user": "linaro",
            "password": "inline-secret-2222",
            "set_active": True,
        },
    )
    assert created.status_code == 201, created.text
    device_id = created.json()["data"]["id"]

    class _Client:
        def close(self):
            return None

    client_obj = _Client()
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.ssh_connect",
        lambda **_kwargs: client_obj,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.probe_edge_ssh_target",
        lambda _cfg=None: {
            "status": "ok",
            "stage": "remote_base",
            "code": "ok",
            "message": "ok",
            "checked_at": "2026-07-09T04:00:00+00:00",
        },
    )
    repairable = {
        "environment_ready": False,
        "architecture": "aarch64",
        "architecture_ok": True,
        "bm1688_device_ok": True,
        "libsophon_ok": True,
        "docker_ok": True,
        "docker_image_ok": False,
        "container_ok": False,
        "python_runtime_ok": False,
        "sophon_demo_ok": False,
        "validator_ok": False,
        "fatal_reasons": [],
        "repairable_reasons": ["docker_image_missing"],
    }
    ready = dict(
        repairable,
        environment_ready=True,
        docker_image_ok=True,
        container_ok=True,
        python_runtime_ok=True,
        sophon_demo_ok=True,
        validator_ok=True,
        repairable_reasons=[],
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.edge_runtime_provision.probe_edge_runtime",
        lambda *_args, **_kwargs: repairable,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.edge_runtime_provision.ensure_edge_runtime",
        lambda *_args, **_kwargs: (client_obj, {"after": ready, "provisioned": True}),
    )

    from labelu.internal.application.service import edge_device_registry as service

    service._run_environment_check_sync(device_id, provision=True)
    detail = client.get(
        f"/api/v1/edge-devices/{device_id}", headers=testuser_token_headers
    )
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["environment_status"] == "ready"
    assert data["environment_report"]["docker_image_ok"] is True


def test_environment_no_space_error_is_persisted_as_dispatch_blocker():
    from labelu.internal.application.service import edge_device_registry as service

    status_value, code, message = service._environment_exception_state(
        service.edge_runtime_provision.EdgeRuntimeProvisionError(
            "OCI runtime exec failed: no space left on device"
        )
    )

    assert status_value == "blocked"
    assert code == "disk_space_insufficient"
    assert "清理 /tmp" in message


def test_environment_state_update_stops_when_device_was_deleted(monkeypatch):
    from labelu.internal.application.service import edge_device_registry as service

    monkeypatch.setattr(
        service.registry_io,
        "update_device_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("边端设备不存在: removed-device")
        ),
    )
    monkeypatch.setattr(
        service.registry_io, "find_device_record", lambda _device_id: None
    )

    assert (
        service._persist_environment_state(
            "removed-device",
            status_value="checking",
        )
        is False
    )


def test_environment_recheck_failure_requires_manual_cleanup():
    from labelu.internal.application.service import edge_device_registry as service

    status_value, code, message = service._environment_exception_state(
        service.edge_runtime_provision.EdgeRuntimeProvisionError(
            "安全日志自动清理后空间仍不足",
            code="manual_cleanup_required",
        )
    )

    assert status_value == "blocked"
    assert code == "manual_cleanup_required"
    assert "人工检查" in message


def _family_test_user(db, username: str) -> User:
    row = db.query(User).filter(User.username == username).first()
    if row is None:
        row = User(
            username=username,
            hashed_password=get_password_hash("User@123"),
        )
        db.add(row)
        db.flush()
    return row


def _family_test_login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users/login",
        json={"username": username, "password": "User@123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": response.json()["data"]["token"]}


def test_bm1688_export_families_keep_complete_history_and_access_boundaries(
    client: TestClient,
    testuser_token_headers,
    db,
    monkeypatch,
    yolo_train_enabled,
):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "admin@123.com")
    monkeypatch.setattr(
        bm1688_export_service,
        "_list_bmodel_names",
        lambda *_args, **_kwargs: [],
    )
    owner = crud_user.get_user_by_username(db, username="test@example.com")
    assert owner is not None
    owner_id = int(owner.id)
    db.rollback()

    with db.begin():
        collaborator = _family_test_user(db, "compile-family-collaborator@example.com")
        outsider = _family_test_user(db, "compile-family-outsider@example.com")
        group = AlgorithmGroup(
            group_key="compile_family_group",
            name="编译系列测试算法组",
            status="ACTIVE",
        )
        db.add(group)
        db.flush()
        algorithm_a = AlgorithmDefinition(
            algorithm_key="compile_family_algorithm_a",
            name="编译系列算法 A",
            task_type="object_detection",
            algorithm_group_id=group.id,
            required_tools_json="[]",
            required_attributes_json="[]",
            training_adapter="yolo_detection",
            evaluation_adapter="yolo_detection",
            supported_chips_json='["BM1688"]',
            status="ACTIVE",
        )
        algorithm_b = AlgorithmDefinition(
            algorithm_key="compile_family_algorithm_b",
            name="编译系列算法 B",
            task_type="object_detection",
            algorithm_group_id=group.id,
            required_tools_json="[]",
            required_attributes_json="[]",
            training_adapter="yolo_detection",
            evaluation_adapter="yolo_detection",
            supported_chips_json='["BM1688"]',
            status="ACTIVE",
        )
        db.add_all([algorithm_a, algorithm_b])
        db.flush()

        dataset = Dataset(
            owner_id=owner_id,
            name="编译系列测试数据集",
            status="ACTIVE",
            created_by=owner_id,
            updated_by=owner_id,
        )
        db.add(dataset)
        db.flush()
        dataset_v1 = DatasetVersion(
            dataset_id=dataset.id,
            version_no=1,
            status="FROZEN",
            manifest_rel_path="manifests/compile-family-v1.json",
            manifest_sha256="1" * 64,
            sample_count=10,
            created_by=owner_id,
        )
        dataset_v2 = DatasetVersion(
            dataset_id=dataset.id,
            version_no=2,
            status="FROZEN",
            manifest_rel_path="manifests/compile-family-v2.json",
            manifest_sha256="2" * 64,
            sample_count=20,
            created_by=owner_id,
        )
        db.add_all([dataset_v1, dataset_v2])
        db.flush()

        target_task = Task(
            name="compile-family-target",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=owner_id,
            updated_by=owner_id,
        )
        other_task = Task(
            name="compile-family-other-task",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=owner_id,
            updated_by=owner_id,
        )
        pure_legacy_task = Task(
            name="compile-family-pure-legacy",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=owner_id,
            updated_by=owner_id,
        )
        db.add_all([target_task, other_task, pure_legacy_task])
        db.flush()
        db.add(
            TaskCollaborator(
                task_id=target_task.id,
                user_id=collaborator.id,
            )
        )

        def add_job(task, *, algorithm=None, recipe="family-recipe"):
            hyperparams = (
                json.dumps(
                    {
                        "algorithm_id": int(algorithm.id),
                        "algorithm_key": algorithm.algorithm_key,
                    }
                )
                if algorithm is not None
                else None
            )
            row = crud_yolo_train_job.create(
                db,
                task_id=int(task.id),
                user_id=owner_id,
                model_weights="yolo11n.pt",
                epochs=1,
                imgsz=640,
                category="compile-family",
                hyperparams_json=hyperparams,
                training_recipe_key=recipe,
            )
            row.status = YoloTrainStatus.COMPLETED.value
            row.weights_path = f"weights/{row.id}.pt"
            return row

        target_a_v1_job = add_job(target_task, algorithm=algorithm_a)
        target_a_v2_job = add_job(target_task, algorithm=algorithm_a)
        target_a_legacy_job = add_job(target_task, algorithm=algorithm_a)
        target_b_job = add_job(target_task, algorithm=algorithm_b)
        target_b_legacy_job = add_job(target_task, algorithm=algorithm_b)
        other_a_job = add_job(other_task, algorithm=algorithm_a)
        pure_legacy_job = add_job(
            pure_legacy_task,
            recipe="pure-legacy-recipe",
        )
        db.flush()

        model_a_v1 = ModelVersion(
            algorithm_id=algorithm_a.id,
            dataset_id=dataset.id,
            dataset_version_id=dataset_v1.id,
            training_job_id=target_a_v1_job.id,
            model_key="compile-family-a-v1",
            version_no=1,
            display_name="编译系列 A v1",
            status="CANDIDATE",
            created_by=owner_id,
        )
        model_a_v2 = ModelVersion(
            algorithm_id=algorithm_a.id,
            dataset_id=dataset.id,
            dataset_version_id=dataset_v2.id,
            training_job_id=target_a_v2_job.id,
            model_key="compile-family-a-v2",
            version_no=2,
            display_name="编译系列 A v2",
            status="CANDIDATE",
            created_by=owner_id,
        )
        model_b_v1 = ModelVersion(
            algorithm_id=algorithm_b.id,
            dataset_id=dataset.id,
            dataset_version_id=dataset_v1.id,
            training_job_id=target_b_job.id,
            model_key="compile-family-b-v1",
            version_no=1,
            display_name="编译系列 B v1",
            status="CANDIDATE",
            created_by=owner_id,
        )
        other_model_a_v3 = ModelVersion(
            algorithm_id=algorithm_a.id,
            dataset_id=dataset.id,
            dataset_version_id=dataset_v2.id,
            training_job_id=other_a_job.id,
            model_key="compile-family-other-a-v3",
            version_no=3,
            display_name="另一个任务 A v3",
            status="CANDIDATE",
            created_by=owner_id,
        )
        db.add_all([model_a_v1, model_a_v2, model_b_v1, other_model_a_v3])
        db.flush()

        def add_export(job, task, export_status):
            row = crud_yolo_bm1688_export.create(
                db,
                user_id=owner_id,
                task_id=int(task.id),
                yolo_train_job_id=int(job.id),
                imgsz=640,
            )
            row.status = export_status
            return row

        export_a_v1_failed = add_export(target_a_v1_job, target_task, "FAILED")
        export_a_v2_processing = add_export(
            target_a_v2_job,
            target_task,
            "PROCESSING",
        )
        export_a_v1_retry = add_export(target_a_v1_job, target_task, "COMPLETED")
        export_a_legacy = add_export(
            target_a_legacy_job,
            target_task,
            "COMPLETED",
        )
        export_b_v1 = add_export(target_b_job, target_task, "COMPLETED")
        export_b_legacy = add_export(
            target_b_legacy_job,
            target_task,
            "PENDING",
        )
        export_other = add_export(other_a_job, other_task, "COMPLETED")
        export_pure_legacy = add_export(
            pure_legacy_job,
            pure_legacy_task,
            "COMPLETED",
        )
        ids = {
            "target_task": int(target_task.id),
            "model_a_v2": int(model_a_v2.id),
            "target_a_v2_job": int(target_a_v2_job.id),
            "target_a_legacy_job": int(target_a_legacy_job.id),
            "export_a_v1_failed": int(export_a_v1_failed.id),
            "export_a_v2_processing": int(export_a_v2_processing.id),
            "export_a_v1_retry": int(export_a_v1_retry.id),
            "export_a_legacy": int(export_a_legacy.id),
            "export_b_v1": int(export_b_v1.id),
            "export_b_legacy": int(export_b_legacy.id),
            "export_other": int(export_other.id),
            "export_pure_legacy": int(export_pure_legacy.id),
            "dataset_v2": int(dataset_v2.id),
        }

    # A legacy row can retain stale algorithm metadata even when it belongs to
    # the same family as a canonical model version.  The family parent should
    # use the canonical latest-version lineage in that case.
    original_lineage_resolver = bm1688_export_service._compile_job_lineages

    def lineage_with_stale_legacy_key(db_session, job_ids):
        lineages = original_lineage_resolver(db_session, job_ids)
        legacy_job_id = ids["target_a_legacy_job"]
        legacy_lineage = lineages.get(legacy_job_id)
        if legacy_lineage is not None:
            lineages[legacy_job_id] = replace(
                legacy_lineage,
                algorithm_key="legacy:stale-algorithm-key",
            )
        return lineages

    monkeypatch.setattr(
        bm1688_export_service,
        "_compile_job_lineages",
        lineage_with_stale_legacy_key,
    )

    response = client.get(
        "/api/v1/yolo-bm1688-exports/families?page=0&size=20",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta_data"] == {"total": 4, "page": 0, "size": 4}
    target_families = [
        family for family in payload["data"] if family["task_id"] == ids["target_task"]
    ]
    assert len(target_families) == 2
    family_a = next(
        family
        for family in target_families
        if family["algorithm_key"] == "compile_family_algorithm_a"
    )
    family_b = next(
        family
        for family in target_families
        if family["algorithm_key"] == "compile_family_algorithm_b"
    )
    assert family_a["grouping_source"] == "mixed"
    assert family_a["version_count"] == 3
    assert family_a["compile_task_count"] == 4
    assert family_a["latest_model_version_id"] == ids["model_a_v2"]
    assert family_a["latest_version_no"] == 2
    assert family_a["latest_export_id"] == ids["export_a_v2_processing"]
    assert family_a["latest_status"] == "PROCESSING"
    assert "latest_version_id" not in family_a
    assert "latest_export_status" not in family_a
    assert [row["id"] for row in family_a["exports"]] == [
        ids["export_a_legacy"],
        ids["export_a_v1_retry"],
        ids["export_a_v2_processing"],
        ids["export_a_v1_failed"],
    ]
    assert family_a["exports"][2]["dataset_version_id"] == ids["dataset_v2"]
    assert family_a["exports"][2]["dataset_version_no"] == 2
    assert family_a["exports"][0]["model_version_id"] is None
    assert family_b["compile_task_count"] == 2
    assert {row["id"] for row in family_b["exports"]} == {
        ids["export_b_v1"],
        ids["export_b_legacy"],
    }

    filtered = client.get(
        "/api/v1/yolo-bm1688-exports/families"
        "?task_name=compile-family-target&export_status=FAILED",
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["meta_data"]["total"] == 1
    assert [row["id"] for row in filtered.json()["data"][0]["exports"]] == [
        ids["export_a_legacy"],
        ids["export_a_v1_retry"],
        ids["export_a_v2_processing"],
        ids["export_a_v1_failed"],
    ]

    for query in (
        f"export_id={ids['export_a_legacy']}",
        f"yolo_train_job_id={ids['target_a_v2_job']}",
    ):
        linked = client.get(
            f"/api/v1/yolo-bm1688-exports/families?{query}",
            headers=testuser_token_headers,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["meta_data"]["total"] == 1
        assert linked.json()["data"][0]["compile_task_count"] == 4

    first_page = client.get(
        "/api/v1/yolo-bm1688-exports/families?page=0&size=1",
        headers=testuser_token_headers,
    )
    last_page = client.get(
        "/api/v1/yolo-bm1688-exports/families?page=3&size=1",
        headers=testuser_token_headers,
    )
    assert first_page.status_code == 200, first_page.text
    assert last_page.status_code == 200, last_page.text
    assert first_page.json()["meta_data"] == {"total": 4, "page": 0, "size": 1}
    assert last_page.json()["meta_data"] == {"total": 4, "page": 3, "size": 1}
    assert first_page.json()["data"][0]["exports"][0]["id"] == ids["export_pure_legacy"]
    assert last_page.json()["data"][0]["compile_task_count"] == 4

    pure_legacy = client.get(
        f"/api/v1/yolo-bm1688-exports/families?export_id={ids['export_pure_legacy']}",
        headers=testuser_token_headers,
    )
    assert pure_legacy.status_code == 200, pure_legacy.text
    legacy_family = pure_legacy.json()["data"][0]
    assert legacy_family["algorithm_id"] is None
    assert legacy_family["algorithm_key"].startswith("legacy:")
    assert legacy_family["latest_model_version_id"] is None
    assert legacy_family["exports"][0]["model_version_id"] is None

    collaborator_headers = _family_test_login(client, collaborator.username)
    collaborator_view = client.get(
        "/api/v1/yolo-bm1688-exports/families?page=0&size=20",
        headers=collaborator_headers,
    )
    assert collaborator_view.status_code == 200, collaborator_view.text
    assert collaborator_view.json()["meta_data"]["total"] == 2
    assert {family["task_id"] for family in collaborator_view.json()["data"]} == {
        ids["target_task"]
    }

    outsider_headers = _family_test_login(client, outsider.username)
    outsider_view = client.get(
        f"/api/v1/yolo-bm1688-exports/families?export_id={ids['export_a_legacy']}",
        headers=outsider_headers,
    )
    assert outsider_view.status_code == 200, outsider_view.text
    assert outsider_view.json()["meta_data"]["total"] == 0
    assert outsider_view.json()["data"] == []


def test_delete_active_edge_device_is_rejected_api(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

    create1 = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-box-01",
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-1111",
            "set_active": True,
        },
    )
    create2 = client.post(
        "/api/v1/edge-devices",
        headers=testuser_token_headers,
        json={
            "name": "edge-box-02",
            "host": "10.10.11.27",
            "user": "linaro",
            "password": "secret-2222",
            "set_active": False,
        },
    )
    assert create1.status_code == 201, create1.text
    assert create2.status_code == 201, create2.text

    blocked = client.delete(
        f"/api/v1/edge-devices/{create1.json()['data']['id']}",
        headers=testuser_token_headers,
    )
    assert blocked.status_code == 400, blocked.text


def test_list_bm1688_exports_empty(
    client: TestClient, testuser_token_headers, yolo_train_enabled
):
    r = client.get(
        "/api/v1/yolo-bm1688-exports?page=0&size=20",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert r.json()["meta_data"]["total"] == 0
    assert r.json()["meta_data"]["summary"] == {
        "total": 0,
        "running": 0,
        "blocked": 0,
        "publish_ready": 0,
        "pending_edge": 0,
        "failed": 0,
    }

    filtered = client.get(
        "/api/v1/yolo-bm1688-exports?page=0&size=1&yolo_train_job_id=901",
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []


def test_list_bm1688_exports_can_skip_summary(
    client: TestClient, testuser_token_headers, yolo_train_enabled
):
    response = client.get(
        "/api/v1/yolo-bm1688-exports?page=0&size=20&include_summary=false",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["meta_data"]["summary"] is None


def test_bm1688_export_catalog_summary_route_is_available(
    client: TestClient, testuser_token_headers, yolo_train_enabled
):
    response = client.get(
        "/api/v1/yolo-bm1688-exports/catalog-summary",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["total"] == 0


def test_bm1688_exports_filter_by_training_dataset_category(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    db,
):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    user_id = int(user.id)
    db.rollback()
    with db.begin():
        category = DatasetCategory(
            category_key="bm_filter_category",
            name="BM filter category",
            status="active",
            created_by=user_id,
            updated_by=user_id,
        )
        task = Task(
            name="bm-category-filter-task",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user_id,
        )
        db.add_all([category, task])
        db.flush()
        matching_job = YoloTrainJob(
            task_id=task.id,
            created_by=user_id,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="/tmp/matching.pt",
            dataset_category_id=category.id,
            class_names_json='["vehicle"]',
            label_set_signature="bm-set-signature",
            class_order_signature="bm-order-signature",
        )
        other_job = YoloTrainJob(
            task_id=task.id,
            created_by=user_id,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="/tmp/other.pt",
        )
        db.add_all([matching_job, other_job])
        db.flush()
        matching_export = crud_yolo_bm1688_export.create(
            db,
            user_id=user_id,
            task_id=task.id,
            yolo_train_job_id=matching_job.id,
            imgsz=640,
        )
        other_export = crud_yolo_bm1688_export.create(
            db,
            user_id=user_id,
            task_id=task.id,
            yolo_train_job_id=other_job.id,
            imgsz=640,
        )
        matching_export.status = "COMPLETED"
        other_export.status = "COMPLETED"
        category_id = int(category.id)
        matching_export_id = int(matching_export.id)

    response = client.get(
        f"/api/v1/yolo-bm1688-exports?page=0&size=20&dataset_category_id={category_id}",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [row["id"] for row in rows] == [matching_export_id]
    assert rows[0]["dataset_category_key"] == "bm_filter_category"
    assert rows[0]["class_names"] == ["vehicle"]
    assert rows[0]["class_order_signature"] == "bm-order-signature"
    assert response.json()["meta_data"]["summary"]["total"] == 1

    by_task_and_status = client.get(
        "/api/v1/yolo-bm1688-exports"
        "?page=0&size=20&task_name=bm-category&export_status=COMPLETED",
        headers=testuser_token_headers,
    )
    assert by_task_and_status.status_code == 200, by_task_and_status.text
    assert len(by_task_and_status.json()["data"]) == 2

    no_match = client.get(
        "/api/v1/yolo-bm1688-exports"
        "?page=0&size=20&task_name=missing-task&export_status=COMPLETED",
        headers=testuser_token_headers,
    )
    assert no_match.status_code == 200, no_match.text
    assert no_match.json()["data"] == []


def test_bm1688_export_payloads_are_loaded_on_demand(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    db,
):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    user_id = int(user.id)
    db.rollback()
    source_files = [
        {"path": f"images/{index:04d}.jpg", "sha256": f"sha-{index}"}
        for index in range(250)
    ]
    calibration_files = [
        {"path": f"calibration/{index:04d}.jpg"} for index in range(25)
    ]
    with db.begin():
        task = Task(
            name="bm-lazy-payload-task",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user_id,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=user_id,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="/tmp/lazy.pt",
        )
        db.add(job)
        db.flush()
        export_row = crud_yolo_bm1688_export.create(
            db,
            user_id=user_id,
            task_id=task.id,
            yolo_train_job_id=job.id,
            imgsz=640,
        )
        export_row.status = "COMPLETED"
        export_row.artifact_manifest_json = json.dumps(
            {
                "manifest_version": "v1",
                "source_dataset": {
                    "content_sha256": "dataset-hash",
                    "class_names": ["vehicle"],
                    "files": source_files,
                },
                "calibration": {"files": calibration_files},
                "artifacts": [{"role": "weights_pt", "sha256": "weights-hash"}],
            }
        )
        export_row.quality_report = json.dumps(
            {"sop_eval": {"status": "ok", "split": "test"}}
        )
        export_id = int(export_row.id)

    listing = client.get(
        "/api/v1/yolo-bm1688-exports?page=0&size=20",
        headers=testuser_token_headers,
    )
    assert listing.status_code == 200, listing.text
    list_row = next(row for row in listing.json()["data"] if row["id"] == export_id)
    assert list_row["artifact_manifest"]["source_dataset"]["file_count"] == 250
    assert "files" not in list_row["artifact_manifest"]["source_dataset"]
    assert list_row["artifact_manifest"]["calibration"]["file_count"] == 25
    assert list_row["board_validation"] is None

    summary = client.get(
        f"/api/v1/yolo-bm1688-exports/{export_id}/summary",
        headers=testuser_token_headers,
    )
    assert summary.status_code == 200, summary.text
    summary_data = summary.json()["data"]
    assert summary_data["quality_report"] is None
    assert summary_data["artifact_manifest"]["source_dataset"]["file_count"] == 250
    assert "files" not in summary_data["artifact_manifest"]["source_dataset"]

    evaluation = client.get(
        f"/api/v1/yolo-bm1688-exports/{export_id}/evaluation",
        headers=testuser_token_headers,
    )
    assert evaluation.status_code == 200, evaluation.text
    evaluation_data = evaluation.json()["data"]
    assert evaluation_data["quality_report"]["sop_eval"]["status"] == "ok"
    assert evaluation_data["artifact_manifest"]["source_dataset"]["file_count"] == 250
    assert "files" not in evaluation_data["artifact_manifest"]["source_dataset"]

    legacy_detail = client.get(
        f"/api/v1/yolo-bm1688-exports/{export_id}",
        headers=testuser_token_headers,
    )
    assert legacy_detail.status_code == 200, legacy_detail.text
    assert (
        len(
            legacy_detail.json()["data"]["artifact_manifest"]["source_dataset"]["files"]
        )
        == 250
    )


def test_verify_config_endpoint(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    cfg_file = tmp_path / "bm1688_compiler_config.json"
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_compiler_config.config_file_path",
        lambda: cfg_file,
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.ensure_bm1688_compiler_ready",
        lambda **_: None,
    )
    r = client.get(
        "/api/v1/yolo-bm1688-exports/verify-config",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert "checks" in data
    assert "passed" in data
    assert "counts" in data


def test_member_compile_diagnostics_and_config_redact_paths(monkeypatch):
    """Public compile diagnostics keep status useful without exposing host paths."""
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "admin@123.com")
    member = SimpleNamespace(username="member-only@example.com")
    admin = SimpleNamespace(username="admin@123.com")

    async def fake_verify_config():
        return {
            "export_root": "C:/labelu/private/exports/24",
            "checks": [
                {
                    "status": "fail",
                    "message": "缺少 /data/private/tpu_mlir/envsetup.sh",
                }
            ],
        }

    monkeypatch.setattr(
        yolo_train_router.yolo_bm1688_export_service,
        "get_compiler_sophon_verify",
        fake_verify_config,
    )
    verify_resp = asyncio.run(
        yolo_train_router.verify_yolo_bm1688_compiler_config(
            authorization=None,
            current_user=member,
        )
    )
    assert verify_resp.data["export_root"] is None
    assert "[路径已隐藏]" in verify_resp.data["checks"][0]["message"]

    admin_verify = asyncio.run(
        yolo_train_router.verify_yolo_bm1688_compiler_config(
            authorization=None,
            current_user=admin,
        )
    )
    assert admin_verify.data["export_root"] == "C:/labelu/private/exports/24"

    compiler = Bm1688CompilerConfigResponse(
        tpu_mlir_root="/opt/tpu-mlir",
        calibration_dataset_dir="/data/calibration",
        quantize_table_path="/data/qtable",
        tpu_mlir_python="/data/venv/bin/python3",
        builtin_script_path="C:/labelu/scripts/compile.sh",
        legacy_sync_file="C:/labelu/config.json",
    )
    monkeypatch.setattr(
        yolo_train_router.bm1688_compiler_cfg_service,
        "get_bm1688_compiler_config_view",
        lambda: compiler,
    )
    compiler_resp = asyncio.run(
        yolo_train_router.get_bm1688_compiler_settings(
            authorization=None,
            db=None,
            current_user=member,
        )
    )
    assert compiler_resp.data.tpu_mlir_root == ""
    assert compiler_resp.data.calibration_dataset_dir == ""
    assert compiler_resp.data.tpu_mlir_python == ""
    admin_compiler_resp = asyncio.run(
        yolo_train_router.get_bm1688_compiler_settings(
            authorization=None,
            db=None,
            current_user=admin,
        )
    )
    assert admin_compiler_resp.data.tpu_mlir_root == "/opt/tpu-mlir"
    assert admin_compiler_resp.data.tpu_mlir_python == "/data/venv/bin/python3"

    ssh = EdgeSshConfigResponse(
        edge_root="/data/labelu_edge",
        remote_base="/data/labelu_edge/jobs",
        sophon_demo_root="/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
    )
    monkeypatch.setattr(
        yolo_train_router.edge_ssh_cfg_service,
        "get_edge_ssh_config_view",
        lambda: ssh,
    )
    ssh_resp = asyncio.run(
        yolo_train_router.get_yolo_bm1688_edge_ssh_settings(
            authorization=None,
            current_user=member,
        )
    )
    assert ssh_resp.data.edge_root == ""
    assert ssh_resp.data.remote_base == ""
    assert ssh_resp.data.sophon_demo_root == ""
    admin_ssh_resp = asyncio.run(
        yolo_train_router.get_yolo_bm1688_edge_ssh_settings(
            authorization=None,
            current_user=admin,
        )
    )
    assert admin_ssh_resp.data.edge_root == "/data/labelu_edge"
    assert admin_ssh_resp.data.remote_base == "/data/labelu_edge/jobs"
    assert (
        admin_ssh_resp.data.sophon_demo_root
        == "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
    )

    report_text = "模型路径: C:/labelu/media/exports/24/model.pt\n指标: mAP50=0.8"
    runtime_text = "run /data/labelu/media/exports/24/compile.log\n"
    monkeypatch.setattr(
        yolo_train_router.yolo_bm1688_export_service,
        "get_sop_eval_report_markdown",
        lambda *args, **kwargs: report_text,
    )
    monkeypatch.setattr(
        yolo_train_router.yolo_bm1688_export_service,
        "get_runtime_log_text",
        lambda *args, **kwargs: runtime_text,
    )
    report_resp = asyncio.run(
        yolo_train_router.download_yolo_bm1688_sop_eval_report(
            export_id=24,
            kind="pt",
            authorization=None,
            db=None,
            current_user=member,
        )
    )
    runtime_resp = asyncio.run(
        yolo_train_router.download_yolo_bm1688_runtime_log(
            export_id=24,
            kind="compile",
            authorization=None,
            db=None,
            current_user=member,
        )
    )
    assert "C:/labelu/media/exports/24/model.pt" not in report_resp.body.decode()
    assert "[路径已隐藏]" in report_resp.body.decode()
    assert "/data/labelu/media/exports/24/compile.log" not in runtime_resp.body.decode()
    assert "[路径已隐藏]" in runtime_resp.body.decode()


def test_compiler_settings_includes_tpu_mlir_python(
    client: TestClient,
    testuser_token_headers,
    yolo_train_enabled,
    tmp_path,
    monkeypatch,
):
    cfg_file = tmp_path / "bm1688_compiler_config.json"
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_compiler_config.config_file_path",
        lambda: cfg_file,
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.ensure_bm1688_compiler_ready",
        lambda **_: None,
    )
    client.patch(
        "/api/v1/yolo-bm1688-exports/compiler-settings",
        headers=testuser_token_headers,
        json={"tpu_mlir_python": "/data/labeIU/labeIuMedia/venv-tpu-mlir/bin/python3"},
    )
    r = client.get(
        "/api/v1/yolo-bm1688-exports/compiler-settings",
        headers=testuser_token_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["tpu_mlir_python"].endswith("python3")


def test_legacy_edge_ssh_endpoint_reuses_unified_manual_job(
    client: TestClient,
    testuser_token_headers,
    db,
    tmp_path,
    monkeypatch,
    yolo_train_enabled,
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="legacy edge ssh task",
                description="description",
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
        crud_attachment.create(
            db,
            TaskAttachment(
                filename="edge_legacy.png",
                url="/upload/edge_legacy.png",
                path="upload/edge_legacy.png",
                task_id=task.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )

    upload_dir = tmp_path / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "edge_legacy.png").write_bytes(b"img")

    called = {}

    def _fake_run_legacy(db_value, *, export_id, current_user):
        called["export_id"] = export_id
        called["current_user_id"] = current_user.id
        return None

    async def _fake_refresh(db_value, *, export_id, current_user, run_onnx_val=False):
        return {
            "id": export_id,
            "task_id": task.id,
            "task_name": task.name,
            "yolo_train_job_id": train_job.id,
            "status": "COMPLETED",
            "imgsz": 640,
            "onnx_filename": None,
            "bmodel_filename": None,
            "bmodel_filenames": [],
            "error_message": None,
            "doc_hint": "",
            "quality_summary": "",
            "validation_gate_label": "",
            "edge_validation_label": "通过",
            "edge_execution_label": "执行完成，待回传",
            "edge_int8_eval_label": "待回传",
            "current_verdict_label": "待回传INT8结果",
            "workflow_stage": "edge_validation",
            "edge_result_status": "result_pending",
            "verdict_code": "edge_pending",
            "created_at": None,
            "updated_at": None,
            "board_validation": None,
            "compile_log": None,
            "export_dir": "",
            "export_dir_abs": "",
            "can_recompile_bmodel": True,
            "quality_report": None,
            "quality_report_refresh": None,
        }

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation._edge_ssh_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_config.probe_edge_ssh_config",
        lambda persist=True: EdgeSshProbeResponse(
            status="ok",
            stage="remote_base",
            code="ok",
            message="ok",
            resolved_host="10.10.11.26",
            resolved_user="linaro",
            remote_base="/data/labelu_edge/jobs",
            password_source="mysql:encrypted",
            checked_at="2026-07-06T00:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.run_legacy_edge_ssh_job_sync",
        _fake_run_legacy,
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.routers.yolo_train.yolo_bm1688_export_service.refresh_export_quality_report",
        _fake_refresh,
    )

    resp = client.post(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}/edge-ssh-validation",
        headers=testuser_token_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["workflow_stage"] == "edge_validation"
    assert data["edge_result_status"] == "result_pending"
    assert data["verdict_code"] == "edge_pending"
    assert called["export_id"] == export_row.id
    assert called["current_user_id"] == current_user.id


def test_board_validation_endpoint_returns_unified_manual_status(
    client: TestClient, testuser_token_headers, db, monkeypatch, yolo_train_enabled
):
    # This test explicitly exercises member-level redaction; the module-level
    # compatibility fixture otherwise grants the test user superuser access.
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "admin@123.com")
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="board validation unified task",
                description="description",
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

    manual_status = BoardValidationStatusResponse(
        validation_id=321,
        export_id=export_row.id,
        status="PROCESSING",
        execution_device_host="10.10.11.26",
        report_summary="manual:run_and_eval; 边端验证任务执行中",
        progress={
            "stage": "pull_output",
            "message": "正在回传边端结果",
            "detail": {
                "remote_host": "10.10.11.26",
                "remote_job_dir": "/data/labelu_edge/jobs/job-321",
                "remote_bundle": "/data/labelu_edge/jobs/job-321/output.zip",
                "results_dir": "/tmp/results",
            },
            "history": [{"stage": "upload", "detail": {"images_dir": "/tmp/images"}}],
        },
        report={
            "source": "manual_edge_validation",
            "export_root_abs": "C:/labelu/media/exports/24",
            "source_path": "C:/labelu/media/metrics.json",
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_aicm_publish._edge_validation_status_for_export",
        lambda db_value, export_id, include_report=False, quality_report=None: manual_status,
    )

    resp = client.get(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}/board-validation",
        headers=testuser_token_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["validation_id"] == 321
    assert data["status"] == "PROCESSING"
    assert data["workflow_stage"] == "edge_validation"
    assert data["execution_device_host"] is None
    assert data["report"]["source"] == "manual_edge_validation"
    assert data["report"]["export_root_abs"] is None
    assert data["report"]["source_path"] is None
    assert data["progress"]["detail"]["remote_host"] is None
    assert data["progress"]["detail"]["remote_job_dir"] is None
    assert data["progress"]["detail"]["remote_bundle"] is None
    assert data["progress"]["detail"]["results_dir"] is None
    assert data["progress"]["history"][0]["detail"]["images_dir"] is None

    # The create/re-run endpoint must apply the same response policy as GET.
    post_status = manual_status.copy(deep=True)
    monkeypatch.setattr(
        "labelu.internal.application.service.board_agent.request_board_validation_for_export",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.board_agent.get_status_for_export",
        lambda *args, **kwargs: post_status,
    )
    post_resp = client.post(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}/board-validation",
        headers=testuser_token_headers,
    )
    assert post_resp.status_code == 200, post_resp.text
    post_data = post_resp.json()["data"]
    assert post_data["report"]["export_root_abs"] is None
    assert post_data["progress"]["detail"]["remote_job_dir"] is None


def test_runtime_log_endpoint_reads_full_compile_and_edge_logs(
    client: TestClient,
    testuser_token_headers,
    db,
    monkeypatch,
    tmp_path,
    yolo_train_enabled,
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="runtime log task",
                description="description",
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
            threshold_source="manual",
            sample_ids_json=json.dumps([], ensure_ascii=False),
            thresholds_json=json.dumps({"conf_thres": 0.3}, ensure_ascii=False),
            ai_thresholds_json=None,
            image_count=0,
            labeled_image_count=0,
            unlabeled_image_count=0,
            report_summary="manual job",
        )

    export_root = tmp_path / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_row.id)
    export_root.mkdir(parents=True, exist_ok=True)
    (export_root / "compile.log").write_text(
        "compile line 1\ncompile line 2\n",
        encoding="utf-8",
    )

    job_root = edge_validation_job_root(export_id=export_row.id, job_id=job.id)
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root / "edge_ssh_remote_run.log").write_text(
        "remote line 1\nremote line 2\n",
        encoding="utf-8",
    )

    compile_resp = client.get(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}/runtime-log/compile",
        headers=testuser_token_headers,
    )
    assert compile_resp.status_code == 200, compile_resp.text
    assert compile_resp.text == "compile line 1\ncompile line 2\n"
    assert compile_resp.headers["content-type"].startswith("text/plain")

    edge_resp = client.get(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}/runtime-log/edge_remote_run",
        headers=testuser_token_headers,
    )
    assert edge_resp.status_code == 200, edge_resp.text
    assert edge_resp.text == "remote line 1\nremote line 2\n"
    assert edge_resp.headers["content-type"].startswith("text/plain")


def test_delete_yolo_bm1688_export_removes_db_and_disk(
    client: TestClient,
    testuser_token_headers,
    db,
    monkeypatch,
    tmp_path,
    yolo_train_enabled,
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="delete export task",
                description="description",
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
        crud_yolo_bm1688_export.update_row(
            db,
            export_row,
            status="COMPLETED",
            onnx_rel_path=f"yolo_bm1688_exports/{export_row.id}/models/demo.onnx",
            bmodel_rel_path=f"yolo_bm1688_exports/{export_row.id}/models/demo.bmodel",
        )
        edge_job = crud_yolo_edge_validation_job.create(
            db,
            user_id=current_user.id,
            task_id=task.id,
            export_id=export_row.id,
            mode="run_only",
            threshold_source="manual",
            sample_ids_json=json.dumps([], ensure_ascii=False),
            thresholds_json=json.dumps({"conf_thres": 0.3}, ensure_ascii=False),
            ai_thresholds_json=None,
            image_count=0,
            labeled_image_count=0,
            unlabeled_image_count=0,
            report_summary="done",
        )
        crud_yolo_edge_validation_job.update(db, edge_job, status="COMPLETED")

    export_root = tmp_path / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_row.id)
    export_root.mkdir(parents=True, exist_ok=True)
    (export_root / "compile.log").write_text("compile data", encoding="utf-8")
    (export_root / "models").mkdir(parents=True, exist_ok=True)
    (export_root / "models" / "demo.onnx").write_text("onnx", encoding="utf-8")
    (export_root / "models" / "demo.bmodel").write_text("bmodel", encoding="utf-8")

    resp = client.delete(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}",
        headers=testuser_token_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] is True
    assert resp.json()["data"]["export_id"] == export_row.id
    assert crud_yolo_bm1688_export.get(db, export_row.id) is None
    assert not export_root.exists()


def test_delete_yolo_bm1688_export_rejects_processing(
    client: TestClient,
    testuser_token_headers,
    db,
    yolo_train_enabled,
):
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="busy export task",
                description="description",
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
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=current_user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="PROCESSING")

    resp = client.delete(
        f"/api/v1/yolo-bm1688-exports/{export_row.id}",
        headers=testuser_token_headers,
    )
    assert resp.status_code == 400, resp.text
    assert crud_yolo_bm1688_export.get(db, export_row.id) is not None
