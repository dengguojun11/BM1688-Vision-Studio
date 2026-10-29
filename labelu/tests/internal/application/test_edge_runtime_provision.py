import base64
from pathlib import Path

import pytest

from labelu.internal.application.service import edge_runtime_provision as mod


def _report(*, ready=False, fatal=None, repairable=None, free_mb=4096):
    return {
        "environment_ready": ready,
        "fatal_reasons": list(fatal or []),
        "repairable_reasons": list(repairable or []),
        "provisionable": bool(repairable) and not fatal,
        "free_mb": free_mb,
        "sophon_demo_root": mod.DEFAULT_DEMO_ROOT if ready else None,
    }


def test_parse_probe_output_classifies_repairable_runtime_components():
    report = mod._parse_probe_output(
        "\n".join(
            (
                "architecture=aarch64",
                "architecture_ok=true",
                "bm1688_device_ok=true",
                "libsophon_ok=true",
                "docker_ok=true",
                "docker_image_ok=false",
                "container_ok=false",
                "python_runtime_ok=false",
                "sophon_demo_ok=false",
                "validator_ok=false",
                "noninteractive_root_ok=true",
                "free_mb=8192",
            )
        )
    )

    assert report["environment_ready"] is False
    assert report["fatal_reasons"] == []
    assert report["provisionable"] is True
    assert report["repairable_reasons"] == [
        "docker_image_missing",
        "container_not_ready",
        "python_runtime_not_ready",
        "sophon_demo_missing",
        "validator_missing",
    ]


def test_parse_probe_output_blocks_nonrepairable_hardware_environment():
    report = mod._parse_probe_output(
        "architecture=x86_64\narchitecture_ok=false\n"
        "bm1688_device_ok=false\nlibsophon_ok=false\ndocker_ok=true\n"
        "noninteractive_root_ok=true\n"
    )

    assert report["provisionable"] is False
    assert report["fatal_reasons"] == [
        "architecture_not_aarch64",
        "bm1688_device_node_missing",
        "libsophon_missing",
    ]


def test_parse_probe_output_blocks_full_tmp_and_docker_filesystems(monkeypatch):
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_FREE_MB", 2048)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_TMP_FREE_MB", 256)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_DOCKER_FREE_MB", 512)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_FREE_INODES", 1024)

    report = mod._parse_probe_output(
        "\n".join(
            (
                "architecture=aarch64",
                "architecture_ok=true",
                "bm1688_device_ok=true",
                "libsophon_ok=true",
                "docker_ok=true",
                "docker_image_ok=true",
                "container_ok=true",
                "python_runtime_ok=true",
                "sophon_demo_ok=true",
                "validator_ok=true",
                "noninteractive_root_ok=true",
                "free_mb=8192",
                "free_inodes=20000",
                "tmp_free_mb=0",
                "tmp_free_inodes=0",
                "docker_root=/var/lib/docker",
                "docker_free_mb=64",
                "docker_free_inodes=100",
            )
        )
    )

    assert report["environment_ready"] is False
    assert report["provisionable"] is False
    assert report["fatal_reasons"] == [
        "tmp_disk_space_insufficient",
        "tmp_inodes_insufficient",
        "docker_disk_space_insufficient",
        "docker_inodes_insufficient",
    ]


def test_parse_probe_output_checks_data_filesystem_independently(monkeypatch):
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_DATA_FREE_MB", 2048)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_FREE_INODES", 1024)

    report = mod._parse_probe_output(
        "\n".join(
            (
                "architecture=aarch64",
                "architecture_ok=true",
                "bm1688_device_ok=true",
                "libsophon_ok=true",
                "docker_ok=true",
                "docker_image_ok=true",
                "container_ok=true",
                "python_runtime_ok=true",
                "sophon_demo_ok=true",
                "validator_ok=true",
                "noninteractive_root_ok=true",
                "free_mb=8192",
                "free_inodes=20000",
                "data_free_mb=128",
                "data_free_inodes=100",
            )
        )
    )

    assert report["environment_ready"] is False
    assert report["fatal_reasons"] == [
        "data_disk_space_insufficient",
        "data_inodes_insufficient",
    ]
    assert mod._storage_cleanup_paths(
        report, remote_root="/data/labelu_edge/runtime"
    ) == ["/data/labelu_edge/logs", "/data/logs"]
    with pytest.raises(ValueError, match="escapes edge root"):
        mod._storage_cleanup_paths(report, remote_root="/opt/runtime")


