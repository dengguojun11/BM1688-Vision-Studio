from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.yolo_train_resource_profile import (
    YoloTrainResourceProfile,
    _min_free_mb_for_gpu,
    compute_yolo_train_resource_profile,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def _profile(**kwargs) -> YoloTrainResourceProfile:
    defaults = dict(
        train_gpu_indices=(0, 1),
        gpus_per_job=2,
        max_concurrent_jobs=1,
        min_free_mb_per_gpu=8192,
        queue_scan_interval_seconds=45,
        cpu_only=False,
    )
    defaults.update(kwargs)
    return YoloTrainResourceProfile(**defaults)


def test_min_free_mb_scales_with_gpu_total():
    assert _min_free_mb_for_gpu(24576) == 3686  # 15% of 24GB
    assert _min_free_mb_for_gpu(1024) == 2048  # floor


def test_compute_profile_cpu_when_no_train_gpus(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [],
    )
    profile = compute_yolo_train_resource_profile()
    assert profile.cpu_only is True
    assert profile.max_concurrent_jobs == 1
    assert profile.min_free_mb_per_gpu == 0


def test_command_from_job_preserves_persisted_trained_weight_reference():
    from labelu.internal.application.service import yolo_train_queue

    job = YoloTrainJob(
        task_id=17,
        model_weights="trained:285",
        epochs=100,
        imgsz=640,
        category="iteration",
        status=YoloTrainStatus.PENDING.value,
    )

    cmd = yolo_train_queue.command_from_job(job)

    assert cmd.model_weights == "trained:285"
    assert cmd.weights_source is None


def test_command_from_job_fails_closed_on_malformed_persisted_hyperparams():
    from labelu.internal.application.service import yolo_train_queue

    job = YoloTrainJob(
        task_id=17,
        model_weights="yolo11n.pt",
        hyperparams_json='{"batch":',
        epochs=50,
        imgsz=640,
        status=YoloTrainStatus.PENDING.value,
    )

    with pytest.raises(ValueError, match="超参持久化内容不可解析"):
        yolo_train_queue.command_from_job(job)


def test_command_from_job_keeps_empty_hyperparams_legacy_compatibility():
    from labelu.internal.application.service import yolo_train_queue

    job = YoloTrainJob(
        task_id=17,
        model_weights="yolo11n.pt",
        hyperparams_json=None,
        epochs=50,
        imgsz=640,
        status=YoloTrainStatus.PENDING.value,
    )

    command = yolo_train_queue.command_from_job(job)
    assert command.hyperparams is None


def test_effective_min_free_lower_for_small_jobs():
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.common.yolo_train_gpu_allocator import (
        effective_min_free_mb_for_job,
    )

    cmd = CreateYoloTrainJobCommand(model_weights="yolo11n.pt", imgsz=640)
    assert effective_min_free_mb_for_job(cmd, base_min_mb=8601) == 4096
    large = CreateYoloTrainJobCommand(
        model_weights="yolo11l.pt", imgsz=640, hyperparams={"batch": 32}
    )
    assert effective_min_free_mb_for_job(large, base_min_mb=8601) == 8601


def test_pid_alive_uses_windows_read_only_probe(monkeypatch):
    from labelu.internal.application.service import yolo_train_queue

    calls = []
    monkeypatch.setattr(yolo_train_queue, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        yolo_train_queue,
        "_windows_pid_alive",
        lambda pid: calls.append(pid) or True,
    )
    monkeypatch.setattr(
        yolo_train_queue.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("os.kill must not run")),
    )

    assert yolo_train_queue._pid_alive(1234) is True
    assert calls == [1234]


def test_pid_alive_treats_posix_system_error_as_exited(monkeypatch):
    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.setattr(yolo_train_queue, "_IS_WINDOWS", False)
    monkeypatch.setattr(
        yolo_train_queue.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(SystemError("dead pid")),
    )

    assert yolo_train_queue._pid_alive(99999) is False


def test_train_child_binds_assigned_physical_gpu_as_logical_zero(monkeypatch):
    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    assert yolo_train_queue._bind_train_child_cuda_device("cuda:1") == "0"
    assert yolo_train_queue.os.environ["CUDA_VISIBLE_DEVICES"] == "1"


def test_train_child_preserves_multi_gpu_allocation_as_logical_devices(monkeypatch):
    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    assert yolo_train_queue._bind_train_child_cuda_device("1,3") == "0,1"
    assert yolo_train_queue.os.environ["CUDA_VISIBLE_DEVICES"] == "1,3"


