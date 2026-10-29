"""图像流水线：.env 自动写入与依赖安装。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from labelu.internal.common.config import settings
from labelu.internal.common.image_pipeline.env_bootstrap import (
    apply_image_pipeline_env_bootstrap,
)
from labelu.internal.common.optional_dependencies import (
    ensure_image_pipeline_packages_installed,
)


def test_env_bootstrap_writes_enabled_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_AUTO_CONFIGURE_ENV", True)
    monkeypatch.delenv("IMAGE_PIPELINE_ENABLED", raising=False)

    assert apply_image_pipeline_env_bootstrap() is True
    assert settings.IMAGE_PIPELINE_ENABLED is True
    dotenv = tmp_path / ".env"
    assert dotenv.is_file()
    assert "IMAGE_PIPELINE_ENABLED=true" in dotenv.read_text(encoding="utf-8")


def test_env_bootstrap_respects_explicit_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("IMAGE_PIPELINE_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_AUTO_CONFIGURE_ENV", True)
    monkeypatch.setenv("IMAGE_PIPELINE_ENABLED", "false")

    assert apply_image_pipeline_env_bootstrap() is False


def test_auto_install_calls_pip_when_import_missing(monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_AUTO_INSTALL_DEPS", True)
    import labelu.internal.common.optional_dependencies as od

    monkeypatch.setattr(od, "_IMAGE_PIPELINE_AVAILABLE", None)
    calls = {"n": 0}

    def fake_try():
        calls["n"] += 1
        return calls["n"] > 1

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    monkeypatch.setattr(od, "_try_import_image_pipeline", fake_try)
    monkeypatch.setattr(od.subprocess, "run", lambda *a, **k: mock_proc)

    assert ensure_image_pipeline_packages_installed(reason="test") is True
    assert calls["n"] >= 2