def test_resolve_offline_archive_verifies_configured_sha256(tmp_path, monkeypatch):
    archive = tmp_path / "runtime.tar"
    archive.write_bytes(b"offline-runtime")
    digest = mod._sha256(archive)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_OFFLINE_ARCHIVE", str(archive))
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_OFFLINE_ARCHIVE_URL", "")
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_OFFLINE_SHA256", digest)
    monkeypatch.setattr(mod, "_cache_dir", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir()

    resolved, actual = mod.resolve_offline_archive()

    assert resolved == archive.resolve()
    assert actual == digest


def test_resolve_offline_archive_ignores_other_toolkit_archives(tmp_path, monkeypatch):
    runtime = tmp_path / "bm1688-edge-runtime-offline-v2.tar"
    runtime.write_bytes(b"runtime")
    (tmp_path / "bmodel-validation-job-toolkit.tar.gz").write_bytes(b"toolkit")
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_OFFLINE_ARCHIVE", "")
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_OFFLINE_ARCHIVE_URL", "")
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_OFFLINE_SHA256", "")
    monkeypatch.setattr(mod, "_cache_dir", lambda: tmp_path)

    resolved, actual = mod.resolve_offline_archive()

    assert resolved == runtime.resolve()
    assert actual == mod._sha256(runtime)


def test_ensure_edge_runtime_skips_archive_when_environment_is_ready(monkeypatch):
    ready = _report(ready=True)
    monkeypatch.setattr(
        mod,
        "prepare_edge_root_and_cleanup_legacy",
        lambda _client: {"status": "completed", "removed": []},
    )
    monkeypatch.setattr(mod, "probe_edge_runtime", lambda *_args, **_kwargs: ready)
    monkeypatch.setattr(
        mod,
        "resolve_offline_archive",
        lambda: pytest.fail("ready environment must not resolve or upload the archive"),
    )
    client = object()

    returned, result = mod.ensure_edge_runtime(
        client,
        configured_demo_root="",
        docker_container="bm1688",
        reconnect=lambda: object(),
    )

    assert returned is client
    assert result["environment_ready"] is True
    assert result["provisioned"] is False


def test_ensure_edge_runtime_does_not_install_fatal_environment(monkeypatch):
    fatal = _report(fatal=["bm1688_device_node_missing"])
    monkeypatch.setattr(
        mod,
        "prepare_edge_root_and_cleanup_legacy",
        lambda _client: {"status": "completed", "removed": []},
    )
    monkeypatch.setattr(mod, "probe_edge_runtime", lambda *_args, **_kwargs: fatal)
    monkeypatch.setattr(
        mod,
        "resolve_offline_archive",
        lambda: pytest.fail("fatal environment must not resolve or upload the archive"),
    )

    with pytest.raises(
        mod.EdgeRuntimeProvisionError, match="bm1688_device_node_missing"
    ):
        mod.ensure_edge_runtime(
            object(),
            configured_demo_root="",
            docker_container="bm1688",
            reconnect=lambda: object(),
        )


def test_cleanup_edge_storage_uses_log_allowlist_on_pressured_filesystem(monkeypatch):
    commands = []

    def fake_ssh_run(_client, command, timeout):
        commands.append((command, timeout))
        return (
            0,
            "\n".join(
                (
                    "LABELU_EDGE_STORAGE_CLEANUP_V1=1",
                    "action=expired_log_files_older_than_7d:/data/logs",
                    "log_files_freed_kb=4096",
                )
            ),
            "",
        )

    monkeypatch.setattr(mod.edge_ssh_validation, "ssh_run", fake_ssh_run)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_AUTO_LOG_CLEANUP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_LOG_RETENTION_DAYS", 7)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_JOURNAL_MAX_MB", 256)

    result = mod.cleanup_edge_storage(
        object(),
        {
            "fatal_reasons": ["tmp_disk_space_insufficient"],
            "runtime_path": "/data/labelu_edge/runtime",
        },
        remote_root="/data/labelu_edge/runtime",
    )

    assert result["status"] == "completed"
    assert result["actions"] == ["expired_log_files_older_than_7d:/data/logs"]
    assert result["log_files_freed_kb"] == 4096
    assert len(commands) == 1
    encoded_command = commands[0][0]
    assert "base64 -d" in encoded_command
    encoded = encoded_command.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0]
    cleanup_script = base64.b64decode(encoded.strip("'")).decode("utf-8")
    assert "/data/labelu_edge/logs" in cleanup_script
    assert "/data/logs" in cleanup_script
    assert "/var/log" not in cleanup_script
    assert 'find "$log_root" -xdev -type f -mtime +7' in cleanup_script
    assert "rm -rf" not in cleanup_script
    assert "/tmp/upload" not in cleanup_script
    assert "/data/docker" not in cleanup_script


