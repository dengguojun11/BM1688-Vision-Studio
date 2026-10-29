from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from labelu.internal.application.command.yolo_edge_validation import (
    CreateYoloEdgeValidationJobCommand,
)
from labelu.internal.application.service import yolo_edge_validation as service


def test_execution_profile_defaults_to_quick_and_rejects_unknown_value():
    command = CreateYoloEdgeValidationJobCommand(export_id=1, task_id=2)
    assert command.execution_profile == "quick"

    with pytest.raises(ValidationError):
        CreateYoloEdgeValidationJobCommand(
            export_id=1,
            task_id=2,
            execution_profile="full",
        )


def test_legacy_job_execution_profile_defaults_to_quick():
    assert service._job_execution_profile(SimpleNamespace(result_json="{}")) == "quick"
    row = SimpleNamespace(
        result_json='{"request":{"execution_profile":"certification"}}'
    )
    assert service._job_execution_profile(row) == "certification"


def test_model_cache_key_is_stable_and_changes_with_model_content(tmp_path):
    staging = tmp_path / "staging"
    model_root = staging / "models" / "BM1688"
    model_root.mkdir(parents=True)
    model_a = model_root / "model_fp16.bmodel"
    model_b = model_root / "model_int8.bmodel"
    model_a.write_bytes(b"fp16")
    model_b.write_bytes(b"int8")

    first = service._edge_model_cache_key(staging)
    second = service._edge_model_cache_key(staging)
    model_b.write_bytes(b"int8-updated")
    changed = service._edge_model_cache_key(staging)

    assert first == second
    assert first != changed
    assert first is not None and len(first) == 64


def test_cached_task_bundle_excludes_models(tmp_path):
    staging = tmp_path / "staging"
    (staging / "models" / "BM1688").mkdir(parents=True)
    (staging / "models" / "BM1688" / "model.bmodel").write_bytes(b"model")
    (staging / "eval_data").mkdir()
    (staging / "eval_data" / "sample.jpg").write_bytes(b"image")
    (staging / "run_config.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "task.tar.gz"

    service._build_cached_edge_task_bundle(staging, destination)

    with tarfile.open(destination, "r:gz") as archive:
        names = archive.getnames()
    assert "run_config.json" in names
    assert "eval_data/sample.jpg" in names
    assert not any(name == "models" or name.startswith("models/") for name in names)


def _write_output_archive(path: Path, *, valid: bool) -> None:
    if not valid:
        path.write_bytes(b"not-a-tar")
        return
    with tarfile.open(path, "w:gz") as archive:
        payload = b'{"status":"ok"}'
        member = tarfile.TarInfo("output/board_report.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))


def test_download_manual_output_uses_lightweight_archive(monkeypatch, tmp_path):
    local_out = tmp_path / "pull" / "output"
    tree_calls = []

    def _download(_client, _remote, local_path):
        _write_output_archive(Path(local_path), valid=True)
        return True

    monkeypatch.setattr(service.edge_ssh_validation, "sftp_download", _download)
    monkeypatch.setattr(
        service.edge_ssh_validation,
        "sftp_download_tree",
        lambda *_args, **_kwargs: tree_calls.append(True),
    )
    summary = {}

    pulled = service._download_manual_edge_output(
        object(),
        remote_job="/data/labelu_edge/jobs/manual-1",
        local_out=local_out,
        summary=summary,
    )

    assert pulled == 1
    assert summary["output_transfer"] == "archive_lite"
    assert summary["output_archive_bytes"] > 0
    assert tree_calls == []
    assert (local_out / "board_report.json").is_file()


def test_download_manual_output_falls_back_when_archive_is_invalid(
    monkeypatch, tmp_path
):
    local_out = tmp_path / "pull" / "output"
    tree_calls = []

    def _download(_client, _remote, local_path):
        _write_output_archive(Path(local_path), valid=False)
        return True

    def _download_tree(_client, remote, destination):
        tree_calls.append(remote)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "summary.json").write_text("{}", encoding="utf-8")
        return 1

    monkeypatch.setattr(service.edge_ssh_validation, "sftp_download", _download)
    monkeypatch.setattr(
        service.edge_ssh_validation, "sftp_download_tree", _download_tree
    )
    summary = {}

    pulled = service._download_manual_edge_output(
        object(),
        remote_job="/data/labelu_edge/jobs/manual-2",
        local_out=local_out,
        summary=summary,
    )

    assert pulled == 1
    assert summary["output_transfer"] == "sftp_tree_fallback"
    assert summary["output_archive_error"]
    assert tree_calls == ["/data/labelu_edge/jobs/manual-2/output"]
    assert (local_out / "summary.json").is_file()
