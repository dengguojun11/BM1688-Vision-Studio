"""BM1688 部署自动配置单元测试。"""
from pathlib import Path

from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common import bm1688_deploy_bootstrap as bootstrap
from labelu.internal.common.bm1688_deploy_bootstrap import (
    ensure_bm1688_compiler_ready,
    ensure_bm1688_docker_deploy,
)


def test_ensure_fills_tpu_from_sibling_dir(monkeypatch, tmp_path):
    base = tmp_path / "base"
    media = base / "labeIuMedia"
    appdata = base / "labelu_appdata"
    tpu = base / "tpu-mlir"
    media.mkdir(parents=True)
    appdata.mkdir()
    tpu.mkdir()
    (tpu / "envsetup.sh").write_text("# mock\n", encoding="utf-8")

    cfg_path = appdata / "bm1688_compiler_config.json"
    monkeypatch.setattr(cfg_io, "config_file_path", lambda: cfg_path)

    class _S:
        MEDIA_ROOT = media
        BASE_DATA_DIR = appdata
        TPU_MLIR_ROOT = ""
        SOPHON_DET_ROOT = ""
        YOLO_BM1688_CALIBRATION_DATASET_DIR = ""
        YOLO_BM1688_QTABLE_PATH = ""
        YOLO_BM1688_COMPILE_PLATFORMS = "bm1688"

    monkeypatch.setattr(
        "labelu.internal.common.bm1688_deploy_bootstrap.settings",
        _S(),
    )

    out = ensure_bm1688_compiler_ready(force_paths=True)
    assert out["tpu_mlir_root"] == str(tpu.resolve())
    assert out["auto_compile"] is True
    assert out["quantize"] == "full"


def test_ensure_bm1688_docker_deploy_skips_on_non_linux(monkeypatch):
    monkeypatch.setattr(
        cfg_io,
        "load_bm1688_compiler_config",
        lambda: {"quantize_backend": "docker"},
    )
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.setattr(
        "labelu.internal.common.yolo_bm1688_docker.docker_cli_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "labelu.internal.common.yolo_bm1688_docker.ensure_bm1688_docker_ready",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not bootstrap")),
    )

    ok, note = ensure_bm1688_docker_deploy()

    assert ok is True
    assert "non-linux host" in note.lower()
    assert "跳过" in note
