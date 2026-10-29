"""CLI / systemd 路径：生产 .env 应在 Settings 加载前被发现。"""
from pathlib import Path

import labelu.internal.common.config as config_mod


def test_bootstrap_labelu_env_file_uses_explicit(monkeypatch, tmp_path):
    env = tmp_path / "prod.env"
    env.write_text("BASE_DATA_DIR=/tmp/labelu-test\n", encoding="utf-8")
    monkeypatch.setenv("LABELU_ENV_FILE", str(env))
    found = config_mod.bootstrap_labelu_env_file()
    assert found == env.resolve()


def test_candidate_deploy_env_includes_venv_parent(tmp_path, monkeypatch):
    venv = tmp_path / "labeIuMedia" / "venv"
    venv.mkdir(parents=True)
    dotenv = tmp_path / "labeIuMedia" / ".env"
    dotenv.write_text("BASE_DATA_DIR=/data/x\n", encoding="utf-8")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.delenv("LABELU_ENV_FILE", raising=False)
    paths = config_mod._candidate_deploy_env_files()
    assert dotenv in paths


def test_settings_accepts_la_max_image_side(monkeypatch, tmp_path):
    env = tmp_path / "prod.env"
    env.write_text(
        "BASE_DATA_DIR=/tmp/labelu-test\n"
        "LA_MAX_IMAGE_SIDE=1536\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LABELU_ENV_FILE", str(env))

    settings = config_mod.Settings()

    assert settings.LA_MAX_IMAGE_SIDE == 1536


def test_explicit_env_file_overrides_repository_env(monkeypatch, tmp_path):
    repository_env = tmp_path / ".env"
    repository_env.write_text(
        "DATABASE_URL=mysql+pymysql://localhost/shared\n", encoding="utf-8"
    )
    selected_env = tmp_path / "selected.env"
    selected_env.write_text(
        "DATABASE_URL=sqlite:///./.tmp/selected.sqlite\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LABELU_ENV_FILE", str(selected_env))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    files = config_mod._settings_env_files()
    assert files == (str(repository_env.resolve()), str(selected_env.resolve()))
    settings = config_mod.Settings(_env_file=files)

    assert settings.DATABASE_URL.startswith("sqlite:///")
