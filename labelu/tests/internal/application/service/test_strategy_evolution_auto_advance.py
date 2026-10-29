import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_strategy_task_integration,
    crud_task,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import Task

from labelu.internal.application.service import strategy_evolution as mod


def test_strategy_export_ready_accepts_completed_export():
    row = SimpleNamespace(status="COMPLETED", bmodel_rel_path=None)

    assert mod._strategy_export_ready(row) is True


def test_strategy_export_ready_accepts_onnx_ready_with_bmodel(monkeypatch, tmp_path):
    bmodel = tmp_path / "candidate.bmodel"
    bmodel.write_bytes(b"bmodel")
    row = SimpleNamespace(status="ONNX_READY", bmodel_rel_path="models/candidate.bmodel")

    monkeypatch.setattr(
        mod.yolo_bm1688_export,
        "resolve_bm1688_media_file",
        lambda relative_path: Path(bmodel),
    )

    assert mod._strategy_export_ready(row) is True


def test_strategy_export_ready_rejects_onnx_ready_without_bmodel(monkeypatch):
    row = SimpleNamespace(status="ONNX_READY", bmodel_rel_path="")

    assert mod._strategy_export_ready(row) is False


def test_scan_and_auto_advance_strategy_tasks_counts_success(monkeypatch):
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_AUTO_ADVANCE_ENABLED",
        True,
    )

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(mod, "session_scope", lambda: _Db())
    monkeypatch.setattr(
        mod,
        "_list_strategy_auto_advance_task_ids",
        lambda db: ["strategy-task-1", "strategy-task-2", "strategy-task-3"],
    )
    monkeypatch.setattr(
        mod,
        "auto_advance_strategy_task",
        lambda external_task_id: external_task_id != "strategy-task-2",
    )

    assert mod.scan_and_auto_advance_strategy_tasks() == 2


def test_auto_advance_scan_keeps_legacy_waiting_rows_discoverable(db: Session) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-legacy-waiting")
    with begin_transaction(db):
        crud_strategy_task_integration.update(
            db,
            row,
            {
                StrategyTaskIntegration.platform_status.key: "waiting_for_model",
                StrategyTaskIntegration.automation_status.key: "succeeded",
            },
        )

    assert "strategy-task-legacy-waiting" in mod._list_strategy_auto_advance_task_ids(db)


def test_schedule_strategy_evolution_watchdog_starts_once(monkeypatch):
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_AUTO_ADVANCE_ENABLED",
        True,
    )
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_AUTO_ADVANCE_SCAN_INTERVAL_SECONDS",
        30,
    )
    monkeypatch.setattr(mod, "_WATCHDOG_STARTED", False)

    thread_calls = {"count": 0}
    timer_calls = {"count": 0}

    def _fake_thread(target=None, daemon=None, name=None):
        thread_calls["count"] += 1
        return SimpleNamespace(start=lambda: None)

    def _fake_timer(interval, target):
        timer_calls["count"] += 1
        return SimpleNamespace(daemon=False, start=lambda: None)

    monkeypatch.setattr(mod.threading, "Thread", _fake_thread)
    monkeypatch.setattr(mod.threading, "Timer", _fake_timer)

    mod.schedule_strategy_evolution_watchdog()
    mod.schedule_strategy_evolution_watchdog()

    assert thread_calls["count"] == 1
    assert timer_calls["count"] == 1


