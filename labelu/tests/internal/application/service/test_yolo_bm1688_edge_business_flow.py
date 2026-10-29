from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from labelu.internal.adapter.persistence import (
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.service import (
    edge_ssh_validation,
    edge_runtime_provision,
    yolo_bm1688_export_auto_resume as auto_resume,
    yolo_edge_validation,
    yolo_edge_validation_scheduler as scheduler,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.task import Task


def _install_test_device(monkeypatch) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": "edge-business-flow",
        "name": "BM1688 business flow",
        "enabled": True,
        "host": "10.10.11.87",
        "user": "linaro",
        "auto_after_export": True,
        "last_probe_status": "ok",
        "last_probe_checked_at": checked_at,
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "last_used_at": None,
    }
    config = {
        **record,
        "port": 22,
        "password": "test-only",
        "remote_base": "/data/labelu_edge/jobs",
        "validation_timeout_sec": 7200,
    }
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "list_device_records",
        lambda: [record],
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "load_device_config",
        lambda device_id: config if device_id == record["id"] else {},
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "mark_device_used",
        lambda _device_id: record,
    )


@pytest.mark.parametrize(
    ("remote_exit_code", "expected_status"),
    [(0, "COMPLETED"), (1, "FAILED")],
)
def test_completed_export_without_edge_job_runs_through_pack_queue_and_dispatch(
    db, tmp_path: Path, monkeypatch, remote_exit_code: int, expected_status: str
):
    monkeypatch.setattr(settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_BM1688_AUTO_RESUME_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    _install_test_device(monkeypatch)
    user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="BM1688 edge business flow",
                description="integration test",
                tips="",
                config=json.dumps({}, ensure_ascii=False),
                created_by=user.id,
                updated_by=user.id,
                media_type="IMAGE",
            ),
        )
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=user.id,
            model_weights="yolo11n.pt",
            epochs=1,
            imgsz=640,
            category="default",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=train_job,
            status="COMPLETED",
            weights_path="weights/best.pt",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=user.id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(
            db,
            export_row,
            status="COMPLETED",
            bmodel_rel_path=(
                f"yolo_bm1688_exports/{export_row.id}/models/BM1688/"
                "electric_vehicle_int8_1b.bmodel"
            ),
        )

    train_root = tmp_path / "train-job"
    image_dir = train_root / "dataset" / "images" / "test"
    label_dir = train_root / "dataset" / "labelsxml" / "test"
    image_dir.mkdir(parents=True)
    label_dir.mkdir(parents=True)
    (image_dir / "sample-001.jpg").write_bytes(b"integration-image")
    (label_dir / "sample-001.xml").write_text(
        "<annotation><object><name>electric_vehicle</name></object></annotation>",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        edge_ssh_validation, "_job_work_root", lambda _job_id: train_root
    )

    # Keep the test at the hardware boundary: enqueue is real, while dispatch
    # is invoked explicitly below so no SSH worker starts in the background.
    monkeypatch.setattr(scheduler, "schedule_edge_validation_dispatch", lambda: None)
    monkeypatch.setattr(scheduler, "_processing_device_ids", lambda _db: set())
    monkeypatch.setattr(
        auto_resume,
        "threading",
        SimpleNamespace(
            Thread=lambda target, daemon, name=None: SimpleNamespace(start=target)
        ),
    )

    assert auto_resume.scan_and_auto_dispatch_completed_exports() == 1

    db.expire_all()
    edge_job = crud_yolo_edge_validation_job.get_latest_for_export(
        db, export_id=export_row.id
    )
    assert edge_job is not None
    assert edge_job.status == "PENDING"
    assert edge_job.queued_at is not None
    assert edge_job.image_count == 1
    assert edge_job.labeled_image_count == 1
    assert edge_job.unlabeled_image_count == 0

    job_root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_BM1688_EXPORT_SUBDIR
        / str(export_row.id)
        / "manual_edge_validation"
        / f"job-{edge_job.id}"
    )
    pack = json.loads(edge_job.result_json or "{}")["pack"]
    pack_root = Path(pack["job_root"])
    manifest = json.loads((pack_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["legacy_dataset_split"] == "test"
    assert manifest["copied_count"] == 1
    assert manifest["labeled_count"] == 1
    assert (
        pack_root / "eval_data" / "images" / "test" / "0000_sample-001.jpg"
    ).is_file()
    assigned = scheduler.dispatch_pending_edge_validation_jobs(start_workers=False)
    assert assigned == [edge_job.id]
    db.expire_all()
    dispatched = crud_yolo_edge_validation_job.get(db, edge_job.id)
    assert dispatched.status == "PROCESSING"
    assert dispatched.edge_device_id == "edge-business-flow"
    assert dispatched.edge_host == "10.10.11.87"

    class _SshClient:
        def close(self) -> None:
            return None

    client = _SshClient()
    bundle = job_root / "edge_ssh_bundle.tar.gz"
    bundle.write_bytes(b"bundle")
    # Keep a stale legacy manifest under the job root and return a different
    # signed-package staging directory. The executor must use the manifest
    # that is actually uploaded, not the old root file.
    (job_root / "manifest.json").write_text(
        json.dumps({"class_names": ["stale-root-class"]}), encoding="utf-8"
    )
    signed_staging = tmp_path / "signed-pack-staging"
    signed_staging.mkdir()
    (signed_staging / "manifest.json").write_text(
        json.dumps({"class_names": ["signed-pack-class"]}), encoding="utf-8"
    )
    monkeypatch.setattr(
        yolo_edge_validation,
        "_build_manual_edge_validation_bundle",
        lambda *_args, **_kwargs: (bundle, signed_staging),
    )
    monkeypatch.setattr(edge_ssh_validation, "ssh_connect", lambda **_kwargs: client)
    monkeypatch.setattr(
        edge_runtime_provision,
        "ensure_edge_runtime",
        lambda ssh_client, **_kwargs: (
            ssh_client,
            {
                "sophon_demo_root": "/opt/sophon-demo",
                "provisioned": False,
            },
        ),
    )
    monkeypatch.setattr(
        edge_ssh_validation,
        "_upload_edge_bundle_with_reconnect",
        lambda ssh_client, *_args, **_kwargs: ssh_client,
    )
    monkeypatch.setattr(
        edge_ssh_validation,
        "probe_sophon_demo_root_via_ssh",
        lambda *_args, **_kwargs: ("/opt/sophon-demo", "ready"),
    )
    monkeypatch.setattr(
        edge_ssh_validation,
        "build_edge_remote_validation_command",
        lambda **_kwargs: "run-edge-validation",
    )
    monkeypatch.setattr(
        edge_ssh_validation,
        "ssh_run",
        lambda *_args, **_kwargs: (
            remote_exit_code,
            "edge validation completed" if remote_exit_code == 0 else "",
            "" if remote_exit_code == 0 else "edge validation failed",
        ),
    )

    def _download_results(_client, _remote_path, local_output):
        local_output.mkdir(parents=True, exist_ok=True)
        (local_output / "board_report.json").write_text(
            json.dumps({"passed": True}),
            encoding="utf-8",
        )
        (local_output / "bmodel_sop_eval.json").write_text(
            json.dumps({"status": "ok", "models": {}}),
            encoding="utf-8",
        )
        return 2

    monkeypatch.setattr(edge_ssh_validation, "sftp_download_tree", _download_results)
    monkeypatch.setattr(
        edge_ssh_validation,
        "_pull_remote_run_log_if_present",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        edge_ssh_validation,
        "_cleanup_remote_job_dir",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "mark_device_validation",
        lambda *_args, **_kwargs: None,
    )

    yolo_edge_validation._run_manual_job_sync(edge_job.id)
    db.expire_all()
    completed = crud_yolo_edge_validation_job.get(db, edge_job.id)
    assert completed.status == expected_status
    assert completed.completed_at is not None
    assert completed.execution_lease_owner is None
    assert completed.execution_lease_expires_at is not None
    assert completed.execution_fencing_token == 1
    progress = json.loads(completed.progress_json)
    assert progress["stage"] == "finalize"
    assert progress["history"][-1]["stage"] == "finalize"
    result = json.loads(completed.result_json)
    assert result["execution"]["status"] == (
        "ok" if expected_status == "COMPLETED" else "fail"
    )
    assert result["execution"]["remote_exit_code"] == remote_exit_code
    assert (
        result["validation_dimensions"]["class_coverage"]["total_class_count"]
        == 1
    )

    # A second watchdog pass must not duplicate active or completed work.
    assert auto_resume.scan_and_auto_dispatch_completed_exports() == 0
    assert (
        crud_yolo_edge_validation_job.get_latest_for_export(
            db, export_id=export_row.id
        ).id
        == edge_job.id
    )
