from __future__ import annotations

from pathlib import Path

import pytest

from labelu.internal.common import yolo_bm1688_sophon as sophon


def test_ultralytics_weights_to_sophon_stem():
    assert sophon.ultralytics_weights_to_sophon_stem("yolo11n.pt") == "yolov11n"
    assert sophon.ultralytics_weights_to_sophon_stem("yolo11s.pt") == "yolov11s"
    assert sophon.ultralytics_weights_to_sophon_stem("yolov11m.pt") == "yolov11m"


def test_onnx_and_qtable_filenames():
    assert sophon.onnx_filename_for_stem("yolov11n") == "yolo11n.onnx"
    assert sophon.qtable_filename_for_stem("yolov11s") == "yolov11s_qtable"


def test_sigmoid_f16_qtable_line_matches_sop():
    fpfwd = "/model.23/dfl/conv/Conv_output_0_Conv"
    assert (
        sophon.sigmoid_f16_qtable_line(fpfwd)
        == "/model.23/Sigmoid_output_0_Sigmoid F16"
    )


def test_ensure_sigmoid_f16_in_qtable_appends_once(tmp_path):
    fpfwd = "/model.23/dfl/conv/Conv_output_0_Conv"
    qt = tmp_path / "yolov11n_qtable"
    qt.write_text("/model.23/dfl/conv/Conv_output_0_Conv F16\n", encoding="utf-8")
    assert sophon.ensure_sigmoid_f16_in_qtable(qt, fpfwd) is True
    text = qt.read_text(encoding="utf-8")
    assert "/model.23/Sigmoid_output_0_Sigmoid F16" in text
    assert sophon.ensure_sigmoid_f16_in_qtable(qt, fpfwd) is False


def test_prepare_calibration_fallback_without_xml(tmp_path):
    root = tmp_path / "dataset"
    train = root / "images" / "train"
    train.mkdir(parents=True)
    (root / "class_names.txt").write_text("cat\n", encoding="utf-8")
    for i in range(5):
        (train / f"img_{i}.jpg").write_bytes(b"jpeg")
    dest = tmp_path / "calib"
    out = sophon.prepare_calibration_from_dataset(
        root, dest, count=3, seed=1, file_cfg={"cali_input_num": 3}
    )
    assert out == dest.resolve()
    assert len(list(dest.glob("*.jpg"))) == 3


def test_resolve_export_calibration_prefers_job(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.yolo_bm1688_sophon.settings.YOLO_BM1688_USE_JOB_CALIBRATION",
        True,
    )
    job_root = tmp_path / "job-1"
    ds = job_root / "dataset"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "class_names.txt").write_text("cat\n", encoding="utf-8")
    train = ds / "images" / "train"
    train.mkdir(parents=True)
    xml_dir = ds / "labelsxml" / "train"
    xml_dir.mkdir(parents=True)
    (train / "garbage_1.jpg").write_bytes(b"x")
    (xml_dir / "garbage_1.xml").write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<annotation><filename>garbage_1.jpg</filename>
<size><width>10</width><height>10</height><depth>3</depth></size>
<object><name>cat</name><bndbox><xmin>1</xmin><ymin>1</ymin><xmax>5</xmax><ymax>5</ymax></bndbox></object>
</annotation>""",
        encoding="utf-8",
    )
    export_root = tmp_path / "export"
    export_root.mkdir()
    cali, note = sophon.resolve_export_calibration_dir(
        export_root=export_root,
        train_job_work_root=job_root,
        file_cfg={"calibration_dataset_dir": str(tmp_path / "coco128")},
    )
    assert "job_balanced_calibration" in note
    assert Path(cali).is_dir()
    assert (export_root / "calib.zip").is_file()
