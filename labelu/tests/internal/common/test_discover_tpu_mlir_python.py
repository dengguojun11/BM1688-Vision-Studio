"""discover_tpu_mlir_python 应识别 venv 内 python3 / python3.10。"""
from pathlib import Path

from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.bm1688_deploy_bootstrap import (
    discover_tpu_mlir_python,
    ensure_bm1688_compiler_ready,
    normalize_tpu_mlir_python_config,
)


def test_discover_python3_10_in_venv_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "labeIuMedia" / "venv-tpu-mlir" / "bin"
    bindir.mkdir(parents=True)
    py = bindir / "python3.10"
    py.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    py.chmod(0o755)

    class _S:
        MEDIA_ROOT = tmp_path / "labeIuMedia"
        BASE_DATA_DIR = tmp_path / "labelu_appdata"
        TPU_MLIR_ROOT = ""
        SOPHON_DET_ROOT = ""
        YOLO_BM1688_CALIBRATION_DATASET_DIR = ""
        YOLO_BM1688_QTABLE_PATH = ""
        YOLO_BM1688_COMPILE_PLATFORMS = "bm1688"

    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.settings",
        _S(),
    )
    found = discover_tpu_mlir_python()
    assert found == str(py)
    assert not found.startswith("/usr/bin/")


def test_discover_keeps_venv_path_when_symlink_to_usr_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "labeIuMedia" / "venv-tpu-mlir" / "bin"
    bindir.mkdir(parents=True)
    fake_system_py = tmp_path / "fakeusr" / "python3.10"
    fake_system_py.parent.mkdir(parents=True)
    fake_system_py.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    fake_system_py.chmod(0o755)
    py3 = bindir / "python3"
    py3.symlink_to(fake_system_py)

    class _S:
        MEDIA_ROOT = tmp_path / "labeIuMedia"
        BASE_DATA_DIR = tmp_path / "labelu_appdata"
        TPU_MLIR_ROOT = ""
        SOPHON_DET_ROOT = ""
        YOLO_BM1688_CALIBRATION_DATASET_DIR = ""
        YOLO_BM1688_QTABLE_PATH = ""
        YOLO_BM1688_COMPILE_PLATFORMS = "bm1688"

    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.settings",
        _S(),
    )
    found = discover_tpu_mlir_python()
    assert found == str(py3)
    assert "venv-tpu-mlir" in found.replace("\\", "/") and found.replace("\\", "/").endswith(
        "/bin/python3"
    )


def test_normalize_replaces_usr_bin_python(tmp_path, monkeypatch):
    bindir = tmp_path / "labeIuMedia" / "venv-tpu-mlir" / "bin"
    bindir.mkdir(parents=True)
    py = bindir / "python3"
    py.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    py.chmod(0o755)

    class _S:
        MEDIA_ROOT = tmp_path / "labeIuMedia"
        BASE_DATA_DIR = tmp_path / "labelu_appdata"
        TPU_MLIR_ROOT = ""
        SOPHON_DET_ROOT = ""
        YOLO_BM1688_CALIBRATION_DATASET_DIR = ""
        YOLO_BM1688_QTABLE_PATH = ""
        YOLO_BM1688_COMPILE_PLATFORMS = "bm1688"

    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.settings",
        _S(),
    )
    cfg_path = tmp_path / "labelu_appdata" / "bm1688_compiler_config.json"
    monkeypatch.setattr(cfg_io, "config_file_path", lambda: cfg_path)
    cfg_io.save_bm1688_compiler_config(
        {**cfg_io.default_config(), "tpu_mlir_python": "/usr/bin/python3.10"}
    )
    out = ensure_bm1688_compiler_ready(force_paths=True)
    assert out["tpu_mlir_python"] == str(py.resolve())
    on_disk = cfg_io.load_bm1688_compiler_config()
    assert on_disk["tpu_mlir_python"] == str(py.resolve())