def test_filter_sample_ids_by_template_classes_excludes_incompatible_tasks_and_details(
    db: Session,
) -> None:
    with begin_transaction(db):
        template_task = crud_task.create(
            db=db,
            task=Task(
                name="template-task",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {
                                            "key": "electric_bike",
                                            "value": "electric_bike",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                media_type="IMAGE",
                created_by=1,
                updated_by=1,
            ),
        )
        compatible_task = crud_task.create(
            db=db,
            task=Task(
                name="compatible-task",
                description="",
                tips="",
                config=template_task.config,
                media_type="IMAGE",
                created_by=1,
                updated_by=1,
            ),
        )
        incompatible_task = crud_task.create(
            db=db,
            task=Task(
                name="incompatible-task",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [{"key": "person", "value": "person"}]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                media_type="IMAGE",
                created_by=1,
                updated_by=1,
            ),
        )

    filtered_ids, filtered_details, incompatible_task_ids = (
        mod._filter_sample_ids_by_template_classes(
            db,
            sample_ids_by_task={
                int(compatible_task.id): [101, 102],
                int(incompatible_task.id): [201],
            },
            selected_sample_details=[
                {"task_id": int(compatible_task.id), "sample_id": 101},
                {"task_id": int(compatible_task.id), "sample_id": 102},
                {"task_id": int(incompatible_task.id), "sample_id": 201},
            ],
            template_task=template_task,
        )
    )

    assert filtered_ids == {int(compatible_task.id): [101, 102]}
    assert filtered_details == [
        {"task_id": int(compatible_task.id), "sample_id": 101},
        {"task_id": int(compatible_task.id), "sample_id": 102},
    ]
    assert incompatible_task_ids == [int(incompatible_task.id)]


def _create_strategy_row(
    db: Session,
    *,
    external_task_id: str,
) -> StrategyTaskIntegration:
    with begin_transaction(db):
        return crud_strategy_task_integration.create(
            db,
            StrategyTaskIntegration(
                external_task_id=external_task_id,
                schema_version="v1",
                task_name=external_task_id,
                task_type="threshold_experiment",
                dataset_s3_uri="",
                dataset_image_prefix="",
                metadata_key="",
                bucket="",
                prefix="",
                labeling_required=False,
                labeling_labels_json="[]",
                target_json="{}",
                data_window_json="{}",
                experiment_json="{}",
                import_status="PENDING",
                platform_status="export_created",
                automation_source="strategy_evolution",
                automation_stage="export",
                automation_status="running",
                outbound_callback_url="http://callback.test/platform-callbacks",
                outbound_callback_status="pending",
            ),
        )


def test_task_type_callback_configuration_overrides_stale_saved_url(monkeypatch) -> None:
    threshold_url = (
        "http://10.10.11.28:21087/api/v1/strategy-evolution/platform-callbacks"
    )
    model_url = (
        "http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks"
    )
    monkeypatch.setattr(
        mod.settings, "STRATEGY_EVOLUTION_THRESHOLD_CALLBACK_URL", threshold_url
    )
    monkeypatch.setattr(
        mod.settings, "STRATEGY_EVOLUTION_MODEL_CALLBACK_URL", model_url
    )

    assert mod._resolved_outbound_callback_url(
        SimpleNamespace(
            task_type="threshold_experiment",
            outbound_callback_url=model_url,
        )
    ) == threshold_url
    assert mod._resolved_outbound_callback_url(
        SimpleNamespace(
            task_type="model_capability",
            outbound_callback_url=threshold_url,
        )
    ) == model_url

    with pytest.raises(mod.StrategyEvolutionError) as exc_info:
        mod._validate_task_callback_url(
            task_type="threshold_experiment",
            callback_url=model_url,
        )
    assert exc_info.value.status_code == 400


def test_get_task_detail_does_not_advance_running_task(monkeypatch) -> None:
    row = SimpleNamespace(
        external_task_id="strategy-task-cached-detail",
        platform_status="export_created",
        platform_result_json=json.dumps(
            {
                "edge_validation_jobs": [
                    {
                        "job_id": 10,
                        "status": "COMPLETED",
                    }
                ]
            },
            ensure_ascii=False,
        ),
    )

    monkeypatch.setattr(
        mod.crud_strategy_task_integration,
        "get_by_external_task_id",
        lambda db, external_task_id: row,
    )

    async def _unexpected_advance(db, *, row):
        raise AssertionError(
            "_advance_automation should not run for cached final detail"
        )

    monkeypatch.setattr(mod, "_advance_automation", _unexpected_advance)
    monkeypatch.setattr(
        mod,
        "_to_detail",
        lambda row, result_override=None: {
            "external_task_id": row.external_task_id,
            "result_override": result_override,
        },
    )

    detail = asyncio.run(
        mod.get_task_detail(db=object(), external_task_id=row.external_task_id)
    )

    assert detail == {
        "external_task_id": "strategy-task-cached-detail",
        "result_override": None,
    }


def test_automation_lease_blocks_duplicate_worker_and_recovers_after_expiry(
    db: Session,
) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-lease")
    now = datetime.now()

    with begin_transaction(db):
        first_claim = crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-1",
            now=now,
            lease_expires_at=now + timedelta(minutes=1),
        )
    assert first_claim is not None
    assert first_claim.automation_attempts == 1

    with begin_transaction(db):
        duplicate_claim = crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-2",
            now=now + timedelta(seconds=10),
            lease_expires_at=now + timedelta(minutes=2),
        )
    assert duplicate_claim is None

    with begin_transaction(db):
        recovered_claim = crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-2",
            now=now + timedelta(minutes=2),
            lease_expires_at=now + timedelta(minutes=3),
        )
    assert recovered_claim is not None
    assert recovered_claim.automation_lease_owner == "worker-2"
    assert recovered_claim.automation_attempts == 2


