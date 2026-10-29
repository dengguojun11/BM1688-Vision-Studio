"""训练稳定性：#100 类 deploy 中断、spawn 退出码、续训退避。"""

from __future__ import annotations

import json
import os
import signal
import sys
import types
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from labelu.internal.application.service.yolo_train import (
    _auto_resume_backoff_active,
    _clear_auto_resume_backoff,
    _completed_post_train_recovery_jobs,
    _disable_ultralytics_online_amp_check,
    _is_nothing_to_resume_error,
    _is_spawn_train_child,
    _job_train_completed_successfully,
    _linux_process_tree_pids,
    _mark_spawn_child_failed,
    _record_auto_resume_failure,
    recover_completed_post_train_jobs,
    _run_post_train_side_effects,
    _run_spawned_yolo_train_with_watchdog,
    _run_yolo_train_sync,
    _should_spawn_train_process,
    _terminate_train_process_tree,
)
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common.config import settings
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def test_should_spawn_for_large_imgsz_so_parent_can_watchdog(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._resolve_yolo_train_device",
        lambda: "0,1",
    )
    assert _should_spawn_train_process(imgsz=1280) is True
    assert _should_spawn_train_process(imgsz=640) is True


def test_spawn_watchdog_fails_child_without_train_progress(tmp_path, monkeypatch):
    job_id = 135
    work = tmp_path / "job-135"
    work.mkdir()
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 9999}),
        encoding="utf-8",
    )
    (work / "process.log").write_text("model.train attempt=1/4\n", encoding="utf-8")
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda jid: work,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.settings.YOLO_TRAIN_STALL_WATCHDOG_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.settings.YOLO_TRAIN_START_STALL_TIMEOUT_SECONDS",
        1,
        raising=False,
    )

    now = iter([0.0, 2.0])
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.time.monotonic",
        lambda: next(now),
    )

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.time.sleep",
        lambda _seconds: None,
    )

    proc = MagicMock()
    proc.exitcode = None
    proc.is_alive.return_value = True
    proc.pid = 4321
    proc.join.side_effect = lambda _seconds=None: None
    proc.terminate.side_effect = lambda: setattr(proc, "exitcode", -15)

    terminate = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._terminate_train_process_tree",
        terminate,
    )
    failed = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._mark_spawn_child_failed",
        failed,
    )
    cleanup = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._cleanup_train_runtime_after_terminal_failure",
        cleanup,
    )

    exit_code = _run_spawned_yolo_train_with_watchdog(
        proc,
        job_id,
        execution_fencing_token=17,
        execution_owner="worker-a",
    )

    assert exit_code == -15
    terminate.assert_called_once_with(job_id, 4321, include_root=True)
    failed.assert_called_once()
    assert "训练启动超过 1 秒未产生 epoch/results 进展" in failed.call_args.args[1]
    assert failed.call_args.kwargs == {
        "execution_fencing_token": 17,
        "execution_owner": "worker-a",
    }
    cleanup.assert_called_once_with(job_id)


def test_spawn_child_failed_rejects_stale_execution_fence(monkeypatch):
    from contextlib import nullcontext
    from labelu.internal.application.service.yolo_train import TrainExecutionFenceLost

    stale = TrainExecutionFenceLost("stale execution token")
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.session_scope",
        lambda: nullcontext(object()),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._assert_train_execution_fence",
        lambda *args, **kwargs: (_ for _ in ()).throw(stale),
    )
    append_log = MagicMock()
    phase = MagicMock()
    update = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        append_log,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._write_train_phase",
        phase,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.update_status",
        update,
    )

    _mark_spawn_child_failed(
        136,
        -15,
        execution_fencing_token=17,
        execution_owner="worker-a",
    )

    phase.assert_not_called()
    update.assert_not_called()
    append_log.assert_called_once_with(
        136, "spawn_child_exit_ignored_stale exit_code=-15"
    )


def test_spawn_child_failed_phase_write_keeps_execution_fence(monkeypatch):
    from contextlib import nullcontext

    from labelu.internal.application.service.yolo_train import _mark_spawn_child_failed

    db = object()
    job = YoloTrainJob(
        id=137,
        task_id=1,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.session_scope",
        lambda: nullcontext(db),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.begin_transaction",
        lambda _db: nullcontext(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._assert_train_execution_fence",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_train_completed_successfully",
        lambda _job_id: False,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.get",
        lambda **kwargs: job,
    )
    phase = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._write_train_phase",
        phase,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda *args, **kwargs: None,
    )
    update = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.update_status",
        update,
    )

    _mark_spawn_child_failed(
        137,
        -15,
        execution_owner="worker-a",
        execution_fencing_token=9,
    )

    phase.assert_called_once_with(
        137,
        "failed",
        detail="训练子进程异常退出 exit_code=-15",
        execution_owner="worker-a",
        execution_fencing_token=9,
    )
    update.assert_called_once()


