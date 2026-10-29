from __future__ import annotations

from datetime import datetime

from labelu.internal.application.command.yolo_quality_policy import (
    CreateOrUpdateYoloQualityPolicyCommand,
)
from labelu.internal.application.response.board_agent import (
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.yolo_train import (
    TrainingAssessmentResponse,
    YoloTestValMetrics,
    YoloTrainingValMetrics,
    YoloValTestComparison,
)
from labelu.internal.application.service import (
    yolo_quality_policy as yolo_quality_policy_service,
)
from labelu.internal.application.service.yolo_quality_assessment import (
    build_assessment_payload,
    build_edge_validation_assessment,
    build_edge_usability_assessment,
    build_generalization_quality_assessment,
    build_quality_assessment_bundle,
    build_release_gate_assessment,
    build_release_readiness_assessment,
    build_training_assessment,
    build_training_quality_assessment,
)


def test_build_training_quality_assessment_pass():
    result = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.61,
            map50=0.72,
            summary_line="val good",
        ),
        total_sample_count=120,
    )

    assert result.status == "达标"
    assert result.score == 0.61


def test_build_training_quality_assessment_uses_auto_training_overall_recall():
    result = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            overall_recall=0.9876,
            overall_recall_threshold=0.95,
            source="auto_training_evaluation",
            summary_line="overall_recall=0.9876",
        ),
        total_sample_count=120,
    )

    assert result.status == "达标"
    assert result.score == 0.9876
    assert result.summary == "overall_recall=0.9876"


def test_build_training_assessment_reports_auto_training_recall_fallback():
    result = build_training_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            overall_recall=0.93,
            overall_recall_threshold=0.95,
            source="auto_training_evaluation",
        ),
        total_sample_count=120,
    )

    assert result.status_code == "failed"
    assert result.status_label == "不达标"
    assert "overall_recall_fallback_only" in result.reason_codes
    assert "overall_recall_below_pass_line" in result.reason_codes


def test_build_training_assessment_prefers_scene_thresholds_and_reason_codes():
    result = build_training_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.57,
            map50=0.66,
            summary_line="val ok",
        ),
        total_sample_count=8,
    )

    assert result.status_code == "conditional_passed"
    assert result.status_label == "条件达标"
    assert "sample_count_low" in result.reason_codes
    assert result.evidence is not None
    assert result.evidence["total_sample_count"] == 8


def test_build_training_quality_assessment_conditional_when_samples_small():
    result = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.57,
            map50=0.66,
            summary_line="val ok",
        ),
        total_sample_count=8,
    )

    assert result.status == "条件达标"
    assert any("训练样本总数较少" in item for item in result.reasons)


def test_build_generalization_quality_assessment_unverified_without_test():
    result = build_generalization_quality_assessment(
        test_val_metrics=None,
        val_test_comparison=None,
    )

    assert result.status == "未验证"


def test_build_generalization_quality_assessment_risky_when_gap_large():
    result = build_generalization_quality_assessment(
        test_val_metrics=YoloTestValMetrics(
            status="ok",
            map50_95=0.56,
            map50=0.7,
            summary_line="test ok",
        ),
        val_test_comparison=YoloValTestComparison(
            delta_map50_95=0.18,
            delta_map50=0.03,
        ),
    )

    assert result.status == "有风险"
    assert any("val/test 指标波动较大" in item for item in result.reasons)


def test_build_edge_usability_assessment_run_only_completed():
    result = build_edge_usability_assessment(
        edge_validation=BoardValidationStatusResponse(
            validation_id=1,
            export_id=2,
            status="COMPLETED",
            completed_at=datetime.now(),
            report_summary="manual run only",
            report={
                "mode": "run_only",
                "execution": {"status": "ok"},
            },
        )
    )

    assert result.status == "可运行但效果待确认"