def test_automation_lease_blocks_duplicate_claim_from_same_process(db: Session) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-same-process")
    now = datetime.now()

    with begin_transaction(db):
        first_claim = crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-process:attempt-1",
            now=now,
            lease_expires_at=now + timedelta(minutes=1),
        )
    with begin_transaction(db):
        duplicate_claim = crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-process:attempt-1",
            now=now + timedelta(seconds=1),
            lease_expires_at=now + timedelta(minutes=1),
        )

    assert first_claim is not None
    assert duplicate_claim is None


def test_strategy_snapshot_rejects_late_write_after_lease_takeover(db: Session) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-late-write")
    initial_platform_status = row.platform_status
    now = datetime.now()
    with begin_transaction(db):
        crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-old:attempt-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=1),
        )
    with begin_transaction(db):
        takeover = crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-new:attempt-2",
            now=now + timedelta(seconds=2),
            lease_expires_at=now + timedelta(minutes=1),
        )

    persisted = mod._persist_strategy_snapshot(
        db,
        external_task_id=row.external_task_id,
        platform_status="model_training_created",
        automation_stage="training",
        automation_status="running",
        result={"derived_train_job_id": 999},
        expected_lease_owner="worker-old:attempt-1",
    )
    db.expire_all()
    refreshed = crud_strategy_task_integration.get_by_external_task_id(
        db,
        row.external_task_id,
    )

    assert takeover is not None
    assert persisted is None
    assert refreshed is not None
    assert refreshed.automation_lease_owner == "worker-new:attempt-2"
    assert refreshed.derived_train_job_id is None
    assert refreshed.platform_status == initial_platform_status


def test_strategy_snapshot_rejects_write_after_lease_expiry(db: Session) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-expired-write")
    initial_platform_status = row.platform_status
    now = datetime.now()
    with begin_transaction(db):
        crud_strategy_task_integration.claim_automation_lease(
            db,
            external_task_id=row.external_task_id,
            lease_owner="worker-expired:attempt-1",
            now=now - timedelta(seconds=2),
            lease_expires_at=now - timedelta(seconds=1),
        )

    persisted = crud_strategy_task_integration.update_by_external_task_id_and_lease_owner(
        db,
        external_task_id=row.external_task_id,
        lease_owner="worker-expired:attempt-1",
        obj_in={StrategyTaskIntegration.platform_status.key: "succeeded"},
        now=now,
    )
    db.expire_all()
    refreshed = crud_strategy_task_integration.get_by_external_task_id(
        db,
        row.external_task_id,
    )

    assert persisted is None
    assert refreshed is not None
    assert refreshed.platform_status == initial_platform_status