def test_cleanup_edge_historical_jobs_only_removes_allowlisted_job_dirs(monkeypatch):
    commands = []

    def fake_ssh_run(_client, command, timeout):
        commands.append((command, timeout))
        return (
            0,
            "removed=manual-job-12\nremoved=export-8\nhistorical_jobs_freed_kb=8192",
            "",
        )

    monkeypatch.setattr(mod.edge_ssh_validation, "ssh_run", fake_ssh_run)
    result = mod.cleanup_edge_historical_jobs(
        object(),
        {"fatal_reasons": ["data_disk_space_insufficient"]},
        remote_root="/data/labelu_edge/runtime",
    )

    assert result["status"] == "completed"
    assert result["removed"] == ["manual-job-12", "export-8"]
    assert result["historical_jobs_freed_kb"] == 8192
    encoded_command = commands[0][0]
    encoded = encoded_command.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0]
    cleanup_script = base64.b64decode(encoded.strip("'")).decode("utf-8")
    assert "cd /data/labelu_edge/jobs" in cleanup_script
    assert "manual-job-[0-9]*|export-[0-9]*" in cleanup_script
    assert 'rm -rf -- "$name"' in cleanup_script
    assert "/data/logs" not in cleanup_script
    assert "/data/labelu_edge/runtime" not in cleanup_script


def test_cleanup_legacy_edge_installations_uses_exact_allowlist(monkeypatch):
    commands = []

    def fake_ssh_run(_client, command, timeout):
        commands.append((command, timeout))
        return (
            0,
            "removed=/data/labelu_edge_runtime\nremoved=/data/labelu_board_agent.py",
            "",
        )

    monkeypatch.setattr(mod.edge_ssh_validation, "ssh_run", fake_ssh_run)

    result = mod.cleanup_legacy_edge_installations(object())

    assert result["status"] == "completed"
    assert result["removed"] == [
        "/data/labelu_edge_runtime",
        "/data/labelu_board_agent.py",
    ]
    assert commands[0][1] == 600
    encoded_command = commands[0][0]
    encoded = encoded_command.split("printf %s ", 1)[1].split(" | base64 -d", 1)[0]
    cleanup_script = base64.b64decode(encoded.strip("'")).decode("utf-8")
    for legacy_path in mod._LEGACY_EDGE_PATHS:
        assert legacy_path in cleanup_script
    assert "rm -rf -- \"$legacy_path\"" in cleanup_script
    assert "/data/docker" not in cleanup_script
    assert "/data/db" not in cleanup_script
    assert "/data/logs" not in cleanup_script
    assert "/data/models" not in cleanup_script


