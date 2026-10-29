from __future__ import annotations

from datetime import datetime

import pytest

from labelu.internal.application.command.yolo_quality_policy import (
    CreateOrUpdateYoloQualityPolicyCommand,
    RecommendYoloQualityPolicyCommand,
)
from labelu.internal.application.response.yolo_train import YoloTrainingValMetrics
from labelu.internal.application.service import yolo_quality_policy as yolo_quality_policy_service
from labelu.internal.application.service import yolo_quality_policy_recommendation as service
from labelu.internal.application.service.yolo_quality_assessment import build_training_quality_assessment


@pytest.fixture(autouse=True)
def isolated_provider_configuration(monkeypatch):
    monkeypatch.setattr(service, "_configured_model_for", lambda _key: None)
    monkeypatch.setattr(service.settings, "VISION_VOLCENGINE_API_KEY", "")


def test_history_snapshot_is_compact_summary(monkeypatch):
    class ModelDumpStub:
        def __init__(self, data):
            self._data = data

        def model_dump(self, mode="python"):
            return self._data

    trained_row = ModelDumpStub(
        {
            "job_id": 11,
            "task_name": "垃圾暴露训练",
            "created_at": "2026-07-02T10:00:00+08:00",
            "train_sample_count": 120,
            "total_sample_count": 160,
            "training_val_metrics": {"map50_95": 0.61, "map50": 0.82, "precision": 0.76, "recall": 0.74},
            "test_val_metrics": {"map50_95": 0.57, "map50": 0.78, "precision": 0.7, "recall": 0.71},
            "validation_pass": {"status": "达标", "detail": "mAP50-95=0.61"},
            "quality_assessment": {
                "training_quality": {"status": "pass", "summary": "训练达标", "reasons": ["a", "b", "c", "d"]},
                "generalization_quality": {"status": "warn", "summary": "泛化一般", "reasons": ["x"]},
            },
            "class_metrics": {"too": "large"},
            "train_steps": [{"too": "large"}],
        }
    )
    publish_row = ModelDumpStub(
        {
            "export_id": 21,
            "task_name": "垃圾暴露发布",
            "rect_preset_slug": "garbageStreet",
            "export_created_at": "2026-07-02T11:00:00+08:00",
            "map50_95": 0.58,
            "map50": 0.8,
            "validation_pass": {"status": "达标", "detail": "可发布"},
            "quality_assessment": {
                "edge_usability": {"status": "pass", "summary": "边端通过", "reasons": ["ok"]},
            },
            "edge_validation": {
                "status": "SUCCESS",
                "report_summary": "板端验证通过",
                "completed_at": "2026-07-02T11:10:00+08:00",
                "report": {"too": "large"},
            },
            "publish_blocked_reason": None,
            "bmodel_options": [{"too": "large"}],
        }
    )

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.yolo_train_service.list_trained_models_global",
        lambda *args, **kwargs: ([trained_row], 1),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.yolo_aicm_publish_service.list_publishable",
        lambda *args, **kwargs: ([publish_row], 1),
    )

    snapshot = service._history_snapshot(
        db=None,
        current_user=type("UserStub", (), {"id": 1})(),
        rect_preset_slug="garbageStreet",
    )

    trained = snapshot["trained_models"][0]
    publishable = snapshot["publishable_models"][0]

    assert set(trained.keys()) == {
        "job_id",
        "created_at",
        "training_metrics",
        "test_metrics",
        "validation_pass",
        "quality_assessment",
    }
    assert trained["quality_assessment"]["training_quality"]["reasons"] == ["a", "b"]
    assert "class_metrics" not in trained
    assert "train_steps" not in trained

    assert set(publishable.keys()) == {
        "export_id",
        "export_created_at",
        "map50_95",
        "validation_pass",
        "quality_assessment",
        "edge_validation",
        "publish_blocked_reason",
    }
    assert publishable["edge_validation"]["status"] == "SUCCESS"
    assert "report" not in publishable["edge_validation"]


