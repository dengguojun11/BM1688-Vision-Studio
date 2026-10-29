"""边端 SSH 验证打包逻辑（不连真实 SSH）。"""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from labelu.internal.application.service.edge_ssh_validation import (
    cleanup_stale_remote_job_dirs,
    _cleanup_remote_job_dir,
    _copy_eval_dataset,
    _edge_run_log_failure_hint,
    _edge_ssh_configured,
    _ingest_remote_results,
    _pull_remote_run_log_if_present,
    _read_json_object_file,
    _retain_failed_remote_job_dir,
    build_edge_remote_validation_command,
    edge_model_cache_exists,
)
from labelu.internal.common.edge_ssh_client import EdgeSshError
from labelu.internal.common.edge_script_paths import (
    resolve_board_agent_script,
    resolve_edge_board_validate_runner,
)


def test_edge_ssh_configured_requires_host(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.edge_cfg_io.load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "",
            "user": "linaro",
            "password": "secret-pass",
        },
    )
    assert _edge_ssh_configured() is False

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.edge_cfg_io.load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-pass",
        },
    )
    assert _edge_ssh_configured() is True


def test_build_edge_remote_validation_command_uses_docker():
    cmd = build_edge_remote_validation_command(
        remote_job="/data/labelu_edge/jobs/export-22",
        demo_env="/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
        docker_container="bm1688",
        docker_use_sudo=True,
    )
    assert "sudo docker exec bm1688 bash -lc" in cmd
    assert "/data/labelu_edge/jobs/export-22" in cmd
    assert "pythonTest" not in cmd
    assert "edge_board_validate_runner.py" in cmd
    assert "import cv2" in cmd
    assert "edge_python=" in cmd
    assert "cp -a work/output/. output/" in cmd
    assert "chown -R" in cmd
    assert "set -o pipefail" in cmd
    assert "runner_code=${PIPESTATUS[0]}" in cmd
    assert "output-lite.tar.gz" in cmd
    assert "--exclude='output/results_bmodel/images'" in cmd
    assert "lightweight output archive failed" in cmd
    assert cmd.endswith("exit $runner_code")


def test_build_edge_remote_validation_command_uses_shared_model_cache():
    cache_key = "a" * 64
    cmd = build_edge_remote_validation_command(
        remote_job="/data/labelu_edge/jobs/manual-22",
        demo_env="/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
        docker_container="bm1688",
        model_cache_key=cache_key,
    )

    assert f"/data/labelu_edge/jobs/.cache/models/{cache_key}" in cmd
    assert 'model_cache_tmp="${model_cache}.tmp.$$"' in cmd
    assert 'mkdir "$model_cache.lock"' in cmd
    assert "edge model cache miss and bundle has no models" in cmd


def test_build_edge_remote_validation_command_uses_device_edge_root():
    cache_key = "b" * 64
    cmd = build_edge_remote_validation_command(
        remote_job="/mnt/edge-data/jobs/manual-23",
        demo_env="/mnt/edge-data/runtime/sophon-demo/sample/YOLOv8_plus_det",
        model_cache_key=cache_key,
        edge_root="/mnt/edge-data",
    )

    assert f"/mnt/edge-data/jobs/.cache/models/{cache_key}" in cmd
    assert "export LABELU_EDGE_ROOT=" in cmd


def test_edge_model_cache_exists_checks_container_filesystem(monkeypatch):
    called = {}

    def _fake_ssh_run(client, command, timeout=7200):
        called["command"] = command
        called["timeout"] = timeout
        return 0, "hit", ""

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.ssh_run",
        _fake_ssh_run,
    )

    assert edge_model_cache_exists(
        object(),
        "c" * 64,
        docker_container="bm1688",
        docker_use_sudo=True,
    )
    assert "sudo docker exec 'bm1688' bash -lc" in called["command"]
    assert "/data/labelu_edge/jobs/.cache/models/" in called["command"]
    assert called["timeout"] == 60