def test_outbound_callback_retries_keep_event_id_and_clear_retry_after_success(
    db: Session,
    monkeypatch,
) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-callback-retry")
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
        "",
    )
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_CALLBACK_RETRY_BASE_SECONDS",
        10,
    )
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_CALLBACK_RETRY_MAX_SECONDS",
        60,
    )
    callback_event_ids: list[str] = []

    def _failed_post(url, json, timeout):
        callback_event_ids.append(json["callback_event_id"])
        raise RuntimeError("callback unavailable")

    monkeypatch.setattr(mod.requests, "post", _failed_post)
    before_first = datetime.now()
    assert (
        mod._send_outbound_callback(db, row=row, result_payload={"ok": True})[0]
        == "failed"
    )
    db.refresh(row)
    first_retry_at = row.outbound_callback_next_retry_at
    assert first_retry_at is not None
    assert first_retry_at >= before_first + timedelta(seconds=9)

    before_second = datetime.now()
    assert (
        mod._send_outbound_callback(db, row=row, result_payload={"ok": True})[0]
        == "failed"
    )
    db.refresh(row)
    second_retry_at = row.outbound_callback_next_retry_at
    assert second_retry_at is not None
    assert second_retry_at >= before_second + timedelta(seconds=19)
    assert callback_event_ids == [f"strategy-auto-{row.id}"] * 2

    class _SuccessResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

    def _successful_post(url, json, timeout):
        callback_event_ids.append(json["callback_event_id"])
        return _SuccessResponse()

    monkeypatch.setattr(mod.requests, "post", _successful_post)
    assert mod._send_outbound_callback(db, row=row, result_payload={"ok": True}) == (
        "succeeded",
        None,
    )
    db.refresh(row)
    assert row.outbound_callback_status == "succeeded"
    assert row.outbound_callback_next_retry_at is None
    assert callback_event_ids == [f"strategy-auto-{row.id}"] * 3


def test_outbound_callbacks_use_each_tasks_persisted_callback_url(
    db: Session,
    monkeypatch,
) -> None:
    threshold_row = _create_strategy_row(
        db, external_task_id="strategy-task-threshold-callback-url"
    )
    model_row = _create_strategy_row(
        db, external_task_id="strategy-task-model-callback-url"
    )
    threshold_url = (
        "http://10.10.11.28:21087/api/v1/strategy-evolution/platform-callbacks"
    )
    model_url = (
        "http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks"
    )
    with begin_transaction(db):
        crud_strategy_task_integration.update(
            db,
            threshold_row,
            {StrategyTaskIntegration.outbound_callback_url.key: threshold_url},
        )
        crud_strategy_task_integration.update(
            db,
            model_row,
            {StrategyTaskIntegration.outbound_callback_url.key: model_url},
        )

    monkeypatch.setattr(mod.settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS",
        True,
    )
    sent: list[tuple[str, dict]] = []

    class _SuccessResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

    def _post(url, json, timeout):
        sent.append((url, json))
        return _SuccessResponse()

    monkeypatch.setattr(mod.requests, "post", _post)

    assert mod._send_outbound_callback(
        db,
        row=threshold_row,
        result_payload={
            "business_thresholds": {"effective": {"edge_low_threshold": 0.4}}
        },
    ) == ("succeeded", None)
    assert mod._send_outbound_callback(
        db,
        row=model_row,
        result_payload={"candidate_model": {"published_model_id": 551}},
    ) == ("succeeded", None)

    assert [item[0] for item in sent] == [threshold_url, model_url]
    assert sent[0][1]["result"]["business_thresholds"]["effective"] == {
        "edge_low_threshold": 0.4
    }
    assert sent[1][1]["result"]["candidate_model"]["published_model_id"] == 551


