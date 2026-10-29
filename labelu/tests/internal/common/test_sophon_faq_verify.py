"""sophon FAQ 验证逻辑单元测试。"""
from pathlib import Path

from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.sophon_faq_verify import (
    SophonVerifyReport,
    verify_export_tree,
)


def _build_valid_tree(root: Path) -> None:
    cfg = cfg_io.default_config()
    onnx_dir = cfg_io.sophon_onnx_dir(root)
    onnx_dir.mkdir(parents=True)
    (onnx_dir / cfg["onnx_filename"]).write_bytes(b"x" * 2048)
    (onnx_dir / cfg["qtable_filename"]).write_bytes(b"# qtable\n")
    for plat in ("BM1688",):
        pdir = root / "models" / plat
        pdir.mkdir(parents=True)
        for name in cfg_io.canonical_bmodel_names(cfg_io.DEFAULT_BMODEL_BASENAME):
            (pdir / name).write_bytes(b"b" * 5000)


def test_verify_export_tree_passes_with_full_layout(tmp_path):
    root = tmp_path / "export1"
    root.mkdir()
    _build_valid_tree(root)
    report = verify_export_tree(root, check_env=False)
    assert report.passed
    assert any(c.name.startswith("models/onnx/") and c.status == "pass" for c in report.checks)
    assert any("yolov11n_fp16_1b.bmodel" in c.name for c in report.checks)


def test_verify_export_tree_fails_without_models(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    report = verify_export_tree(root, check_env=False)
    assert not report.passed


def test_verify_compiler_config_warns_without_tpu(tmp_path, monkeypatch):
    from labelu.internal.common import sophon_faq_verify as v

    monkeypatch.setattr(
        cfg_io,
        "load_bm1688_compiler_config",
        lambda: {**cfg_io.default_config(), "tpu_mlir_root": ""},
    )
    report = SophonVerifyReport()
    v.verify_compiler_config(report)
    assert any(c.name == "TPU_MLIR_ROOT" and c.status == "fail" for c in report.checks)
