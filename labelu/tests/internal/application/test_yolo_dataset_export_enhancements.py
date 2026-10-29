"""YOLO 导出增强：场景 XML、验收报告。"""
import json
import xml.etree.ElementTree as ET

from labelu.internal.application.service.yolo_dataset import (
    extract_scene_attrs_from_result,
    merge_scene_attrs,
    write_voc_xml_with_scene,
    yolo_job_reports_dir,
    _write_dataset_yaml_files,
)
from labelu.internal.application.service.yolo_dataset_validation import (
    validate_exported_yolo_dataset,
)
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample


def _sample(*, sid: int, filename: str, data: dict) -> TaskSample:
    att = TaskAttachment()
    att.id = sid
    att.filename = filename
    att.path = f"/upload/{filename}"
    s = TaskSample()
    s.id = sid
    s.task_id = 1
    s.state = SampleState.DONE.value
    s.file = att
    s.data = json.dumps(
        {"result": json.dumps(data, ensure_ascii=False)},
        ensure_ascii=False,
    )
    return s


def test_merge_scene_attrs_tagtool_and_pipeline():
    outer = {"image_pipeline": {"scene_id": "scene_03"}}
    result = {
        "tagTool": {
            "result": [
                {"type": "tag", "value": {"sceneType": ["urbanArterial"], "weather": ["sunny"]}}
            ]
        }
    }
    attrs = merge_scene_attrs(outer, result)
    assert attrs["scene_id"] == "scene_03"
    assert attrs["sceneType"] == "urbanArterial"
    assert attrs["weather"] == "sunny"


def test_write_voc_xml_extra_annotations(tmp_path):
    xml_path = tmp_path / "a.xml"
    write_voc_xml_with_scene(
        xml_path,
        image_name="a.jpg",
        width=100,
        height=80,
        class_names=["cat"],
        yolo_lines=["0 0.5 0.5 0.2 0.2"],
        scene_attrs={"sceneType": "night"},
    )
    root = ET.parse(xml_path).getroot()
    extra = root.find("extra_annotations")
    assert extra is not None
    assert extra.find("sceneType").text == "night"
    assert root.find("object/name").text == "cat"


def test_validate_exported_dataset_detects_bad_box(tmp_path):
    root = tmp_path / "ds"
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    (root / "images" / "train" / "1.jpg").write_bytes(b"\xff\xd8\xff")
    (root / "labels" / "train" / "1.txt").write_text("0 2.0 0.5 0.1 0.1\n", encoding="utf-8")
    (root / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\nnc: 1\nnames:\n  0: cat\n",
        encoding="utf-8",
    )
    summary = validate_exported_yolo_dataset(root, strict=False, write_reports=False)
    assert summary["ok"] is False
    assert summary["error_count"] >= 1


def test_extract_scene_attrs_empty_without_tagtool():
    assert extract_scene_attrs_from_result({"rectTool": {"result": []}}) == {}


def test_write_dataset_yaml_only_data_yaml(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    _write_dataset_yaml_files(root, ["cat", "dog"], include_test=True)
    assert (root / "data.yaml").is_file()
    assert (root / "class_names.txt").is_file()
    assert not (root / "dataset.yaml").exists()
    assert not (root / "test.yaml").exists()
    text = (root / "data.yaml").read_text(encoding="utf-8")
    assert f"path: {root.resolve().as_posix()}" in text
    assert "test: images/test" in text


def test_validate_reports_under_job_reports_dir(tmp_path):
    root = tmp_path / "job-1" / "dataset"
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    (root / "images" / "val").mkdir(parents=True)
    (root / "labels" / "val").mkdir(parents=True)
    (root / "images" / "train" / "1.jpg").write_bytes(b"\xff\xd8\xff")
    (root / "labels" / "train" / "1.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    (root / "images" / "val" / "2.jpg").write_bytes(b"\xff\xd8\xff")
    (root / "labels" / "val" / "2.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    (root / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\nnc: 1\nnames:\n  0: cat\n",
        encoding="utf-8",
    )
    reports = yolo_job_reports_dir(root)
    validate_exported_yolo_dataset(root, strict=True, write_reports=True, reports_dir=reports)
    assert (reports / "dataset_validation_report.md").is_file()
    assert not (root / "dataset_validation").exists()