def test_waiting_threshold_experiment_rechecks_prerequisites(monkeypatch) -> None:
    row = SimpleNamespace(
        id=91,
        schema_version="v1",
        external_task_id="strategy-task-waiting-retry",
        task_name="waiting threshold retry",
        task_type="threshold_experiment",
        source_package_id="package-ready-later",
        target_json=json.dumps(
            {
                "device_type": "normal_camera",
                "event_type": "electric_bike",
                "model_id": "537",
                "scene_type": "default",
                "strategy_id": "strategy-1",
            }
        ),
        data_window_json=json.dumps(
            {
                "start_time": "2026-07-18 00:00:00",
                "end_time": "2026-07-19 00:00:00",
            }
        ),
        experiment_json=json.dumps(
            {
                "current_thresholds": {
                    "edge_low_threshold": 0.35,
                    "edge_high_threshold": 0.8,
                    "flink_filter_threshold": 0.6,
                },
                "requested_thresholds": ["edge_low_threshold"],
            }
        ),
        outbound_callback_url="http://callback.test/platform-callbacks",
        platform_status="waiting_for_annotations",
        derived_export_id=None,
        source_export_id=9,
        derived_edge_validation_job_id=None,
    )
    called = {}

    async def _resume(db, *, cmd, row):
        called["package_id"] = cmd.source_package_id
        return "export_created", "ready", {"derived_export_id": 9}

    monkeypatch.setattr(mod, "_orchestrate_threshold_experiment", _resume)

    status_value, message, result = asyncio.run(
        mod._advance_threshold_experiment(object(), row=row, result={})
    )

    assert called == {"package_id": "package-ready-later"}
    assert status_value == "export_created"
    assert message == "ready"
    assert result["derived_export_id"] == 9
    assert result["external_task_id"] == row.external_task_id