def test_build_edge_validation_assessment_run_only_completed():
    result = build_edge_validation_assessment(
        edge_validation=BoardValidationStatusResponse(
            validation_id=1,
            export_id=2,
            status="COMPLETED",
            completed_at=datetime.now(),
            report_summary="manual run only",
            report={
                "mode": "run_only",
                "execution": {"status": "ok"},
            },
        )
    )

    assert result.status_code == "usable_needs_review"
    assert result.status_label == "可运行但效果待确认"
    assert "run_only_only" in result.reason_codes


def test_completed_edge_expected_comparison_missing_requires_review():
    edge = BoardValidationStatusResponse(
        validation_id=1,
        export_id=2,
        status="COMPLETED",
        completed_at=datetime.now(),
        report={"mode": "run_and_eval", "execution": {"status": "ok"}},
    )

    result = build_edge_validation_assessment(
        edge_validation=edge,
        pt_to_bmodel_comparison_expected=True,
    )

    assert result.status_code == "usable_needs_review"
    assert "pt_bmodel_comparison_missing" in result.reason_codes


def test_missing_fp16_comparison_uses_deployment_conversion_wording():
    edge = BoardValidationStatusResponse(
        validation_id=1,
        export_id=2,
        status="COMPLETED",
        completed_at=datetime.now(),
        report={"mode": "run_and_eval", "execution": {"status": "ok"}},
    )

    result = build_edge_usability_assessment(
        edge_validation=edge,
        pt_to_bmodel_comparison={
            "status": "skip",
            "verdict": "not_evaluable",
            "precision": "fp16",
            "reason_code": "comparison_metrics_missing",
        },
        pt_to_bmodel_comparison_expected=True,
    )

    assert result.status == "可运行但效果待确认"
    assert "部署转换精度损失" in result.reasons[0]
    assert "量化掉点" not in result.reasons[0]


def test_completed_edge_without_conversion_context_keeps_legacy_usable_status():
    result = build_edge_validation_assessment(
        edge_validation=BoardValidationStatusResponse(
            validation_id=1,
            export_id=2,
            status="COMPLETED",
            completed_at=datetime.now(),
            report={"mode": "run_and_eval", "execution": {"status": "ok"}},
        )
    )

    assert result.status_code == "usable"


def test_build_release_readiness_assessment_blocked_by_publish_gate():
    training = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(map50_95=0.7, map50=0.8),
        total_sample_count=100,
    )
    generalization = build_generalization_quality_assessment(
        test_val_metrics=YoloTestValMetrics(status="ok", map50_95=0.68, map50=0.79),
        val_test_comparison=YoloValTestComparison(
            delta_map50_95=0.02, delta_map50=0.01
        ),
    )
    edge = build_edge_usability_assessment(
        edge_validation=BoardValidationStatusResponse(
            validation_id=1,
            export_id=2,
            status="COMPLETED",
            completed_at=datetime.now(),
            report={"mode": "run_and_eval", "execution": {"status": "ok"}},
        )
    )

    result = build_release_readiness_assessment(
        training_quality=training,
        generalization_quality=generalization,
        edge_usability=edge,
        publish_blocked_reason="边端验证未通过",
    )

    assert result.status == "不可发布"


def test_build_release_gate_assessment_turns_conditional_training_into_review():
    training_assessment = build_training_assessment(
        training_val_metrics=YoloTrainingValMetrics(map50_95=0.58, map50=0.72),
        total_sample_count=10,
    )
    edge_assessment = build_edge_validation_assessment(
        edge_validation=BoardValidationStatusResponse(
            validation_id=1,
            export_id=2,
            status="COMPLETED",
            completed_at=datetime.now(),
            report={"mode": "run_and_eval", "execution": {"status": "ok"}},
        )
    )
    generalization = build_generalization_quality_assessment(
        test_val_metrics=YoloTestValMetrics(status="ok", map50_95=0.62, map50=0.74),
        val_test_comparison=YoloValTestComparison(
            delta_map50_95=0.01, delta_map50=0.01
        ),
    )

    result = build_release_gate_assessment(
        training_assessment=training_assessment,
        edge_validation_assessment=edge_assessment,
        generalization_quality=generalization,
    )

    assert result.status_code == "review_required"
    assert result.status_label == "需复核"
    assert "training_conditional" in result.reason_codes