def test_plate_train_child_disables_online_amp_check_before_runner(monkeypatch):
    from labelu.internal.application.service import (
        plate_recognition_train,
        yolo_train,
        yolo_train_queue,
    )

    events = []
    job = type("Job", (), {"job_type": "plate_recognition"})()

    class FakeScope:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(yolo_train_queue, "session_scope", lambda: FakeScope())
    monkeypatch.setattr(
        yolo_train_queue.crud_yolo_train_job,
        "get",
        lambda _db, _job_id: job,
    )
    monkeypatch.setattr(
        yolo_train_queue,
        "_bind_train_child_cuda_device",
        lambda _device: "0",
    )
    monkeypatch.setattr(yolo_train, "_append_train_process_log", lambda *_args: None)
    monkeypatch.setattr(yolo_train, "_write_train_phase", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        yolo_train,
        "_disable_ultralytics_online_amp_check",
        lambda *, job_id: events.append(("amp_patch", job_id)),
    )
    monkeypatch.setattr(
        plate_recognition_train,
        "run_plate_recognition_train_job",
        lambda **kwargs: events.append(("runner", kwargs)),
    )

    yolo_train_queue._run_plate_recognition_train_child(
        270,
        138,
        50,
        640,
        {"amp": True},
        "0",
        False,
    )

    assert events[0] == ("amp_patch", 270)
    assert events[1][0] == "runner"
    assert events[1][1] == {
        "job_id": 270,
        "task_id": 138,
        "epochs": 50,
        "imgsz": 640,
        "hyperparams": {"amp": True},
        "train_device": "0",
        "resume": False,
        "execution_fencing_token": None,
        "execution_owner": None,
    }


def test_plate_train_child_forwards_fencing_generation_context(monkeypatch):
    from labelu.internal.application.service import (
        plate_recognition_train,
        yolo_train,
        yolo_train_queue,
    )

    events = []
    job = type("Job", (), {"job_type": "plate_detection"})()

    class FakeScope:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(yolo_train_queue, "session_scope", lambda: FakeScope())
    monkeypatch.setattr(
        yolo_train_queue.crud_yolo_train_job,
        "get",
        lambda _db, _job_id: job,
    )
    monkeypatch.setattr(
        yolo_train_queue,
        "_bind_train_child_cuda_device",
        lambda _device: "0",
    )
    monkeypatch.setattr(yolo_train, "_append_train_process_log", lambda *_args: None)
    monkeypatch.setattr(yolo_train, "_write_train_phase", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        yolo_train,
        "_disable_ultralytics_online_amp_check",
        lambda *, job_id: None,
    )
    monkeypatch.setattr(
        plate_recognition_train,
        "run_plate_detection_train_job",
        lambda **kwargs: events.append(kwargs),
    )

    yolo_train_queue._run_plate_recognition_train_child(
        271,
        139,
        50,
        640,
        {"batch": 4},
        "0",
        False,
        12,
        "worker-a",
    )

    assert events == [
        {
            "job_id": 271,
            "task_id": 139,
            "epochs": 50,
            "imgsz": 640,
            "hyperparams": {"batch": 4},
            "train_device": "0",
            "resume": False,
            "execution_fencing_token": 12,
            "execution_owner": "worker-a",
        }
    ]


def test_plate_subprocess_persists_child_runtime_pid(monkeypatch):
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service import yolo_train, yolo_train_queue

    process_args = {}

    class FakeProcess:
        pid = 43210
        exitcode = 0

        def start(self):
            process_args["started"] = True

        def join(self):
            process_args["joined"] = True

    class FakeContext:
        def Process(self, *, target, args, name):
            process_args.update(target=target, args=args, name=name)
            return FakeProcess()

    phases = []
    monkeypatch.setattr(yolo_train_queue.mp, "get_context", lambda _mode: FakeContext())
    monkeypatch.setattr(
        yolo_train,
        "_write_train_phase",
        lambda job_id, phase, **kwargs: phases.append((job_id, phase, kwargs)),
    )
    monkeypatch.setattr(yolo_train, "is_stop_requested", lambda _job_id: False)
    monkeypatch.setattr(
        yolo_train,
        "_handle_spawn_child_exit",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("successful plate child must not be marked failed")
        ),
    )

    yolo_train_queue._run_plate_recognition_train_subprocess(
        258,
        122,
        CreateYoloTrainJobCommand(epochs=100, imgsz=640, hyperparams={"batch": 16}),
        resume=True,
        train_device="1",
    )

    assert process_args["started"] is True
    assert process_args["joined"] is True
    assert process_args["target"] is yolo_train_queue._run_plate_recognition_train_child
    assert process_args["name"] == "plate-train-258"
    assert phases == [
        (
            258,
            "running",
            {
                "detail": "plate_recognition subprocess device=1",
                "runtime_pid": 43210,
            },
        )
    ]