def test_edge_model_cache_exists_checks_host_without_container(monkeypatch):
    called = {}

    def _fake_ssh_run(client, command, timeout=7200):
        called["command"] = command
        return 1, "", "missing"

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.ssh_run",
        _fake_ssh_run,
    )

    assert not edge_model_cache_exists(object(), "d" * 64)
    assert "docker exec" not in called["command"]


def test_cleanup_remote_job_dir_uses_parent_cd_and_job_name(monkeypatch):
    called = {}

    def _fake_ssh_run(client, command, timeout=7200):
        called["command"] = command
        called["timeout"] = timeout
        return 0, "", ""

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.ssh_run",
        _fake_ssh_run,
    )

    ok, msg = _cleanup_remote_job_dir(
        object(), remote_job="/data/labelu_edge/jobs/export-22"
    )
    assert ok is True
    assert msg == ""
    assert "cd '/data/labelu_edge/jobs'" in called["command"]
    assert "rm -rf -- 'export-22'" in called["command"]
    assert called["timeout"] == 300


def test_failed_remote_job_is_marked_for_retention(monkeypatch):
    called = {}

    def _fake_ssh_run(client, command, timeout=7200):
        called["command"] = command
        return 0, "", ""

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.ssh_run",
        _fake_ssh_run,
    )

    ok, msg = _retain_failed_remote_job_dir(
        object(),
        remote_job="/data/labelu_edge/jobs/manual-job-31",
        retention_hours=24,
    )

    assert ok is True
    assert msg == ""
    assert ".labelu_failed_retained" in called["command"]
    assert "rm -rf" not in called["command"]


def test_stale_remote_cleanup_only_removes_allowlisted_job_directories(monkeypatch):
    calls = []

    def _fake_ssh_run(client, command, timeout=7200):
        calls.append(command)
        if len(calls) == 1:
            return 0, "./manual-job-31\n./export-22\n", ""
        return 0, "", ""

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.ssh_run",
        _fake_ssh_run,
    )

    result = cleanup_stale_remote_job_dirs(
        object(), remote_base="/data/labelu_edge/jobs", retention_hours=24
    )

    assert result["status"] == "completed"
    assert result["removed"] == ["manual-job-31", "export-22"]
    assert "-mmin +1440" in calls[0]
    assert "manual-job-[0-9]*|export-[0-9]*" in calls[1]
    assert 'rm -rf -- "$name"' in calls[1]


def test_stale_remote_cleanup_rejects_broad_base_path():
    with pytest.raises(ValueError, match="escapes edge root"):
        cleanup_stale_remote_job_dirs(object(), remote_base="/data", retention_hours=24)


def test_upload_edge_bundle_verified_resumes_part_before_atomic_move(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"payload")
    digest = mod._file_sha256(bundle)
    calls = {"run": []}

    def _fake_stat(_client, remote_path):
        if remote_path.endswith("bundle.tar.gz.part"):
            return len(b"payload")
        raise EdgeSshError("missing")

    def _fake_resume(client, local_path, remote_path, *, progress_callback=None):
        calls["resume"] = (client, local_path, remote_path, progress_callback)
        if progress_callback:
            progress_callback(7, 7)
        return 7

    def _fake_run(_client, command, timeout=7200):
        calls["run"].append((command, timeout))
        if "bundle.tar.gz.part.sha256" in command and "cat " in command:
            return 0, digest, ""
        return 0, "", ""

    progress = []
    monkeypatch.setattr(mod, "sftp_stat_size", _fake_stat)
    monkeypatch.setattr(mod, "sftp_upload_resumable", _fake_resume)
    monkeypatch.setattr(mod, "ssh_run", _fake_run)

    mod._upload_edge_bundle_verified(
        object(),
        bundle,
        "/data/labelu_edge/jobs/job/bundle.tar.gz",
        progress_callback=lambda sent, total: progress.append((sent, total)),
    )

    assert calls["resume"][2] == "/data/labelu_edge/jobs/job/bundle.tar.gz.part"
    assert calls["resume"][3] is not None
    assert progress == [(7, 7)]
    commands = [command for command, _timeout in calls["run"]]
    assert not any("rm -f" in command for command in commands)
    move_command, move_timeout = calls["run"][-1]
    assert move_command.count("mv -f") == 2
    assert "bundle.tar.gz.part.sha256" in move_command
    assert "bundle.tar.gz.sha256" in move_command
    assert move_timeout == 120


