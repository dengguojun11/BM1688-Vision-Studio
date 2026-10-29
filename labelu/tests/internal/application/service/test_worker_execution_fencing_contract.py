"""BM1688/边端执行函数必须接入执行期 fencing 保护。"""

from pathlib import Path

from labelu.internal.application.service.yolo_edge_validation import (
    _requeue_when_execution_fencing_unavailable,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


ROOT = Path(__file__).parents[4]


def _service_source(name: str) -> str:
    return (ROOT / "internal" / "application" / "service" / name).read_text(
        encoding="utf-8"
    )


def test_bm1688_worker_uses_execution_lease_and_conditional_writes():
    source = _service_source("yolo_bm1688_export.py")
    worker = source[source.index("def _run_bm1688_export_sync"):]
    assert "claim_execution_lease" in worker
    assert "ExecutionLeaseMonitor" in worker
    assert "execution_monitor.conditional_update" in worker
    assert "ExecutionFencingLost" in worker
    assert "release_execution_lease" in worker


def test_edge_worker_uses_execution_lease_and_rejects_stale_failure_write():
    source = _service_source("yolo_edge_validation.py")
    worker = source[source.index("def _run_manual_job_sync"):]
    assert "claim_execution_lease" in worker
    assert "ExecutionLeaseMonitor" in worker
    assert "execution_monitor.conditional_update" in worker
    assert "ExecutionFencingLost" in worker
    assert "skip stale edge validation failure write" in worker
    assert (
        "_ensure_manual_execution_current(execution_monitor)\n"
        "            client = edge_ssh_validation.ssh_connect"
    ) in worker
    assert "summary[\"remote_cleanup\"] = \"skipped_stale_lease\"" in worker


def test_edge_worker_fails_closed_when_execution_fencing_is_unavailable():
    source = _service_source("yolo_edge_validation.py")
    worker = source[source.index("def _run_manual_job_sync"):]
    assert "fail closed without SSH" in worker
    assert "execution_fencing_unavailable" in worker
    assert "using legacy compatibility path" not in worker
    assert "return\n            if execution_token is None" in worker


def test_edge_worker_requeues_and_releases_device_when_fencing_is_unavailable(db):
    row = YoloEdgeValidationJob(
        user_id=1,
        task_id=1,
        export_id=901,
        status="PROCESSING",
        mode="run_only",
        threshold_source="system_default",
        sample_ids_json="[]",
        thresholds_json="{}",
        image_count=1,
        labeled_image_count=1,
        unlabeled_image_count=0,
        edge_device_id="edge-a",
        edge_device_name="边端一号",
        edge_host="10.0.0.1",
        edge_port=22,
        edge_user="root",
        queue_reason="dispatching",
    )
    db.add(row)
    db.commit()
    with begin_transaction(db):
        assert _requeue_when_execution_fencing_unavailable(db, row.id) is True
    db.expire_all()
    refreshed = db.get(YoloEdgeValidationJob, row.id)
    assert refreshed.status == "PENDING"
    assert refreshed.queue_reason == "execution_fencing_unavailable"
    assert refreshed.edge_device_id is None
    assert refreshed.edge_host is None
    assert refreshed.edge_port is None
    assert refreshed.edge_user is None
    assert "等待迁移完成" in refreshed.report_summary


def test_plate_specific_bm1688_workers_reuse_execution_fencing_helper():
    for name in ("plate_detection_bm1688.py", "plate_recognition_bm1688.py"):
        source = _service_source(name)
        worker_name = (
            "run_plate_detection_bm1688_export"
            if name.startswith("plate_detection")
            else "run_plate_recognition_bm1688_export"
        )
        worker = source[source.index(f"def {worker_name}"):]
        assert "_start_export_execution_monitor" in worker
        assert "execution_monitor.conditional_update" in worker
        assert "_stop_export_execution_monitor" in worker


def test_specialized_plate_bm1688_workers_share_execution_fencing_contract():
    for filename, worker_name in (
        ("plate_detection_bm1688.py", "run_plate_detection_bm1688_export"),
        ("plate_recognition_bm1688.py", "run_plate_recognition_bm1688_export"),
    ):
        worker = _service_source(filename)
        worker = worker[worker.index(f"def {worker_name}"):]
        assert "_start_export_execution_monitor" in worker
        assert "conditional_update" in worker
        assert "manage_transaction=True" in worker
        assert "_stop_export_execution_monitor" in worker
