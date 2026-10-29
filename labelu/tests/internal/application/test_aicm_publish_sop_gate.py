from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from labelu.internal.application.service import yolo_aicm_publish as pub
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.bm1688_compile_contract import profile_snapshot_digest
from labelu.internal.common.model_manifest_signature import sign_model_manifest


def test_bmodel_eval_gate_notice_warns_on_fail(tmp_path, monkeypatch):
    export_id = 99
    root = tmp_path / "yolo_bm1688_exports" / str(export_id) / "server_eval"
    root.mkdir(parents=True)
    (root / "sop_eval_summary.json").write_text(
        '{"comparison_pt_bmodel":{"status":"fail","detail":"差 12%"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(pub.settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(pub.settings, "YOLO_BM1688_EXPORT_SUBDIR", "yolo_bm1688_exports")

    notice = pub._bmodel_eval_gate_notice(export_id)
    assert notice is not None
    assert "差 12%" in notice
    # Legacy comparison summaries in the review band are warning-only and do
    # not require a second manual review before publication.
    pub._assert_bmodel_eval_gate(export_id)


def test_bmodel_eval_gate_blocks_explicit_drop_over_policy(tmp_path, monkeypatch):
    export_id = 102
    root = tmp_path / "yolo_bm1688_exports" / str(export_id) / "server_eval"
    root.mkdir(parents=True)
    (root / "sop_eval_summary.json").write_text(
        (
            '{"pt":{"metrics":{"map50":0.80}},'
            '"bmodel":{"metrics":{"map50":0.77}},'
            '"comparison_pt_bmodel":{"pt_map50":0.80,"bmodel_map50":0.77}}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pub.settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(pub.settings, "YOLO_BM1688_EXPORT_SUBDIR", "yolo_bm1688_exports")

    with pytest.raises(LabelUException) as exc_info:
        pub._assert_bmodel_eval_gate(export_id)

    assert "0.0200" in str(exc_info.value.msg or exc_info.value)


def test_bmodel_eval_gate_legacy_ok_is_publishable_with_warning(tmp_path, monkeypatch):
    export_id = 100
    root = tmp_path / "yolo_bm1688_exports" / str(export_id) / "server_eval"
    root.mkdir(parents=True)
    (root / "sop_eval_summary.json").write_text(
        '{"comparison_pt_bmodel":{"status":"ok","detail":"within 5%"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(pub.settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(pub.settings, "YOLO_BM1688_EXPORT_SUBDIR", "yolo_bm1688_exports")

    notice = pub._bmodel_eval_gate_notice(export_id)
    assert notice is not None
    assert "旧版比较" in notice
    pub._assert_bmodel_eval_gate(export_id)


def test_bmodel_eval_gate_requires_comparison_for_non_int8(tmp_path, monkeypatch):
    export_id = 101
    notice = pub._bmodel_eval_gate_notice(export_id, precision="fp16")
    assert notice is not None
    assert "FP16" in notice
    with pytest.raises(LabelUException):
        pub._assert_bmodel_eval_gate(export_id, precision="fp16")


def test_review_option_is_publishable_with_quality_warning(monkeypatch):
    monkeypatch.setattr(
        pub,
        "_bmodel_eval_gate_result",
        lambda *args, **kwargs: ("review", None, 0.02),
    )
    option = pub.AicmPublishBmodelOption(
        rel_path="BM1688/model_int8_1b.bmodel",
        filename="model_int8_1b.bmodel",
        precision="int8",
        label="INT8 量化",
    )

    result = pub._prepare_publish_bmodel_options(
        type("Job", (), {"job_type": "yolo_detection"})(),
        [option],
        export_id=12,
        allow_quality_risk=True,
    )

    assert result[0].validation_status == "review"
    assert result[0].publishable is True
    assert result[0].blocked_reason is None
    assert result[0].quality_warning


@pytest.mark.parametrize(
    ("edge_status", "expected"),
    [
        (None, "边端验证未开始"),
        ("PENDING", "PENDING"),
        ("PROCESSING", "PROCESSING"),
        ("FAILED", "FAILED"),
        ("COMPLETED", None),
    ],
)
def test_publish_block_reason_from_edge_validation(edge_status, expected):
    edge_validation = None
    if edge_status is not None:
        edge_validation = type("EdgeValidation", (), {"status": edge_status})()

    reason = pub._publish_block_reason_from_edge_validation(edge_validation)

    if expected is None:
        assert reason is None
    else:
        assert expected in reason


def test_publish_gate_reason_codes_cover_edge_and_training_states():
    edge_validation = type("EdgeValidation", (), {"status": "PROCESSING"})()
    validation_pass = type("ValidationPass", (), {"status": "不达标"})()

    codes = pub._publish_gate_reason_codes(
        edge_validation=edge_validation,
        validation_pass=validation_pass,
    )

    assert codes == ["edge_validation_incomplete", "training_validation_failed"]


def test_publish_gate_requires_compilation_profile_digest():
    profile = {"profile_id": "bm1688-yolov11-detection-v1", "input_size": [640, 640]}
    manifest = {"bm1688_profile": profile}
    edge_validation = type(
        "EdgeValidation",
        (),
        {
            "status": "COMPLETED",
            "report": {"profile_digest": "wrong"},
            "result_json": None,
        },
    )()
    codes = pub._publish_gate_reason_codes(
        edge_validation=edge_validation,
        artifact_manifest=manifest,
    )
    assert "profile_digest_mismatch" in codes

    edge_validation.report = {"profile_digest": profile_snapshot_digest(profile)}
    codes = pub._publish_gate_reason_codes(
        edge_validation=edge_validation,
        artifact_manifest=manifest,
    )
    assert "profile_digest_missing" not in codes
    assert "profile_digest_mismatch" not in codes


def test_publish_gate_requires_model_manifest_signature_in_production(monkeypatch):
    monkeypatch.setattr(pub.settings, "APP_ENV", "production")
    monkeypatch.setattr(pub.settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "")
    assert pub._model_manifest_publish_reason({"execution_signature": "sig"}) == (
        "manifest_signature_secret_missing"
    )


def test_publish_gate_rejects_tampered_signed_model_manifest(monkeypatch):
    monkeypatch.setattr(pub.settings, "APP_ENV", "test")
    monkeypatch.setattr(pub.settings, "YOLO_MODEL_MANIFEST_HMAC_REQUIRED", False)
    monkeypatch.setattr(pub.settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "manifest-secret")
    monkeypatch.setattr(pub.settings, "YOLO_MODEL_MANIFEST_HMAC_KEY_ID", "k1")
    manifest = sign_model_manifest(
        {
            "manifest_id": "export:11",
            "manifest_schema_version": "v1",
            "export_id": 11,
            "execution_signature": "sig",
            "artifacts": [],
        },
        secret="manifest-secret",
        key_id="k1",
    )
    manifest["execution_signature"] = "changed"
    assert pub._model_manifest_publish_reason(manifest, export_id=11) == (
        "manifest_signature_invalid"
    )


def test_assert_edge_profile_for_publish_blocks_mismatch():
    profile = {"profile_id": "bm1688-yolov11-detection-v1"}
    edge_validation = type(
        "EdgeValidation",
        (),
        {"report": {"profile_digest": "wrong"}, "result_json": None},
    )()
    with pytest.raises(LabelUException):
        pub._assert_edge_profile_for_publish(
            edge_validation, {"bm1688_profile": profile}
        )


def test_publish_gate_status_maps_release_readiness():
    release_gate_assessment = type(
        "ReleaseGateAssessment",
        (),
        {"status_code": "review_required"},
    )()

    status = pub._publish_gate_status(
        reason_codes=[],
        release_gate_assessment=release_gate_assessment,
    )

    assert status == "pending_review"


def test_publish_gate_status_prioritizes_explicit_blocking_reason():
    release_gate_assessment = type(
        "ReleaseGateAssessment",
        (),
        {"status_code": "publishable"},
    )()

    status = pub._publish_gate_status(
        reason_codes=["fixed_eval_signature_missing"],
        release_gate_assessment=release_gate_assessment,
    )

    assert status == "blocked"


def test_publish_gate_reason_codes_include_publish_grade_edge_evidence():
    edge_validation = type(
        "EdgeValidation",
        (),
        {
            "status": "COMPLETED",
            "report": {
                "status": "ok",
                "device": {"device_id": "bm1688-1"},
                "model_identity": {"export_id": 1},
                "execution": {"status": "ok"},
                "prediction_summary": {"image_count": 10},
            },
            "result_json": None,
        },
    )()
    manifest = {
        "execution_signature": "execution-1",
        "bm1688_profile": {"profile_id": "bm1688-yolov11-detection-v1"},
    }

    codes = pub._publish_gate_reason_codes(
        edge_validation=edge_validation,
        artifact_manifest=manifest,
    )

    assert "artifact_signature_missing" in codes
    assert "execution_generation_state_missing" in codes
    assert "fixed_eval_signature_missing" in codes
    assert "p95_latency_missing" in codes
    assert "error_rate_missing" in codes
    assert "short_stability_missing" in codes
    assert "execution_fencing_missing" in codes


def test_plate_bmodel_runtime_result_is_not_left_pending():
    edge_validation = type(
        "EdgeValidation",
        (),
        {
            "status": "COMPLETED",
            "report": None,
            "result_json": json.dumps(
                {
                    "execution_summary": {"status": "ok", "has_bmodel_eval": True},
                    "eval_summary": {
                        "status": "ok",
                        "models": {
                            "fp16": {
                                "status": "ok",
                                "backend": "plate_recognition_bm1688",
                            }
                        },
                    },
                }
            ),
        },
    )()

    assert pub._edge_result_status_from_validation(edge_validation) == "pass"


def test_assert_edge_validation_completed_for_publish_raises_when_missing(monkeypatch):
    monkeypatch.setattr(pub, "_edge_validation_status_for_export", lambda db, export_id: None)

    with pytest.raises(LabelUException) as exc_info:
        pub._assert_edge_validation_completed_for_publish(object(), export_id=12)

    assert "边端验证未开始" in str(exc_info.value.msg or exc_info.value)


def test_assert_edge_validation_completed_for_publish_returns_completed(monkeypatch):
    completed = type("EdgeValidation", (), {"status": "COMPLETED"})()
    monkeypatch.setattr(
        pub, "_edge_validation_status_for_export", lambda db, export_id: completed
    )

    result = pub._assert_edge_validation_completed_for_publish(object(), export_id=12)

    assert result is completed


def test_manual_edge_validation_status_for_export_maps_completed_job(monkeypatch):
    completed_at = datetime.now()
    updated_at = completed_at + timedelta(seconds=3)
    row = type(
        "ManualJob",
        (),
        {
            "id": 88,
            "export_id": 12,
            "status": "COMPLETED",
            "mode": "run_and_eval",
            "threshold_source": "ai_recommended",
            "thresholds_json": '{"conf_thres":0.31,"nms_thres":0.68,"match_iou_thres":0.5,"source":"ai_recommended"}',
            "ai_thresholds_json": '{"reason":"edge sample review","confidence_level":"high","provider_key":"mock-llm"}',
            "image_count": 6,
            "labeled_image_count": 4,
            "unlabeled_image_count": 2,
            "report_summary": "manual done",
            "error_message": None,
            "completed_at": completed_at,
            "updated_at": updated_at,
            "result_json": '{"execution":{"status":"ok"}}',
        },
    )()
    monkeypatch.setattr(
        pub.crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda db, export_id: row,
    )

    status = pub._manual_edge_validation_status_for_export(object(), export_id=12)

    assert status is not None
    assert status.status == "COMPLETED"
    assert status.validation_id == 88
    assert status.workflow_stage == "edge_validation"
    assert status.edge_result_status == "result_pending"
    assert status.report["source"] == "manual_edge_validation"
    assert status.report["mode"] == "run_and_eval"
    assert status.report["thresholds"]["conf_thres"] == 0.31
    assert status.report["thresholds"]["nms_thres"] == 0.68
    assert status.report["thresholds"]["match_iou_thres"] == 0.5
    assert status.threshold_contract["threshold_unit"] == "ratio_0_1"
    assert status.threshold_contract["conf_thres"] == 0.31
    assert status.threshold_contract["nms_thres"] == 0.68
    assert status.threshold_contract["match_iou_thres"] == 0.5
    assert status.report["ai_thresholds"]["provider_key"] == "mock-llm"
    assert status.report["execution"]["status"] == "ok"


def test_manual_edge_validation_status_for_export_maps_processing_progress(monkeypatch):
    updated_at = datetime.now()
    row = type(
        "ManualJob",
        (),
        {
            "id": 89,
            "export_id": 12,
            "status": "PROCESSING",
            "mode": "run_only",
            "threshold_source": "system_default",
            "image_count": 3,
            "labeled_image_count": 1,
            "unlabeled_image_count": 2,
            "report_summary": "正在拉取边端输出",
            "error_message": None,
            "completed_at": None,
            "updated_at": updated_at,
            "result_json": '{"execution":{"status":"pending"}}',
            "progress_json": (
                '{"stage":"pull_output","message":"正在回传 output 目录","detail":{"remote_job_dir":"/data/manual-job-89"},'
                '"updated_at":"2026-07-04T11:22:33","history":[{"stage":"upload","message":"上传完成","at":"2026-07-04T11:20:00"}]}'
            ),
        },
    )()
    monkeypatch.setattr(
        pub.crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda db, export_id: row,
    )

    status = pub._manual_edge_validation_status_for_export(object(), export_id=12)

    assert status is not None
    assert status.status == "PROCESSING"
    assert status.workflow_stage == "edge_validation"
    assert status.edge_result_status == "running"
    assert status.progress is not None
    assert status.progress.stage == "pull_output"
    assert status.progress.message == "正在回传 output 目录"
    assert status.progress.detail["remote_job_dir"] == "/data/manual-job-89"
    assert status.progress.history[-1]["stage"] == "upload"
    assert status.report["source"] == "manual_edge_validation"


def test_manual_edge_validation_report_uses_board_metric_normalization(monkeypatch):
    from labelu.internal.application.service import yolo_edge_validation_pack

    completed_at = datetime.now()
    row = type(
        "ManualJob",
        (),
        {
            "id": 90,
            "export_id": 12,
            "status": "COMPLETED",
            "mode": "run_and_eval",
            "threshold_source": "system_default",
            "thresholds_json": None,
            "ai_thresholds_json": None,
            "image_count": 32,
            "labeled_image_count": 32,
            "unlabeled_image_count": 0,
            "report_summary": "manual board report",
            "error_message": None,
            "completed_at": completed_at,
            "updated_at": completed_at,
            "result_json": '{"status":"ok"}',
            "progress_json": None,
        },
    )()
    legacy_report = {
        "passed": True,
        "steps": [
            {
                "step": "infer_fp16",
                "bmodel": "yolov11n_fp16_1b.bmodel",
                "returncode": 0,
                "stderr": (
                    "decode_time(ms): 18.19\n"
                    "preprocess_time(ms): 9.80\n"
                    "inference_time(ms): 19.39\n"
                    "postprocess_time(ms): 6.91\n"
                ),
            }
        ],
        "metrics": {
            "infer_runs": [
                {
                    "tag": "infer_fp16",
                    "bmodel": "yolov11n_fp16_1b.bmodel",
                    "ok": True,
                }
            ]
        },
    }
    monkeypatch.setattr(
        pub.crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda db, export_id: row,
    )
    monkeypatch.setattr(
        yolo_edge_validation_pack,
        "load_manual_edge_board_report",
        lambda *, export_id, job_id: legacy_report,
    )
    monkeypatch.setattr(
        pub.board_agent,
        "get_status_for_export",
        lambda db, export_id, include_report=False, quality_report=None: None,
    )

    status = pub._edge_validation_status_for_export(
        object(),
        export_id=12,
        include_report=True,
        quality_report={},
    )

    infer = status.report["metrics"]["infer_runs"][0]
    assert infer["decode_ms_avg"] == 18.19
    assert infer["preprocess_ms_avg"] == 9.8
    assert infer["infer_ms_avg"] == 19.39
    assert infer["postprocess_ms_avg"] == 6.91
    comparison = {
        row["metric"]: row for row in status.train_board_comparison["rows"]
    }
    assert comparison["infer_ms_avg_yolov11n_fp16_1b.bmodel"]["edge"] == "19.39"


def test_edge_validation_status_recovers_legacy_disk_report_read_only(
    tmp_path, monkeypatch
):
    export_id = 12
    report_path = (
        tmp_path
        / "yolo_bm1688_exports"
        / str(export_id)
        / "edge_ssh_pull"
        / "output"
        / "board_report.json"
    )
    report_path.parent.mkdir(parents=True)
    legacy_report = {
        "passed": True,
        "summary": "board validation passed",
        "steps": [
            {
                "step": "infer_fp32",
                "bmodel": "yolov11n_fp32_1b.bmodel",
                "returncode": 0,
                "stderr": (
                    "decode_time(ms): 17.98\n"
                    "preprocess_time(ms): 10.16\n"
                    "inference_time(ms): 51.40\n"
                    "postprocess_time(ms): 6.70\n"
                ),
            }
        ],
        "metrics": {
            "infer_runs": [
                {
                    "tag": "infer_fp32",
                    "bmodel": "yolov11n_fp32_1b.bmodel",
                    "ok": True,
                }
            ]
        },
    }
    original_text = json.dumps(legacy_report, ensure_ascii=False, indent=2)
    report_path.write_text(original_text, encoding="utf-8")
    board_status = pub.BoardValidationStatusResponse(
        validation_id=5,
        export_id=export_id,
        status="COMPLETED",
        edge_result_status="result_pending",
        report=None,
    )
    monkeypatch.setattr(pub.settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        pub.settings, "YOLO_BM1688_EXPORT_SUBDIR", "yolo_bm1688_exports"
    )
    monkeypatch.setattr(
        pub.board_agent,
        "get_status_for_export",
        lambda db, export_id, include_report=False, quality_report=None: board_status,
    )
    monkeypatch.setattr(
        pub,
        "_manual_edge_validation_status_for_export",
        lambda db, export_id: None,
    )

    status = pub._edge_validation_status_for_export(
        object(),
        export_id=export_id,
        include_report=True,
        quality_report={},
    )

    assert status.edge_result_status == "pass"
    assert status.report_summary == "board validation passed"
    infer = status.report["metrics"]["infer_runs"][0]
    assert infer["decode_ms_avg"] == 17.98
    assert infer["preprocess_ms_avg"] == 10.16
    assert infer["infer_ms_avg"] == 51.4
    assert infer["postprocess_ms_avg"] == 6.7
    comparison = {
        row["metric"]: row for row in status.train_board_comparison["rows"]
    }
    assert comparison["infer_ms_avg_yolov11n_fp32_1b.bmodel"]["edge"] == "51.4"
    assert report_path.read_text(encoding="utf-8") == original_text


def test_edge_validation_status_for_export_prefers_newer_manual_job(monkeypatch):
    board_completed_at = datetime.now()
    manual_completed_at = board_completed_at + timedelta(minutes=5)
    board_status = type(
        "BoardStatus",
        (),
        {"status": "FAILED", "completed_at": board_completed_at, "report": None},
    )()
    manual_status = type(
        "ManualStatus",
        (),
        {"status": "COMPLETED", "completed_at": manual_completed_at, "report": None},
    )()
    monkeypatch.setattr(
        pub.board_agent,
        "get_status_for_export",
        lambda db, export_id, include_report=False, quality_report=None: board_status,
    )
    monkeypatch.setattr(
        pub,
        "_manual_edge_validation_status_for_export",
        lambda db, export_id: manual_status,
    )

    chosen = pub._edge_validation_status_for_export(object(), export_id=12)

    assert chosen is manual_status


def test_edge_validation_status_for_export_keeps_newer_board_status(monkeypatch):
    manual_completed_at = datetime.now()
    board_completed_at = manual_completed_at + timedelta(minutes=5)
    board_status = type(
        "BoardStatus",
        (),
        {"status": "FAILED", "completed_at": board_completed_at, "report": None},
    )()
    manual_status = type(
        "ManualStatus",
        (),
        {"status": "COMPLETED", "completed_at": manual_completed_at, "report": None},
    )()
    monkeypatch.setattr(
        pub.board_agent,
        "get_status_for_export",
        lambda db, export_id, include_report=False, quality_report=None: board_status,
    )
    monkeypatch.setattr(
        pub,
        "_manual_edge_validation_status_for_export",
        lambda db, export_id: manual_status,
    )

    chosen = pub._edge_validation_status_for_export(object(), export_id=12)

    assert chosen is board_status


def test_start_publish_job_blocks_when_edge_validation_not_completed(monkeypatch):
    export_row = type(
        "ExportRow",
        (),
        {"id": 12, "task_id": 34, "category": "demo"},
    )()
    task_row = type("TaskRow", (), {"id": 34, "name": "demo-task", "created_by": 56})()
    user = type("UserRow", (), {"id": 56, "username": "tester"})()

    monkeypatch.setattr(pub.crud_yolo_bm1688_export, "get", lambda db, export_id: export_row)
    monkeypatch.setattr(pub.crud_task, "get", lambda db, task_id: task_row)
    monkeypatch.setattr(
        pub,
        "_assert_edge_validation_completed_for_publish",
        lambda db, export_id: (_ for _ in ()).throw(
            LabelUException(pub.ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR, message="杈圭楠岃瘉鏈畬鎴愶紝绂佹鍙戝竷")
        ),
    )
    monkeypatch.setattr(pub, "resolve_rect_preset_for_task", lambda db, task: ("preset", "preset"))
    monkeypatch.setattr(pub, "repair_stored_config_if_needed", lambda: None)
    monkeypatch.setattr(pub, "get_active_environment_id", lambda: "env-1")
    monkeypatch.setattr(pub, "load_aicm_publish_config", lambda environment_id=None: {"base_url": "http://x", "username": "u", "password": "p"})
    monkeypatch.setattr(pub, "config_is_complete", lambda cfg: True)
    monkeypatch.setattr(pub, "find_environment_record", lambda env_id: {"name": "prod"})
    monkeypatch.setattr(pub, "create_job", lambda **kwargs: pytest.fail("create_job should not be called"))
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            pub.start_publish_job(
                object(),
                user,
                export_id=12,
            )
        )

    assert "杈圭楠岃瘉" in str(exc_info.value.msg or exc_info.value)


def test_start_manual_publish_job_blocks_without_edge_evidence(monkeypatch):
    export_row = type(
        "ExportRow",
        (),
        {"id": 12, "task_id": 34, "category": "demo", "yolo_train_job_id": 78},
    )()
    user = type("UserRow", (), {"id": 56, "username": "tester"})()
    task_row = type(
        "TaskRow",
        (),
        {"id": 34, "name": "demo-task", "created_by": 99, "collaborators": [user]},
    )()
    train_job = type("TrainJob", (), {"id": 78, "job_type": "yolo_detection", "category": "demo"})()
    created = {}

    monkeypatch.setattr(pub.crud_yolo_bm1688_export, "get", lambda db, export_id: export_row)
    monkeypatch.setattr(pub.crud_task, "get", lambda db, task_id: task_row)
    monkeypatch.setattr(pub.crud_yolo_train_job, "get", lambda db, job_id: train_job)
    monkeypatch.setattr(pub, "_edge_validation_status_for_export", lambda db, export_id: None)
    monkeypatch.setattr(pub, "resolve_rect_preset_for_task", lambda db, task: ("preset", "preset"))
    monkeypatch.setattr(pub, "repair_stored_config_if_needed", lambda: None)
    monkeypatch.setattr(pub, "get_active_environment_id", lambda: "env-1")
    monkeypatch.setattr(pub, "load_aicm_publish_config", lambda environment_id=None: {"base_url": "http://x", "username": "u", "password": "p"})
    monkeypatch.setattr(pub, "config_is_complete", lambda cfg: True)
    monkeypatch.setattr(pub, "find_environment_record", lambda env_id: {"name": "prod"})
    monkeypatch.setattr(pub, "_dataset_category_fields_for_export", lambda db, export_id: {"dataset_category_id": None, "dataset_category_key": None, "dataset_category_name": None})
    monkeypatch.setattr(pub.yolo_train_service, "_job_contract_response_fields", lambda job: {
        "class_names": ["demo"],
        "label_set_signature": "labels",
        "class_order_signature": "order",
        "data_yaml_sha256": "yaml",
        "dataset_content_signature": "dataset",
        "training_recipe_key": "yolo",
        "training_recipe_version": 1,
        "runtime_profile": "yolo",
        "runtime_interface_version": 1,
    })
    monkeypatch.setattr(pub, "quality_policy_snapshot_for_job", lambda job: {})
    monkeypatch.setattr(pub.bm_export_service, "_effective_pt_bmodel_comparison", lambda row, edge: {})
    monkeypatch.setattr(
        pub,
        "_bmodel_eval_gate_result",
        lambda *args, **kwargs: ("missing", None, 0.02),
    )
    monkeypatch.setattr(pub, "create_job", lambda **kwargs: created.update(kwargs) or "manual-job")
    monkeypatch.setattr(pub, "load_job", lambda job_id: {
        "job_id": job_id,
        "export_id": 12,
        "status": "PROCESSING",
        **created,
    })
    monkeypatch.setattr(pub, "_job_to_response", lambda db, raw: raw)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: type("Loop", (), {"run_in_executor": lambda *args: None})())

    with pytest.raises(LabelUException):
        asyncio.run(
            pub.start_publish_job(object(), user, export_id=12, publish_mode="manual")
        )
    assert created == {}