def test_upload_edge_bundle_verified_treats_short_part_as_interrupted_transfer(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"payload")
    digest = mod._file_sha256(bundle)

    monkeypatch.setattr(
        mod,
        "_remote_digest_text",
        lambda _client, path: digest if path.endswith(".part.sha256") else "",
    )
    monkeypatch.setattr(mod, "sftp_upload_resumable", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(mod, "sftp_stat_size", lambda *_args, **_kwargs: 3)

    with pytest.raises(mod.EdgeSshTransferInterrupted, match="大小校验失败"):
        mod._upload_edge_bundle_verified(
            object(), bundle, "/data/labelu_edge/jobs/job/bundle.tar.gz"
        )


def test_upload_edge_bundle_verified_rebuilds_part_when_digest_changes(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"new-data")
    commands = []
    resume_calls = []

    def _fake_run(_client, command, timeout=7200):
        commands.append(command)
        if "cat " in command:
            return 0, "old-digest", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "ssh_run", _fake_run)
    monkeypatch.setattr(
        mod,
        "sftp_upload_resumable",
        lambda _client, _local, remote, **_kwargs: resume_calls.append(remote)
        or len(b"new-data"),
    )
    monkeypatch.setattr(
        mod,
        "sftp_stat_size",
        lambda _client, remote: (
            len(b"new-data")
            if remote.endswith(".part")
            else (_ for _ in ()).throw(EdgeSshError("missing"))
        ),
    )

    mod._upload_edge_bundle_verified(
        object(), bundle, "/data/labelu_edge/jobs/job/bundle.tar.gz"
    )

    reset_command = next(command for command in commands if "rm -f" in command)
    assert "/data/labelu_edge/jobs/job/bundle.tar.gz.part" in reset_command
    assert "/data/labelu_edge/jobs/job/bundle.tar.gz.part.sha256" in reset_command
    assert mod._file_sha256(bundle) in reset_command
    assert resume_calls == ["/data/labelu_edge/jobs/job/bundle.tar.gz.part"]


def test_upload_edge_bundle_verified_reuses_matching_completed_bundle(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"complete")
    digest = mod._file_sha256(bundle)
    progress = []

    monkeypatch.setattr(mod, "_remote_digest_text", lambda *_args: digest)
    monkeypatch.setattr(mod, "sftp_stat_size", lambda *_args: len(b"complete"))
    monkeypatch.setattr(
        mod,
        "sftp_upload_resumable",
        lambda *_args, **_kwargs: pytest.fail("matching bundle must not upload"),
    )

    mod._upload_edge_bundle_verified(
        object(),
        bundle,
        "/data/labelu_edge/jobs/job/bundle.tar.gz",
        progress_callback=lambda sent, total: progress.append((sent, total)),
    )

    assert progress == [(len(b"complete"), len(b"complete"))]