def test_retry_outbound_callback_resends_skipped_comparison_failure(
    db: Session,
    monkeypatch,
) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-callback-resend")
    with begin_transaction(db):
        crud_strategy_task_integration.update(
            db,
            row,
            {
                StrategyTaskIntegration.platform_status.key: "comparison_failed",
                StrategyTaskIntegration.platform_result_json.key: json.dumps(
                    {"comparison": {"reason": "formal threshold analysis failed"}},
                    ensure_ascii=False,
                ),
                StrategyTaskIntegration.outbound_callback_status.key: "skipped",
                StrategyTaskIntegration.outbound_callback_attempts.key: 1,
            },
    )

    monkeypatch.setattr(mod.settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")
    monkeypatch.setattr(
        mod.settings, "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS", True
    )
    sent: list[tuple[str, dict]] = []

    class _SuccessResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

    def _post(url, json, timeout):
        sent.append((url, json))
        return _SuccessResponse()

    monkeypatch.setattr(mod.requests, "post", _post)
    detail = mod.retry_outbound_callback(
        db,
        external_task_id=row.external_task_id,
        callback_url="http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks",
    )

    assert len(sent) == 1
    assert sent[0][0].endswith(":21088/api/v1/strategy-evolution/platform-callbacks")
    assert sent[0][1]["status"] == "failed"
    assert sent[0][1]["error"] == {
        "code": "COMPARISON_FAILED",
        "message": "formal threshold analysis failed",
    }
    assert detail.outbound_callback_status == "succeeded"
    assert detail.outbound_callback_attempts == 2


def test_retry_outbound_callback_rejects_non_terminal_task(db: Session) -> None:
    row = _create_strategy_row(
        db, external_task_id="strategy-task-callback-non-terminal"
    )
    with pytest.raises(mod.StrategyEvolutionError) as exc_info:
        mod.retry_outbound_callback(db, external_task_id=row.external_task_id)
    assert exc_info.value.status_code == 409


@pytest.mark.parametrize(
    ("platform_status", "expected_error_code"),
    [
        ("succeeded", None),
        ("failed", "FAILED"),
        ("cancelled", "CANCELLED"),
        ("comparison_failed", "COMPARISON_FAILED"),
        ("callback_failed", "CALLBACK_FAILED"),
    ],
)
def test_final_callback_maps_every_terminal_status_explicitly(
    db: Session,
    monkeypatch,
    platform_status: str,
    expected_error_code: str | None,
) -> None:
    row = SimpleNamespace(
        outbound_callback_status="pending",
        outbound_callback_attempts=0,
        outbound_callback_last_error=None,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        mod,
        "_build_outbound_callback_result_payload",
        lambda *args, **kwargs: {"ok": True},
    )

    def _capture_callback(db, *, row, result_payload=None, error_payload=None):
        captured["result"] = result_payload
        captured["error"] = error_payload
        return "succeeded", None

    monkeypatch.setattr(mod, "_send_outbound_callback", _capture_callback)

    assert mod._maybe_send_final_callback(
        db,
        row=row,
        platform_status=platform_status,
        result={"message": f"terminal status {platform_status}"},
    ) == ("succeeded", None)
    if expected_error_code is None:
        assert captured["result"] == {"ok": True}
        assert captured["error"] is None
    else:
        assert captured["result"] is None
        assert captured["error"] == {
            "code": expected_error_code,
            "message": f"terminal status {platform_status}",
        }


def test_outbound_callback_hmac_signs_and_sends_exact_canonical_body(
    db: Session,
    monkeypatch,
) -> None:
    row = _create_strategy_row(db, external_task_id="strategy-task-callback-hmac")
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
        "callback-secret",
    )
    monkeypatch.setattr(mod.time, "time", lambda: 1_721_234_567.9)
    captured: dict[str, object] = {}

    class _SuccessResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

    def _post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _SuccessResponse()

    monkeypatch.setattr(mod.requests, "post", _post)

    assert mod._send_outbound_callback(
        db,
        row=row,
        result_payload={"threshold": 0.35, "label": "night"},
    ) == ("succeeded", None)

    body = captured["data"]
    assert isinstance(body, bytes)
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "json" not in captured
    assert captured["timeout"] == 30
    assert headers["Content-Type"] == "application/json"
    assert headers[mod.CALLBACK_TIMESTAMP_HEADER] == "1721234567"
    assert headers[mod.CALLBACK_EVENT_ID_HEADER] == f"strategy-auto-{row.id}"
    decoded = json.loads(body.decode("utf-8"))
    canonical = json.dumps(
        decoded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert body == canonical
    signed_content = (
        b"1721234567." + f"strategy-auto-{row.id}".encode("utf-8") + b"." + body
    )
    expected = hmac.new(
        b"callback-secret",
        signed_content,
        hashlib.sha256,
    ).hexdigest()
    assert headers[mod.CALLBACK_SIGNATURE_HEADER] == f"sha256={expected}"


def test_outbound_callback_empty_hmac_secret_keeps_legacy_json_request(monkeypatch):
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
        "",
    )
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS",
        True,
    )
    payload = {"callback_event_id": "event-legacy", "status": "succeeded"}

    assert mod._outbound_callback_request_kwargs(payload) == {
        "json": payload,
        "timeout": 30,
    }


def test_outbound_callback_empty_hmac_secret_is_rejected_by_default(monkeypatch):
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
        "",
    )
    monkeypatch.setattr(
        mod.settings,
        "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS",
        False,
    )

    try:
        mod._outbound_callback_request_kwargs(
            {"callback_event_id": "event-unsigned", "status": "succeeded"}
        )
    except mod.StrategyEvolutionError as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("unsigned outbound callback must be rejected")


def test_publish_summary_supports_allowed_and_legacy_ok_fields():
    assert (
        mod._publish_summary_payload(
            {"status": "COMPLETED", "result": {"allowed": True}}
        )["allowed"]
        is True
    )
    assert (
        mod._publish_summary_payload(
            {"status": "COMPLETED", "result": {"allowed": False, "ok": True}}
        )["allowed"]
        is False
    )
    assert (
        mod._publish_summary_payload({"status": "COMPLETED", "result": {"ok": True}})[
            "allowed"
        ]
        is True
    )