def test_assessment_payload_uses_composite_training_override_for_plate_jobs():
    composite = TrainingAssessmentResponse(
        status_code="conditional_passed",
        status_label="条件达标",
        reason_codes=["ocr_val_accuracy"],
        reason_summary="OCR 验证指标待复核",
        summary="OCR 验证指标待复核",
        evidence={"reason_details": ["OCR 验证指标待复核"]},
    )

    payload = build_assessment_payload(
        training_val_metrics=None,
        training_assessment_override=composite,
        test_val_metrics=None,
        val_test_comparison=None,
        total_sample_count=3227,
        edge_validation=BoardValidationStatusResponse(
            validation_id=126,
            export_id=146,
            status="COMPLETED",
            completed_at=datetime.now(),
            report={"mode": "run_and_eval", "execution": {"status": "ok"}},
        ),
        rect_preset_slug="plateRecognition",
        pt_to_bmodel_comparison_expected=True,
    )

    assert payload.training_assessment is composite
    assert payload.quality_assessment.training_quality.status == "条件达标"
    assert payload.release_gate_assessment.status_code == "review_required"
    assert "training_pending" not in payload.release_gate_assessment.reason_codes


def test_build_quality_assessment_bundle_happy_path():
    bundle = build_quality_assessment_bundle(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.66,
            map50=0.77,
            summary_line="val pass",
        ),
        test_val_metrics=YoloTestValMetrics(
            status="ok",
            map50_95=0.63,
            map50=0.75,
            summary_line="test pass",
        ),
        val_test_comparison=YoloValTestComparison(
            delta_map50_95=0.03,
            delta_map50=0.02,
        ),
        total_sample_count=160,
        edge_validation=BoardValidationStatusResponse(
            validation_id=3,
            export_id=4,
            status="COMPLETED",
            completed_at=datetime.now(),
            report_summary="edge ok",
            report={"mode": "run_and_eval", "execution": {"status": "ok"}},
        ),
        publish_blocked_reason=None,
        rect_preset_slug="preset-a",
    )

    assert bundle.training_quality is not None
    assert bundle.generalization_quality is not None
    assert bundle.edge_usability is not None
    assert bundle.release_readiness is not None
    assert bundle.release_readiness.status == "可发布"


def test_build_training_quality_assessment_uses_active_scene_policy(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        yolo_quality_policy_service,
        "ensure_quality_policy_preset_exists",
        lambda db, rect_preset_slug: None,
    )
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="strict-scene",
            scene_name="严格场景",
            rect_preset_slug="strict_scene",
            status="active",
            source="manual",
            training_quality={"map50_95_pass": 0.70, "map50_warn": 0.65},
        )
    )

    result = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.66, map50=0.76, summary_line="val ok"
        ),
        total_sample_count=100,
        rect_preset_slug="strict_scene",
    )

    assert result.status == "条件达标"
    assert any("0.70" in item for item in result.reasons)


def test_build_training_quality_assessment_disallows_conditional_pass_when_policy_strict(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        yolo_quality_policy_service,
        "ensure_quality_policy_preset_exists",
        lambda db, rect_preset_slug: None,
    )
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="strict-no-conditional",
            scene_name="严格场景",
            rect_preset_slug="strict_scene",
            status="active",
            source="manual",
            training_quality={
                "map50_95_pass": 0.70,
                "map50_warn": 0.65,
                "allow_conditional_pass": False,
            },
        )
    )

    result = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.66, map50=0.76, summary_line="val ok"
        ),
        total_sample_count=100,
        rect_preset_slug="strict_scene",
    )

    assert result.status == "不达标"
    assert any("不允许条件达标" in item for item in result.reasons)