def test_upload_edge_bundle_verified_does_not_reuse_same_size_other_content(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"new-data")
    upload_calls = []

    monkeypatch.setattr(mod, "_remote_digest_text", lambda *_args: "old-digest")
    monkeypatch.setattr(mod, "ssh_run", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(
        mod,
        "sftp_stat_size",
        lambda _client, remote: (
            len(b"new-data") if remote.endswith(".part") else len(b"new-data")
        ),
    )
    monkeypatch.setattr(
        mod,
        "sftp_upload_resumable",
        lambda _client, _local, remote, **_kwargs: upload_calls.append(remote)
        or len(b"new-data"),
    )

    mod._upload_edge_bundle_verified(
        object(), bundle, "/data/labelu_edge/jobs/job/bundle.tar.gz"
    )

    assert upload_calls == ["/data/labelu_edge/jobs/job/bundle.tar.gz.part"]


def test_upload_edge_bundle_with_reconnect_retries_transport_failure(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"payload")

    class Client:
        def __init__(self, name):
            self.name = name
            self.closed = False

        def close(self):
            self.closed = True

    first = Client("first")
    second = Client("second")
    upload_clients = []
    mkdir_clients = []
    retries = []

    def _fake_upload(client, *_args, **_kwargs):
        upload_clients.append(client.name)
        if client is first:
            raise mod.EdgeSshTransferInterrupted("Server connection dropped")

    monkeypatch.setattr(mod, "_upload_edge_bundle_verified", _fake_upload)
    monkeypatch.setattr(mod, "ssh_run", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(
        mod,
        "ssh_mkdir_p",
        lambda client, _path: mkdir_clients.append(client.name),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    result = mod._upload_edge_bundle_with_reconnect(
        first,
        bundle,
        "/data/labelu_edge/jobs/job/bundle.tar.gz",
        reconnect=lambda: second,
        retry_callback=lambda next_attempt, cap, exc: retries.append(
            (next_attempt, cap, str(exc))
        ),
        attempts=3,
        retry_delay_seconds=0.01,
    )

    assert result is second
    assert first.closed is True
    assert upload_clients == ["first", "second"]
    assert mkdir_clients == ["first", "second"]
    assert retries == [(2, 3, "Server connection dropped")]


def test_upload_edge_bundle_with_reconnect_does_not_retry_permission_error(
    monkeypatch, tmp_path
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"payload")
    reconnect_calls = []
    monkeypatch.setattr(mod, "ssh_run", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(mod, "ssh_mkdir_p", lambda *_args: None)
    monkeypatch.setattr(
        mod,
        "_upload_edge_bundle_verified",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EdgeSshError("SFTP 续传权限不足")
        ),
    )

    with pytest.raises(EdgeSshError, match="权限不足"):
        mod._upload_edge_bundle_with_reconnect(
            object(),
            bundle,
            "/data/labelu_edge/jobs/job/bundle.tar.gz",
            reconnect=lambda: reconnect_calls.append(True),
            attempts=3,
            retry_delay_seconds=0,
        )

    assert reconnect_calls == []


def test_manual_execution_payload_keeps_upload_summary(monkeypatch, tmp_path):
    from labelu.internal.application.service import yolo_edge_validation as mod

    monkeypatch.setattr(mod, "_manual_output_root", lambda *_args: tmp_path)
    monkeypatch.setattr(mod, "_manual_report_root", lambda *_args: tmp_path)

    payload = mod._build_manual_execution_payload(
        job_id=39,
        export_id=68,
        summary={
            "status": "ok",
            "upload_bytes": 1_913_347_150,
            "upload_retry_count": 2,
            "execution_profile": "quick",
            "model_cache_hit": True,
            "output_transfer": "archive_lite",
            "stage_durations_seconds": {"upload": 12.5, "total": 90.0},
        },
    )

    assert payload["upload_bytes"] == 1_913_347_150
    assert payload["upload_retry_count"] == 2
    assert payload["execution_profile"] == "quick"
    assert payload["model_cache_hit"] is True
    assert payload["output_transfer"] == "archive_lite"
    assert payload["stage_durations_seconds"]["total"] == 90.0


def test_pull_remote_run_log_if_present_returns_false_on_ssh_error(
    monkeypatch, tmp_path
):
    def _boom(*args, **kwargs):
        raise EdgeSshError("boom")

    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.sftp_download",
        _boom,
    )
    assert (
        _pull_remote_run_log_if_present(
            object(),
            remote_job="/data/labelu_edge/jobs/export-22",
            export_root=tmp_path,
        )
        is False
    )


def test_edge_board_scripts_no_future_annotations():
    """边端容器 python3 常为 3.6，脚本不得使用 annotations future。"""
    import ast

    for path in (resolve_edge_board_validate_runner(), resolve_board_agent_script()):
        src = path.read_text(encoding="utf-8")
        assert not any(
            ln.strip() == "from __future__ import annotations"
            for ln in src.splitlines()
        ), path
        ast.parse(src, filename=str(path))


def test_edge_run_log_failure_hint_python_version(tmp_path):
    export_root = tmp_path / "exports" / "24"
    export_root.mkdir(parents=True)
    (export_root / "edge_ssh_remote_run.log").write_text(
        "SyntaxError: future feature annotations is not defined\n",
        encoding="utf-8",
    )
    hint = _edge_run_log_failure_hint(export_root)
    assert "Python" in hint or "python3" in hint
    assert "annotations" in hint


def test_read_json_object_file_empty(tmp_path):
    p = tmp_path / "board_report.json"
    p.write_text("", encoding="utf-8")
    data, warn = _read_json_object_file(p, label="board_report.json")
    assert data is None
    assert warn and "为空" in warn


def test_ingest_remote_results_bmodel_ok_with_skipped_board(tmp_path, monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    export_root = tmp_path / "exports" / "25"
    export_root.mkdir(parents=True)
    out = export_root / "edge_ssh_pull" / "output"
    out.mkdir(parents=True)
    results = out / "results_bmodel"
    results.mkdir(parents=True)
    (results / "eval_summary.json").write_text('{"map50": 0.85}', encoding="utf-8")
    (out / "board_report.json").write_text(
        '{"passed": false, "skipped": true, "status": "skipped"}',
        encoding="utf-8",
    )
    (out / "bmodel_sop_eval.json").write_text(
        '{"status": "ok", "metrics": {"map50": 0.85}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_export_dir_for_row", lambda _eid: export_root)
    monkeypatch.setattr(
        mod,
        "load_server_sop_eval_summary",
        lambda _root: {
            "status": "pending",
            "export_id": 25,
            "pt": {"metrics": {"map50": 0.87}},
        },
    )
    monkeypatch.setattr(mod, "enrich_sop_eval_summary", lambda sop, _root: sop)

    ingest = _ingest_remote_results(25, out)
    assert ingest.get("bmodel_sop", {}).get("status") == "ok"
    assert ingest.get("board_report", {}).get("skipped") is True
    assert ingest.get("sop_eval_summary", {}).get("bmodel", {}).get("status") == "ok"


def test_ingest_remote_results_fallback_from_eval_summary(tmp_path, monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    export_root = tmp_path / "exports" / "29"
    export_root.mkdir(parents=True)
    out = export_root / "edge_ssh_pull" / "output"
    out.mkdir(parents=True)
    results = out / "results_bmodel"
    results.mkdir(parents=True)
    (results / "eval_summary.json").write_text(
        json.dumps(
            {
                "kind": "bmodel",
                "map50": 0.0839,
                "precision": 1.0,
                "recall": 0.0138,
                "images": 12,
            }
        ),
        encoding="utf-8",
    )
    (out / "bmodel_sop_eval.json").write_text("", encoding="utf-8")

    monkeypatch.setattr(mod, "_export_dir_for_row", lambda _eid: export_root)
    monkeypatch.setattr(
        mod,
        "load_server_sop_eval_summary",
        lambda _root: {
            "status": "pending",
            "export_id": 29,
            "pt": {"metrics": {"map50": 0.0769, "precision": 1.0, "recall": 0.0069}},
        },
    )
    monkeypatch.setattr(mod, "enrich_sop_eval_summary", lambda sop, _root: sop)

    ingest = _ingest_remote_results(29, out)
    bm = ingest.get("bmodel_sop") or {}
    assert bm.get("status") == "ok"
    assert bm.get("metrics", {}).get("map50") == 0.0839
    comp = ingest.get("sop_eval_summary", {}).get("comparison_pt_bmodel") or {}
    assert comp.get("pt_map50") == 0.0769
    assert comp.get("bmodel_map50") == 0.0839


def test_ingest_remote_results_empty_board_report(tmp_path, monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    export_root = tmp_path / "exports" / "22"
    export_root.mkdir(parents=True)
    out = export_root / "edge_ssh_pull" / "output"
    out.mkdir(parents=True)
    (out / "board_report.json").write_text("", encoding="utf-8")

    monkeypatch.setattr(mod, "_export_dir_for_row", lambda _eid: export_root)
    monkeypatch.setattr(
        mod,
        "load_server_sop_eval_summary",
        lambda _root: {"status": "pending", "export_id": 22},
    )
    monkeypatch.setattr(mod, "enrich_sop_eval_summary", lambda sop, _root: sop)

    ingest = _ingest_remote_results(22, out)
    assert "board_report" not in ingest
    assert ingest.get("warnings")


def test_ingest_remote_results_bmcv_preview(tmp_path, monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    export_root = tmp_path / "exports" / "31"
    export_root.mkdir(parents=True)
    out = export_root / "edge_ssh_pull" / "output"
    out.mkdir(parents=True)
    preview = out / "bmcv_preview"
    preview.mkdir(parents=True)
    (preview / "a.jpg").write_bytes(b"jpeg")
    (out / "board_report.json").write_text(
        json.dumps({"passed": True, "metrics": {}}),
        encoding="utf-8",
    )
    (out / "bmcv_infer_summary.json").write_text(
        json.dumps(
            {
                "detection_summary": {
                    "image_count": 700,
                    "with_boxes": 120,
                    "total_boxes": 350,
                    "sample": {"image": "x.jpg", "boxes": 2},
                },
                "preview": {"viz_image_count": 700, "preview_files": ["a.jpg"]},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_export_dir_for_row", lambda _eid: export_root)
    monkeypatch.setattr(
        mod,
        "load_server_sop_eval_summary",
        lambda _root: {"status": "pending", "export_id": 31},
    )
    monkeypatch.setattr(mod, "enrich_sop_eval_summary", lambda sop, _root: sop)

    ingest = _ingest_remote_results(31, out)
    assert "bmcv_preview" in ingest.get("ingested", [])
    br = ingest.get("board_report") or {}
    assert (
        br.get("bmcv_infer", {}).get("detection_summary", {}).get("image_count") == 700
    )
    m = br.get("metrics", {}).get("bmcv_detection", {})
    assert m.get("total_boxes") == 350
    assert (export_root / "server_eval" / "bmcv_preview" / "a.jpg").is_file()


def test_copy_eval_dataset_from_test_split(tmp_path, monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "MEDIA_ROOT", str(tmp_path / "media"))
    job_id = 99
    base = (
        Path(config.settings.MEDIA_ROOT)
        / config.settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "dataset"
    )
    job_root = base.parent
    if job_root.exists():
        shutil.rmtree(job_root)
    img = base / "images" / "test"
    xml = base / "labelsxml" / "test"
    img.mkdir(parents=True, exist_ok=True)
    xml.mkdir(parents=True, exist_ok=True)
    (img / "a.jpg").write_bytes(b"x")
    (xml / "a.xml").write_text("<annotation/>", encoding="utf-8")

    dest = tmp_path / "eval_data"
    n = _copy_eval_dataset(job_id, dest)
    assert n == 1
    copied = list((dest / "images" / "test").iterdir())
    assert len(copied) == 1
    assert copied[0].name == "0000_a.jpg"
    assert (dest / "labelsxml" / "test" / "0000_a.xml").is_file()


def test_copy_eval_dataset_sanitizes_image_and_xml_names(tmp_path, monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "MEDIA_ROOT", str(tmp_path / "media"))
    job_id = 100
    base = (
        Path(config.settings.MEDIA_ROOT)
        / config.settings.YOLO_TRAIN_WORK_SUBDIR
        / f"job-{job_id}"
        / "dataset"
    )
    job_root = base.parent
    if job_root.exists():
        shutil.rmtree(job_root)
    img = base / "images" / "test"
    xml = base / "labelsxml" / "test"
    img.mkdir(parents=True, exist_ok=True)
    xml.mkdir(parents=True, exist_ok=True)
    original_name = "16382_中文 空格-2026-05-22T112441.850.png"
    (img / original_name).write_bytes(b"x")
    (xml / "16382_中文 空格-2026-05-22T112441.850.xml").write_text(
        "<annotation/>", encoding="utf-8"
    )

    dest = tmp_path / "eval_data"
    n = _copy_eval_dataset(job_id, dest)
    assert n == 1
    assert not (dest / "images" / "test" / original_name).exists()
    assert (
        dest / "images" / "test" / "0000_16382__-2026-05-22T112441.850.png"
    ).is_file()
    assert (
        dest / "labelsxml" / "test" / "0000_16382__-2026-05-22T112441.850.xml"
    ).is_file()


def test_edge_run_log_failure_hint_tar_eof(tmp_path):
    export_root = tmp_path / "exports" / "35"
    export_root.mkdir(parents=True)
    (export_root / "edge_ssh_remote_run.log").write_text(
        "gzip: stdin: unexpected end of file\n" "tar: Unexpected EOF in archive\n",
        encoding="utf-8",
    )
    hint = _edge_run_log_failure_hint(export_root)
    assert "bundle.tar.gz" in hint
    assert "重复触发" in hint or "并发" in hint


def test_run_edge_ssh_validation_sync_busy_when_locked(monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    lock = mod._export_edge_ssh_lock(35)
    lock.acquire()
    try:
        monkeypatch.setattr(
            mod.edge_cfg_io,
            "is_edge_ssh_configured",
            lambda _cfg=None: True,
        )
        monkeypatch.setattr(mod.edge_cfg_io, "load_edge_ssh_config", lambda: {})
        out = mod.run_edge_ssh_validation_sync(
            db=None,
            export_id=35,
            yolo_train_job_id=1,
            imgsz=640,
        )
        assert out.get("status") == "busy"
        out_skip = mod.run_edge_ssh_validation_sync(
            db=None,
            export_id=35,
            yolo_train_job_id=1,
            imgsz=640,
            skip_if_busy=True,
        )
        assert out_skip.get("status") == "skipped"
    finally:
        lock.release()


def test_run_edge_ssh_catchup_sync_persists_probe_failure(monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    saved = {}
    monkeypatch.setattr(mod.edge_cfg_io, "should_auto_run_edge_ssh", lambda: True)

    def _fake_probe(*_args, **_kwargs):
        payload = {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-pass",
            "auto_after_export": True,
        }
        payload.update(
            {
                "probe_status": "fail",
                "probe_stage": "auth",
                "probe_code": "auth_failed",
                "probe_message": "SSH auth failed",
                "probe_checked_at": "2026-07-06T00:00:00+00:00",
            }
        )
        saved.update(payload)
        return {
            "status": "fail",
            "stage": "auth",
            "code": "auth_failed",
            "message": "SSH auth failed",
            "resolved_host": "10.10.11.26",
            "resolved_user": "linaro",
            "remote_base": "/data/labelu_edge/jobs",
            "password_source": "mysql:encrypted",
            "checked_at": "2026-07-06T00:00:00+00:00",
        }

    monkeypatch.setattr(mod, "probe_and_persist_edge_ssh_target", _fake_probe)
    monkeypatch.setattr(
        mod.edge_cfg_io,
        "load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-pass",
            "auto_after_export": True,
        },
    )

    out = mod.run_edge_ssh_catchup_sync()
    assert out["status"] == "skipped"
    assert out["reason"] == "edge_ssh_probe_failed:auth_failed:auth"
    assert saved["probe_status"] == "fail"
    assert saved["probe_code"] == "auth_failed"


def test_run_edge_ssh_validation_sync_persists_probe_failure(monkeypatch):
    from labelu.internal.application.service import edge_ssh_validation as mod

    saved = {}
    monkeypatch.setattr(
        mod.edge_cfg_io, "is_edge_ssh_configured", lambda _cfg=None: True
    )
    monkeypatch.setattr(
        mod.edge_cfg_io,
        "load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-pass",
            "auto_after_export": True,
        },
    )
    monkeypatch.setattr(
        mod.edge_cfg_io,
        "save_edge_ssh_config",
        lambda payload: saved.update(payload),
    )

    def _fake_probe(*_args, **_kwargs):
        payload = {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-pass",
            "auto_after_export": True,
        }
        payload.update(
            {
                "probe_status": "fail",
                "probe_stage": "auth",
                "probe_code": "auth_failed",
                "probe_message": "SSH auth failed",
                "probe_checked_at": "2026-07-06T00:00:00+00:00",
            }
        )
        saved.update(payload)
        return {
            "status": "fail",
            "stage": "auth",
            "code": "auth_failed",
            "message": "SSH auth failed",
            "resolved_host": "10.10.11.26",
            "resolved_user": "linaro",
            "remote_base": "/data/labelu_edge/jobs",
            "password_source": "mysql:encrypted",
            "checked_at": "2026-07-06T00:00:00+00:00",
        }

    monkeypatch.setattr(mod, "probe_and_persist_edge_ssh_target", _fake_probe)

    out = mod.run_edge_ssh_validation_sync(
        db=None,
        export_id=88,
        yolo_train_job_id=1,
        imgsz=640,
    )
    assert out["status"] == "skipped"
    assert out["reason"] == "edge_ssh_probe_failed:auth_failed:auth"
    assert saved["probe_status"] == "fail"
    assert saved["probe_code"] == "auth_failed"


def test_run_edge_ssh_validation_sync_records_active_device_usage_and_validation(
    monkeypatch,
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    events = []
    monkeypatch.setattr(
        mod.edge_cfg_io, "is_edge_ssh_configured", lambda _cfg=None: True
    )
    monkeypatch.setattr(
        mod.edge_cfg_io,
        "load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "port": 22,
            "user": "linaro",
            "password": "secret-pass",
            "auto_after_export": True,
        },
    )
    monkeypatch.setattr(
        mod,
        "probe_and_persist_edge_ssh_target",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "stage": "remote_base",
            "code": "ok",
            "message": "ready",
        },
    )
    monkeypatch.setattr(mod, "_mark_active_device_used", lambda: events.append("used"))
    monkeypatch.setattr(
        mod,
        "_record_active_device_validation_status",
        lambda status_value: events.append(status_value),
    )
    monkeypatch.setattr(
        mod,
        "_run_edge_ssh_validation_locked",
        lambda *args, **kwargs: {"status": "ok", "export_id": kwargs.get("export_id")},
    )

    out = mod.run_edge_ssh_validation_sync(
        db=None,
        export_id=99,
        yolo_train_job_id=1,
        imgsz=640,
    )
    assert out["status"] == "ok"
    assert events == ["used", "ok"]


def test_export_with_review_comparison_does_not_repeat_edge_validation(
    tmp_path, monkeypatch
):
    from labelu.internal.application.service import edge_ssh_validation as mod

    export_root = tmp_path / "export-8"
    summary_dir = export_root / "server_eval"
    summary_dir.mkdir(parents=True)
    (summary_dir / "sop_eval_summary.json").write_text(
        json.dumps(
            {
                "comparison_pt_bmodel": {
                    "status": "warn",
                    "verdict": "review",
                    "drop_map50": 0.015,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_export_dir_for_row", lambda export_id: export_root)

    assert mod._export_needs_edge_ssh_validation(8) is False
