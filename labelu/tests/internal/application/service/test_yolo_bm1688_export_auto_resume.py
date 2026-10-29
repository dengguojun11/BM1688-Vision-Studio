"""BM1688 导出自动恢复与进度持久化。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from labelu.internal.application.response.board_agent import (
    BoardValidationProgressResponse,
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.yolo_train import YoloBm1688ExportRowResponse
from labelu.internal.application.service import yolo_bm1688_export as export_svc
from labelu.internal.application.service.yolo_bm1688_export_auto_resume import (
    maybe_auto_resume_export,
    maybe_auto_dispatch_completed_export,
    scan_and_auto_dispatch_completed_exports,
    scan_and_auto_resume_all_exports,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.execution_fencing import ExecutionFencingLost


@pytest.fixture(autouse=True)
def _clear_active_exports():
    with export_svc._ACTIVE_BM1688_EXPORTS_LOCK:
        export_svc._ACTIVE_BM1688_EXPORTS.clear()
    import labelu.internal.application.service.yolo_bm1688_export_auto_resume as auto_resume

    with auto_resume._EDGE_RECOVERY_LOCK:
        auto_resume._EDGE_RECOVERY_ACTIVE.clear()
    yield
    with export_svc._ACTIVE_BM1688_EXPORTS_LOCK:
        export_svc._ACTIVE_BM1688_EXPORTS.clear()
    with auto_resume._EDGE_RECOVERY_LOCK:
        auto_resume._EDGE_RECOVERY_ACTIVE.clear()


def test_persist_export_progress_writes_compile_log(monkeypatch):
    row = MagicMock()
    row.id = 24
    db = MagicMock()
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=db)
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: fake_scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.crud_yolo_bm1688_export.get",
        lambda _db, eid: row if eid == 24 else None,
    )
    updated = {}

    def _update(_db, _row, **kwargs):
        updated.update(kwargs)
        return _row

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.crud_yolo_bm1688_export.update_row",
        _update,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.begin_transaction",
        lambda _db: MagicMock(__enter__=MagicMock(), __exit__=MagicMock()),
    )

    export_svc._persist_export_progress(
        24,
        ["export_start=now", "phase=onnx_export"],
        status="PROCESSING",
    )
    assert updated["compile_log"] == "export_start=now\nphase=onnx_export"
    assert updated["status"] == "PROCESSING"


def test_persist_export_progress_reuses_execution_session(monkeypatch):
    row = MagicMock()
    row.id = 25
    db = MagicMock(name="export-db")
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=db)
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: fake_scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.crud_yolo_bm1688_export.get",
        lambda _db, eid: row if eid == 25 else None,
    )

    captured = {}

    class _Monitor:
        def conditional_update(self, values, *, db=None, manage_transaction=False):
            captured.update(values=values, db=db, manage_transaction=manage_transaction)

    export_svc._persist_export_progress(
        25,
        ["phase=bmodel_compile"],
        status="PROCESSING",
        execution_monitor=_Monitor(),
    )
    assert captured["db"] is db
    assert captured["manage_transaction"] is True
    assert captured["values"]["status"] == "PROCESSING"


def test_export_preflight_failures_use_execution_fencing():
    """Preflight failures must not let a stale BM1688 worker write FAILED."""
    source = open(export_svc.__file__, encoding="utf-8").read()
    section = source[
        source.index("def _run_bm1688_export_sync") : source.index(
            "def create_export_for_job_system"
        )
    ]

    for message in (
        "训练作业未完成或缺少权重路径",
        "当前环境未安装 ultralytics，无法导出 ONNX",
    ):
        marker = section.index(message)
        block = section[max(0, marker - 180) : marker + 520]
        assert "if execution_monitor is not None:" in block
        assert "execution_monitor.conditional_update(" in block
        assert "manage_transaction=True" in block
        assert "else:" in block


def test_list_bmodel_names_exposes_only_combined_plate_model(tmp_path, monkeypatch):
    export_root = tmp_path / "146"
    model_dir = export_root / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    for filename in (
        "plate_color_fp16_1b.bmodel",
        "plate_ocr_fp16_1b.bmodel",
        "plate_pose_fp16_1b.bmodel",
        "plate_recognition_fp16_1b.bmodel",
        "plate_type_fp16_1b.bmodel",
    ):
        (model_dir / filename).write_bytes(b"model")
    monkeypatch.setattr(export_svc, "_export_dir_for_row", lambda _id: export_root)

    assert export_svc._list_bmodel_names(146) == [
        "BM1688/plate_recognition_fp16_1b.bmodel"
    ]


def test_bmodel_download_rejects_path_escape_and_unlisted_files(tmp_path, monkeypatch):
    export_root = tmp_path / "146"
    models_root = export_root / "models" / "BM1688"
    models_root.mkdir(parents=True)
    allowed = models_root / "yolov11n_fp16_1b.bmodel"
    allowed.write_bytes(b"model")
    outside = export_root / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(export_svc, "_export_dir_for_row", lambda _id: export_root)
    monkeypatch.setattr(
        export_svc,
        "_list_bmodel_names",
        lambda _id: ["BM1688/yolov11n_fp16_1b.bmodel"],
    )
    row = SimpleNamespace(id=146, bmodel_rel_path=None)

    assert export_svc.resolve_bmodel_download_path(
        row, filename="BM1688/yolov11n_fp16_1b.bmodel"
    ) == allowed.resolve()
    with pytest.raises(LabelUException) as escape:
        export_svc.resolve_bmodel_download_path(row, filename="../secret.txt")
    assert escape.value.status_code == 403
    with pytest.raises(LabelUException) as unlisted:
        export_svc.resolve_bmodel_download_path(row, filename="BM1688/secret.bmodel")
    assert unlisted.value.status_code == 404


def test_bmodel_download_checks_persisted_manifest_digest(tmp_path, monkeypatch):
    export_root = tmp_path / "147"
    models_root = export_root / "models" / "BM1688"
    models_root.mkdir(parents=True)
    model = models_root / "yolov11n_fp16_1b.bmodel"
    model.write_bytes(b"model-content")
    monkeypatch.setattr(export_svc, "_export_dir_for_row", lambda _id: export_root)
    monkeypatch.setattr(
        export_svc,
        "_list_bmodel_names",
        lambda _id: ["BM1688/yolov11n_fp16_1b.bmodel"],
    )
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    row = SimpleNamespace(
        id=147,
        bmodel_rel_path=None,
        artifact_manifest_json=json.dumps(
            {
                "artifacts": [
                    {
                        "role": "model_bmodel",
                        "relative_path": "models/BM1688/yolov11n_fp16_1b.bmodel",
                        "bytes": model.stat().st_size,
                        "sha256": digest,
                    }
                ]
            }
        ),
    )

    assert export_svc.resolve_bmodel_download_path(
        row, filename="BM1688/yolov11n_fp16_1b.bmodel"
    ) == model.resolve()
    model.write_bytes(b"tampered")
    with pytest.raises(LabelUException) as exc_info:
        export_svc.resolve_bmodel_download_path(
            row, filename="BM1688/yolov11n_fp16_1b.bmodel"
        )
    assert exc_info.value.status_code == 404


def test_release_ready_requires_completed_three_precision_bundle(monkeypatch):
    row = type("ExportRow", (), {"id": 146, "status": "ONNX_READY"})()
    monkeypatch.setattr(
        export_svc,
        "_list_bmodel_names",
        lambda _id: ["BM1688/yolov11n_int8_1b.bmodel"],
    )

    assert export_svc.is_bmodel_artifact_ready(row) is False
    assert export_svc.is_bmodel_release_ready(row) is False

    row.status = "COMPLETED"
    assert export_svc.is_bmodel_artifact_ready(row) is True
    assert export_svc.is_bmodel_release_ready(row) is False


def test_release_ready_allows_specialized_single_fp16_contract(monkeypatch):
    row = type("ExportRow", (), {"id": 147, "status": "COMPLETED"})()
    monkeypatch.setattr(
        export_svc,
        "_list_bmodel_names",
        lambda _id: ["BM1688/plate_recognition_fp16_1b.bmodel"],
    )

    assert export_svc.is_bmodel_release_ready(row) is False
    assert export_svc.is_bmodel_release_ready(
        row, required_precisions=("fp16",)
    ) is True


def test_maybe_auto_resume_skips_when_active(monkeypatch):
    export_svc._mark_export_active(7)
    called = {"n": 0}

    def _should_not_run(_eid):
        called["n"] += 1

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._run_bm1688_export_sync",
        _should_not_run,
    )
    assert maybe_auto_resume_export(7) is False
    assert called["n"] == 0


def test_maybe_auto_resume_skips_when_execution_lease_is_active(monkeypatch):
    row = SimpleNamespace(
        status="PROCESSING",
        auto_training_round_id=None,
        execution_lease_owner="compiler-a",
        execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
    )
    scope = MagicMock()
    scope.__enter__.return_value = MagicMock()
    scope.__exit__.return_value = False
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.session_scope",
        lambda: scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.crud_yolo_bm1688_export.get",
        lambda _db, _eid: row,
    )
    monkeypatch.setattr(export_svc, "is_export_active", lambda _eid: False)
    monkeypatch.setattr(
        export_svc,
        "_resolve_onnx_path_for_row",
        lambda _row: pytest.fail("active execution lease must stop recovery first"),
    )

    assert maybe_auto_resume_export(73) is False


def test_maybe_auto_resume_returns_to_pending_for_dedicated_worker(monkeypatch):
    row = SimpleNamespace(
        id=74,
        status="PROCESSING",
        auto_training_round_id=None,
        execution_lease_owner=None,
        execution_lease_expires_at=None,
        worker_action="recompile",
    )
    db = MagicMock()
    scope = MagicMock()
    scope.__enter__.return_value = db
    scope.__exit__.return_value = False
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.session_scope",
        lambda: scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.crud_yolo_bm1688_export.get",
        lambda _db, _eid: row,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume._dedicated_bm1688_worker_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.crud_yolo_bm1688_export.update_row",
        lambda _db, current, **kwargs: (
            setattr(current, "status", kwargs["status"]),
            setattr(current, "error_message", kwargs["error_message"]),
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.threading.Thread",
        lambda **_kwargs: pytest.fail("独立 Worker 模式不应启动编译线程"),
    )

    assert maybe_auto_resume_export(74) is False
    assert row.status == "PENDING"
    assert row.worker_action == "recompile"


def test_export_guard_released_when_compiler_setup_fails(monkeypatch):
    released = []

    class _Guard:
        def release(self):
            released.append(True)

    monkeypatch.setattr(export_svc, "_acquire_export_execution_guard", lambda _eid: _Guard())

    def _raise_setup_error():
        raise RuntimeError("compiler unavailable")

    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.ensure_bm1688_compiler_ready",
        _raise_setup_error,
    )

    with pytest.raises(RuntimeError, match="compiler unavailable"):
        export_svc._run_bm1688_export_sync(41)

    assert released == [True]


def test_recompile_guard_released_when_compiler_setup_fails(monkeypatch):
    released = []
    persisted = {}

    class _Guard:
        def release(self):
            released.append(True)

    class _Monitor:
        def ensure_current(self):
            return None

    monkeypatch.setattr(export_svc, "_acquire_export_execution_guard", lambda _eid: _Guard())
    monkeypatch.setattr(
        export_svc,
        "_start_export_execution_monitor",
        lambda _eid, *, label: (_Monitor(), False, False),
    )
    monkeypatch.setattr(
        export_svc,
        "_stop_export_execution_monitor",
        lambda _monitor: None,
    )

    def _persist_failure(export_id, error_message, *, execution_monitor):
        persisted.update(
            export_id=export_id,
            error_message=error_message,
            execution_monitor=execution_monitor,
        )
        return "FAILED"

    monkeypatch.setattr(export_svc, "_persist_recompile_failure", _persist_failure)

    def _raise_setup_error():
        raise RuntimeError("compiler unavailable")

    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.ensure_bm1688_compiler_ready",
        _raise_setup_error,
    )

    with pytest.raises(RuntimeError, match="compiler unavailable"):
        export_svc._run_bm1688_recompile_sync(42)

    assert released == [True]
    assert persisted["export_id"] == 42
    assert "可重试" in persisted["error_message"]
    assert persisted["execution_monitor"] is not None


def test_persist_recompile_failure_uses_execution_fencing(monkeypatch):
    row = SimpleNamespace(id=42, status="PROCESSING", error_message=None)
    db = MagicMock(name="recompile-db")
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=db)
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: fake_scope,
    )
    monkeypatch.setattr(
        export_svc.crud_yolo_bm1688_export,
        "get",
        lambda _db, export_id: row if int(export_id) == 42 else None,
    )
    captured = {}

    class _Monitor:
        def conditional_update(self, values, *, db=None, manage_transaction=False):
            captured.update(
                values=values,
                db=db,
                manage_transaction=manage_transaction,
            )

    status = export_svc._persist_recompile_failure(
        42,
        "BM1688 重新编译失败（可重试）：compiler unavailable",
        execution_monitor=_Monitor(),
    )

    assert status == "FAILED"
    assert captured["values"]["status"] == "FAILED"
    assert "可重试" in captured["values"]["error_message"]
    assert captured["db"] is db
    assert captured["manage_transaction"] is True


def test_recompile_path_has_execution_fencing_and_conditional_final_write():
    source = open(export_svc.__file__, encoding="utf-8").read()
    section = source[
        source.index("def _run_bm1688_recompile_sync") : source.index(
            "def verify_export_sophon_sync"
        )
    ]
    assert '_start_export_execution_monitor(' in section
    assert 'label="bm1688-recompile"' in section
    assert "execution_monitor.ensure_current()" in section
    assert "execution_monitor.conditional_update(" in section
    assert "_stop_export_execution_monitor(execution_monitor)" in section


def test_recompile_execution_exception_is_failed_and_retryable():
    source = open(export_svc.__file__, encoding="utf-8").read()
    section = source[
        source.index("def _run_bm1688_recompile_sync") : source.index(
            "def verify_export_sophon_sync"
        )
    ]
    failure_handler = section[section.index("except Exception as e:") :]
    assert "_persist_recompile_failure(" in failure_handler
    assert "if execution_monitor is not None or fencing_unavailable:" in failure_handler
    assert '"retryable"] = True' in failure_handler
    assert '"status": "ONNX_READY"' not in failure_handler


def test_start_export_for_job_forwards_user_to_response_redaction(monkeypatch):
    db = MagicMock()
    task = SimpleNamespace(id=10, name="demo-task")
    job = SimpleNamespace(
        id=22,
        task_id=10,
        status="COMPLETED",
        weights_path="weights/best.pt",
        imgsz=640,
    )
    row = SimpleNamespace(id=31)
    current_user = SimpleNamespace(id=7, username="admin@123.com")
    captured = {}

    monkeypatch.setattr(export_svc.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(export_svc.crud_task, "get", lambda **_kwargs: task)
    monkeypatch.setattr(export_svc, "_assert_task_access", lambda *_args: None)
    monkeypatch.setattr(export_svc.crud_yolo_train_job, "get", lambda *_args: job)
    monkeypatch.setattr(
        export_svc.crud_yolo_bm1688_export,
        "has_processing_for_job",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        export_svc.crud_yolo_bm1688_export,
        "create",
        lambda *_args, **_kwargs: row,
    )
    monkeypatch.setattr(
        export_svc.crud_yolo_bm1688_export,
        "get",
        lambda *_args, **_kwargs: row,
    )
    tx = MagicMock()
    tx.__enter__.return_value = db
    tx.__exit__.return_value = False
    monkeypatch.setattr(export_svc, "begin_transaction", lambda _db: tx)
    loop = MagicMock()
    monkeypatch.setattr(export_svc.asyncio, "get_event_loop", lambda: loop)

    def _capture_response(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "response"

    monkeypatch.setattr(export_svc, "_export_row_to_response", _capture_response)

    result = asyncio.run(
        export_svc.start_export_for_job(
            db,
            task_id=10,
            job_id=22,
            current_user=current_user,
        )
    )

    assert result == "response"
    assert captured["kwargs"]["current_user"] is current_user


def test_start_export_for_job_only_enqueues_when_dedicated_worker_is_enabled(monkeypatch):
    db = MagicMock()
    task = SimpleNamespace(id=10, name="demo-task")
    job = SimpleNamespace(
        id=22,
        task_id=10,
        status="COMPLETED",
        weights_path="weights/best.pt",
        imgsz=640,
    )
    row = SimpleNamespace(id=31)
    user = SimpleNamespace(id=7, username="admin@123.com")
    created = {}

    monkeypatch.setattr(export_svc.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(export_svc.crud_task, "get", lambda **_kwargs: task)
    monkeypatch.setattr(export_svc, "_assert_task_access", lambda *_args: None)
    monkeypatch.setattr(export_svc.crud_yolo_train_job, "get", lambda *_args: job)
    monkeypatch.setattr(
        export_svc.crud_yolo_bm1688_export,
        "has_processing_for_job",
        lambda *_args, **_kwargs: False,
    )

    def _create(*_args, **kwargs):
        created.update(kwargs)
        return row

    monkeypatch.setattr(export_svc.crud_yolo_bm1688_export, "create", _create)
    monkeypatch.setattr(
        export_svc.crud_yolo_bm1688_export, "get", lambda *_args, **_kwargs: row
    )
    tx = MagicMock()
    tx.__enter__.return_value = db
    tx.__exit__.return_value = False
    monkeypatch.setattr(export_svc, "begin_transaction", lambda _db: tx)
    loop = MagicMock()
    monkeypatch.setattr(export_svc.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(export_svc, "dedicated_bm1688_worker_enabled", lambda: True)
    monkeypatch.setattr(export_svc, "_export_row_to_response", lambda *a, **k: "response")

    result = asyncio.run(
        export_svc.start_export_for_job(
            db, task_id=10, job_id=22, current_user=user
        )
    )

    assert result == "response"
    assert created["worker_action"] == "export"
    loop.run_in_executor.assert_not_called()


def test_maybe_auto_resume_recompile_when_onnx_exists(monkeypatch):
    row = MagicMock(status="PROCESSING", onnx_rel_path="yolo_bm1688_exports/24/models/onnx/x.onnx")
    row.auto_training_round_id = None
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=MagicMock())
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.session_scope",
        lambda: fake_scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.crud_yolo_bm1688_export.get",
        lambda _db, eid: row if eid == 24 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._resolve_onnx_path_for_row",
        lambda _row: __import__("pathlib").Path("/tmp/x.onnx"),
    )
    started = []

    def _fake_recompile(eid):
        started.append(eid)

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._run_bm1688_recompile_sync",
        _fake_recompile,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.threading.Thread",
        lambda target, daemon, name: MagicMock(start=lambda: target()),
    )

    assert maybe_auto_resume_export(24) is True
    assert started == [24]


def test_maybe_auto_resume_full_export_when_no_onnx(monkeypatch):
    row = MagicMock(status="PENDING")
    row.auto_training_round_id = None
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=MagicMock())
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.session_scope",
        lambda: fake_scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.crud_yolo_bm1688_export.get",
        lambda _db, eid: row if eid == 9 else None,
    )

    def _raise(_row):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=404,
            message="no onnx",
        )

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._resolve_onnx_path_for_row",
        _raise,
    )
    started = []

    def _fake_export(eid):
        started.append(eid)

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._run_bm1688_export_sync",
        _fake_export,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.threading.Thread",
        lambda target, daemon, name: MagicMock(start=lambda: target()),
    )

    assert maybe_auto_resume_export(9) is True
    assert started == [9]


def test_phase_one_gate_skips_automatic_export_resume_but_keeps_manual(
    monkeypatch,
):
    import labelu.internal.application.service.yolo_bm1688_export_auto_resume as auto_resume

    rows = {
        31: SimpleNamespace(
            id=31,
            status="PROCESSING",
            auto_training_round_id=71,
        ),
        32: SimpleNamespace(
            id=32,
            status="PROCESSING",
            auto_training_round_id=None,
        ),
    }
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=MagicMock())
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(auto_resume, "session_scope", lambda: fake_scope)
    monkeypatch.setattr(
        auto_resume.crud_yolo_bm1688_export,
        "get",
        lambda _db, export_id: rows.get(int(export_id)),
    )
    monkeypatch.setattr(auto_resume.settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(
        export_svc,
        "_resolve_onnx_path_for_row",
        lambda _row: __import__("pathlib").Path("/tmp/manual.onnx"),
    )
    started = []
    monkeypatch.setattr(
        export_svc,
        "_run_bm1688_recompile_sync",
        lambda export_id: started.append(int(export_id)),
    )
    monkeypatch.setattr(
        auto_resume.threading,
        "Thread",
        lambda target, daemon, name: SimpleNamespace(start=target),
    )

    assert maybe_auto_resume_export(31) is False
    assert maybe_auto_resume_export(32) is True
    assert started == [32]


def test_scan_and_auto_resume_all_exports(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.settings.YOLO_TRAIN_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.settings.YOLO_BM1688_AUTO_RESUME_ENABLED",
        True,
    )
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=MagicMock())
    fake_scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.session_scope",
        lambda: fake_scope,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume._list_stuck_export_ids",
        lambda _db: [3, 4],
    )
    calls = []

    def _resume(eid):
        calls.append(eid)
        return True

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export_auto_resume.maybe_auto_resume_export",
        _resume,
    )
    assert scan_and_auto_resume_all_exports() == 2
    assert calls == [3, 4]


def test_maybe_auto_dispatch_completed_export_creates_missing_job(monkeypatch):
    import labelu.internal.application.service.yolo_bm1688_export_auto_resume as auto_resume

    row = SimpleNamespace(
        id=148,
        status="COMPLETED",
        user_id=7,
        task_id=91,
        yolo_train_job_id=271,
        imgsz=640,
        bmodel_rel_path="models/BM1688/model_int8_1b.bmodel",
    )
    db = MagicMock()
    scope = MagicMock()
    scope.__enter__ = MagicMock(return_value=db)
    scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(auto_resume, "session_scope", lambda: scope)
    monkeypatch.setattr(auto_resume, "_edge_auto_dispatch_enabled", lambda: True)
    monkeypatch.setattr(export_svc, "is_export_active", lambda _eid: False)
    monkeypatch.setattr(
        auto_resume.crud_yolo_bm1688_export,
        "get",
        lambda _db, eid: row if int(eid) == 148 else None,
    )
    monkeypatch.setattr(
        auto_resume.crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda _db, *, export_id: None,
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_user.get",
        lambda db=None, id=None: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_task.get",
        lambda db=None, task_id=None: SimpleNamespace(id=task_id),
    )
    started = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._maybe_run_edge_ssh_validation_sync",
        lambda export_id, **kwargs: started.append((export_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        auto_resume.threading,
        "Thread",
        lambda target, daemon, name: SimpleNamespace(start=target),
    )

    assert maybe_auto_dispatch_completed_export(148) is True
    assert started == [
        (
            148,
            {"yolo_train_job_id": 271, "imgsz": 640, "force_run": True},
        )
    ]


@pytest.mark.parametrize(
    ("bmodel_rel_path", "task"),
    [
        (None, SimpleNamespace(id=91)),
        ("models/BM1688/model_int8_1b.bmodel", None),
    ],
)
def test_maybe_auto_dispatch_completed_export_skips_unusable_export(
    monkeypatch, bmodel_rel_path, task
):
    import labelu.internal.application.service.yolo_bm1688_export_auto_resume as auto_resume

    row = SimpleNamespace(
        id=148,
        status="COMPLETED",
        user_id=7,
        task_id=91,
        yolo_train_job_id=271,
        imgsz=640,
        bmodel_rel_path=bmodel_rel_path,
    )
    db = MagicMock()
    scope = MagicMock()
    scope.__enter__ = MagicMock(return_value=db)
    scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(auto_resume, "session_scope", lambda: scope)
    monkeypatch.setattr(auto_resume, "_edge_auto_dispatch_enabled", lambda: True)
    monkeypatch.setattr(export_svc, "is_export_active", lambda _eid: False)
    monkeypatch.setattr(auto_resume.crud_yolo_bm1688_export, "get", lambda *_args: row)
    monkeypatch.setattr(
        auto_resume.crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_task.get",
        lambda db=None, task_id=None: task,
    )

    assert maybe_auto_dispatch_completed_export(148) is False


@pytest.mark.parametrize("status", ["PENDING", "PROCESSING", "COMPLETED", "PARTIAL_COMPLETED"])
def test_maybe_auto_dispatch_completed_export_skips_existing_job(monkeypatch, status):
    import labelu.internal.application.service.yolo_bm1688_export_auto_resume as auto_resume

    row = SimpleNamespace(
        id=148,
        status="COMPLETED",
        user_id=7,
        yolo_train_job_id=271,
        imgsz=640,
    )
    db = MagicMock()
    scope = MagicMock()
    scope.__enter__ = MagicMock(return_value=db)
    scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(auto_resume, "session_scope", lambda: scope)
    monkeypatch.setattr(auto_resume, "_edge_auto_dispatch_enabled", lambda: True)
    monkeypatch.setattr(export_svc, "is_export_active", lambda _eid: False)
    monkeypatch.setattr(auto_resume.crud_yolo_bm1688_export, "get", lambda *_args: row)
    monkeypatch.setattr(
        auto_resume.crud_yolo_edge_validation_job,
        "get_latest_for_export",
        lambda *_args, **_kwargs: SimpleNamespace(status=status),
    )
    called = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._maybe_run_edge_ssh_validation_sync",
        lambda *_args, **_kwargs: called.append(True),
    )

    assert maybe_auto_dispatch_completed_export(148) is False
    assert called == []


def test_scan_and_auto_dispatch_completed_exports(monkeypatch):
    import labelu.internal.application.service.yolo_bm1688_export_auto_resume as auto_resume

    db = MagicMock()
    scope = MagicMock()
    scope.__enter__ = MagicMock(return_value=db)
    scope.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(auto_resume, "session_scope", lambda: scope)
    monkeypatch.setattr(auto_resume, "_edge_auto_dispatch_enabled", lambda: True)
    monkeypatch.setattr(
        auto_resume,
        "_list_completed_exports_missing_edge_jobs",
        lambda _db: [148, 149],
    )
    monkeypatch.setattr(
        auto_resume,
        "maybe_auto_dispatch_completed_export",
        lambda eid: int(eid) == 148,
    )
    monkeypatch.setattr(auto_resume.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(auto_resume.settings, "YOLO_BM1688_AUTO_RESUME_ENABLED", True)

    assert scan_and_auto_dispatch_completed_exports() == 1


def test_maybe_run_edge_ssh_validation_sync_reuses_unified_manual_job(monkeypatch):
    row = MagicMock()
    row.id = 24
    row.user_id = 7
    user = MagicMock()
    user.id = 7
    fake_db = MagicMock()
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=fake_db)
    fake_scope.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.crud_yolo_bm1688_export.get",
        lambda _db, eid: row if eid == 24 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_user.get",
        lambda db=None, id=None: user if int(id) == 7 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_config.should_auto_run_edge_ssh",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_probe.probe_edge_ssh_target",
        lambda *_args, **_kwargs: {"status": "ok", "code": "ok", "stage": "remote_base", "message": "ok"},
    )
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: fake_scope,
    )
    called = {}

    def _fake_run(db, *, export_id, current_user):
        called["db"] = db
        called["export_id"] = export_id
        called["current_user"] = current_user

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.run_legacy_edge_ssh_job_sync",
        _fake_run,
    )

    export_svc._maybe_run_edge_ssh_validation_sync(
        24,
        yolo_train_job_id=101,
        imgsz=640,
    )

    assert called["db"] is fake_db
    assert called["export_id"] == 24
    assert called["current_user"] is user


def test_maybe_run_edge_ssh_validation_sync_can_force_run(monkeypatch):
    row = MagicMock()
    row.id = 25
    row.user_id = 8
    user = MagicMock()
    user.id = 8
    fake_db = MagicMock()
    fake_scope = MagicMock()
    fake_scope.__enter__ = MagicMock(return_value=fake_db)
    fake_scope.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.crud_yolo_bm1688_export.get",
        lambda _db, eid: row if eid == 25 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_user.get",
        lambda db=None, id=None: user if int(id) == 8 else None,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_config.should_auto_run_edge_ssh",
        lambda: False,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_probe.probe_edge_ssh_target",
        lambda *_args, **_kwargs: {"status": "ok", "code": "ok", "stage": "remote_base", "message": "ok"},
    )
    monkeypatch.setattr(
        "labelu.internal.common.db.session_scope",
        lambda: fake_scope,
    )
    called = {}

    def _fake_run(db, *, export_id, current_user):
        called["db"] = db
        called["export_id"] = export_id
        called["current_user"] = current_user

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.run_legacy_edge_ssh_job_sync",
        _fake_run,
    )

    export_svc._maybe_run_edge_ssh_validation_sync(
        25,
        yolo_train_job_id=102,
        imgsz=640,
        force_run=True,
    )

    assert called["db"] is fake_db
    assert called["export_id"] == 25
    assert called["current_user"] is user


def test_maybe_run_edge_ssh_validation_sync_skips_when_probe_fails(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_config.should_auto_run_edge_ssh",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_probe.probe_edge_ssh_target",
        lambda *_args, **_kwargs: {
            "status": "fail",
            "code": "auth_failed",
            "stage": "auth",
            "message": "SSH 认证失败",
        },
    )
    called = {"run": False}
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.run_legacy_edge_ssh_job_sync",
        lambda *_args, **_kwargs: called.__setitem__("run", True),
    )

    export_svc._maybe_run_edge_ssh_validation_sync(
        25,
        yolo_train_job_id=102,
        imgsz=640,
    )

    assert called["run"] is False


class _StaleExecutionMonitor:
    def ensure_current(self):
        raise ExecutionFencingLost("stale execution lease")


def test_post_compile_edge_callback_is_blocked_after_execution_lease_loss(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_scheduler.should_auto_dispatch",
        lambda: True,
    )
    called = []
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation.run_legacy_edge_ssh_job_sync",
        lambda *_args, **_kwargs: called.append(True),
    )

    with pytest.raises(ExecutionFencingLost):
        export_svc._maybe_run_edge_ssh_validation_sync(
            25,
            yolo_train_job_id=102,
            imgsz=640,
            execution_monitor=_StaleExecutionMonitor(),
        )

    assert called == []


def test_post_compile_quality_callback_is_blocked_after_execution_lease_loss(monkeypatch):
    called = []
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_quality_report.persist_quality_report_for_export",
        lambda *_args, **_kwargs: called.append(True),
    )

    with pytest.raises(ExecutionFencingLost):
        export_svc._generate_quality_report_sync(
            25,
            run_onnx_val=False,
            execution_monitor=_StaleExecutionMonitor(),
        )

    assert called == []


def test_post_compile_sop_callback_is_blocked_after_execution_lease_loss(monkeypatch):
    monkeypatch.setattr(
        export_svc.settings,
        "YOLO_SOP_SERVER_EVAL_ENABLED",
        True,
    )

    with pytest.raises(ExecutionFencingLost):
        from labelu.internal.application.service.yolo_sop_model_evaluation import (
            run_server_sop_evaluations_for_export,
        )

        run_server_sop_evaluations_for_export(
            25,
            yolo_train_job_id=102,
            execution_monitor=_StaleExecutionMonitor(),
        )


def test_export_row_to_response_uses_effective_edge_validation_status(monkeypatch):
    row = MagicMock()
    row.id = 24
    row.task_id = 11
    row.yolo_train_job_id = 77
    row.status = "COMPLETED"
    row.imgsz = 640
    row.onnx_rel_path = None
    row.bmodel_rel_path = None
    row.error_message = None
    row.created_at = None
    row.updated_at = None
    row.quality_report = None

    manual_status = BoardValidationStatusResponse(
        validation_id=88,
        export_id=24,
        status="COMPLETED",
        report={"source": "manual_edge_validation"},
    )

    monkeypatch.setattr(export_svc, "_list_bmodel_names", lambda export_id: [])
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._validation_gate_label_for_job",
        lambda db, job_id: "达标",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_aicm_publish._edge_validation_status_for_export",
        lambda db, export_id: manual_status,
    )

    resp = export_svc._export_row_to_response(row, "demo-task", db=MagicMock())

    assert resp.edge_validation_label == "已完成"
    assert resp.edge_result_status == "result_pending"
    assert resp.board_validation is manual_status


def test_export_row_to_response_marks_reused_threshold_export(monkeypatch):
    row = MagicMock()
    row.id = 59
    row.task_id = 11
    row.yolo_train_job_id = 77
    row.status = "COMPLETED"
    row.imgsz = 640
    row.onnx_rel_path = None
    row.bmodel_rel_path = None
    row.error_message = None
    row.created_at = None
    row.updated_at = None
    row.quality_report = None

    strategy_row = SimpleNamespace(
        task_type="threshold_experiment",
        external_task_id="third-party-threshold-reused-export",
        platform_status="edge_validation_created",
    )
    monkeypatch.setattr(export_svc, "_list_bmodel_names", lambda export_id: [])
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._validation_gate_label_for_job",
        lambda db, job_id: "",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_aicm_publish._edge_validation_status_for_export",
        lambda db, export_id: None,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.crud_strategy_task_integration.get_by_related_export_id",
        lambda db, export_id: strategy_row,
    )

    response = export_svc._export_row_to_response(row, "source-task", db=MagicMock())

    assert response.strategy_task is not None
    assert response.strategy_task.task_type == "threshold_experiment"
    assert response.strategy_task.external_task_id == "third-party-threshold-reused-export"


def test_export_payload_redacts_machine_paths_for_non_superuser():
    payload = {
        "export_root": "C:/labelu/media/exports/24",
        "source_dataset": {
            "dataset_root": "C:/labelu/media/tasks/11/dataset",
            "files": [
                {
                    "path": "C:/labelu/media/tasks/11/dataset/images/a.jpg",
                    "relative_path": "images/a.jpg",
                    "sha256": "abc",
                }
            ],
        },
        "remote_host": "192.168.54.169",
        "message": "目录: C:/labelu/media/exports/24/models",
        "metrics": {"map50": 0.8},
    }

    member = MagicMock(username="test@example.com")
    redacted = export_svc._export_payload_for_user(payload, member)

    assert redacted["export_root"] is None
    assert redacted["source_dataset"]["dataset_root"] is None
    assert redacted["source_dataset"]["files"][0]["path"] is None
    assert redacted["source_dataset"]["files"][0]["relative_path"] == "images/a.jpg"
    assert "C:/labelu/media/exports/24/models" not in redacted["message"]
    assert "[路径已隐藏]" in redacted["message"]
    assert redacted["metrics"]["map50"] == 0.8

    admin = MagicMock(username="admin@123.com")
    assert export_svc._export_payload_for_user(payload, admin) is payload


def test_board_validation_response_redacts_progress_and_extended_path_keys():
    progress = BoardValidationProgressResponse(
        stage="pull_output",
        message="正在回传边端结果",
        detail={
            "remote_host": "10.0.0.8",
            "remote_job_dir": "/data/labelu_edge/jobs/job-24",
            "remote_bundle": "/data/labelu_edge/jobs/job-24/output.zip",
            "remote_base": "/data/labelu_edge/jobs",
            "results_dir": "/tmp/results",
        },
        history=[
            {
                "stage": "upload",
                "detail": {
                    "images_dir": "/tmp/images",
                    "xml_dir": "/tmp/xml",
                },
            }
        ],
    )
    status = BoardValidationStatusResponse(
        validation_id=24,
        export_id=24,
        status="PROCESSING",
        progress=progress,
        report={
            "export_root_abs": "C:/labelu/media/exports/24",
            "source_path": "C:/labelu/media/metrics.json",
            "remote_bundle": "/data/labelu_edge/jobs/job-24/output.zip",
            "metrics": {"map50": 0.8},
        },
        train_board_comparison={"results_dir": "/tmp/results", "delta": 0.01},
    )

    member = MagicMock(username="test@example.com")
    redacted = export_svc._redact_board_validation_status_for_user(status, member)

    assert redacted is status
    assert redacted.progress.detail["remote_host"] is None
    assert redacted.progress.detail["remote_job_dir"] is None
    assert redacted.progress.detail["remote_bundle"] is None
    assert redacted.progress.detail["remote_base"] is None
    assert redacted.progress.detail["results_dir"] is None
    assert redacted.progress.history[0]["detail"]["images_dir"] is None
    assert redacted.progress.history[0]["detail"]["xml_dir"] is None
    assert redacted.report["export_root_abs"] is None
    assert redacted.report["source_path"] is None
    assert redacted.report["remote_bundle"] is None
    assert redacted.report["metrics"]["map50"] == 0.8
    assert redacted.train_board_comparison["results_dir"] is None
    assert redacted.train_board_comparison["delta"] == 0.01

    admin = MagicMock(username="admin@123.com")
    admin_status = BoardValidationStatusResponse(
        validation_id=24,
        export_id=24,
        status="PROCESSING",
        progress=progress,
        report={"export_root_abs": "C:/labelu/media/exports/24"},
    )
    assert export_svc._redact_board_validation_status_for_user(admin_status, admin) is admin_status
    assert admin_status.report["export_root_abs"] == "C:/labelu/media/exports/24"


def test_export_sophon_verify_redacts_paths_for_member(monkeypatch):
    monkeypatch.setattr(
        export_svc,
        "get_export_for_download",
        lambda db, *, export_id, current_user: object(),
    )
    monkeypatch.setattr(
        export_svc,
        "verify_export_sophon_sync",
        lambda export_id: {
            "export_root": "C:/labelu/media/exports/24",
            "checks": [
                {
                    "name": "models",
                    "message": "目录: C:/labelu/media/exports/24/models",
                    "source_path": "C:/labelu/media/exports/24/models",
                }
            ],
        },
    )

    import asyncio

    member = MagicMock(username="test@example.com")
    report = asyncio.run(
        export_svc.get_export_sophon_verify(
            MagicMock(), export_id=24, current_user=member
        )
    )

    assert report["export_root"] is None
    assert report["checks"][0]["source_path"] is None
    assert "C:/labelu/media/exports/24/models" not in report["checks"][0]["message"]
    assert "[路径已隐藏]" in report["checks"][0]["message"]


def test_path_response_model_redacts_string_fields_without_none_validation():
    member = MagicMock(username="member-only@example.com")
    compiler = SimpleNamespace(
        tpu_mlir_root="/opt/tpu-mlir",
        calibration_dataset_dir="/data/calibration",
        quantize_table_path="/data/qtable",
        tpu_mlir_python="/data/venv/bin/python3",
        builtin_script_path="C:/labelu/scripts/compile.sh",
        legacy_sync_file="C:/labelu/config.json",
        custom_compile_command="bash /opt/tpu-mlir/compile.sh",
    )

    # Use the real Pydantic model so the test catches None-to-str regressions.
    from labelu.internal.application.response.bm1688_compiler import (
        Bm1688CompilerConfigResponse,
    )

    compiler_response = Bm1688CompilerConfigResponse(**vars(compiler))
    redacted = export_svc._redact_path_response_model_for_user(
        compiler_response,
        member,
        empty_path_fields=(
            "tpu_mlir_root",
            "calibration_dataset_dir",
            "quantize_table_path",
            "tpu_mlir_python",
            "builtin_script_path",
            "legacy_sync_file",
        ),
    )
    assert redacted.tpu_mlir_root == ""
    assert redacted.calibration_dataset_dir == ""
    assert redacted.quantize_table_path == ""
    assert redacted.tpu_mlir_python == ""
    assert redacted.builtin_script_path == ""
    assert redacted.legacy_sync_file == ""
    assert "[路径已隐藏]" in redacted.custom_compile_command


def test_export_detail_redacts_quality_refresh_error_path(monkeypatch):
    row = SimpleNamespace(id=24, compile_log="compile /data/private/log")
    base = YoloBm1688ExportRowResponse(
        id=24,
        task_id=11,
        yolo_train_job_id=77,
        status="COMPLETED",
        imgsz=640,
    )
    monkeypatch.setattr(export_svc, "_export_row_to_response", lambda *args, **kwargs: base)
    monkeypatch.setattr(export_svc, "_can_recompile_bmodel", lambda _row: False)
    monkeypatch.setattr(
        export_svc,
        "_export_dir_display_for_user",
        lambda _export_id, _current_user: "yolo_bm1688_exports/24",
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_quality_report.hydrate_quality_report_from_disk",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_quality_refresh.read_refresh_status",
        lambda _export_id: {
            "status": "FAILED",
            "error_message": "failed at C:/labelu/media/exports/24/refresh.log",
        },
    )

    member = MagicMock(username="member-only@example.com")
    detail = export_svc._export_detail_response(
        row,
        "task",
        MagicMock(),
        current_user=member,
    )

    assert detail.quality_report_refresh["status"] == "FAILED"
    assert "C:/labelu/media/exports/24/refresh.log" not in detail.quality_report_refresh[
        "error_message"
    ]
    assert "[路径已隐藏]" in detail.quality_report_refresh["error_message"]


def test_missing_sop_assets_do_not_return_filesystem_paths(monkeypatch):
    monkeypatch.setattr(
        export_svc,
        "get_export_for_download",
        lambda db, *, export_id, current_user: object(),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_sop_model_evaluation.resolve_sop_eval_report_path",
        lambda _export_id, _kind: (_ for _ in ()).throw(
            FileNotFoundError("C:/labelu/media/exports/24/server_eval/evaluation_report.md")
        ),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_sop_model_evaluation.resolve_sop_eval_asset_path",
        lambda _export_id, _kind, _filename: (_ for _ in ()).throw(
            FileNotFoundError("/data/labelu/media/exports/24/server_eval/curve.png")
        ),
    )
    member = MagicMock(username="member-only@example.com")

    with pytest.raises(LabelUException) as report_exc:
        export_svc.get_sop_eval_report_markdown(
            MagicMock(), export_id=24, kind="pt", current_user=member
        )
    assert report_exc.value.msg == "评估报告尚未生成"
    assert "C:/labelu/media/exports/24" not in report_exc.value.msg

    with pytest.raises(LabelUException) as asset_exc:
        export_svc.get_sop_eval_asset_path(
            MagicMock(),
            export_id=24,
            kind="pt",
            filename="curve.png",
            current_user=member,
        )
    assert asset_exc.value.msg == "评估资源不存在"
    assert "/data/labelu/media/exports/24" not in asset_exc.value.msg

    admin = MagicMock(username="admin@123.com")
    with pytest.raises(LabelUException) as admin_report_exc:
        export_svc.get_sop_eval_report_markdown(
            MagicMock(), export_id=24, kind="pt", current_user=admin
        )
    assert "C:/labelu/media/exports/24/server_eval/evaluation_report.md" in admin_report_exc.value.msg
