import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.auto_label import BatchAutoLabelCommand
from labelu.internal.application.service import auto_label
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.task import MediaType


def test_formal_bbox_task_contract_without_published_prompt_fails_closed(monkeypatch):
    task = SimpleNamespace(id=71)
    monkeypatch.setattr(
        auto_label.annotation_prompt_service,
        "resolve_published_prompt_for_task",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auto_label.annotation_contract,
        "current_contract",
        lambda *_args, **_kwargs: SimpleNamespace(id=900, revision=2),
    )

    with pytest.raises(LabelUException) as exc_info:
        auto_label._resolve_contract_prompt(
            object(),
            task,
            include_geometry_tools=True,
        )

    assert exc_info.value.status_code == 409
    assert "已发布 AI 提示词" in str(exc_info.value)


def test_formal_global_tag_task_keeps_explicit_legacy_fallback_until_dimensions_are_snapshot(
    monkeypatch,
):
    """Global tag dimensions are still sourced from task.config.

    DatasetLabelContract currently snapshots object labels only. A formal
    contract therefore must not block the established tagTool path merely
    because no global_tag prompt binding can be compiled yet.
    """
    task = SimpleNamespace(id=711)
    monkeypatch.setattr(
        auto_label.annotation_prompt_service,
        "resolve_published_prompt_for_task",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auto_label.annotation_contract,
        "current_contract",
        lambda *_args, **_kwargs: SimpleNamespace(id=900, revision=2),
    )

    assert (
        auto_label._resolve_contract_prompt(
            object(), task, include_geometry_tools=False
        )
        is None
    )


def test_subset_contract_signature_mismatch_cannot_fall_back_to_legacy_prompt(
    monkeypatch,
):
    """A SUBSET task is still formal even when its full-contract prompt exists."""
    task = SimpleNamespace(id=72)
    calls = []

    def no_matching_binding(*_args, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        auto_label.annotation_prompt_service,
        "resolve_published_prompt_for_task",
        no_matching_binding,
    )
    monkeypatch.setattr(
        auto_label.annotation_contract,
        "current_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            id=901,
            revision=3,
            label_scope_mode="SUBSET",
            label_set_signature="subset-signature",
        ),
    )

    with pytest.raises(LabelUException):
        auto_label._resolve_contract_prompt(
            object(), task, include_geometry_tools=True
        )
    assert calls
    assert all(call["operation"] == "bbox_detection" for call in calls)


def test_legacy_task_without_formal_contract_keeps_explicit_fallback(
    monkeypatch,
):
    task = SimpleNamespace(id=73)
    monkeypatch.setattr(
        auto_label.annotation_prompt_service,
        "resolve_published_prompt_for_task",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        auto_label.annotation_contract,
        "current_contract",
        lambda *_args, **_kwargs: None,
    )

    assert (
        auto_label._resolve_contract_prompt(
            object(), task, include_geometry_tools=True
        )
        is None
    )


def test_prompt_resolution_uses_tested_managed_model_name(monkeypatch):
    task = SimpleNamespace(id=74)
    calls = []

    monkeypatch.setattr(
        auto_label,
        "_resolve_model_integration_compat",
        lambda provider_key, **kwargs: (
            {"model": "managed-bbox-model"}
            if provider_key == "volcengine" and kwargs.get("capability") == "bbox_detection"
            else None
        ),
    )
    monkeypatch.setattr(
        auto_label.annotation_prompt_service,
        "resolve_published_prompt_for_task",
        lambda *_args, **kwargs: calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        auto_label.annotation_contract,
        "current_contract",
        lambda *_args, **_kwargs: None,
    )

    assert (
        auto_label._resolve_contract_prompt(
            object(), task, include_geometry_tools=True
        )
        is None
    )
    assert calls
    assert all(call["model_name"] == "managed-bbox-model" for call in calls)


def test_model_pinned_prompt_wins_over_provider_wide_prompt(monkeypatch):
    task = SimpleNamespace(id=75)
    monkeypatch.setattr(
        auto_label,
        "_prompt_runtime_model_name",
        lambda **_kwargs: "managed-bbox-model",
    )
    monkeypatch.setattr(
        auto_label.annotation_contract,
        "current_contract",
        lambda *_args, **_kwargs: None,
    )
    returned = {
        "volcengine": {
            "id": 1,
            "provider_key": "volcengine",
            "model_name": None,
        },
        "bbox_detection": {
            "id": 2,
            "provider_key": "bbox_detection",
            "model_name": "managed-bbox-model",
        },
    }
    monkeypatch.setattr(
        auto_label.annotation_prompt_service,
        "resolve_published_prompt_for_task",
        lambda _db, _task, **kwargs: returned.get(kwargs["provider_key"]),
    )

    resolved = auto_label._resolve_contract_prompt(
        object(), task, include_geometry_tools=True
    )

    assert resolved is returned["bbox_detection"]


