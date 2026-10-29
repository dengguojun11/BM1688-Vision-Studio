from labelu.internal.application.service import edge_remote_maintenance as mod


def test_cleanup_stale_remote_edge_jobs_scans_each_configured_device(monkeypatch):
    records = [
        {"id": "edge-1", "enabled": True},
        {"id": "edge-disabled", "enabled": False},
    ]
    closed = []
    cleanup_calls = []

    class _Client:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        mod.edge_device_registry, "list_device_records", lambda: records
    )
    monkeypatch.setattr(
        mod.edge_device_registry,
        "load_device_config",
        lambda _device_id: {
            "host": "192.168.54.104",
            "port": 22,
            "user": "linaro",
            "password": "secret",
            "remote_base": "/data/labelu_edge/jobs",
            "connect_timeout_sec": 15,
        },
    )
    monkeypatch.setattr(mod, "ssh_connect", lambda **_kwargs: _Client())
    monkeypatch.setattr(
        mod.edge_ssh_validation,
        "cleanup_stale_remote_job_dirs",
        lambda _client, **kwargs: cleanup_calls.append(kwargs)
        or {"status": "completed", "removed": ["manual-job-8"]},
    )
    monkeypatch.setattr(mod.settings, "EDGE_REMOTE_FAILED_JOB_RETENTION_HOURS", 24)

    result = mod.cleanup_stale_remote_edge_jobs()

    assert cleanup_calls == [
        {"remote_base": "/data/labelu_edge/jobs", "retention_hours": 24}
    ]
    assert result["removed_count"] == 1
    assert result["devices"][0]["device_id"] == "edge-1"
    assert closed == [True]