def test_recommend_quality_policy_falls_back_without_provider(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._candidate_providers",
        lambda: [],
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbage_exposure",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is True
    assert result.fallback_reason == "未配置可用的 AI 服务，已生成系统建议模板。"
    assert result.recommendation.scene_name == "垃圾暴露"
    assert result.recommendation.source == "ai_recommended"
    assert result.recommendation.training_quality["map50_95_pass"] == 0.55
    assert result.default_recommendation.training_quality["map50_95_pass"] == 0.55


def test_recommend_quality_policy_uses_ai_result_when_json_valid(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {
            "llm_profiles": [{"id": "demo-model", "base_url": "http://x", "model": "demo-model", "api_key": "k"}],
            "annotation_chain_profile_ids": [],
            "enabled_providers": [],
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        lambda **kwargs: (
            '{"policy_name":"garbage-ai-v2","scene_name":"垃圾暴露","rect_preset_slug":"garbage_exposure",'
            '"training_quality":{"map50_95_pass":0.62},"key_classes":["垃圾暴露"],'
            '"reasons":["来自AI"],"risks":"夜间场景需要继续评估"}'
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {"trained_models": [], "publishable_models": []},
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbage_exposure",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is False
    assert result.provider_key == "demo-model"
    assert result.fallback_reason == ""
    assert result.recommendation.policy_name == "garbage-ai-v2"
    assert result.recommendation.training_quality["map50_95_pass"] == 0.62
    assert result.default_recommendation.training_quality["map50_95_pass"] == 0.55
    assert result.recommendation.key_classes == [{"label": "垃圾暴露", "min_ap50_95": 0.5}]
    assert "夜间场景需要继续评估" in result.recommendation.risks
    assert any("质量标准草稿" in item for item in result.recommendation.risks)


def test_recommend_quality_policy_does_not_call_retired_builtin_qwen(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {
            "llm_profiles": [],
            "annotation_chain_profile_ids": [],
            "enabled_providers": ["qwen_plus"],
        },
    )
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.VISION_QWEN_API_KEY",
        "builtin-qwen-key",
    )
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.VISION_QWEN_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.VISION_QWEN_MODEL_PLUS",
        "qwen3-vl-plus",
    )
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.VISION_QWEN_MODEL_235B",
        "qwen3-vl-235b-a22b-thinking",
    )
    monkeypatch.setattr(
        "labelu.internal.common.config.settings.VISION_VOLCENGINE_API_KEY",
        "",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.resolve_effective_vision_call_plan",
        lambda runtime: {
            "mode": "builtin_env",
            "steps": [
                {
                    "kind": "builtin",
                    "provider_key": "qwen_plus",
                    "label": "千问 Plus",
                    "model": "qwen3-vl-plus",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "configured": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        lambda **kwargs: pytest.fail("retired Qwen must never be called"),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {"trained_models": [], "publishable_models": []},
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbage_exposure",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is True
    assert result.fallback_reason == "未配置可用的 AI 服务，已生成系统建议模板。"


def test_candidate_providers_prefers_managed_coding_plan(monkeypatch):
    monkeypatch.setattr(service, "_configured_model_for", lambda _key: {
        "provider_key": "volcengine", "name": "火山 Coding Plan",
        "endpoint": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
        "model": "doubao-seed-2.1-pro", "api_key": "managed-key",
    })
    monkeypatch.setattr(service, "load_ai_labeling_runtime", lambda: pytest.fail("managed config must override legacy profiles"))

    assert service._candidate_providers() == [{
        "provider_key": "volcengine", "label": "火山 Coding Plan",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "model": "doubao-seed-2.1-pro", "api_key": "managed-key",
    }]


@pytest.mark.parametrize("configuration", [
    {"blocked": True, "provider_key": "volcengine"},
    {"provider_key": "volcengine", "endpoint": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-seed-2.1-pro", "api_key": "test-key"},
    {"provider_key": "vision_llm", "endpoint": "http://proxy.example/v1", "model": "qwen-vl", "api_key": "test-key"},
])
def test_candidate_providers_rejected_managed_config_blocks_legacy(monkeypatch, configuration):
    monkeypatch.setattr(service, "_configured_model_for", lambda _key: configuration)
    monkeypatch.setattr(service, "load_ai_labeling_runtime", lambda: pytest.fail("rejected managed config must block fallback"))

    assert service._candidate_providers() == []


def test_candidate_providers_filters_qwen_profiles(monkeypatch):
    monkeypatch.setattr(service, "load_ai_labeling_runtime", lambda: {
        "llm_profiles": [{"id": "aliased", "model": "legacy", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test-key"}],
    })

    assert service._candidate_providers() == []


def test_candidate_providers_keeps_managed_unauthenticated_service(monkeypatch):
    monkeypatch.setattr(service, "_configured_model_for", lambda _key: {
        "provider_key": "vision_llm", "name": "本地视觉服务", "auth_type": "none",
        "endpoint": "http://localhost:18081/v1", "model": "vision-model", "api_key": "",
    })

    providers = service._candidate_providers()
    assert len(providers) == 1
    assert providers[0]["provider_key"] == "vision_llm"
    assert providers[0]["api_key"] == ""


def test_recommend_quality_policy_normalizes_ai_payload_shape(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {"llm_profiles": [{"base_url": "http://x", "model": "demo-model", "api_key": "k"}]},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        lambda **kwargs: (
            '{"policy_name":"garbage-ai-v3","scene_name":"垃圾暴露","rect_preset_slug":"garbage_exposure",'
            '"training_quality":{"mAP_50_95":0.63},"release_readiness":"条件就绪","key_classes":["垃圾暴露"],'
            '"reasons":["来自AI兼容结构"]}'
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {"trained_models": [], "publishable_models": []},
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbage_exposure",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is False
    assert result.fallback_reason == ""
    assert result.recommendation.policy_name == "garbage-ai-v3"
    assert result.recommendation.training_quality["map50_95_pass"] == 0.55
    assert result.recommendation.release_readiness["summary"] == "条件就绪"
    assert result.recommendation.key_classes == [{"label": "垃圾暴露", "min_ap50_95": 0.5}]


def test_recommend_quality_policy_discards_final_verdict_sections(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {"llm_profiles": [{"base_url": "http://x", "model": "demo-model", "api_key": "k"}]},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        lambda **kwargs: (
            '{"policy_name":"garbage-ai-v4","scene_name":"垃圾暴露","rect_preset_slug":"garbage_exposure",'
            '"source_summary":{"business_goal":"????"},'
            '"training_quality":{"status":"达标","score":0.61,"reasons":["训练完成"]},'
            '"generalization_quality":{"status":"未验证","reasons":["未执行 test 集验证"]},'
            '"edge_usability":{"status":"可用","reasons":["边端可用"]},'
            '"release_readiness":"可发布","reasons":["来自AI结论"]}'
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {"trained_models": [], "publishable_models": []},
    )

    cmd = RecommendYoloQualityPolicyCommand(
        scene_name="垃圾暴露",
        rect_preset_slug="garbage_exposure",
        business_goal="减少漏检",
        key_classes=["垃圾暴露"],
    )
    fallback = service._default_recommendation(cmd)

    result = service.recommend_quality_policy(
        db,
        cmd=cmd,
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is False
    assert result.recommendation.training_quality == fallback["training_quality"]
    assert result.recommendation.generalization_quality == fallback["generalization_quality"]
    assert result.recommendation.edge_usability == fallback["edge_usability"]
    assert result.recommendation.release_readiness["summary"] == fallback["release_readiness"]["summary"]
    assert result.recommendation.source_summary["business_goal"] == "减少漏检"


def test_recommend_quality_policy_constrains_release_summary_and_labels(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {"llm_profiles": [{"base_url": "http://x", "model": "demo-model", "api_key": "k"}]},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        lambda **kwargs: (
            '{"policy_name":"garbage-ai-v4","scene_name":"户外垃圾暴露检测","rect_preset_slug":"garbage_exposure",'
            '"release_readiness":"可发布","key_classes":["plastic_bag","垃圾暴露"],'
            '"reasons":["模型已经可发布"],"risks":[]}'
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {"trained_models": [], "publishable_models": []},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._preset_snapshot",
        lambda db, rect_preset_slug: {
            "slug": "garbage_exposure",
            "name": "垃圾暴露",
            "labels": [{"label_key": "garbage_exposure", "label_value": "垃圾暴露"}],
        },
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbage_exposure",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is False
    assert result.recommendation.scene_name == "垃圾暴露"
    assert result.recommendation.rect_preset_slug == "garbage_exposure"
    assert result.recommendation.status == "draft"
    assert result.recommendation.source == "ai_recommended"
    assert result.recommendation.release_readiness["summary"] == (
        "用于约束训练完成后的发布门禁，不代表当前模型已可发布"
    )
    assert result.recommendation.key_classes == [{"label": "垃圾暴露", "min_ap50_95": 0.5}]
    assert any("质量标准草稿" in item for item in result.recommendation.risks)


def test_recommend_quality_policy_serializes_history_snapshot_rows(db, monkeypatch):
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {"llm_profiles": [{"base_url": "http://x", "model": "demo-model", "api_key": "k"}]},
    )

    class RowWithDatetime:
        def model_dump(self):
            return {
                "job_id": 101,
                "created_at": datetime(2026, 6, 29, 20, 0, 0),
                "scene_name": "垃圾暴露",
            }

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {
            "trained_models": [service._row_to_dict(RowWithDatetime())],
            "publishable_models": [],
        },
    )

    def _fake_chat(**kwargs):
        captured["user_text"] = kwargs["user_text"]
        return (
            '{"policy_name":"garbage-street-ai-v1","scene_name":"垃圾暴露","rect_preset_slug":"garbageStreet",'
            '"training_quality":{"map50_95_pass":0.6},"reasons":["结合历史训练结果生成"]}'
        )

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        _fake_chat,
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbageStreet",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    assert result.fallback_used is False
    assert result.recommendation.policy_name == "garbage-street-ai-v1"
    assert '"created_at": "2026-06-29T20:00:00"' in captured["user_text"]


def test_recommended_policy_rules_can_drive_training_assessment(tmp_path, monkeypatch, db):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.load_ai_labeling_runtime",
        lambda: {"llm_profiles": [{"base_url": "http://x", "model": "demo-model", "api_key": "k"}]},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.chat_multimodal_markdown",
        lambda **kwargs: (
            '{"policy_name":"garbage-street-ai-v2","scene_name":"垃圾暴露","rect_preset_slug":"garbageStreet",'
            '"training_quality":{"map50_95_pass":0.72,"map50_warn":0.68,"min_total_samples":60,"allow_conditional_pass":true},'
            '"generalization_quality":{"require_test_metrics":true,"test_map50_95_warn":0.58,"max_gap_map50_95":0.05,"max_gap_map50":0.05},'
            '"edge_usability":{"require_edge_validation":true,"require_eval_when_labeled_images_exist":true,"max_pt_to_bmodel_map50_drop":0.04},'
            '"release_readiness":{"block_publish_when_training_failed":true,"block_publish_when_edge_unverified":true,"allow_review_when_generalization_missing":false},'
            '"reasons":["结合历史训练结果生成"]}'
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation._history_snapshot",
        lambda db, current_user, rect_preset_slug: {"trained_models": [], "publishable_models": []},
    )
    monkeypatch.setattr(
        yolo_quality_policy_service,
        "ensure_quality_policy_preset_exists",
        lambda db, rect_preset_slug: None,
    )

    result = service.recommend_quality_policy(
        db,
        cmd=RecommendYoloQualityPolicyCommand(
            scene_name="垃圾暴露",
            rect_preset_slug="garbageStreet",
            business_goal="减少漏检",
            key_classes=["垃圾暴露"],
        ),
        current_user=type("UserStub", (), {"id": 1})(),
    )

    yolo_quality_policy_service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name=result.recommendation.policy_name,
            scene_name=result.recommendation.scene_name,
            rect_preset_slug=result.recommendation.rect_preset_slug,
            status="active",
            source=result.recommendation.source,
            version=result.recommendation.version,
            source_summary=result.recommendation.source_summary,
            training_quality=result.recommendation.training_quality,
            generalization_quality=result.recommendation.generalization_quality,
            edge_usability=result.recommendation.edge_usability,
            release_readiness=result.recommendation.release_readiness,
            key_classes=result.recommendation.key_classes,
            reasons=result.recommendation.reasons,
            risks=result.recommendation.risks,
        ),
        db=db,
    )

    assessment = build_training_quality_assessment(
        training_val_metrics=YoloTrainingValMetrics(map50_95=0.69, map50=0.8, summary_line="val ok"),
        total_sample_count=120,
        rect_preset_slug="garbageStreet",
    )

    assert assessment.status == "条件达标"
    assert any("0.72" in item for item in assessment.reasons)