def test_third_party_callback_contract_exposes_threshold_and_optimization_metadata():
    contract = mod._threshold_contract_payload()
    assert contract["value_scale"] == "ratio_0_1"
    assert contract["percent_values_allowed"] is False
    assert contract["ranges"]["business_thresholds"]["edge_low_threshold"] == {
        "minimum": 0.0,
        "maximum": 1.0,
    }
    assert contract["operating_ranges"]["conf_thres"] == {
        "minimum": 0.15,
        "maximum": 0.6,
    }

    training = mod._summarize_training_job_payload(
        {
            "job_id": 91,
            "status": "COMPLETED",
            "model_weights": "trained:37",
            "epochs": 50,
            "imgsz": 640,
            "hyperparams": {"lr0": 0.01},
            "execution_mode": "local",
            "train_node_id": None,
            "weights_path": r"C:\private\training\best.pt",
        }
    )
    assert "weights_path" not in training
    optimization = mod._optimization_payload(
        experiment={
            "capability_issue_type": "high_score_false_positive",
            "symptoms": ["night_scene_false_positive"],
        },
        result={},
        candidate_training_metrics=training,
    )
    assert optimization["status"] == "applied"
    assert optimization["recommendation_source"] == "strategy_evolution_fixed_defaults"
    assert optimization["applied_parameters"] == {
        "model_weights": "trained:37",
        "epochs": 50,
        "imgsz": 640,
        "hyperparams": {"lr0": 0.01},
        "execution_mode": "local",
        "train_node_id": None,
    }
    assert optimization["missing_fields"] == ["train_node_id"]


def test_candidate_artifact_contract_hides_absolute_paths_across_platforms(monkeypatch):
    export_row = SimpleNamespace(
        published_model_id=537,
        artifact_manifest_json=json.dumps(
            {
                "manifest_version": "v1",
                "content_sha256": "manifest-digest",
                "artifacts": [
                    {
                        "role": "model_bmodel",
                        "path": r"C:\private\exports\candidate_int8.bmodel",
                        "relative_path": "models/candidate_int8.bmodel",
                        "bytes": 1024,
                        "sha256": "artifact-digest",
                    },
                    {
                        "role": "unsafe_windows",
                        "relative_path": r"C:\private\exports\leak.bmodel",
                    },
                    {
                        "role": "unsafe_posix",
                        "relative_path": "/data/private/leak.onnx",
                    },
                ],
            }
        ),
    )
    monkeypatch.setattr(
        mod.crud_yolo_bm1688_export,
        "get",
        lambda db, export_id: export_row,
    )

    for package_path in (
        r"C:\private\packages\candidate.zip",
        "/data/private/packages/candidate.zip",
    ):
        candidate = mod._candidate_model_payload(
            object(),
            task_type=mod.MODEL_CAPABILITY_TASK_TYPE,
            base={"export_id": 12},
            export_id=12,
            publish_job={
                "status": "COMPLETED",
                "result": {
                    "allowed": True,
                    "model_id": "537",
                    "model_set_id": 537,
                    "package_path": package_path,
                },
            },
        )
        assert candidate["published_model_id"] == 537
        assert candidate["model_set_id"] == 537
        assert candidate["publication_status"] == "published"
        assert candidate["precision"] == "int8"
        assert candidate["artifact"] == {
            "availability": "available",
            "missing_reason": None,
            "manifest_version": "v1",
            "manifest_sha256": "manifest-digest",
            "package_basename": "candidate.zip",
            "files": [
                {
                    "role": "model_bmodel",
                    "relative_path": "models/candidate_int8.bmodel",
                    "size_bytes": 1024,
                    "sha256": "artifact-digest",
                }
            ],
        }
        serialized = json.dumps(candidate, ensure_ascii=False)
        assert r"C:\private" not in serialized
        assert "/data/private" not in serialized


