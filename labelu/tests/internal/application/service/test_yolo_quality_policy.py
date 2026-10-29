from __future__ import annotations

import json

from labelu.internal.application.command.yolo_quality_policy import (
    ActivateYoloQualityPolicyCommand,
    CreateOrUpdateYoloQualityPolicyCommand,
)
from labelu.internal.application.service import yolo_quality_policy as service


def test_list_quality_policies_bundle_returns_default_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))

    bundle = service.list_quality_policies_bundle()

    assert bundle.policies
    assert bundle.policies[0].policy_name == "default-policy-v1"
    assert bundle.policies[0].scene_name == "默认质量标准"


def test_upsert_quality_policy_persists_utf8_and_activate(tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))

    bundle = service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="garbage-exposure-ai-draft-v1",
            scene_name="垃圾暴露",
            rect_preset_slug="garbage_exposure",
            status="active",
            source="ai_recommended",
            source_summary={"business_goal": "减少漏检"},
            training_quality={"map50_95_pass": 0.58},
            generalization_quality={"test_map50_95_warn": 0.5},
            edge_usability={"require_edge_validation": True},
            release_readiness={"block_publish_when_edge_unverified": True},
            key_classes=[{"label": "垃圾暴露", "min_ap50_95": 0.5}],
            reasons=["AI 推荐草稿"],
        )
    )

    active = next(item for item in bundle.policies if item.policy_name == "garbage-exposure-ai-draft-v1")
    assert active.is_active is True
    assert active.scene_name == "垃圾暴露"

    policy_file = tmp_path / "yolo_quality_policy.json"
    raw = policy_file.read_text(encoding="utf-8")
    assert "垃圾暴露" in raw
    assert "减少漏检" in raw
    payload = json.loads(raw)
    assert payload["active_policy_name"] == "garbage-exposure-ai-draft-v1"
    assert payload["active_policy_names"]["garbage_exposure"] == "garbage-exposure-ai-draft-v1"


def test_activate_quality_policy_switches_active_policy(tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))

    service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="scene-a",
            scene_name="场景A",
            rect_preset_slug="scene_a",
            status="draft",
            source="manual",
        )
    )
    service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="scene-b",
            scene_name="场景B",
            rect_preset_slug="scene_b",
            status="draft",
            source="manual",
        )
    )

    bundle = service.activate_quality_policy(ActivateYoloQualityPolicyCommand(policy_name="scene-b"))

    scene_b = next(item for item in bundle.policies if item.policy_name == "scene-b")
    assert scene_b.is_active is True
    scene_a = next(item for item in bundle.policies if item.policy_name == "scene-a")
    assert scene_a.status == "draft"


def test_activate_quality_policy_isolated_per_rect_preset_slug(tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))

    service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="scene-a-v1",
            scene_name="鍦烘櫙A",
            rect_preset_slug="scene_a",
            status="draft",
            source="manual",
        )
    )
    service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="scene-b-v1",
            scene_name="鍦烘櫙B",
            rect_preset_slug="scene_b",
            status="draft",
            source="manual",
        )
    )

    service.activate_quality_policy(ActivateYoloQualityPolicyCommand(policy_name="scene-a-v1"))
    bundle = service.activate_quality_policy(ActivateYoloQualityPolicyCommand(policy_name="scene-b-v1"))

    assert bundle.active_policy_names["scene_a"] == "scene-a-v1"
    assert bundle.active_policy_names["scene_b"] == "scene-b-v1"
    scene_a = next(item for item in bundle.policies if item.policy_name == "scene-a-v1")
    scene_b = next(item for item in bundle.policies if item.policy_name == "scene-b-v1")
    assert scene_a.is_active is True
    assert scene_b.is_active is True


def test_resolve_policy_for_preset_prefers_active_scene_policy(tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))

    service.upsert_quality_policy(
        CreateOrUpdateYoloQualityPolicyCommand(
            policy_name="scene-c",
            scene_name="场景C",
            rect_preset_slug="scene_c",
            status="active",
            source="manual",
            training_quality={"map50_95_pass": 0.66},
        )
    )

    resolved = service.resolve_policy_for_preset("scene_c")

    assert resolved["policy_name"] == "scene-c"
    assert resolved["training_quality"]["map50_95_pass"] == 0.66