def test_yolo_subprocess_persists_child_runtime_pid(monkeypatch):
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service import yolo_train, yolo_train_queue

    process_args = {}

    class FakeProcess:
        pid = 54321
        exitcode = 0

        def start(self):
            process_args["started"] = True

        def join(self):
            process_args["joined"] = True

    class FakeContext:
        def Process(self, *, target, args, name):
            process_args.update(target=target, args=args, name=name)
            return FakeProcess()

    phases = []
    monkeypatch.setattr(yolo_train_queue.mp, "get_context", lambda _mode: FakeContext())
    monkeypatch.setattr(
        yolo_train,
        "_write_train_phase",
        lambda job_id, phase, **kwargs: phases.append((job_id, phase, kwargs)),
    )
    monkeypatch.setattr(yolo_train, "is_stop_requested", lambda _job_id: False)
    lifecycle_events = []
    monkeypatch.setattr(
        yolo_train,
        "_handle_spawn_child_exit",
        lambda *args: lifecycle_events.append(args),
    )

    cmd = CreateYoloTrainJobCommand(epochs=50, imgsz=640)
    yolo_train_queue._run_yolo_train_subprocess(
        259,
        157,
        cmd,
        resume=True,
        train_device="1",
    )

    assert process_args["started"] is True
    assert process_args["joined"] is True
    assert process_args["target"] is yolo_train_queue._run_yolo_train_child
    assert process_args["args"] == (259, 157, cmd, True, "1")
    assert process_args["name"] == "yolo-train-259"
    assert phases == [
        (
            259,
            "running",
            {"detail": "yolo subprocess device=1", "runtime_pid": 54321},
        )
    ]
    assert lifecycle_events == [(259, 0)]


def test_train_subprocess_terminates_when_execution_lease_is_lost():
    from labelu.internal.application.service.yolo_train_queue import (
        _join_train_process_with_lease,
    )

    class FakeProcess:
        def __init__(self):
            self.alive = True
            self.terminated = False
            self.join_calls = []

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.join_calls.append(timeout)

        def terminate(self):
            self.terminated = True
            self.alive = False

    process = FakeProcess()

    def lost():
        raise RuntimeError("execution lease lost")

    with pytest.raises(RuntimeError, match="execution lease lost"):
        _join_train_process_with_lease(process, lease_check=lost)
    assert process.terminated is True
    assert process.join_calls[0] == 0.5


@pytest.mark.parametrize(
    ("subprocess_name", "job_id", "task_id", "exit_code"),
    [
        ("_run_yolo_train_subprocess", 260, 158, 0),
        ("_run_plate_recognition_train_subprocess", 261, 159, 1),
    ],
)
def test_child_exit_does_not_write_lifecycle_state_after_fencing_loss(
    monkeypatch, subprocess_name, job_id, task_id, exit_code
):
    """旧 owner 在子进程退出收尾窗口不能再写 FAILED/终态。"""
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service import yolo_train, yolo_train_queue

    class FakeProcess:
        pid = 60000
        exitcode = exit_code

        def start(self):
            return None

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    class FakeContext:
        def Process(self, *, target, args, name):
            return FakeProcess()

    lifecycle_events = []
    monkeypatch.setattr(yolo_train_queue.mp, "get_context", lambda _mode: FakeContext())
    monkeypatch.setattr(yolo_train, "_write_train_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(yolo_train, "_append_train_process_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(yolo_train, "is_stop_requested", lambda _job_id: False)
    monkeypatch.setattr(
        yolo_train,
        "_handle_spawn_child_exit",
        lambda *args: lifecycle_events.append(args),
    )

    def stale_token():
        raise RuntimeError("stale execution token")

    command = CreateYoloTrainJobCommand(epochs=1, imgsz=320)
    with pytest.raises(RuntimeError, match="stale execution token"):
        getattr(yolo_train_queue, subprocess_name)(
            job_id,
            task_id,
            command,
            resume=False,
            train_device="cpu",
            lease_check=stale_token,
        )
    assert lifecycle_events == []


def test_runtime_pid_update_failure_terminates_started_process():
    from labelu.internal.application.service.yolo_train_queue import (
        _update_runtime_pid_or_terminate,
    )

    class FakeProcess:
        pid = 123

        def __init__(self):
            self.alive = True
            self.terminated = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def join(self, timeout=None):
            return None

    process = FakeProcess()

    with pytest.raises(RuntimeError, match="stale token"):
        _update_runtime_pid_or_terminate(
            process, lambda _pid: (_ for _ in ()).throw(RuntimeError("stale token"))
        )
    assert process.terminated is True


def test_compute_profile_from_nvidia_smi(monkeypatch):
    from labelu.internal.common.yolo_train_resource_profile import GpuMemorySnapshot

    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._train_gpu_indices",
        lambda: [0, 1],
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_resource_profile._query_nvidia_gpu_memory",
        lambda: [
            GpuMemorySnapshot(index=0, total_mb=24576, free_mb=20000),
            GpuMemorySnapshot(index=1, total_mb=24576, free_mb=20000),
        ],
    )
    profile = compute_yolo_train_resource_profile(queue_has_waiters=True)
    assert profile.train_gpu_indices == (0, 1)
    assert profile.max_concurrent_jobs == 1
    assert profile.min_free_mb_per_gpu == 3686
    assert profile.queue_scan_interval_seconds == 5


