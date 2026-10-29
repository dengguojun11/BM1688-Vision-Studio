"""BM1688 编译命令与导出终态逻辑（不依赖真实 TPU-MLIR）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from labelu.internal.application.service import bm1688_compiler_config as compile_cfg
from labelu.internal.application.service.yolo_bm1688_export import (
    _compile_error_summary,
    _run_bmodel_compile_phase,
)
from labelu.internal.common import bm1688_compiler_config as cfg_io


@pytest.fixture
def bm1688_cfg_file(tmp_path, monkeypatch):
    cfg_file = tmp_path / "bm1688_compiler_config.json"
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_compiler_config.config_file_path",
        lambda: cfg_file,
    )
    return cfg_file


def test_resolve_compile_cmd_includes_tpu_python_and_export_qtable(
    bm1688_cfg_file, tmp_path, monkeypatch
):
    import labelu

    script = Path(labelu.__file__).resolve().parent / "scripts" / "bm1688_onnx_to_bmodel.sh"
    if not script.is_file():
        pytest.skip("bm1688_onnx_to_bmodel.sh not in package")

    tpu_root = tmp_path / "tpu-mlir"
    tpu_root.mkdir()
    (tpu_root / "envsetup.sh").write_text("# mock\n", encoding="utf-8")

    out_dir = tmp_path / "export1"
    onnx_dir = out_dir / "models" / "onnx"
    onnx_dir.mkdir(parents=True)
    onnx_path = onnx_dir / "yolo11s.onnx"
    onnx_path.write_bytes(b"\x00" * 2048)
    qtable_name = cfg_io.DEFAULT_QTABLE_FILENAME
    qtable_in_tree = onnx_dir / qtable_name
    qtable_in_tree.write_text("# mock qtable\n", encoding="utf-8")

    cfg_io.save_bm1688_compiler_config(
        {
            **cfg_io.default_config(),
            "tpu_mlir_root": str(tpu_root),
            "tpu_mlir_python": "/data/labeIU/labeIuMedia/venv-tpu-mlir/bin/python3",
            "auto_compile": True,
            "calibration_dataset_dir": str(tmp_path / "coco128"),
            "quantize_table_path": str(tmp_path / "other" / "ignored_qtable"),
        }
    )
    (tmp_path / "coco128").mkdir()

    do, cmd = compile_cfg.resolve_compile_for_export(
        onnx_abs=onnx_path,
        out_dir=out_dir,
        imgsz=640,
    )
    assert do is True
    assert "TPU_MLIR_PYTHON=" in cmd
    assert "venv-tpu-mlir" in cmd
    assert "export PATH=" in cmd
    assert "/usr/bin:/usr/bin:/bin" not in cmd.split("export PATH=")[1].split("&&")[0]
    assert str(qtable_in_tree.resolve()) in cmd
    assert "bm1688_onnx_to_bmodel.sh" in cmd


def test_compile_phase_skips_without_calibration_dir(bm1688_cfg_file, tmp_path, monkeypatch):
    import labelu

    script = Path(labelu.__file__).resolve().parent / "scripts" / "bm1688_onnx_to_bmodel.sh"
    if not script.is_file():
        pytest.skip("bm1688_onnx_to_bmodel.sh not in package")

    tpu_root = tmp_path / "tpu-mlir"
    tpu_root.mkdir()
    (tpu_root / "envsetup.sh").write_text("# mock\n", encoding="utf-8")
    out_dir = tmp_path / "export1"
    onnx_dir = out_dir / "models" / "onnx"
    onnx_dir.mkdir(parents=True)
    onnx_path = onnx_dir / "yolo11s.onnx"
    onnx_path.write_bytes(b"\x00" * 2048)

    cfg_io.save_bm1688_compiler_config(
        {
            **cfg_io.default_config(),
            "tpu_mlir_root": str(tpu_root),
            "auto_compile": True,
            "quantize": "full",
            "calibration_dataset_dir": "",
        }
    )

    status, _, log, err = _run_bmodel_compile_phase(1, onnx_path, out_dir, 640)
    assert status == "ONNX_READY"
    assert "calibration_dataset_dir" in log
    assert err == ""


def test_compile_error_summary_from_exit_code():
    parts = ["exit_code=1", "ModuleNotFoundError: No module named 'foo'"]
    msg = _compile_error_summary(parts, 1)
    assert "ModuleNotFoundError" in msg or "退出码" in msg


def test_compile_phase_streams_docker_progress_into_compile_log(
    bm1688_cfg_file, tmp_path, monkeypatch
):
    out_dir = tmp_path / "export1"
    onnx_dir = out_dir / "models" / "onnx"
    onnx_dir.mkdir(parents=True)
    onnx_path = onnx_dir / "yolo11n.onnx"
    onnx_path.write_bytes(b"\x00" * 2048)

    cali_dir = tmp_path / "calibration"
    cali_dir.mkdir()
    work_root = tmp_path / "job_work"
    weights = work_root / "train" / "weights"
    weights.mkdir(parents=True)
    (weights / "best.pt").write_bytes(b"pt")

    cfg_io.save_bm1688_compiler_config(
        {
            **cfg_io.default_config(),
            "auto_compile": True,
            "quantize": "int8",
            "quantize_backend": "docker",
            "calibration_dataset_dir": str(cali_dir),
        }
    )

    progress_snapshots = []

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._job_work_root",
        lambda job_id: work_root,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._persist_export_progress",
        lambda export_id, parts, **kwargs: progress_snapshots.append("\n".join(parts)),
    )

    def _fake_docker_quant(**kwargs):
        line_callback = kwargs["line_callback"]
        line_callback("docker phase: prepare")
        line_callback("docker phase: convert")
        plat_dir = cfg_io.sophon_platform_dir(out_dir, "bm1688")
        plat_dir.mkdir(parents=True, exist_ok=True)
        (plat_dir / "yolov11n_int8_1b.bmodel").write_text("ok", encoding="utf-8")
        return True, "docker phase: done"

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.run_docker_int8_quant",
        _fake_docker_quant,
    )

    status, bmodel_rel, log, err = _run_bmodel_compile_phase(
        7,
        onnx_path,
        out_dir,
        640,
        yolo_train_job_id=5,
        model_weights="yolo11n.pt",
    )

    assert status == "COMPLETED"
    assert bmodel_rel == "models/BM1688/yolov11n_int8_1b.bmodel"
    assert err == ""
    assert "docker phase: prepare" in log
    assert any("docker phase: prepare" in snap for snap in progress_snapshots)


def test_compile_phase_full_docker_returns_completed_with_all_bmodels(
    bm1688_cfg_file, tmp_path, monkeypatch
):
    out_dir = tmp_path / "export1"
    onnx_dir = out_dir / "models" / "onnx"
    onnx_dir.mkdir(parents=True)
    onnx_path = onnx_dir / "yolo11n.onnx"
    onnx_path.write_bytes(b"onnx")
    cali_dir = tmp_path / "calibration"
    cali_dir.mkdir()
    work_root = tmp_path / "job_work"
    weights = work_root / "train" / "weights"
    weights.mkdir(parents=True)
    (weights / "best.pt").write_bytes(b"pt")
    compiler_config = {
        **cfg_io.default_config(),
        "auto_compile": True,
        "quantize": "full",
        "quantize_backend": "docker",
        "calibration_dataset_dir": str(cali_dir),
    }
    monkeypatch.setattr(
        cfg_io, "load_bm1688_compiler_config", lambda: compiler_config
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._job_work_root",
        lambda job_id: work_root,
    )

    def _fake_docker_quant(**kwargs):
        out = cfg_io.sophon_platform_dir(out_dir, "bm1688")
        out.mkdir(parents=True, exist_ok=True)
        for suffix in ("fp32", "fp16", "int8"):
            (out / f"yolov11n_{suffix}_1b.bmodel").write_text("ok", encoding="utf-8")
        return True, "docker full done"

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.run_docker_int8_quant",
        _fake_docker_quant,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.bm1688_compiler_cfg_service.resolve_compile_for_export",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("host compile should not run")),
    )

    status, bmodel_rel, log, err = _run_bmodel_compile_phase(
        8,
        onnx_path,
        out_dir,
        640,
        yolo_train_job_id=5,
        model_weights="yolo11n.pt",
    )

    assert status == "COMPLETED"
    assert bmodel_rel == "models/BM1688/yolov11n_int8_1b.bmodel"
    assert "docker_full_bmodels=fp32,fp16,int8" in log
    assert err == ""


def test_full_compile_status_uses_training_model_stem_not_config_default(
    bm1688_cfg_file, tmp_path, monkeypatch
):
    """三精度文件以前缀 yolov11s 落盘时不能被默认 yolov11n 配置误判。"""
    out_dir = tmp_path / "export1"
    onnx_dir = out_dir / "models" / "onnx"
    onnx_dir.mkdir(parents=True)
    onnx_path = onnx_dir / "yolo11s.onnx"
    onnx_path.write_bytes(b"onnx")
    cali_dir = tmp_path / "calibration"
    cali_dir.mkdir()
    work_root = tmp_path / "job_work"
    weights = work_root / "train" / "weights"
    weights.mkdir(parents=True)
    (weights / "best.pt").write_bytes(b"pt")
    compiler_config = {
        **cfg_io.default_config(),
        "auto_compile": True,
        "quantize": "full",
        "quantize_backend": "docker",
        "calibration_dataset_dir": str(cali_dir),
        # Keep the global default different from the training weight stem.
        "bmodel_basename": "yolov11n",
    }
    monkeypatch.setattr(
        cfg_io, "load_bm1688_compiler_config", lambda: compiler_config
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._job_work_root",
        lambda job_id: work_root,
    )

    def _fake_docker_quant(**kwargs):
        out = cfg_io.sophon_platform_dir(out_dir, "bm1688")
        out.mkdir(parents=True, exist_ok=True)
        for suffix in ("fp32", "fp16", "int8"):
            (out / f"yolov11s_{suffix}_1b.bmodel").write_text(
                "ok", encoding="utf-8"
            )
        return True, "docker full done"

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.run_docker_int8_quant",
        _fake_docker_quant,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export.bm1688_compiler_cfg_service.resolve_compile_for_export",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("host compile should not run when Docker produced all variants")
        ),
    )

    status, bmodel_rel, log, err = _run_bmodel_compile_phase(
        9,
        onnx_path,
        out_dir,
        640,
        yolo_train_job_id=5,
        model_weights="yolo11s.pt",
    )

    assert status == "COMPLETED"
    assert bmodel_rel == "models/BM1688/yolov11s_int8_1b.bmodel"
    assert "docker_full_bmodels=fp32,fp16,int8" in log
    assert err == ""
