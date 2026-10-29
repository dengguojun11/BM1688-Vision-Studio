import json
from types import SimpleNamespace

from labelu.internal.application.service import (
    yolo_quality_evidence_auto_recovery as recovery,
)
from labelu.internal.application.service import yolo_train
from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def test_edge_comparison_is_required_only_for_completed_plate_detection_eval():
    train = SimpleNamespace(job_type="plate_detection")
    missing = SimpleNamespace(
        status="COMPLETED",
        mode="run_and_eval",
        result_json=json.dumps({"eval_summary": {"map50": 0.9}}),
    )
    complete = SimpleNamespace(
        status="COMPLETED",
        mode="run_and_eval",
        result_json=json.dumps({"comparison_pt_bmodel": {"status": "ok"}}),
    )
    assert recovery._edge_comparison_missing(missing, train) is True
    assert recovery._edge_comparison_missing(complete, train) is False
    assert (
        recovery._edge_comparison_missing(
            SimpleNamespace(status="PROCESSING", mode="run_and_eval", result_json="{}"),
            train,
        )
        is False
    )


def test_edge_failure_can_be_retried_after_missing_comparison():
    train = SimpleNamespace(job_type="plate_detection")
    failed = SimpleNamespace(status="FAILED", mode="run_and_eval", result_json="{}")
    assert recovery._edge_comparison_missing(failed, train) is True


def test_run_train_recovery_passes_task_id_and_records_success(tmp_path, monkeypatch):
    state_path = tmp_path / ".quality_evidence_auto.json"
    monkeypatch.setattr(
        recovery,
        "_state_path_for_train",
        lambda _job_id: state_path,
    )
    calls = []
    monkeypatch.setattr(
        recovery.yolo_train_service,
        "_run_test_val_sync",
        lambda job_id, task_id: calls.append((job_id, task_id)),
    )
    monkeypatch.setattr(recovery, "_test_val_evidence_ready", lambda _job_id: True)
    recovery._ACTIVE_TRAIN_RECOVERY.add(12)
    recovery._run_train_recovery(12, 171)
    assert calls == [(12, 171)]
    assert (
        json.loads(state_path.read_text(encoding="utf-8"))["last_status"] == "completed"
    )
    assert 12 not in recovery._ACTIVE_TRAIN_RECOVERY


def test_failed_test_val_evidence_remains_recoverable(monkeypatch):
    job = SimpleNamespace(
        status="COMPLETED",
        job_type="plate_detection",
        weights_path="best.pt",
    )
    monkeypatch.setattr(
        recovery.yolo_train_service,
        "_test_val_metrics_for_job",
        lambda _job: SimpleNamespace(status="fail"),
    )
    assert recovery._test_val_missing(job) is True

    monkeypatch.setattr(
        recovery.yolo_train_service,
        "_test_val_metrics_for_job",
        lambda _job: SimpleNamespace(status="ok"),
    )
    assert recovery._test_val_missing(job) is False


def test_run_train_recovery_retries_when_evidence_is_still_unusable(
    tmp_path, monkeypatch
):
    state_path = tmp_path / ".quality_evidence_auto.json"
    monkeypatch.setattr(recovery, "_state_path_for_train", lambda _job_id: state_path)
    monkeypatch.setattr(
        recovery.yolo_train_service,
        "_run_test_val_sync",
        lambda _job_id, _task_id: None,
    )
    monkeypatch.setattr(recovery, "_test_val_evidence_ready", lambda _job_id: False)

    recovery._run_train_recovery(330, 171)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_status"] == "failed"
    assert state["retry_at"]
    assert "without usable evidence" in state["last_error"]


def test_edge_recovery_state_is_written_under_export_root(tmp_path, monkeypatch):
    state_path = tmp_path / ".quality_evidence_edge_auto.json"
    monkeypatch.setattr(recovery, "_state_path_for_edge", lambda _export_id: state_path)

    recovery._mark_edge_state(174, {"attempts": 1, "running": True})

    assert json.loads(state_path.read_text(encoding="utf-8"))["attempts"] == 1


def test_test_val_data_yaml_resolves_plate_detection_dataset(tmp_path, monkeypatch):
    work_root = tmp_path / "job-330"
    pose_data = work_root / "plate_dataset" / "pose" / "data.yaml"
    pose_data.parent.mkdir(parents=True)
    pose_data.write_text("names: [plate]\n", encoding="utf-8")
    monkeypatch.setattr(yolo_train, "_job_work_root", lambda _job_id: work_root)

    assert yolo_train._test_val_data_yaml_path(330) == pose_data


def test_phase_one_gate_skips_automatic_quality_recovery_but_keeps_manual(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(recovery, "_test_val_missing", lambda _job: True)
    monkeypatch.setattr(
        recovery, "_state_path_for_train", lambda jid: tmp_path / f"{jid}.json"
    )
    started: list[tuple[int, int]] = []

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            started.append((int(self.args[0]), int(self.args[1])))

    monkeypatch.setattr(recovery.threading, "Thread", ImmediateThread)
    recovery._ACTIVE_TRAIN_RECOVERY.clear()

    with db.begin():
        task = Task(
            name="pytest-quality-recovery-phase-one-gate",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        automatic = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status="COMPLETED",
            job_type="yolo_detection",
            weights_path="automatic.pt",
            queue_source="AUTO",
        )
        manual = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status="COMPLETED",
            job_type="yolo_detection",
            weights_path="manual.pt",
            queue_source="MANUAL",
        )
        db.add_all([automatic, manual])
        db.flush()
        automatic_id = int(automatic.id)
        manual_id = int(manual.id)

    assert recovery.maybe_recover_test_val(automatic_id) is False
    assert recovery.maybe_recover_test_val(manual_id) is True
    assert started == [(manual_id, int(task.id))]
    assert automatic_id not in recovery._ACTIVE_TRAIN_RECOVERY
    recovery._ACTIVE_TRAIN_RECOVERY.discard(manual_id)