def test_request_train_start_queues_when_slots_full(monkeypatch):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_queue import (
        _TRAIN_START_QUEUE,
        _QUEUE_LOCK,
        request_train_start,
    )
    from labelu.internal.application.command.yolo_train import (
        CreateYoloTrainJobCommand,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(
            max_concurrent_jobs=1, min_free_mb_per_gpu=0, cpu_only=True
        ),
    )

    started: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._start_yolo_train_job_immediate",
        lambda jid, tid, cmd, resume=False, train_device=None: started.append(int(jid)),
    )

    yolo_train._ACTIVE_TRAIN_JOBS.add(99)
    cmd = CreateYoloTrainJobCommand()

    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()

    assert request_train_start(101, 1, cmd) is False
    assert started == []
    assert 101 in {item.job_id for item in _TRAIN_START_QUEUE}

    yolo_train._ACTIVE_TRAIN_JOBS.discard(99)
    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()


def test_request_train_start_fail_closes_when_dedicated_worker_is_not_ready(monkeypatch):
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_worker.dedicated_worker_ready",
        lambda: (False, "训练表缺少执行租约字段"),
    )
    queued: list[tuple[int, str]] = []
    monkeypatch.setattr(
        yolo_train_queue,
        "_mark_job_queued_for_resources",
        lambda job_id, reason: queued.append((int(job_id), str(reason))),
    )
    monkeypatch.setattr(
        yolo_train_queue,
        "_start_yolo_train_job_immediate",
        lambda *args, **kwargs: pytest.fail("独立 Worker 未就绪时不应启动 Web 训练"),
    )

    assert yolo_train_queue.request_train_start(
        909, 1, CreateYoloTrainJobCommand()
    ) is False
    assert queued == [(909, "独立训练 Worker 未就绪，等待运维修复：训练表缺少执行租约字段")]


def test_dispatch_queued_train_jobs_keeps_queue_when_dedicated_worker_is_not_ready(
    monkeypatch,
):
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service.yolo_train_queue import (
        _QUEUE_LOCK,
        _TRAIN_START_QUEUE,
        _QueuedTrainStart,
        dispatch_queued_train_jobs,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_worker.dedicated_worker_ready",
        lambda: (False, "Worker 未启动"),
    )
    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()
        _TRAIN_START_QUEUE.append(
            _QueuedTrainStart(910, 1, CreateYoloTrainJobCommand(), False)
        )
    try:
        assert dispatch_queued_train_jobs() == 0
        with _QUEUE_LOCK:
            assert [item.job_id for item in _TRAIN_START_QUEUE] == [910]
    finally:
        with _QUEUE_LOCK:
            _TRAIN_START_QUEUE.clear()


def test_database_drain_is_disabled_when_dedicated_worker_switch_is_on(monkeypatch):
    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.setattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_worker.dedicated_worker_ready",
        lambda: (False, "Worker 未启动"),
    )
    assert yolo_train_queue.drain_db_pending_train_jobs() == 0


def test_web_training_watchdog_is_disabled_when_dedicated_worker_switch_is_on(
    monkeypatch,
):
    from labelu.internal.application.service import yolo_train_queue

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_worker.dedicated_worker_ready",
        lambda: (False, "Worker 未启动"),
    )
    monkeypatch.setattr(
        yolo_train_queue,
        "log_yolo_train_resource_profile_once",
        lambda **kwargs: pytest.fail("独立 Worker 模式不应启动 Web 看门狗"),
    )
    monkeypatch.setattr(yolo_train_queue, "_WATCHDOG_STARTED", False)
    yolo_train_queue.schedule_train_queue_watchdog()
    assert yolo_train_queue._WATCHDOG_STARTED is False


def test_fixed_single_gpu_mode_passes_resolved_gpu_to_child(monkeypatch):
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
    from labelu.internal.application.service import yolo_train, yolo_train_queue

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        yolo_train_queue, "yolo_train_gpu_auto_alloc_enabled", lambda: False
    )
    monkeypatch.setattr(
        yolo_train_queue,
        "_yolo_train_resources_available_fixed",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(yolo_train, "_resolve_yolo_train_device", lambda: "0")

    ok, reason, device = yolo_train_queue._resolve_train_start_slot(
        901, CreateYoloTrainJobCommand()
    )

    assert ok is True
    assert reason == ""
    assert device == "0"


def test_request_train_start_can_use_free_gpu_while_queue_non_empty(monkeypatch):
    from labelu.internal.application.service.yolo_train_queue import (
        _QueuedTrainStart,
        _TRAIN_START_QUEUE,
        _QUEUE_LOCK,
        request_train_start,
    )
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.yolo_train_gpu_auto_alloc_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(max_concurrent_jobs=2, min_free_mb_per_gpu=0),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._resolve_train_start_slot",
        lambda jid, cmd: (True, "", "1"),
    )

    started: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._start_yolo_train_job_immediate",
        lambda jid, tid, cmd, resume=False, train_device=None: started.append(int(jid)),
    )

    cmd = CreateYoloTrainJobCommand()
    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()
        _TRAIN_START_QUEUE.append(_QueuedTrainStart(888, 1, cmd, False))

    assert request_train_start(777, 1, cmd) is True
    assert started == [777]
    with _QUEUE_LOCK:
        assert len(_TRAIN_START_QUEUE) == 1


