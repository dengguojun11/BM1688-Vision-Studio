"""启动时 MEDIA_HOST 与 --host/--port 对齐。"""
from __future__ import annotations

from labelu.internal.common.config import settings
from labelu.internal.common.server_runtime import (
    apply_listen_settings,
    resolve_media_host,
    sync_dotenv_media_host,
)


def test_resolve_media_host_from_listen():
    assert (
        resolve_media_host("127.0.0.1", 8002, None) == "http://127.0.0.1:8002"
    )


def test_apply_listen_settings_overrides_env(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_HOST", "http://127.0.0.1:8001")
    media = apply_listen_settings("127.0.0.1", 8002, None)
    assert media == "http://127.0.0.1:8002"
    assert settings.MEDIA_HOST == "http://127.0.0.1:8002"
    assert settings.PORT == "8002"


def test_sync_dotenv_media_host_updates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "MEDIA_HOST_AUTO_SYNC", True)
    (tmp_path / ".env").write_text("MEDIA_HOST=http://127.0.0.1:8001\n", encoding="utf-8")
    sync_dotenv_media_host("http://127.0.0.1:8002")
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MEDIA_HOST=http://127.0.0.1:8002" in text
    assert "8001" not in text
