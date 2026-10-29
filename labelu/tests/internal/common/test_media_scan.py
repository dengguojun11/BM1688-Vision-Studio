from pathlib import Path
from types import SimpleNamespace

from labelu.internal.common.config import settings
from labelu.internal.common.media_scan import scan_media_path


def test_media_scan_disabled_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", False)
    result = scan_media_path(tmp_path / "sample.jpg")
    assert result.status == "NOT_CONFIGURED"
    assert result.provider == "disabled"


def test_media_scan_maps_provider_exit_codes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_COMMAND", "scanner --path {path}")

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("labelu.internal.common.media_scan.subprocess.run", fake_run)
    clean = scan_media_path(Path("C:/tmp/sample.jpg"))
    assert clean.status == "CLEAN"
    assert calls[0][1]["shell"] is False

    monkeypatch.setattr(
        "labelu.internal.common.media_scan.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="infected"),
    )
    infected = scan_media_path(Path("C:/tmp/sample.jpg"))
    assert infected.status == "INFECTED"


def test_media_scan_missing_command_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_COMMAND", "")
    result = scan_media_path(tmp_path / "sample.jpg")
    assert result.status == "ERROR"
    assert "未配置" in (result.error or "")