def test_build_generalization_quality_assessment_can_skip_test_by_policy(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        yolo_quality_policy_service,
        "ensure_quality_policy_preset_exists",
        lambda db, rect_preset_slug: None,
    )
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="generalization-optional",
            scene_name="可跳过泛化验证",
            rect_preset_slug="generalization_optional",
            status="active",
            source="manual",
            generalization_quality={"require_test_metrics": False},
        )
    )

    result = build_generalization_quality_assessment(
        test_val_metrics=None,
        val_test_comparison=None,
        rect_preset_slug="generalization_optional",
    )

    assert result.status == "未要求"


def test_build_quality_assessment_bundle_respects_edge_and_release_policy(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        yolo_quality_policy_service,
        "ensure_quality_policy_preset_exists",
        lambda db, rect_preset_slug: None,
    )
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="release-strict-scene",
            scene_name="发布严格场景",
            rect_preset_slug="release_strict_scene",
            status="active",
            source="manual",
            training_quality={"map50_95_pass": 0.60, "map50_warn": 0.55},
            generalization_quality={"require_test_metrics": True},
            edge_usability={"require_edge_validation": False},
            release_readiness={"allow_review_when_generalization_missing": False},
        )
    )

    bundle = build_quality_assessment_bundle(
        training_val_metrics=YoloTrainingValMetrics(
            map50_95=0.66,
            map50=0.77,
            summary_line="val pass",
        ),
        test_val_metrics=None,
        val_test_comparison=None,
        total_sample_count=120,
        edge_validation=None,
        publish_blocked_reason=None,
        rect_preset_slug="release_strict_scene",
    )

    assert bundle.edge_usability.status == "未要求"
    assert bundle.generalization_quality.status == "未验证"
    assert bundle.release_readiness.status == "不可发布"


def test_scene_bmodel_drop_policy_blocks_edge_and_release(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path)
    )
    monkeypatch.setattr(
        yolo_quality_policy_service,
        "ensure_quality_policy_preset_exists",
        lambda db, rect_preset_slug: None,
    )
    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="strict-bmodel-drop",
            scene_name="量化严格场景",
            rect_preset_slug="strict_bmodel",
            status="active",
            source="manual",
            edge_usability={
                "require_edge_validation": True,
                "require_eval_when_labeled_images_exist": True,
                "max_pt_to_bmodel_map50_drop": 0.015,
            },
            release_readiness={"block_publish_when_edge_unverified": True},
        )
    )
    edge = BoardValidationStatusResponse(
        validation_id=3,
        export_id=4,
        status="COMPLETED",
        completed_at=datetime.now(),
        report={"mode": "run_and_eval", "execution": {"status": "ok"}},
    )

    payload = build_assessment_payload(
        training_val_metrics=YoloTrainingValMetrics(map50_95=0.70, map50=0.80),
        test_val_metrics=YoloTestValMetrics(status="ok", map50_95=0.68, map50=0.78),
        val_test_comparison=YoloValTestComparison(
            delta_map50_95=0.02,
            delta_map50=0.02,
        ),
        total_sample_count=120,
        edge_validation=edge,
        rect_preset_slug="strict_bmodel",
        pt_to_bmodel_comparison={"pt_map50": 0.80, "bmodel_map50": 0.78},
        pt_to_bmodel_comparison_expected=True,
    )

    assert payload.edge_validation_assessment.status_code == "unusable"
    assert (
        "pt_bmodel_drop_exceeds_policy"
        in payload.edge_validation_assessment.reason_codes
    )
    assert payload.release_gate_assessment.status_code == "blocked"
    assert (
        "pt_bmodel_drop_exceeds_policy" in payload.release_gate_assessment.reason_codes
    )
    assert payload.quality_assessment.edge_usability.status == "不可用"
    assert payload.quality_assessment.release_readiness.status == "不可发布"