def test_is_nothing_to_resume_error():
    assert _is_nothing_to_resume_error(
        RuntimeError("last.pt training to 200 epochs is finished, nothing to resume.")
    )
    assert not _is_nothing_to_resume_error(RuntimeError("cuda oom"))


def test_linux_process_tree_reads_task_children(tmp_path):
    proc = tmp_path / "proc"
    for pid in (100, 101, 102):
        (proc / str(pid) / "task" / str(pid)).mkdir(parents=True)
    (proc / "100" / "task" / "100" / "children").write_text("101\n", encoding="utf-8")
    (proc / "101" / "task" / "101" / "children").write_text("102\n", encoding="utf-8")
    (proc / "102" / "task" / "102" / "children").write_text("", encoding="utf-8")

    assert _linux_process_tree_pids(100, proc_root=proc) == {100, 101, 102}


def test_spawn_child_failed_keeps_completed_status(tmp_path, monkeypatch):
    job_id = 100
    work = tmp_path / "job-100"
    work.mkdir()
    (work / "phase.json").write_text(
        json.dumps({"phase": "completed", "detail": "ok"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda jid: work if jid == job_id else tmp_path / f"job-{jid}",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._run_post_train_side_effects",
        MagicMock(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda *a, **k: None,
    )
    with patch(
        "labelu.internal.application.service.yolo_train.session_scope"
    ) as mock_scope:
        mock_scope.return_value.__enter__.return_value = MagicMock()
        with patch(
            "labelu.internal.application.service.yolo_train.crud_yolo_train_job.get",
            return_value=YoloTrainJob(
                id=job_id,
                task_id=1,
                created_by=1,
                status=YoloTrainStatus.COMPLETED.value,
            ),
        ):
            _mark_spawn_child_failed(job_id, -6)
    with patch(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.update_status"
    ) as mock_update:
        assert mock_update.call_count == 0


def test_job_train_completed_successfully_from_phase(tmp_path, monkeypatch):
    job_id = 100
    work = tmp_path / "job-100"
    work.mkdir()
    (work / "phase.json").write_text(
        json.dumps({"phase": "completed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda jid: work,
    )
    assert _job_train_completed_successfully(job_id) is True


def test_auto_resume_backoff(tmp_path, monkeypatch):
    job_id = 100
    work = tmp_path / "job-100"
    work.mkdir()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda jid: work,
    )
    for i in range(5):
        _record_auto_resume_failure(job_id, f"fail-{i}")
    assert _auto_resume_backoff_active(job_id) is True
    _clear_auto_resume_backoff(job_id)
    assert _auto_resume_backoff_active(job_id) is False


def test_post_train_hooks_run_once(tmp_path, monkeypatch):
    job_id = 100
    work = tmp_path / "job-100"
    work.mkdir()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda jid: work,
    )
    calls = {"model": 0, "eval": 0, "compare": 0}

    def _eval(jid: int) -> None:
        calls["eval"] += 1

    monkeypatch.setattr(
        "labelu.internal.application.service.model_version.register_completed_training_job_system",
        lambda jid: calls.__setitem__("model", calls["model"] + 1) or True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_evaluation.schedule_auto_evaluations_after_train",
        _eval,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_gt_pred_compare.schedule_gt_pred_compare_after_train",
        lambda jid: calls.__setitem__("compare", calls["compare"] + 1),
    )
    _run_post_train_side_effects(job_id)
    _run_post_train_side_effects(job_id)
    assert calls == {"model": 1, "eval": 1, "compare": 1}


def test_post_train_recovery_skips_complete_legacy_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    jobs = [
        types.SimpleNamespace(
            id=201,
            ai_eval_full_status="RUNNING",
            ai_eval_next_status=None,
        ),
        types.SimpleNamespace(
            id=202,
            ai_eval_full_status=None,
            ai_eval_next_status=None,
        ),
    ]
    (tmp_path / "job-201").mkdir()
    (tmp_path / "job-202").mkdir()
    (tmp_path / "job-202" / ".post_train_hooks_done").write_text("1", encoding="utf-8")

    class FakeSession:
        def close(self):
            return None

    eval_calls: list[int] = []
    compare_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr("labelu.internal.common.db.SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        "labelu.internal.application.service.model_version.register_completed_training_job_system",
        lambda job_id: False,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.list_completed_with_weights",
        lambda db: jobs,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda job_id: tmp_path / f"job-{job_id}",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_evaluation.schedule_missing_auto_evaluations_for_job",
        lambda job_id: eval_calls.append(job_id) or True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_gt_pred_compare.recover_gt_pred_compare_after_restart",
        lambda job_id, include_idle=False: compare_calls.append((job_id, include_idle))
        or False,
    )

    result = recover_completed_post_train_jobs()

    assert result == {"eval": 1, "compare": 0}
    assert eval_calls == [201]
    assert compare_calls == [(201, True), (202, False)]


def test_system_model_registration_skips_jobs_without_frozen_dataset_version(
    monkeypatch,
):
    from labelu.internal.application.service import (
        model_version as model_version_service,
    )

    job = types.SimpleNamespace(
        id=401,
        status="COMPLETED",
        weights_path="legacy.pt",
        created_by=1,
        hyperparams_json="{}",
        dataset_manifest_json='{"split_counts":{"train":10,"val":2}}',
    )
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: nullcontext(object()),
    )
    monkeypatch.setattr(
        model_version_service.crud_yolo_train_job,
        "get",
        lambda _db, _job_id: job,
    )
    monkeypatch.setattr(
        model_version_service.crud_model_version,
        "get_by_training_job",
        lambda _db, _job_id: None,
    )
    monkeypatch.setattr(
        model_version_service,
        "register_from_training_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy job must not enter model registration")
        ),
    )

    assert model_version_service.register_completed_training_job_system(401) is False