def test_prepare_edge_root_before_legacy_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mod,
        "_prepare_remote_runtime_root",
        lambda _client, path: calls.append(("prepare", path)),
    )
    monkeypatch.setattr(
        mod,
        "cleanup_legacy_edge_installations",
        lambda _client: calls.append(("cleanup", None))
        or {"status": "completed", "removed": []},
    )

    result = mod.prepare_edge_root_and_cleanup_legacy(object())

    assert calls == [
        ("prepare", "/data/labelu_edge/runtime"),
        ("cleanup", None),
    ]
    assert result["status"] == "completed"


def test_ensure_edge_runtime_cleans_storage_and_rechecks_ready(monkeypatch):
    before = _report(fatal=["tmp_disk_space_insufficient"])
    before.update(tmp_free_mb=0, data_free_mb=12000)
    after = _report(ready=True)
    after.update(tmp_free_mb=1024, data_free_mb=12000)
    reports = iter((before, after))
    cleanup = {"status": "completed", "actions": ["journal_vacuum_256mb"]}
    monkeypatch.setattr(
        mod, "probe_edge_runtime", lambda *_args, **_kwargs: next(reports)
    )
    monkeypatch.setattr(
        mod,
        "prepare_edge_root_and_cleanup_legacy",
        lambda _client: {"status": "completed", "removed": []},
    )
    monkeypatch.setattr(mod, "cleanup_edge_storage", lambda *_args, **_kwargs: cleanup)

    _, result = mod.ensure_edge_runtime(
        object(),
        configured_demo_root="",
        docker_container="bm1688",
        reconnect=lambda: object(),
    )

    assert result["environment_ready"] is True
    assert result["storage_cleanup"]["freed_mb"] == 1024
    assert result["after"]["manual_intervention_required"] is False


def test_ensure_edge_runtime_requires_manual_cleanup_when_recheck_still_full(
    monkeypatch,
):
    before = _report(fatal=["tmp_disk_space_insufficient"])
    before.update(tmp_free_mb=0)
    after_logs = _report(fatal=["tmp_disk_space_insufficient"])
    after_logs.update(tmp_free_mb=20)
    after_jobs = _report(fatal=["tmp_disk_space_insufficient"])
    after_jobs.update(tmp_free_mb=30)
    reports = iter((before, after_logs, after_jobs))
    monkeypatch.setattr(
        mod, "probe_edge_runtime", lambda *_args, **_kwargs: next(reports)
    )
    monkeypatch.setattr(
        mod,
        "prepare_edge_root_and_cleanup_legacy",
        lambda _client: {"status": "completed", "removed": []},
    )
    monkeypatch.setattr(
        mod,
        "cleanup_edge_storage",
        lambda *_args, **_kwargs: {"status": "completed", "actions": []},
    )
    monkeypatch.setattr(
        mod,
        "cleanup_edge_historical_jobs",
        lambda *_args, **_kwargs: {"status": "completed", "removed": []},
    )

    with pytest.raises(mod.EdgeRuntimeProvisionError) as exc_info:
        mod.ensure_edge_runtime(
            object(),
            configured_demo_root="",
            docker_container="bm1688",
            reconnect=lambda: object(),
        )

    assert exc_info.value.code == "manual_cleanup_required"
    assert exc_info.value.report["manual_intervention_required"] is True
    assert exc_info.value.result["storage_cleanup"]["remaining_reasons"] == [
        "tmp_disk_space_insufficient"
    ]
    assert exc_info.value.result["historical_job_cleanup"][
        "remaining_reasons"
    ] == ["tmp_disk_space_insufficient"]


