from __future__ import annotations

from pathlib import Path

from labelu.internal.common.yolo_bm1688_calibration import (
    sample_calibration_images,
    write_calibration_zip,
)
from labelu.internal.common.yolo_bm1688_sophon import prepare_calibration_from_dataset


def _write_train_sample(
    root: Path,
    stem: str,
    *,
    scene: str = "urban",
    label: str = "cat",
):
    img = root / "images" / "train" / f"{stem}.jpg"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"jpeg")
    lbl = root / "labels" / "train" / f"{stem}.txt"
    lbl.parent.mkdir(parents=True, exist_ok=True)
    lbl.write_text(f"0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    xml = root / "labelsxml" / "train" / f"{stem}.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    xml.write_text(
        f"""<?xml version='1.0' encoding='utf-8'?>
<annotation>
  <filename>{stem}.jpg</filename>
  <size><width>100</width><height>100</height><depth>3</depth></size>
  <extra_annotations><sceneType>{scene}</sceneType></extra_annotations>
  <object><name>{label}</name><bndbox>
    <xmin>10</xmin><ymin>10</ymin><xmax>30</xmax><ymax>30</ymax>
  </bndbox></object>
</annotation>""",
        encoding="utf-8",
    )


def test_balanced_calibration_respects_prefix(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir(parents=True, exist_ok=True)
    (root / "class_names.txt").write_text("cat\ndog\n", encoding="utf-8")
    _write_train_sample(root, "garbage_a", scene="day", label="cat")
    _write_train_sample(root, "garbage_b", scene="night", label="dog")
    _write_train_sample(root, "other_c", scene="day", label="cat")

    dest = tmp_path / "calib"
    sample_calibration_images(
        root,
        dest,
        class_names=["cat", "dog"],
        count=2,
        seed=1,
        balance_scene=True,
        filename_prefixes=["garbage"],
    )
    names = {p.name for p in dest.iterdir() if p.suffix == ".jpg"}
    assert names == {"garbage_a.jpg", "garbage_b.jpg"}
    assert (dest / "sample_report.json").is_file()


def test_prepare_calibration_zip(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir(parents=True, exist_ok=True)
    (root / "class_names.txt").write_text("cat\n", encoding="utf-8")
    for i in range(4):
        _write_train_sample(root, f"img_{i}", label="cat")

    dest = tmp_path / "calib"
    prepare_calibration_from_dataset(
        root,
        dest,
        count=2,
        seed=0,
        file_cfg={"calibration_balance_scene": True, "cali_input_num": 2},
    )
    assert len(list(dest.glob("*.jpg"))) == 2
    z = tmp_path / "calib.zip"
    write_calibration_zip(dest, z)
    assert z.is_file()
