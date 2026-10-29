"""Training-qualified BM1688 auto-compile orchestration tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from labelu.internal.application.service import yolo_bm1688_auto_compile as auto_compile


def test_compiler_ready_requires_auto_compile(monkeypatch, tmp_path):
    monkeypatch.setattr(
        auto_compile.settings, "YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED", True
    )
    monkeypatch.setattr(
        auto_compile.cfg_io,
        "load_bm1688_compiler_config",
        lambda: {"auto_compile": False, "tpu_mlir_root": str(tmp_path)},
    )
    assert auto_compile._compiler_ready() is False


def test_compiler_ready_accepts_configured_builtin(monkeypatch, tmp_path):
    script = tmp_path / "bm1688_onnx_to_bmodel.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        auto_compile.settings, "YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED", True
    )
    monkeypatch.setattr(
        auto_compile.cfg_io,
        "load_bm1688_compiler_config",
        lambda: {"auto_compile": True, "tpu_mlir_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        auto_compile.export_service, "_builtin_compile_script", lambda: script
    )
    assert auto_compile._compiler_ready() is True


def test_recovery_scan_is_batched_and_delegates_to_idempotent_submit(monkeypatch):
    jobs = [SimpleNamespace(id=7), SimpleNamespace(id=8), SimpleNamespace(id=9)]
    fake_db = SimpleNamespace()
    fake_scope = MagicMock()
    fake_scope.__enter__.return_value = fake_db
    fake_scope.__exit__.return_value = False
    monkeypatch.setattr(auto_compile, "session_scope", lambda: fake_scope)
    monkeypatch.setattr(
        auto_compile.crud_yolo_train_job,
        "list_completed_with_weights",
        lambda _db: jobs,
    )
    monkeypatch.setattr(auto_compile, "_compiler_ready", lambda: True)
    monkeypatch.setattr(auto_compile.settings, "YOLO_BM1688_AUTO_COMPILE_BATCH_SIZE", 2)
    selected = []

    def _fake_submit(job_id, *, trigger):
        selected.append((job_id, trigger))
        return True

    monkeypatch.setattr(auto_compile, "maybe_auto_compile_job", _fake_submit)
    assert auto_compile.scan_passed_training_jobs_missing_exports() == 2
    assert selected == [(7, "recovery_scan"), (8, "recovery_scan")]