def test_resolve_train_start_slot_passes_queue_depth_to_allocator(monkeypatch):
    from labelu.internal.application.service.yolo_train_queue import (
        _QueuedTrainStart,
        _TRAIN_START_QUEUE,
        _QUEUE_LOCK,
        _resolve_train_start_slot,
    )
    from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.yolo_train_gpu_auto_alloc_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(max_concurrent_jobs=2, min_free_mb_per_gpu=0),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._gpu_free_memory_mb",
        lambda: {0: 20000, 1: 20000},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._build_occupied_gpus",
        lambda **kw: {0: None, 1: None},
    )

    seen: dict[str, int] = {}

    def _fake_try_allocate(job_id, cmd, **kwargs):
        seen["queued_job_count"] = int(kwargs.get("queued_job_count", -1))
        return None

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.try_allocate_gpu_for_job",
        _fake_try_allocate,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.allocation_unavailable_reason",
        lambda *args, **kwargs: "busy",
    )

    cmd = CreateYoloTrainJobCommand()
    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()
        _TRAIN_START_QUEUE.append(_QueuedTrainStart(901, 1, cmd, False))
        ok, reason, device = _resolve_train_start_slot(902, cmd)
        _TRAIN_START_QUEUE.clear()

    assert ok is False
    assert reason == "busy"
    assert device is None
    assert seen["queued_job_count"] == 1


def test_dispatch_queued_train_jobs_fifo(monkeypatch):
    from labelu.internal.application.service.yolo_train_queue import (
        _QueuedTrainStart,
        _TRAIN_START_QUEUE,
        _QUEUE_LOCK,
        dispatch_queued_train_jobs,
    )
    from labelu.internal.application.command.yolo_train import (
        CreateYoloTrainJobCommand,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(
            max_concurrent_jobs=2, min_free_mb_per_gpu=0, cpu_only=True
        ),
    )

    started: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._start_yolo_train_job_immediate",
        lambda jid, tid, cmd, resume=False, train_device=None: started.append(int(jid)),
    )

    cmd = CreateYoloTrainJobCommand()
    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()
        _TRAIN_START_QUEUE.append(_QueuedTrainStart(201, 1, cmd, False))
        _TRAIN_START_QUEUE.append(_QueuedTrainStart(202, 1, cmd, False))

    n = dispatch_queued_train_jobs()
    assert n == 2
    assert started == [201, 202]
    with _QUEUE_LOCK:
        assert len(_TRAIN_START_QUEUE) == 0


def test_phase_one_gate_holds_automatic_queue_but_dispatches_manual(monkeypatch):
    from labelu.internal.application.command.yolo_train import (
        CreateYoloTrainJobCommand,
    )
    from labelu.internal.application.service.yolo_train_queue import (
        _QueuedTrainStart,
        _QUEUE_LOCK,
        _TRAIN_START_QUEUE,
        dispatch_queued_train_jobs,
    )

    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **_kwargs: _profile(
            max_concurrent_jobs=2, min_free_mb_per_gpu=0, cpu_only=True
        ),
    )
    started: list[int] = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._start_yolo_train_job_immediate",
        lambda jid, tid, cmd, resume=False, train_device=None: started.append(int(jid)),
    )

    cmd = CreateYoloTrainJobCommand()
    with _QUEUE_LOCK:
        _TRAIN_START_QUEUE.clear()
        _TRAIN_START_QUEUE.append(
            _QueuedTrainStart(211, 1, cmd, False, queue_source="AUTO")
        )
        _TRAIN_START_QUEUE.append(
            _QueuedTrainStart(212, 1, cmd, False, queue_source="MANUAL")
        )

    assert dispatch_queued_train_jobs() == 1
    assert started == [212]
    with _QUEUE_LOCK:
        assert len(_TRAIN_START_QUEUE) == 0


def test_queue_metadata_treats_legacy_lineage_as_automatic(monkeypatch):
    from types import SimpleNamespace

    from labelu.internal.application.service import yolo_train_queue

    scope = MagicMock()
    scope.__enter__.return_value = MagicMock()
    scope.__exit__.return_value = False
    monkeypatch.setattr(yolo_train_queue, "session_scope", lambda: scope)
    monkeypatch.setattr(
        yolo_train_queue.crud_yolo_train_job,
        "get",
        lambda **_kwargs: SimpleNamespace(
            queue_source="MANUAL",
            queue_priority=9,
            auto_training_plan_id=None,
            auto_training_round_id=81,
        ),
    )

    assert yolo_train_queue._queue_metadata(211) == ("AUTO", 9)


def test_resources_unavailable_when_vram_below_auto_threshold(monkeypatch):
    from labelu.internal.application.service.yolo_train_queue import (
        yolo_train_resources_available,
    )

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(min_free_mb_per_gpu=8000),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._gpu_free_memory_mb",
        lambda: {0: 7000, 1: 12000},
    )

    ok, reason = yolo_train_resources_available()
    assert ok is False
    assert "GPU 0" in reason
    assert "8000" in reason