def test_ensure_edge_runtime_cleans_logs_before_historical_jobs(monkeypatch):
    before = _report(fatal=["data_disk_space_insufficient"], free_mb=128)
    after_logs = _report(fatal=["data_disk_space_insufficient"], free_mb=512)
    after_jobs = _report(ready=True, free_mb=4096)
    reports = iter((before, after_logs, after_jobs))
    calls = []
    monkeypatch.setattr(
        mod, "probe_edge_runtime", lambda *_args, **_kwargs: next(reports)
    )
    monkeypatch.setattr(
        mod,
        "prepare_edge_root_and_cleanup_legacy",
        lambda _client: {"status": "completed", "removed": []},
    )
    monkeypatch.setattr(
        mod,
        "cleanup_edge_storage",
        lambda *_args, **_kwargs: calls.append("logs")
        or {"status": "completed", "actions": ["logs"]},
    )
    monkeypatch.setattr(
        mod,
        "cleanup_edge_historical_jobs",
        lambda *_args, **_kwargs: calls.append("jobs")
        or {"status": "completed", "removed": ["manual-job-1"]},
    )

    _, result = mod.ensure_edge_runtime(
        object(),
        configured_demo_root="",
        docker_container="bm1688",
        reconnect=lambda: object(),
    )

    assert calls == ["logs", "jobs"]
    assert result["environment_ready"] is True
    assert result["storage_cleanup"]["freed_mb"] == 384
    assert result["historical_job_cleanup"]["freed_mb"] == 3584
    assert result["after"]["manual_intervention_required"] is False


def test_ensure_edge_runtime_uploads_installs_and_rechecks(tmp_path, monkeypatch):
    archive = tmp_path / "runtime.tar"
    archive.write_bytes(b"runtime")
    before = _report(repairable=["sophon_demo_missing"])
    after = _report(ready=True)
    reports = iter((before, after))
    uploads = []
    commands = []
    client = object()

    monkeypatch.setattr(
        mod, "probe_edge_runtime", lambda *_args, **_kwargs: next(reports)
    )
    monkeypatch.setattr(
        mod,
        "prepare_edge_root_and_cleanup_legacy",
        lambda _client: {"status": "completed", "removed": []},
    )
    monkeypatch.setattr(mod, "resolve_offline_archive", lambda: (archive, "abc123"))
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_AUTO_PROVISION_ENABLED", True)
    monkeypatch.setattr(mod.settings, "EDGE_RUNTIME_MIN_FREE_MB", 512)
    monkeypatch.setattr(mod.settings, "EDGE_REMOTE_ROOT", "/data/labelu_edge")
    monkeypatch.setattr(
        mod.edge_ssh_validation,
        "_upload_edge_bundle_with_reconnect",
        lambda current, local, remote, **_kwargs: uploads.append((local, remote))
        or current,
    )

    def fake_ssh_run(_client, command, timeout):
        commands.append((command, timeout))
        return 0, "Environment installation completed.", ""

    monkeypatch.setattr(mod.edge_ssh_validation, "ssh_run", fake_ssh_run)

    returned, result = mod.ensure_edge_runtime(
        client,
        configured_demo_root="",
        docker_container="bm1688",
        reconnect=lambda: object(),
    )

    assert returned is client
    assert uploads == [
        (archive, "/data/labelu_edge/runtime/archives/abc123.tar")
    ]
    assert "sudo -n env" in commands[0][0]
    assert "LABELU_EDGE_ROOT=/data/labelu_edge" in commands[0][0]
    assert "LABELU_VALIDATOR_ROOT=/data/labelu_edge/runtime/validator" in commands[0][0]
    assert "TMPDIR=/data/labelu_edge/tmp" in commands[0][0]
    assert result["provisioned"] is True
    assert result["environment_ready"] is True
    assert result["sophon_demo_root"] == mod.DEFAULT_DEMO_ROOT