def test_cached_prompt_requires_current_published_binding_provenance():
    binding = {
        "id": 41,
        "contract_revision": 4,
        "label_set_signature": "labels",
        "class_order_signature": "order",
        "prompt_sha256": "hash",
    }
    matching = {
        "contract_prompt_status": "published",
        "prompt_binding_id": 41,
        "contract_revision": 4,
        "label_set_signature": "labels",
        "class_order_signature": "order",
        "prompt_sha256": "hash",
    }
    assert auto_label._cached_contract_prompt_matches(matching, binding)

    for stale in (
        {**matching, "prompt_sha256": "old-hash"},
        {**matching, "contract_revision": 3},
        {**matching, "contract_prompt_status": "legacy_fallback"},
        {"contract_prompt_status": "published", "prompt_binding_id": 41},
    ):
        assert not auto_label._cached_contract_prompt_matches(stale, binding)


def test_batch_formal_bbox_contract_does_not_require_legacy_rect_tool_config(monkeypatch):
    """A published bbox contract supplies geometry vocabulary for batch jobs.

    During rollout, old task rows can still have an empty ``task.config``. The
    batch enqueue gate must use the published binding rather than rejecting
    those rows as if they were unconfigured legacy tasks.
    """

    task = SimpleNamespace(
        id=91,
        media_type=MediaType.IMAGE.value,
        config="{}",
        dataset_label_contract_id=901,
    )
    sample = SimpleNamespace(id=92)
    job = SimpleNamespace(
        id=93,
        task_id=91,
        status="PENDING",
        sample_count=1,
        processed_count=0,
        success_count=0,
        failed_count=0,
        error_message=None,
        created_at=None,
    )
    published_binding = {
        "id": 94,
        "contract_id": 901,
        "contract_revision": 3,
        "label_set_signature": "labels",
        "class_order_signature": "order",
        "prompt_sha256": "prompt-hash",
        "effective_prompt": "detect person with rectTool",
    }

    monkeypatch.setattr(auto_label.settings, "AI_AUTO_LABEL_ENABLED", True)
    monkeypatch.setattr(
        auto_label,
        "_batch_mode_requests_geometry",
        lambda _cmd: True,
    )
    monkeypatch.setattr(auto_label, "_ensure_ai_backend_configured", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto_label.crud_task, "get", lambda **kwargs: task)
    monkeypatch.setattr(auto_label, "assert_user_has_task_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto_label, "resolve_ai_task_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        auto_label,
        "_resolve_contract_prompt",
        lambda *args, **kwargs: published_binding,
    )

    def fail_if_legacy_validation_called(*args, **kwargs):
        pytest.fail("formal contract batch enqueue must not validate stale task.config")

    monkeypatch.setattr(auto_label, "_validate_auto_label_task_config", fail_if_legacy_validation_called)
    monkeypatch.setattr(
        auto_label.crud_sample,
        "list_batch_auto_label_targets",
        lambda **kwargs: [sample],
    )
    monkeypatch.setattr(auto_label, "_filter_batch_targets", lambda samples, *args, **kwargs: samples)
    monkeypatch.setattr(auto_label, "begin_transaction", lambda _db: nullcontext())
    monkeypatch.setattr(auto_label, "assert_no_active_task_background_job", lambda **kwargs: None)
    monkeypatch.setattr(auto_label.crud_auto_label_job, "create", lambda **kwargs: job)
    monkeypatch.setattr(auto_label, "biz_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auto_label.asyncio,
        "get_event_loop",
        lambda: SimpleNamespace(run_in_executor=lambda *args, **kwargs: None),
    )

    result = asyncio.run(
        auto_label.create_batch_job(
            db=object(),
            task_id=91,
            cmd=BatchAutoLabelCommand(mode="BOX_AND_TAGS", include_geometry_tools=True),
            current_user=SimpleNamespace(id=3),
        )
    )

    assert result.id == 93
    assert result.task_id == 91
    assert result.sample_count == 1