def test_job_can_auto_resume_skips_when_gpu_still_busy(monkeypatch, tmp_path):
    from labelu.internal.application.service import yolo_train

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(cpu_only=True),
    )

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-401" / "train" / "weights"
    work.mkdir(parents=True)
    (work / "last.pt").write_bytes(b"fake")

    job = YoloTrainJob(
        id=401,
        task_id=1,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
        model_weights="yolo11n.pt",
    )
    (tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-401").mkdir(
        parents=True, exist_ok=True
    )
    phase = {
        "phase": "running",
        "detail": "",
        "runtime_pid": 12345,
    }
    import json

    (tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-401" / "phase.json").write_text(
        json.dumps(phase),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.active_yolo_train_job_ids_on_gpu",
        lambda: {401},
    )

    assert yolo_train.job_can_auto_resume(job) is False


def test_job_can_auto_resume_skips_active_execution_lease(monkeypatch, tmp_path):
    from labelu.internal.application.service import yolo_train

    job_id = 402
    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    (work / "train" / "weights").mkdir(parents=True)
    (work / "train" / "weights" / "last.pt").write_bytes(b"fake")
    job = YoloTrainJob(
        id=job_id,
        task_id=1,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
        model_weights="yolo11n.pt",
        execution_lease_owner="worker-a",
        execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
        execution_fencing_token=13,
    )
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._auto_resume_backoff_active",
        lambda _job_id: False,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._runtime_fields_for_job",
        lambda _job: {"is_restartable": True, "runtime_status": "STOPPED"},
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._job_work_root",
        lambda _job_id: work,
    )

    assert yolo_train.job_can_auto_resume(job) is False


def test_reconcile_keeps_remote_downloading_processing_job(db, tmp_path, monkeypatch):
    from labelu.internal.application.service.yolo_train_queue import (
        reconcile_stale_processing_jobs,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.active_yolo_train_job_ids_on_gpu",
        lambda: set(),
    )

    with db.begin():
        task = Task(
            name="pytest-remote-reconcile",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=1,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=1,
            status=YoloTrainStatus.PROCESSING.value,
            model_weights="yolo11n.pt",
            execution_mode="remote",
            train_node_id="gpu-54-120",
            remote_status_json='{"phase":"downloading"}',
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{job_id}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "phase.json").write_text(
        json.dumps(
            {
                "phase": "running",
                "detail": "remote pull node=54.120",
                "runtime_pid": 99999,
            }
        ),
        encoding="utf-8",
    )

    assert reconcile_stale_processing_jobs() == 0
    row = db.query(YoloTrainJob).filter(YoloTrainJob.id == job_id).one()
    assert row.status == YoloTrainStatus.PROCESSING.value