def test_candidate_precision_is_unknown_without_evidence(monkeypatch):
    export_row = SimpleNamespace(
        published_model_id=537,
        artifact_manifest_json=None,
    )
    monkeypatch.setattr(
        mod.crud_yolo_bm1688_export,
        "get",
        lambda db, export_id: export_row,
    )

    candidate = mod._candidate_model_payload(
        object(),
        task_type=mod.MODEL_CAPABILITY_TASK_TYPE,
        base={"export_id": 12},
        export_id=12,
        publish_job={
            "status": "COMPLETED",
            "result": {
                "allowed": True,
                "model_id": "537",
                "model_set_id": 537,
            },
        },
    )

    assert candidate["publication_status"] == "published"
    assert candidate["precision"] is None


def test_job_threshold_payload_preserves_explicit_nulls_and_default_metadata():
    payload = mod._job_threshold_payload(
        {
            "conf_thres": None,
            "nms_thres": None,
            "match_iou_thres": None,
            "requested": {
                "conf_thres": None,
                "nms_thres": None,
                "match_iou_thres": None,
            },
            "defaulted_fields": [
                "conf_thres",
                "nms_thres",
                "match_iou_thres",
            ],
        }
    )

    assert payload["conf_thres"] is None
    assert payload["nms_thres"] is None
    assert payload["match_iou_thres"] is None
    assert payload["requested"]["conf_thres"] is None
    assert payload["defaulted_fields"] == [
        "conf_thres",
        "nms_thres",
        "match_iou_thres",
    ]


def test_model_capability_threshold_fallback_mapping_only_changes_edge_low(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_resolve_current_business_thresholds",
        lambda db, experiment, source_export_id: (
            {
                "edge_low_threshold": 0.35,
                "edge_high_threshold": 0.8,
                "flink_filter_threshold": 0.6,
            },
            "published_model_thresholds",
        ),
    )
    monkeypatch.setattr(
        mod,
        "_summarize_edge_job_collection",
        lambda jobs: {
            "validation_thresholds": {"conf_thres": 0.42},
            "business_threshold_analysis": None,
        },
    )
    row = SimpleNamespace(
        experiment_json="{}",
        source_export_id=11,
    )

    recommendation = mod._build_model_capability_threshold_recommendation(
        object(),
        row=row,
        result={"edge_validation_jobs": [{"job_id": 7}]},
    )

    assert recommendation["recommended_thresholds"] == {
        "edge_low_threshold": 0.42,
        "edge_high_threshold": 0.8,
        "flink_filter_threshold": 0.6,
    }
    assert recommendation["mapping_detail"] == {
        "edge_low_threshold": "candidate.validation_thresholds.conf_thres",
        "flink_filter_threshold": "current.flink_filter_threshold",
        "edge_high_threshold": "current.edge_high_threshold",
    }


def test_model_quality_gate_rejects_per_class_regression_even_when_average_improves():
    formal = {
        "status": "ok",
        "recommended_thresholds": {
            "edge_low_threshold": 0.35,
            "edge_high_threshold": 0.8,
            "flink_filter_threshold": 0.6,
        },
    }
    source_job = {
        "status": "COMPLETED",
        "result": {
            "eval_summary": {
                "map50": 0.80,
                "class_metrics": [{"name": "bike", "f1": 0.80}],
            }
        },
    }
    candidate_job = {
        "status": "COMPLETED",
        "result": {
            "eval_summary": {
                "map50": 0.85,
                "class_metrics": [{"name": "bike", "f1": 0.70}],
                "business_threshold_analysis": formal,
            }
        },
    }

    comparison = mod._build_model_comparison_payload(
        source_jobs=[source_job],
        candidate_jobs=[candidate_job],
        source_train_job={"training_val_metrics": {"map50": 0.80}},
        candidate_train_job={"training_val_metrics": {"map50": 0.82}},
        quality_policy={},
    )

    assert comparison["passed"] is False
    assert "per_class_f1_not_regressed" in comparison["quality_gate"]["failed_checks"]