def test_system_model_registration_skips_non_frozen_dataset_version(monkeypatch):
    from labelu.internal.application.service import (
        model_version as model_version_service,
    )

    job = types.SimpleNamespace(
        id=402,
        status="COMPLETED",
        weights_path="legacy.pt",
        created_by=1,
        hyperparams_json='{"dataset_version_id": 99}',
        dataset_manifest_json="{}",
    )
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: nullcontext(object()),
    )
    monkeypatch.setattr(
        model_version_service.crud_yolo_train_job,
        "get",
        lambda _db, _job_id: job,
    )
    monkeypatch.setattr(
        model_version_service.crud_model_version,
        "get_by_training_job",
        lambda _db, _job_id: None,
    )
    monkeypatch.setattr(
        model_version_service.crud_dataset,
        "get_version",
        lambda _db, _version_id: types.SimpleNamespace(status="DRAFT"),
    )

    assert model_version_service.register_completed_training_job_system(402) is False


def test_phase_one_post_train_candidate_query_excludes_automatic_lineage(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    jobs = [
        YoloTrainJob(
            task_id=1,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="manual.pt",
            queue_source="MANUAL",
        ),
        YoloTrainJob(
            task_id=1,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="queue-auto.pt",
            queue_source="AUTO",
        ),
        YoloTrainJob(
            task_id=1,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="plan-auto.pt",
            auto_training_plan_id=801,
        ),
        YoloTrainJob(
            task_id=1,
            created_by=1,
            status=YoloTrainStatus.COMPLETED.value,
            weights_path="round-auto.pt",
            auto_training_round_id=802,
        ),
    ]
    with db.begin():
        db.add_all(jobs)
        db.flush()
        manual_id = int(jobs[0].id)

    assert [int(job.id) for job in _completed_post_train_recovery_jobs(db)] == [
        manual_id
    ]


def test_phase_one_post_train_execution_rechecks_lineage_before_downstream(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    jobs = [
        types.SimpleNamespace(
            id=301,
            queue_source="AUTO",
            auto_training_plan_id=None,
            auto_training_round_id=None,
            ai_eval_full_status="RUNNING",
            ai_eval_next_status=None,
        ),
        types.SimpleNamespace(
            id=302,
            queue_source="MANUAL",
            auto_training_plan_id=None,
            auto_training_round_id=None,
            ai_eval_full_status="RUNNING",
            ai_eval_next_status=None,
        ),
    ]
    (tmp_path / "job-301").mkdir()
    (tmp_path / "job-302").mkdir()

    class FakeSession:
        def close(self):
            return None

    eval_calls: list[int] = []
    compare_calls: list[int] = []
    monkeypatch.setattr("labelu.internal.common.db.SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        "labelu.internal.application.service.model_version.register_completed_training_job_system",
        lambda job_id: False,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._completed_post_train_recovery_jobs",
        lambda _db: jobs,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda job_id: tmp_path / f"job-{job_id}",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_evaluation.schedule_missing_auto_evaluations_for_job",
        lambda job_id: eval_calls.append(job_id) or True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_gt_pred_compare.recover_gt_pred_compare_after_restart",
        lambda job_id, include_idle=False: compare_calls.append(job_id) or False,
    )

    result = recover_completed_post_train_jobs()

    assert result == {"eval": 1, "compare": 0}
    assert eval_calls == [302]
    assert compare_calls == [302]


def test_is_spawn_train_child():
    assert _is_spawn_train_child() is False


def test_disable_ultralytics_online_amp_check_patches_trainer(monkeypatch):
    trainer_module = types.SimpleNamespace(check_amp=lambda *args, **kwargs: False)
    engine_module = types.SimpleNamespace(trainer=trainer_module)
    ultralytics_module = types.SimpleNamespace(engine=engine_module)
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics_module)
    monkeypatch.setitem(sys.modules, "ultralytics.engine", engine_module)
    monkeypatch.setitem(sys.modules, "ultralytics.engine.trainer", trainer_module)
    train_log = MagicMock()

    _disable_ultralytics_online_amp_check(train_log)

    assert trainer_module.check_amp(object()) is True
    train_log.write.assert_called_once_with(
        "skip_online_amp_check patched ultralytics.engine.trainer.check_amp -> True"
    )


def test_disable_ultralytics_online_amp_check_logs_by_job_id(monkeypatch):
    trainer_module = types.SimpleNamespace(check_amp=lambda *args, **kwargs: False)
    engine_module = types.SimpleNamespace(trainer=trainer_module)
    ultralytics_module = types.SimpleNamespace(engine=engine_module)
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics_module)
    monkeypatch.setitem(sys.modules, "ultralytics.engine", engine_module)
    monkeypatch.setitem(sys.modules, "ultralytics.engine.trainer", trainer_module)
    process_logs = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda job_id, line: process_logs.append((job_id, line)),
    )

    _disable_ultralytics_online_amp_check(job_id=270)

    assert trainer_module.check_amp(object()) is True
    assert process_logs == [
        (
            270,
            "skip_online_amp_check patched ultralytics.engine.trainer.check_amp -> True",
        )
    ]