def test_reconcile_skips_processing_job_with_active_execution_lease(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service.yolo_train_queue import (
        reconcile_stale_processing_jobs,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.active_yolo_train_job_ids_on_gpu",
        lambda: set(),
    )
    job = YoloTrainJob(
        task_id=1,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
        model_weights="yolo11n.pt",
        execution_lease_owner="worker-a",
        execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
        execution_fencing_token=11,
    )
    db.add(job)
    db.commit()

    assert reconcile_stale_processing_jobs() == 0
    db.expire_all()
    refreshed = db.query(YoloTrainJob).filter(YoloTrainJob.id == job.id).one()
    assert refreshed.status == YoloTrainStatus.PROCESSING.value


def test_legacy_queue_reset_skips_active_execution_lease(db, monkeypatch):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_queue import (
        _mark_job_queued_for_resources,
    )

    job = YoloTrainJob(
        task_id=1,
        created_by=1,
        status=YoloTrainStatus.PROCESSING.value,
        model_weights="yolo11n.pt",
        execution_lease_owner="worker-a",
        execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
        execution_fencing_token=12,
    )
    db.add(job)
    db.commit()
    monkeypatch.setattr(yolo_train, "_write_train_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(yolo_train, "_append_train_process_log", lambda *args: None)

    _mark_job_queued_for_resources(job.id, "GPU busy")

    db.expire_all()
    refreshed = db.query(YoloTrainJob).filter(YoloTrainJob.id == job.id).one()
    assert refreshed.status == YoloTrainStatus.PROCESSING.value


def test_resume_reverts_to_pending_when_queued(tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_queue import (
        _TRAIN_START_QUEUE,
        _QUEUE_LOCK,
    )
    from labelu.tests.conftest import TestingSessionLocal, TEST_USERNAME
    from labelu.internal.adapter.persistence import crud_user

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_AUTO_RESUME_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.get_yolo_train_resource_profile",
        lambda **kw: _profile(cpu_only=True),
    )
    monkeypatch.setattr(
        yolo_train,
        "ensure_ultralytics_installed",
        lambda reason="": True,
    )

    work = tmp_path / settings.YOLO_TRAIN_WORK_SUBDIR / "job-9501" / "train" / "weights"
    work.mkdir(parents=True)
    (work / "last.pt").write_bytes(b"fake")

    lookup_db = TestingSessionLocal()
    try:
        user_id = int(
            crud_user.get_user_by_username(lookup_db, username=TEST_USERNAME).id
        )
    finally:
        lookup_db.close()

    db = TestingSessionLocal()
    try:
        with db.begin():
            task = Task(
                name="pytest-queue-resume",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=user_id,
            )
            db.add(task)
            db.flush()
            db.add(
                YoloTrainJob(
                    id=9501,
                    task_id=task.id,
                    created_by=user_id,
                    status=YoloTrainStatus.CANCELLED.value,
                    model_weights="yolo11n.pt",
                )
            )

        yolo_train._ACTIVE_TRAIN_JOBS.add(88)
        with _QUEUE_LOCK:
            _TRAIN_START_QUEUE.clear()

        assert yolo_train._resume_train_job_from_checkpoint(db, 9501) is True

        verify_db = TestingSessionLocal()
        try:
            refreshed = verify_db.get(YoloTrainJob, 9501)
            assert refreshed is not None
            assert refreshed.status == YoloTrainStatus.PENDING.value
        finally:
            verify_db.close()

        yolo_train._ACTIVE_TRAIN_JOBS.discard(88)
        with _QUEUE_LOCK:
            _TRAIN_START_QUEUE.clear()
    finally:
        db.close()


def test_restart_existing_job_uses_fresh_run_when_last_checkpoint_missing(
    db, tmp_path, monkeypatch
):
    from labelu.internal.application.service import yolo_train
    from labelu.tests.conftest import TEST_USERNAME, TestingSessionLocal
    from labelu.internal.adapter.persistence import crud_user

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        yolo_train, "ensure_ultralytics_installed", lambda reason="": True
    )

    launched = []
    logs = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda job_id, task_id, cmd, *, resume=False: launched.append(
            (job_id, task_id, cmd, resume)
        ),
    )
    monkeypatch.setattr(
        yolo_train,
        "_append_train_process_log",
        lambda job_id, line: logs.append((job_id, line)),
    )

    lookup_db = TestingSessionLocal()
    try:
        user_id = int(
            crud_user.get_user_by_username(lookup_db, username=TEST_USERNAME).id
        )
    finally:
        lookup_db.close()

    with db.begin():
        task = Task(
            name="pytest-restart-fresh",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user_id,
        )
        db.add(task)
        db.flush()
        db.add(
            YoloTrainJob(
                id=9502,
                task_id=task.id,
                created_by=user_id,
                status=YoloTrainStatus.FAILED.value,
                model_weights="yolo11n.pt",
            )
        )

    yolo_train._ACTIVE_TRAIN_JOBS.add(9502)

    assert yolo_train._restart_train_job_from_existing_job(db, 9502) is True
    assert launched and launched[0][3] is False
    assert 9502 not in yolo_train._ACTIVE_TRAIN_JOBS
    assert logs == [
        (
            9502,
            "restart=fresh detail=last.pt missing; rerun existing job with original config",
        )
    ]


def test_reconcile_skips_jobs_with_active_worker_thread(monkeypatch, tmp_path):
    from labelu.internal.application.service import yolo_train
    from labelu.internal.application.service.yolo_train_queue import (
        reconcile_stale_processing_jobs,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    job = type("Job", (), {"id": 601, "status": YoloTrainStatus.PROCESSING.value})()

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [job]

    class _FakeDb:
        def query(self, *args, **kwargs):
            return _FakeQuery()

    class _FakeScope:
        def __enter__(self):
            return _FakeDb()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.session_scope",
        lambda: _FakeScope(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._read_train_phase",
        lambda _jid: ("running", "device=0,1", 99999),
    )
    yolo_train._ACTIVE_TRAIN_JOBS.add(601)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.active_yolo_train_job_ids_on_gpu",
        lambda: set(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._runtime_train_process_on_gpu",
        lambda _pid: False,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue._pid_alive",
        lambda _pid: False,
    )

    assert reconcile_stale_processing_jobs() == 0
    yolo_train._ACTIVE_TRAIN_JOBS.discard(601)


def test_resolve_job_ids_from_cmdline():
    from labelu.internal.application.service.yolo_train_queue import (
        _job_ids_in_cmdline,
    )

    cmd = "python -c from labelu; " "/data/media/yolo_train/job-126/train args.yaml"
    assert _job_ids_in_cmdline(cmd) == {126}


def test_drain_pending_job_clears_sigterm_stop_flag_and_resumes_checkpoint(
    db, tmp_path, monkeypatch
):
    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.application.service import yolo_train, yolo_train_queue
    from labelu.tests.conftest import TEST_USERNAME

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    user = crud_user.get_user_by_username(db, username=TEST_USERNAME)
    with begin_transaction(db):
        task = Task(
            name="pytest-drain-resume",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=user.id,
            status=YoloTrainStatus.PENDING.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    checkpoint = yolo_train._job_work_root(job_id) / "train" / "weights" / "last.pt"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"checkpoint")
    yolo_train.request_stop_train_job(job_id)
    submitted = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda jid, tid, cmd, *, resume=False: submitted.append((jid, tid, resume)),
    )
    yolo_train._ACTIVE_TRAIN_JOBS.discard(job_id)
    with yolo_train_queue._QUEUE_LOCK:
        yolo_train_queue._TRAIN_START_QUEUE.clear()

    assert yolo_train_queue.drain_db_pending_train_jobs() == 1
    assert submitted == [(job_id, int(task.id), True)]
    assert yolo_train.is_stop_requested(job_id) is False


