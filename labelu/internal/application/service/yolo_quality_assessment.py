from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
from typing import Any, Optional
import json

from labelu.internal.application.response.board_agent import (
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.yolo_train import (
    EdgeValidationAssessmentResponse,
    ReleaseGateAssessmentResponse,
    TrainingAssessmentResponse,
    YoloQualityAssessmentBundle,
    YoloQualityGateItem,
    YoloTestValMetrics,
    YoloTrainingValMetrics,
    YoloValTestComparison,
)
from labelu.internal.common.config import settings
from labelu.internal.application.service.yolo_quality_policy import (
    resolve_policy_for_preset,
)

_FROZEN_POLICY: ContextVar[dict[str, Any] | None] = ContextVar(
    "frozen_quality_policy", default=None
)


def quality_policy_snapshot_for_job(job: Any) -> dict[str, Any] | None:
    raw = getattr(job, "quality_policy_snapshot_json", None)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _default_policy() -> dict[str, Any]:
    return {
        "default_policy": {
            "training_quality": {
                "map50_95_pass": float(settings.YOLO_VAL_PASS_MAP5095),
                "map50_warn": float(settings.YOLO_VAL_PASS_MAP50),
                "min_total_samples": 20,
            },
            "generalization_quality": {
                "test_map50_95_warn": max(
                    float(settings.YOLO_VAL_PASS_MAP5095) - 0.1, 0.0
                ),
                "max_gap_map50_95": 0.12,
                "max_gap_map50": 0.12,
            },
            "edge_usability": {
                "require_edge_validation": False,
                "require_eval_when_labeled_images_exist": True,
                "max_pt_to_bmodel_map50_drop": 0.02,
            },
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _policy_for_preset(rect_preset_slug: Optional[str]) -> dict[str, Any]:
    default_policy = _default_policy().get("default_policy") or {}
    resolved = _FROZEN_POLICY.get() or resolve_policy_for_preset(rect_preset_slug)
    if not isinstance(resolved, dict):
        return default_policy
    merged = dict(default_policy)
    for section in (
        "training_quality",
        "generalization_quality",
        "edge_usability",
        "release_readiness",
    ):
        current = merged.get(section) or {}
        override = resolved.get(section) or {}
        if isinstance(current, dict) and isinstance(override, dict):
            merged[section] = _deep_merge(current, override)
    return merged


def _round_score(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _policy_max_bmodel_drop(policy: dict[str, Any]) -> float:
    try:
        return max(float(policy.get("max_pt_to_bmodel_map50_drop", 0.02)), 0.0)
    except (TypeError, ValueError):
        return 0.02


def _evaluate_pt_bmodel_comparison_for_policy(
    comparison: Optional[dict[str, Any]],
    *,
    max_drop: float,
) -> tuple[str, Optional[dict[str, Any]], Optional[float]]:
    """Return policy state, normalized comparison and one-sided absolute drop."""
    if not isinstance(comparison, dict) or not comparison:
        return "missing", None, None

    effective = dict(comparison)
    pt_map50 = effective.get("pt_map50")
    bmodel_map50 = effective.get("bmodel_map50")
    if pt_map50 is not None and bmodel_map50 is not None:
        try:
            from labelu.internal.application.service.yolo_sop_model_evaluation import (
                BMODEL_PASS_MAX_DROP,
                _compare_pt_bmodel,
            )

            precision = str(effective.get("precision") or "").strip().lower()
            preserved_evidence = {
                key: effective.get(key)
                for key in ("same_batch", "dataset_evidence", "eval_params")
                if effective.get(key) is not None
            }
            effective = _compare_pt_bmodel(
                {"map50": float(pt_map50)},
                {"map50": float(bmodel_map50)},
                precision=precision or None,
                pass_max_drop=min(BMODEL_PASS_MAX_DROP, max_drop),
                max_drop=max_drop,
            )
            effective.update(preserved_evidence)
        except (TypeError, ValueError):
            pass

    drop_value = effective.get("drop_map50")
    if drop_value is None and pt_map50 is not None and bmodel_map50 is not None:
        try:
            drop_value = max(float(pt_map50) - float(bmodel_map50), 0.0)
        except (TypeError, ValueError):
            drop_value = None
    try:
        drop = float(drop_value) if drop_value is not None else None
    except (TypeError, ValueError):
        drop = None
    if drop is None or str(effective.get("verdict") or "").lower() == "not_evaluable":
        legacy_status = str(effective.get("status") or "").strip().lower()
        if legacy_status in {"ok", "warn", "fail"}:
            effective.update(
                {
                    "status": "warn",
                    "verdict": "review",
                    "reason_code": "legacy_comparison_evidence_requires_review",
                    "evidence_quality": "legacy_unverified",
                    "legacy_status": legacy_status,
                    "detail": (
                        str(effective.get("detail") or "").strip()
                        + "；旧版比较缺少单向绝对掉点证据，需按新口径复核"
                    ).lstrip("；"),
                }
            )
            return "review", effective, None
        return "missing", effective, None
    if drop > max_drop:
        return "exceeded", effective, drop
    if (
        str(effective.get("verdict") or "").lower() == "review"
        or str(effective.get("status") or "").lower() == "warn"
    ):
        return "review", effective, drop
    return "pass", effective, drop


@dataclass(frozen=True)
class AssessmentPayload:
    training_assessment: TrainingAssessmentResponse
    edge_validation_assessment: EdgeValidationAssessmentResponse
    release_gate_assessment: ReleaseGateAssessmentResponse
    generalization_quality: YoloQualityGateItem
    quality_assessment: YoloQualityAssessmentBundle


def _first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _reason_details_from_evidence(evidence: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    details = evidence.get("reason_details")
    if isinstance(details, list):
        return [str(item).strip() for item in details if str(item).strip()]
    return []


def _gate_item_from_assessment(
    *,
    key: str,
    label: str,
    assessment: (
        TrainingAssessmentResponse
        | EdgeValidationAssessmentResponse
        | ReleaseGateAssessmentResponse
    ),
) -> YoloQualityGateItem:
    evidence = assessment.evidence if isinstance(assessment.evidence, dict) else {}
    score = _round_score(evidence.get("score"))
    summary = _first_non_empty(
        assessment.summary,
        assessment.reason_summary,
        assessment.status_label,
    )
    reasons = _reason_details_from_evidence(evidence)
    if not reasons and assessment.reason_summary:
        reasons = [assessment.reason_summary]
    return YoloQualityGateItem(
        key=key,
        label=label,
        status=assessment.status_label,
        score=score,
        summary=summary,
        reasons=reasons,
    )


def build_training_quality_assessment(
    *,
    training_val_metrics: Optional[YoloTrainingValMetrics],
    total_sample_count: Optional[int],
    rect_preset_slug: Optional[str] = None,
) -> YoloQualityGateItem:
    policy = _policy_for_preset(rect_preset_slug).get("training_quality") or {}
    pass_line = float(policy.get("map50_95_pass", settings.YOLO_VAL_PASS_MAP5095))
    warn_line = float(policy.get("map50_warn", settings.YOLO_VAL_PASS_MAP50))
    min_total_samples = int(policy.get("min_total_samples", 20))
    allow_conditional_pass = bool(policy.get("allow_conditional_pass", True))
    reasons: list[str] = []

    if training_val_metrics is None:
        return YoloQualityGateItem(
            key="training_quality",
            label="训练质量",
            status="待验证",
            summary="缺少训练验证指标",
            reasons=["未读取到 val 指标"],
        )

    score = (
        training_val_metrics.map50_95
        if training_val_metrics.map50_95 is not None
        else training_val_metrics.map50
    )
    if total_sample_count is not None and int(total_sample_count) < min_total_samples:
        reasons.append(f"训练样本总数较少({total_sample_count})")
    overall_recall = training_val_metrics.overall_recall
    # Auto-training's trusted evaluator can be the only quality evidence for
    # a local fixture or a historical job.  Keep this fallback explicit: an
    # overall recall must never be silently treated as mAP.
    if (
        training_val_metrics.map50_95 is None
        and training_val_metrics.map50 is None
        and overall_recall is not None
    ):
        try:
            recall_pass_line = float(
                policy.get(
                    "overall_recall_pass",
                    training_val_metrics.overall_recall_threshold
                    if training_val_metrics.overall_recall_threshold is not None
                    else 0.95,
                )
            )
        except (TypeError, ValueError):
            recall_pass_line = 0.95
        try:
            recall_warn_line = float(
                policy.get("overall_recall_warn", recall_pass_line)
            )
        except (TypeError, ValueError):
            recall_warn_line = recall_pass_line
        recall_pass_line = min(max(recall_pass_line, 0.0), 1.0)
        recall_warn_line = min(max(recall_warn_line, 0.0), recall_pass_line)
        score = overall_recall
        if float(overall_recall) >= recall_pass_line:
            status = "达标" if not reasons else "条件达标" if allow_conditional_pass else "不达标"
        elif float(overall_recall) >= recall_warn_line:
            reasons.append(
                f"overall_recall={float(overall_recall):.4f} 低于达标线 {recall_pass_line:.2f}"
            )
            status = "条件达标" if allow_conditional_pass else "不达标"
        else:
            status = "不达标"
            reasons.append(
                f"overall_recall={float(overall_recall):.4f} 低于预期"
            )
        summary = training_val_metrics.summary_line or "基于自动训练评估整体召回率"
        return YoloQualityGateItem(
            key="training_quality",
            label="训练质量",
            status=status,
            score=_round_score(score),
            summary=summary,
            reasons=reasons,
        )
    if training_val_metrics.map50_95 is not None:
        if float(training_val_metrics.map50_95) >= pass_line:
            if not reasons:
                status = "达标"
            else:
                status = "条件达标" if allow_conditional_pass else "不达标"
        elif float(training_val_metrics.map50_95) >= warn_line:
            reasons.append(
                f"mAP50-95={training_val_metrics.map50_95:.4f} 低于达标线 {pass_line:.2f}"
            )
            status = "条件达标" if allow_conditional_pass else "不达标"
        else:
            status = "不达标"
            reasons.append(f"mAP50-95={training_val_metrics.map50_95:.4f} 低于预期")
    elif training_val_metrics.map50 is not None:
        if float(training_val_metrics.map50) >= warn_line:
            reasons.append("缺少 mAP50-95，当前仅按 mAP50 粗判")
            status = "条件达标" if allow_conditional_pass else "不达标"
        else:
            status = "不达标"
            reasons.append(f"mAP50={training_val_metrics.map50:.4f} 低于预期")
    else:
        status = "待验证"
        reasons.append("训练验证指标缺失")

    if status in {"条件达标", "不达标"} and not allow_conditional_pass:
        reasons.append("当前场景标准不允许条件达标")

    summary = training_val_metrics.summary_line or "基于训练验证集指标聚合"
    return YoloQualityGateItem(
        key="training_quality",
        label="训练质量",
        status=status,
        score=_round_score(score),
        summary=summary,
        reasons=reasons,
    )


def build_training_assessment(
    *,
    training_val_metrics: Optional[YoloTrainingValMetrics],
    total_sample_count: Optional[int],
    rect_preset_slug: Optional[str] = None,
) -> TrainingAssessmentResponse:
    item = build_training_quality_assessment(
        training_val_metrics=training_val_metrics,
        total_sample_count=total_sample_count,
        rect_preset_slug=rect_preset_slug,
    )
    policy = _policy_for_preset(rect_preset_slug).get("training_quality") or {}
    pass_line = float(policy.get("map50_95_pass", settings.YOLO_VAL_PASS_MAP5095))
    warn_line = float(policy.get("map50_warn", settings.YOLO_VAL_PASS_MAP50))
    min_total_samples = int(policy.get("min_total_samples", 20))
    allow_conditional_pass = bool(policy.get("allow_conditional_pass", True))
    reason_codes: list[str] = []
    if training_val_metrics is None:
        reason_codes.append("val_metrics_missing")
    else:
        if (
            total_sample_count is not None
            and int(total_sample_count) < min_total_samples
        ):
            reason_codes.append("sample_count_low")
        if (
            training_val_metrics.map50_95 is None
            and training_val_metrics.map50 is None
            and training_val_metrics.overall_recall is not None
        ):
            reason_codes.append("overall_recall_fallback_only")
            try:
                recall_pass_line = float(
                    policy.get(
                        "overall_recall_pass",
                        training_val_metrics.overall_recall_threshold
                        if training_val_metrics.overall_recall_threshold is not None
                        else 0.95,
                    )
                )
            except (TypeError, ValueError):
                recall_pass_line = 0.95
            if float(training_val_metrics.overall_recall) < recall_pass_line:
                reason_codes.append("overall_recall_below_pass_line")
                try:
                    recall_warn_line = float(
                        policy.get("overall_recall_warn", recall_pass_line)
                    )
                except (TypeError, ValueError):
                    recall_warn_line = recall_pass_line
                if (
                    float(training_val_metrics.overall_recall) >= recall_warn_line
                    and not allow_conditional_pass
                ):
                    reason_codes.append("conditional_pass_not_allowed")
        elif training_val_metrics.map50_95 is not None:
            if float(training_val_metrics.map50_95) < pass_line:
                reason_codes.append("map5095_below_pass_line")
                if (
                    float(training_val_metrics.map50_95) >= warn_line
                    and not allow_conditional_pass
                ):
                    reason_codes.append("conditional_pass_not_allowed")
        elif training_val_metrics.map50 is not None:
            reason_codes.append("map50_fallback_only")
            if (
                float(training_val_metrics.map50) >= warn_line
                and not allow_conditional_pass
            ):
                reason_codes.append("conditional_pass_not_allowed")
        else:
            reason_codes.append("val_metrics_missing")
    status_map = {
        "达标": "passed",
        "条件达标": "conditional_passed",
        "不达标": "failed",
        "待验证": "pending",
    }
    score = (
        training_val_metrics.map50_95
        if training_val_metrics and training_val_metrics.map50_95 is not None
        else (
            training_val_metrics.map50
            if training_val_metrics and training_val_metrics.map50 is not None
            else (
                training_val_metrics.overall_recall
                if training_val_metrics
                else None
            )
        )
    )
    return TrainingAssessmentResponse(
        status_code=status_map.get(str(item.status or "").strip(), "pending"),
        status_label=str(item.status or "待验证").strip() or "待验证",
        reason_codes=reason_codes,
        reason_summary=_first_non_empty(
            item.reasons[0] if item.reasons else "",
            item.summary,
            item.status,
        ),
        summary=item.summary,
        evidence={
            "score": _round_score(score),
            "total_sample_count": total_sample_count,
            "rect_preset_slug": rect_preset_slug,
            "training_val_metrics": (
                training_val_metrics.dict() if training_val_metrics else None
            ),
            "effective_standard_snapshot": policy,
            "reason_details": list(item.reasons or []),
        },
    )


def build_generalization_quality_assessment(
    *,
    test_val_metrics: Optional[YoloTestValMetrics],
    val_test_comparison: Optional[YoloValTestComparison],
    rect_preset_slug: Optional[str] = None,
) -> YoloQualityGateItem:
    policy = _policy_for_preset(rect_preset_slug).get("generalization_quality") or {}
    require_test_metrics = bool(policy.get("require_test_metrics", True))
    warn_line = float(
        policy.get(
            "test_map50_95_warn", max(float(settings.YOLO_VAL_PASS_MAP5095) - 0.1, 0.0)
        )
    )
    max_gap_map50_95 = float(policy.get("max_gap_map50_95", 0.12))
    max_gap_map50 = float(policy.get("max_gap_map50", 0.12))
    reasons: list[str] = []

    if test_val_metrics is None or str(test_val_metrics.status or "").lower() not in {
        "ok",
        "completed",
    }:
        if not require_test_metrics:
            return YoloQualityGateItem(
                key="generalization_quality",
                label="泛化质量",
                status="未要求",
                summary="当前场景标准未要求 test 集验证",
                reasons=["当前启用标准允许先完成训练判定"],
            )
        return YoloQualityGateItem(
            key="generalization_quality",
            label="泛化质量",
            status="未验证",
            summary="缺少 test 集验证结果",
            reasons=["未执行 test 集验证"],
        )

    score = (
        test_val_metrics.map50_95
        if test_val_metrics.map50_95 is not None
        else test_val_metrics.map50
    )
    status = "稳定"
    if (
        test_val_metrics.map50_95 is not None
        and float(test_val_metrics.map50_95) < warn_line
    ):
        status = "有风险"
        reasons.append(f"test mAP50-95={test_val_metrics.map50_95:.4f} 偏低")
    if val_test_comparison is not None:
        gap_5095 = abs(float(val_test_comparison.delta_map50_95 or 0.0))
        gap_50 = abs(float(val_test_comparison.delta_map50 or 0.0))
        if gap_5095 > max_gap_map50_95 or gap_50 > max_gap_map50:
            status = "有风险"
            reasons.append(
                f"val/test 指标波动较大(Δ50-95={gap_5095:.4f}, Δ50={gap_50:.4f})"
            )

    return YoloQualityGateItem(
        key="generalization_quality",
        label="泛化质量",
        status=status,
        score=_round_score(score),
        summary=test_val_metrics.summary_line or "基于 test 集验证结果聚合",
        reasons=reasons,
    )


def build_edge_usability_assessment(
    *,
    edge_validation: Optional[BoardValidationStatusResponse],
    rect_preset_slug: Optional[str] = None,
    pt_to_bmodel_comparison: Optional[dict[str, Any]] = None,
    pt_to_bmodel_comparison_expected: bool = False,
) -> YoloQualityGateItem:
    policy = _policy_for_preset(rect_preset_slug).get("edge_usability") or {}
    require_edge_validation = bool(policy.get("require_edge_validation", False))
    require_eval_when_labeled_images_exist = bool(
        policy.get("require_eval_when_labeled_images_exist", True)
    )
    max_bmodel_drop = _policy_max_bmodel_drop(policy)
    comparison_state, effective_comparison, comparison_drop = (
        _evaluate_pt_bmodel_comparison_for_policy(
            pt_to_bmodel_comparison,
            max_drop=max_bmodel_drop,
        )
    )
    comparison_expected = bool(
        pt_to_bmodel_comparison_expected or pt_to_bmodel_comparison is not None
    )
    reasons: list[str] = []
    if edge_validation is None:
        if not require_edge_validation:
            return YoloQualityGateItem(
                key="edge_usability",
                label="边端可用性",
                status="未要求",
                summary="当前场景标准未要求边端验证",
                reasons=["当前启用标准允许先完成训练判定"],
            )
        return YoloQualityGateItem(
            key="edge_usability",
            label="边端可用性",
            status="未验证",
            summary="尚无边端验证记录",
            reasons=["未进行边端验证"],
        )

    status_raw = str(edge_validation.status or "").upper()
    report = edge_validation.report if isinstance(edge_validation.report, dict) else {}
    mode = str(report.get("mode") or "").strip().lower()
    execution = (
        report.get("execution") if isinstance(report.get("execution"), dict) else {}
    )

    if status_raw == "COMPLETED":
        if mode == "run_only":
            if require_eval_when_labeled_images_exist:
                status = "可运行但效果待确认"
                reasons.append("本次仅验证模型可运行，未做标注集精度评估")
            else:
                status = "可用"
                reasons.append("当前场景标准仅要求边端可运行")
        elif comparison_state == "exceeded":
            status = "不可用"
            reasons.append(
                f"PT 到 BModel 的 mAP50 掉点 {float(comparison_drop or 0.0):.4f} "
                f"超过当前场景上限 {max_bmodel_drop:.4f}"
            )
        elif comparison_expected and comparison_state == "missing":
            status = "可运行但效果待确认"
            precision = (
                str((effective_comparison or {}).get("precision") or "int8")
                .strip()
                .lower()
            )
            loss_name = "量化掉点" if precision == "int8" else "部署转换精度损失"
            reasons.append(f"缺少 PT 与 BModel 同口径精度比较，无法确认{loss_name}")
        elif comparison_state == "review":
            # The conversion drop is inside the allowed review band. Keep the
            # measured drop visible as a warning, but do not create a manual
            # review gate: completed edge evidence is publishable.
            status = "可用"
            reasons.append(
                str(
                    (effective_comparison or {}).get("detail")
                    or "BModel 与 PT 存在精度差异，请关注掉点提示"
                )
            )
        else:
            status = "可用"
    elif status_raw in {"PROCESSING", "PENDING"}:
        status = "未验证"
        reasons.append("边端验证尚未完成")
    elif status_raw == "PARTIAL_COMPLETED":
        status = "可运行但效果待确认"
        reasons.append("边端验证部分完成")
    else:
        status = "不可用"
        if edge_validation.error_message:
            reasons.append(str(edge_validation.error_message))
        elif execution.get("status"):
            reasons.append(f"执行状态: {execution.get('status')}")
        else:
            reasons.append("边端验证失败")

    assessment_summary = edge_validation.report_summary or "基于最新边端验证结果聚合"
    if comparison_state == "review":
        comparison_detail = str(
            (effective_comparison or {}).get("detail")
            or "BModel 与 PT 存在精度差异，请关注掉点提示"
        )
        assessment_summary = f"边端检查完成，存在精度提示（不阻断发布）：{comparison_detail}"

    return YoloQualityGateItem(
        key="edge_usability",
        label="边端可用性",
        status=status,
        summary=assessment_summary,
        reasons=reasons,
    )


def build_edge_validation_assessment(
    *,
    edge_validation: Optional[BoardValidationStatusResponse],
    rect_preset_slug: Optional[str] = None,
    pt_to_bmodel_comparison: Optional[dict[str, Any]] = None,
    pt_to_bmodel_comparison_expected: bool = False,
) -> EdgeValidationAssessmentResponse:
    item = build_edge_usability_assessment(
        edge_validation=edge_validation,
        rect_preset_slug=rect_preset_slug,
        pt_to_bmodel_comparison=pt_to_bmodel_comparison,
        pt_to_bmodel_comparison_expected=pt_to_bmodel_comparison_expected,
    )
    policy = _policy_for_preset(rect_preset_slug).get("edge_usability") or {}
    require_edge_validation = bool(policy.get("require_edge_validation", False))
    require_eval_when_labeled_images_exist = bool(
        policy.get("require_eval_when_labeled_images_exist", True)
    )
    max_bmodel_drop = _policy_max_bmodel_drop(policy)
    comparison_state, effective_comparison, comparison_drop = (
        _evaluate_pt_bmodel_comparison_for_policy(
            pt_to_bmodel_comparison,
            max_drop=max_bmodel_drop,
        )
    )
    comparison_expected = bool(
        pt_to_bmodel_comparison_expected or pt_to_bmodel_comparison is not None
    )
    reason_codes: list[str] = []
    report = (
        getattr(edge_validation, "report", None)
        if edge_validation is not None
        else None
    )
    report = report if isinstance(report, dict) else {}
    if edge_validation is None:
        if require_edge_validation:
            reason_codes.append("edge_validation_missing")
    else:
        status_raw = str(getattr(edge_validation, "status", "") or "").upper()
        mode = str(report.get("mode") or "").strip().lower()
        if status_raw in {"PENDING", "PROCESSING"}:
            reason_codes.append("edge_validation_running")
        elif status_raw == "FAILED":
            reason_codes.append("edge_execution_failed")
        elif status_raw == "PARTIAL_COMPLETED":
            reason_codes.append("edge_eval_failed")
        elif status_raw == "COMPLETED" and mode == "run_only":
            reason_codes.append("run_only_only")
            if require_eval_when_labeled_images_exist:
                reason_codes.append("labeled_eval_missing")
        elif (
            status_raw == "COMPLETED"
            and comparison_expected
            and comparison_state == "missing"
        ):
            reason_codes.append("pt_bmodel_comparison_missing")
        elif status_raw == "COMPLETED" and comparison_state == "exceeded":
            reason_codes.append("pt_bmodel_drop_exceeds_policy")
        # A comparison in the review band is warning-only. It is deliberately
        # omitted from blocking reason codes so the release gate can proceed.
    status_map = {
        "可用": "usable",
        "可运行但效果待确认": "usable_needs_review",
        "未验证": "pending",
        "不可用": "unusable",
        "未要求": "usable",
    }
    return EdgeValidationAssessmentResponse(
        status_code=status_map.get(str(item.status or "").strip(), "pending"),
        status_label=(
            "可用"
            if str(item.status or "").strip() == "未要求"
            else str(item.status or "未验证").strip() or "未验证"
        ),
        reason_codes=reason_codes,
        reason_summary=_first_non_empty(
            item.reasons[0] if item.reasons else "",
            item.summary,
            item.status,
        ),
        summary=item.summary,
        evidence={
            "rect_preset_slug": rect_preset_slug,
            "validation_status": (
                str(getattr(edge_validation, "status", "") or "").upper()
                if edge_validation is not None
                else ""
            ),
            "mode": str(report.get("mode") or "").strip().lower(),
            "report_summary": (
                getattr(edge_validation, "report_summary", None)
                if edge_validation is not None
                else None
            ),
            "effective_standard_snapshot": policy,
            "max_pt_to_bmodel_map50_drop": max_bmodel_drop,
            "pt_to_bmodel_comparison": effective_comparison,
            "pt_to_bmodel_drop_map50": comparison_drop,
            "reason_details": list(item.reasons or []),
        },
    )


def build_release_readiness_assessment(
    *,
    training_quality: YoloQualityGateItem,
    generalization_quality: YoloQualityGateItem,
    edge_usability: YoloQualityGateItem,
    publish_blocked_reason: Optional[str] = None,
    rect_preset_slug: Optional[str] = None,
) -> YoloQualityGateItem:
    policy = _policy_for_preset(rect_preset_slug).get("release_readiness") or {}
    block_publish_when_training_failed = bool(
        policy.get("block_publish_when_training_failed", True)
    )
    block_publish_when_edge_unverified = bool(
        policy.get("block_publish_when_edge_unverified", False)
    )
    allow_review_when_generalization_missing = bool(
        policy.get("allow_review_when_generalization_missing", True)
    )
    reasons: list[str] = []
    if publish_blocked_reason:
        reasons.append(str(publish_blocked_reason))
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="不可发布",
            summary="当前发布门禁未通过",
            reasons=reasons,
        )

    if training_quality.status == "待验证":
        reasons.extend(training_quality.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="不可发布",
            summary="训练质量尚未完成判定",
            reasons=reasons,
        )

    if training_quality.status == "不达标":
        reasons.extend(training_quality.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="不可发布" if block_publish_when_training_failed else "需复核",
            summary=(
                "训练质量未达标"
                if block_publish_when_training_failed
                else "训练质量未达标，需人工复核"
            ),
            reasons=reasons,
        )

    if edge_usability.status == "不可用":
        reasons.extend(edge_usability.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="不可发布",
            summary="边端可用性验证未通过",
            reasons=reasons,
        )

    if edge_usability.status in {"未验证", "可运行但效果待确认"}:
        reasons.extend(edge_usability.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="不可发布" if block_publish_when_edge_unverified else "需复核",
            summary=(
                "边端可用性尚未充分确认"
                if not block_publish_when_edge_unverified
                else "边端验证尚未满足发布要求"
            ),
            reasons=reasons,
        )

    if generalization_quality.status == "未验证":
        reasons.extend(generalization_quality.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="需复核" if allow_review_when_generalization_missing else "不可发布",
            summary=(
                "泛化质量仍需人工复核"
                if allow_review_when_generalization_missing
                else "泛化质量验证缺失，不满足发布要求"
            ),
            reasons=reasons,
        )

    if generalization_quality.status == "有风险":
        reasons.extend(generalization_quality.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="需复核",
            summary="泛化质量仍需人工复核",
            reasons=reasons,
        )

    if training_quality.status == "条件达标":
        reasons.extend(training_quality.reasons or [])
        return YoloQualityGateItem(
            key="release_readiness",
            label="发布就绪度",
            status="需复核",
            summary="训练质量为条件达标",
            reasons=reasons,
        )

    return YoloQualityGateItem(
        key="release_readiness",
        label="发布就绪度",
        status="可发布",
        summary="训练、泛化与边端验证结果均满足当前要求",
        reasons=reasons,
    )


def build_release_gate_assessment(
    *,
    training_assessment: TrainingAssessmentResponse,
    edge_validation_assessment: EdgeValidationAssessmentResponse,
    generalization_quality: YoloQualityGateItem,
    publish_blocked_reason: Optional[str] = None,
    rect_preset_slug: Optional[str] = None,
) -> ReleaseGateAssessmentResponse:
    training_quality = _gate_item_from_assessment(
        key="training_quality",
        label="训练质量",
        assessment=training_assessment,
    )
    edge_usability = _gate_item_from_assessment(
        key="edge_usability",
        label="边端可用性",
        assessment=edge_validation_assessment,
    )
    item = build_release_readiness_assessment(
        training_quality=training_quality,
        generalization_quality=generalization_quality,
        edge_usability=edge_usability,
        publish_blocked_reason=publish_blocked_reason,
        rect_preset_slug=rect_preset_slug,
    )
    status_map = {
        "可发布": "publishable",
        "需复核": "review_required",
        "不可发布": "blocked",
    }
    reason_codes: list[str] = []
    if publish_blocked_reason:
        reason_codes.append("publish_config_blocked")
    elif training_assessment.status_code == "pending":
        reason_codes.append("training_pending")
    elif training_assessment.status_code == "failed":
        reason_codes.append("training_failed")
    elif edge_validation_assessment.status_code == "unusable":
        reason_codes.extend(
            edge_validation_assessment.reason_codes or ["edge_unusable"]
        )
    elif edge_validation_assessment.status_code in {"pending", "usable_needs_review"}:
        reason_codes.extend(edge_validation_assessment.reason_codes or ["edge_pending"])
    elif generalization_quality.status == "未验证":
        reason_codes.append("generalization_missing_review")
    elif generalization_quality.status == "有风险":
        reason_codes.append("generalization_risky_review")
    elif training_assessment.status_code == "conditional_passed":
        reason_codes.append("training_conditional")
    return ReleaseGateAssessmentResponse(
        status_code=status_map.get(str(item.status or "").strip(), "blocked"),
        status_label=str(item.status or "不可发布").strip() or "不可发布",
        reason_codes=reason_codes,
        reason_summary=_first_non_empty(
            item.reasons[0] if item.reasons else "",
            item.summary,
            item.status,
        ),
        summary=item.summary,
        evidence={
            "training_status_code": training_assessment.status_code,
            "edge_status_code": edge_validation_assessment.status_code,
            "edge_reason_codes": list(edge_validation_assessment.reason_codes or []),
            "release_rule_snapshot": _policy_for_preset(rect_preset_slug).get(
                "release_readiness"
            )
            or {},
            "reason_details": list(item.reasons or []),
        },
    )


def project_quality_assessment_compat(
    *,
    training_assessment: TrainingAssessmentResponse,
    generalization_quality: YoloQualityGateItem,
    edge_validation_assessment: EdgeValidationAssessmentResponse,
    release_gate_assessment: ReleaseGateAssessmentResponse,
) -> YoloQualityAssessmentBundle:
    return YoloQualityAssessmentBundle(
        training_quality=_gate_item_from_assessment(
            key="training_quality",
            label="训练质量",
            assessment=training_assessment,
        ),
        generalization_quality=generalization_quality,
        edge_usability=_gate_item_from_assessment(
            key="edge_usability",
            label="边端可用性",
            assessment=edge_validation_assessment,
        ),
        release_readiness=_gate_item_from_assessment(
            key="release_readiness",
            label="发布就绪度",
            assessment=release_gate_assessment,
        ),
    )


def _build_assessment_payload_impl(
    *,
    training_val_metrics: Optional[YoloTrainingValMetrics],
    test_val_metrics: Optional[YoloTestValMetrics],
    val_test_comparison: Optional[YoloValTestComparison],
    total_sample_count: Optional[int],
    training_assessment_override: Optional[TrainingAssessmentResponse] = None,
    edge_validation: Optional[BoardValidationStatusResponse] = None,
    publish_blocked_reason: Optional[str] = None,
    rect_preset_slug: Optional[str] = None,
    pt_to_bmodel_comparison: Optional[dict[str, Any]] = None,
    pt_to_bmodel_comparison_expected: bool = False,
) -> AssessmentPayload:
    training_quality = (
        _gate_item_from_assessment(
            key="training_quality",
            label="训练质量",
            assessment=training_assessment_override,
        )
        if training_assessment_override is not None
        else build_training_quality_assessment(
            training_val_metrics=training_val_metrics,
            total_sample_count=total_sample_count,
            rect_preset_slug=rect_preset_slug,
        )
    )
    generalization_quality = build_generalization_quality_assessment(
        test_val_metrics=test_val_metrics,
        val_test_comparison=val_test_comparison,
        rect_preset_slug=rect_preset_slug,
    )
    edge_usability = build_edge_usability_assessment(
        edge_validation=edge_validation,
        rect_preset_slug=rect_preset_slug,
        pt_to_bmodel_comparison=pt_to_bmodel_comparison,
        pt_to_bmodel_comparison_expected=pt_to_bmodel_comparison_expected,
    )
    release_readiness = build_release_readiness_assessment(
        training_quality=training_quality,
        generalization_quality=generalization_quality,
        edge_usability=edge_usability,
        publish_blocked_reason=publish_blocked_reason,
        rect_preset_slug=rect_preset_slug,
    )
    training_assessment = training_assessment_override or build_training_assessment(
        training_val_metrics=training_val_metrics,
        total_sample_count=total_sample_count,
        rect_preset_slug=rect_preset_slug,
    )
    edge_validation_assessment = build_edge_validation_assessment(
        edge_validation=edge_validation,
        rect_preset_slug=rect_preset_slug,
        pt_to_bmodel_comparison=pt_to_bmodel_comparison,
        pt_to_bmodel_comparison_expected=pt_to_bmodel_comparison_expected,
    )
    release_gate_assessment = build_release_gate_assessment(
        training_assessment=training_assessment,
        edge_validation_assessment=edge_validation_assessment,
        generalization_quality=generalization_quality,
        publish_blocked_reason=publish_blocked_reason,
        rect_preset_slug=rect_preset_slug,
    )
    return AssessmentPayload(
        training_assessment=training_assessment,
        edge_validation_assessment=edge_validation_assessment,
        release_gate_assessment=release_gate_assessment,
        generalization_quality=generalization_quality,
        quality_assessment=YoloQualityAssessmentBundle(
            training_quality=training_quality,
            generalization_quality=generalization_quality,
            edge_usability=edge_usability,
            release_readiness=release_readiness,
        ),
    )


def build_assessment_payload(
    *,
    training_val_metrics: Optional[YoloTrainingValMetrics],
    test_val_metrics: Optional[YoloTestValMetrics],
    val_test_comparison: Optional[YoloValTestComparison],
    total_sample_count: Optional[int],
    training_assessment_override: Optional[TrainingAssessmentResponse] = None,
    edge_validation: Optional[BoardValidationStatusResponse] = None,
    publish_blocked_reason: Optional[str] = None,
    rect_preset_slug: Optional[str] = None,
    pt_to_bmodel_comparison: Optional[dict[str, Any]] = None,
    pt_to_bmodel_comparison_expected: bool = False,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
) -> AssessmentPayload:
    token = _FROZEN_POLICY.set(quality_policy_snapshot or None)
    try:
        return _build_assessment_payload_impl(
            training_val_metrics=training_val_metrics,
            test_val_metrics=test_val_metrics,
            val_test_comparison=val_test_comparison,
            total_sample_count=total_sample_count,
            training_assessment_override=training_assessment_override,
            edge_validation=edge_validation,
            publish_blocked_reason=publish_blocked_reason,
            rect_preset_slug=rect_preset_slug,
            pt_to_bmodel_comparison=pt_to_bmodel_comparison,
            pt_to_bmodel_comparison_expected=pt_to_bmodel_comparison_expected,
        )
    finally:
        _FROZEN_POLICY.reset(token)


def build_quality_assessment_bundle(
    *,
    training_val_metrics: Optional[YoloTrainingValMetrics],
    test_val_metrics: Optional[YoloTestValMetrics],
    val_test_comparison: Optional[YoloValTestComparison],
    total_sample_count: Optional[int],
    edge_validation: Optional[BoardValidationStatusResponse] = None,
    publish_blocked_reason: Optional[str] = None,
    rect_preset_slug: Optional[str] = None,
    pt_to_bmodel_comparison: Optional[dict[str, Any]] = None,
    pt_to_bmodel_comparison_expected: bool = False,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
) -> YoloQualityAssessmentBundle:
    payload = build_assessment_payload(
        training_val_metrics=training_val_metrics,
        test_val_metrics=test_val_metrics,
        val_test_comparison=val_test_comparison,
        total_sample_count=total_sample_count,
        edge_validation=edge_validation,
        publish_blocked_reason=publish_blocked_reason,
        rect_preset_slug=rect_preset_slug,
        pt_to_bmodel_comparison=pt_to_bmodel_comparison,
        pt_to_bmodel_comparison_expected=pt_to_bmodel_comparison_expected,
        quality_policy_snapshot=quality_policy_snapshot,
    )
    return payload.quality_assessment