def test_failed_train_clears_pending_runtime_params_and_terminates_runtime(
    tmp_path, monkeypatch
):
    job_id = 127
    work = tmp_path / "job-127"
    weights = work / "train" / "weights" / "last.pt"
    weights.parent.mkdir(parents=True)
    weights.write_text("stub", encoding="utf-8")
    (work / "pending_runtime_hyperparams.json").write_text(
        json.dumps({"hyperparams": {"batch": 192}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "phase.json").write_text(
        json.dumps({"phase": "running", "runtime_pid": 4094293}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda jid: work,
    )
    monkeypatch.setattr(
        "labelu.internal.common.pipeline_process_log.yolo_train_log_path",
        lambda jid: work / "process.log",
    )
    fake_db = MagicMock()
    fake_job = YoloTrainJob(
        id=job_id,
        task_id=6,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
        model_weights="yolo11n.pt",
        epochs=60,
        imgsz=640,
        hyperparams_json=json.dumps({"batch": 16}),
        train_sample_count=1,
        val_sample_count=1,
    )

    class Scope:
        def __enter__(self):
            return fake_db

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.session_scope",
        lambda: Scope(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.get",
        lambda db, job_id: fake_job,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train.crud_yolo_train_job.update_status",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._build_resume_train_kwargs",
        lambda **kwargs: {"epochs": 60, "batch": 192, "device": "0,1"},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._consume_pending_runtime_hyperparams_for_current_run",
        lambda jid: None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._model_train_with_retry",
        MagicMock(side_effect=RuntimeError("ddp failed")),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._make_train_process_callbacks",
        lambda *args, **kwargs: (lambda *a, **k: None,) * 5,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._resolve_train_device_for_job",
        lambda *args, **kwargs: "0,1",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._apply_large_imgsz_safe_train_profile",
        lambda train_kw, hp_extra, train_device, job_id, train_log: train_device,
    )
    terminate = MagicMock()
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._terminate_train_process_tree",
        terminate,
    )
    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        types.SimpleNamespace(YOLO=lambda weights_path: MagicMock()),
    )

    _run_yolo_train_sync(
        job_id,
        6,
        CreateYoloTrainJobCommand(
            model_weights="yolo11n.pt",
            epochs=60,
            imgsz=640,
            hyperparams={"batch": 192},
        ),
        resume=True,
    )

    assert not (work / "pending_runtime_hyperparams.json").exists()
    terminate.assert_called_once()
    assert terminate.call_args.args[0] == job_id
    assert terminate.call_args.args[1] == os.getpid()
    assert terminate.call_args.kwargs == {"include_root": False}


def test_terminal_failure_process_cleanup_skips_current_root_pid(monkeypatch):
    killed: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._linux_process_tree_pids",
        lambda root_pid: {root_pid, 3001, 3002},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    _terminate_train_process_tree(127, 3000, include_root=False)

    assert killed == [(3002, signal.SIGTERM), (3001, signal.SIGTERM)]


def test_terminate_process_tree_never_kills_current_service_process(monkeypatch):
    killed: list[tuple[int, signal.Signals]] = []

    monkeypatch.setattr(os, "getpid", lambda: 3000)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._linux_process_tree_pids",
        lambda _root_pid: {3000, 3001},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    _terminate_train_process_tree(128, 3000, include_root=True)

    assert killed == [(3001, signal.SIGTERM)]