def test_drain_pending_job_preserves_explicit_user_pause(db, tmp_path, monkeypatch):
    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.application.service import yolo_train, yolo_train_queue
    from labelu.tests.conftest import TEST_USERNAME

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    user = crud_user.get_user_by_username(db, username=TEST_USERNAME)
    with begin_transaction(db):
        task = Task(
            name="pytest-drain-user-pause",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=user.id,
            status=YoloTrainStatus.PENDING.value,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    yolo_train.mark_user_pause_requested(job_id)
    yolo_train.request_stop_train_job(job_id)
    submitted = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )
    yolo_train._ACTIVE_TRAIN_JOBS.discard(job_id)
    with yolo_train_queue._QUEUE_LOCK:
        yolo_train_queue._TRAIN_START_QUEUE.clear()

    assert yolo_train_queue.drain_db_pending_train_jobs() == 0
    assert submitted == []
    assert yolo_train.is_stop_requested(job_id) is True
    assert yolo_train.is_user_pause_requested(job_id) is True


def test_drain_skips_automatic_job_without_expired_lease(db, tmp_path, monkeypatch):
    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.application.service import yolo_train, yolo_train_queue
    from labelu.tests.conftest import TEST_USERNAME

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    user = crud_user.get_user_by_username(db, username=TEST_USERNAME)
    with begin_transaction(db):
        task = Task(
            name="pytest-drain-auto-no-lease",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
        )
        db.add(task)
        db.flush()
        db.add(
            YoloTrainJob(
                task_id=task.id,
                created_by=user.id,
                status=YoloTrainStatus.PENDING.value,
                model_weights="yolo11n.pt",
                auto_training_plan_id=1,
                auto_training_round_id=1,
                queue_source="AUTO",
                queue_lease_expires_at=None,
            )
        )

    submitted = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )
    with yolo_train_queue._QUEUE_LOCK:
        yolo_train_queue._TRAIN_START_QUEUE.clear()

    assert yolo_train_queue.drain_db_pending_train_jobs() == 0
    assert submitted == []


def test_drain_claims_expired_automatic_lease_once(db, tmp_path, monkeypatch):
    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.application.service import yolo_train, yolo_train_queue
    from labelu.tests.conftest import TEST_USERNAME

    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    user = crud_user.get_user_by_username(db, username=TEST_USERNAME)
    expired_at = datetime.now() - timedelta(seconds=1)
    with begin_transaction(db):
        task = Task(
            name="pytest-drain-auto-expired-lease",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
        )
        db.add(task)
        db.flush()
        job = YoloTrainJob(
            task_id=task.id,
            created_by=user.id,
            status=YoloTrainStatus.PENDING.value,
            model_weights="yolo11n.pt",
            auto_training_plan_id=1,
            auto_training_round_id=2,
            queue_source="AUTO",
            queue_lease_owner="dead-worker",
            queue_lease_expires_at=expired_at,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    submitted = []
    monkeypatch.setattr(
        yolo_train,
        "_submit_yolo_train_job",
        lambda jid, tid, cmd, *, resume=False: submitted.append(
            (int(jid), int(tid), resume)
        ),
    )
    with yolo_train_queue._QUEUE_LOCK:
        yolo_train_queue._TRAIN_START_QUEUE.clear()

    assert yolo_train_queue.drain_db_pending_train_jobs() == 1
    assert submitted == [(job_id, int(task.id), False)]
    claimed = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert claimed.queue_lease_owner.startswith("train-drain:")
    assert claimed.queue_lease_expires_at > datetime.now()

    # The lease is still active, so a second watchdog pass cannot submit it
    # again even though the status remains PENDING until the worker starts.
    assert yolo_train_queue.drain_db_pending_train_jobs() == 0
    assert submitted == [(job_id, int(task.id), False)]


def test_persistent_queue_order_prioritizes_manual_jobs():
    from labelu.internal.application.service.yolo_train_queue import (
        pending_job_ordering_key,
    )

    jobs = [
        (30, "AUTO", 1100),
        (20, None, None),
        (10, "MANUAL", 50),
        (40, "AUTO", 1001),
    ]

    assert sorted(jobs, key=lambda row: pending_job_ordering_key(*row)) == [
        (20, None, None),
        (10, "MANUAL", 50),
        (40, "AUTO", 1001),
        (30, "AUTO", 1100),
    ]
