from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

from labelu.internal.application.service import media_upload as service
from labelu.internal.common.config import settings
from labelu.internal.common.media_scan import MediaScanResult, scan_media_path
from labelu.internal.domain.models.media_upload import MediaUploadSession


def test_media_scan_is_compatible_when_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", False)

    def fail_run(*args, **kwargs):
        raise AssertionError("disabled scanner must not execute a process")

    monkeypatch.setattr(subprocess, "run", fail_run)
    result = scan_media_path(tmp_path / "sample.bin")

    assert result.status == "NOT_CONFIGURED"
    assert result.provider == "disabled"


def test_media_scan_parses_infected_signature(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_COMMAND", "clamscan --no-summary {path}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout=f"{tmp_path / 'sample.bin'}: Eicar-Test-Signature FOUND\n", stderr=""
        ),
    )

    result = scan_media_path(tmp_path / "sample.bin")

    assert result.status == "INFECTED"
    assert result.signature == "Eicar-Test-Signature"


def test_infected_upload_is_quarantined_and_cleanup_removes_file(
    client, testuser_token_headers, db, monkeypatch
):
    task_response = client.post(
        "/api/v1/tasks",
        headers=testuser_token_headers,
        json={"name": "病毒扫描任务", "description": "", "tips": ""},
    )
    assert task_response.status_code in (200, 201), task_response.text
    task_id = task_response.json()["data"]["id"]
    monkeypatch.setattr(
        service,
        "scan_media_path",
        lambda path: MediaScanResult(
            "INFECTED",
            "clamav",
            "扫描器报告文件感染",
            datetime.now(timezone.utc),
            "Eicar-Test-Signature",
        ),
    )
    created = client.post(
        "/api/v1/data-governance/upload-sessions",
        headers=testuser_token_headers,
        json={
            "task_id": task_id,
            "filename": "sample.bin",
            "total_size": 256 * 1024,
            "chunk_size": 256 * 1024,
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["data"]["session_id"]
    uploaded = client.put(
        f"/api/v1/data-governance/upload-sessions/{session_id}/chunks/0",
        headers=testuser_token_headers,
        files={"file": ("chunk", b"x" * (256 * 1024), "application/octet-stream")},
    )
    assert uploaded.status_code == 200, uploaded.text

    completed = client.post(
        f"/api/v1/data-governance/upload-sessions/{session_id}/complete",
        headers=testuser_token_headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "QUARANTINED"
    assert completed.json()["data"]["scan_signature"] == "Eicar-Test-Signature"

    db.expire_all()
    row = db.query(MediaUploadSession).filter_by(session_id=session_id).one()
    quarantine = Path(row.quarantine_path)
    assert quarantine.is_file()
    assert service.cleanup_expired_quarantine(
        db, now=row.quarantine_until + timedelta(seconds=1)
    ) == 1
    db.refresh(row)
    assert not quarantine.exists()
    assert row.quarantine_path is None
    assert row.quarantine_until is None


def test_optional_scan_error_does_not_block_upload(
    client, testuser_token_headers, monkeypatch
):
    task_response = client.post(
        "/api/v1/tasks",
        headers=testuser_token_headers,
        json={"name": "兼容扫描任务", "description": "", "tips": ""},
    )
    assert task_response.status_code in (200, 201), task_response.text
    task_id = task_response.json()["data"]["id"]
    monkeypatch.setattr(
        service,
        "scan_media_path",
        lambda path: MediaScanResult(
            "ERROR", "clamav", "ClamAV 扫描器不可用", datetime.now(timezone.utc)
        ),
    )
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_REQUIRED", False)
    created = client.post(
        "/api/v1/data-governance/upload-sessions",
        headers=testuser_token_headers,
        json={
            "task_id": task_id,
            "filename": "sample.bin",
            "total_size": 256 * 1024,
            "chunk_size": 256 * 1024,
        },
    )
    session_id = created.json()["data"]["session_id"]
    client.put(
        f"/api/v1/data-governance/upload-sessions/{session_id}/chunks/0",
        headers=testuser_token_headers,
        files={"file": ("chunk", b"y" * (256 * 1024), "application/octet-stream")},
    )
    completed = client.post(
        f"/api/v1/data-governance/upload-sessions/{session_id}/complete",
        headers=testuser_token_headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "COMPLETED"
    assert completed.json()["data"]["scan_status"] == "ERROR"
