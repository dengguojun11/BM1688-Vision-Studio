import importlib.util
import json
import tarfile
from pathlib import Path

import pytest


def _load_validator():
    path = (
        Path(__file__).parents[3] / "scripts" / "edge_bmodel_validator" / "validate.py"
    )
    spec = importlib.util.spec_from_file_location("manual_bmodel_validator_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_edge_path_guard_rejects_paths_outside_managed_root(tmp_path):
    validator = _load_validator()
    validator.EDGE_ROOT = tmp_path / "edge-root"
    inside = validator.EDGE_ROOT / "jobs" / "job-1"

    assert validator._require_edge_path(inside) == inside.resolve()
    with pytest.raises(validator.ValidationError, match="under /data/labelu_edge"):
        validator._require_edge_path(tmp_path / "outside")


def _plate_contract():
    return {
        "format": "labelu_plate_recognition_bm1688_v1",
        "job_type": "plate_recognition",
        "combined_bmodel": "plate_recognition_fp16_1b.bmodel",
        "networks": {
            "plate_pose": {},
            "plate_ocr": {"characters": ["A", "1"]},
            "plate_color": {"output": {"classes": ["green"]}},
            "plate_type": {"output": {"classes": ["greenPlate"]}},
        },
    }


def _plate_detection_contract():
    return {
        "format": "labelu_plate_detection_bm1688_v1",
        "job_type": "plate_detection",
        "bmodel": "plate_detection_fp16_1b.bmodel",
        "network": {
            "name": "plate_detection",
            "class_names": ["doubleEP"],
        },
    }


def test_resolve_single_model_requires_exactly_one_bmodel(tmp_path):
    validator = _load_validator()
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    with pytest.raises(validator.ValidationError, match="No .bmodel"):
        validator._resolve_single_model(model_dir)

    expected = model_dir / "candidate_int8_1b.bmodel"
    expected.write_bytes(b"model")
    assert validator._resolve_single_model(model_dir) == expected.resolve()

    (model_dir / "candidate_fp16_1b.bmodel").write_bytes(b"model")
    with pytest.raises(validator.ValidationError, match="exactly one"):
        validator._resolve_single_model(model_dir)


def test_discover_images_accepts_supported_files_only(tmp_path):
    validator = _load_validator()
    (tmp_path / "a.jpg").write_bytes(b"image")
    (tmp_path / "b.PNG").write_bytes(b"image")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    assert [path.name for path in validator._discover_images(tmp_path)] == [
        "a.jpg",
        "b.PNG",
    ]


def test_plate_model_requires_matching_contract(tmp_path):
    validator = _load_validator()
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    model = model_dir / "plate_recognition_fp16_1b.bmodel"
    model.write_bytes(b"model")

    with pytest.raises(validator.ValidationError, match="requires"):
        validator._resolve_model_bundle(model_dir)

    contract_path = model_dir / "plate_recognition_contract.json"
    contract_path.write_text(json.dumps(_plate_contract()), encoding="utf-8")

    bundle = validator._resolve_model_bundle(model_dir)

    assert bundle == {
        "model_type": "plate_recognition",
        "model": model.resolve(),
        "contract": contract_path.resolve(),
    }


def test_plate_detection_model_requires_matching_contract(tmp_path):
    validator = _load_validator()
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    model = model_dir / "plate_detection_fp16_1b.bmodel"
    model.write_bytes(b"model")

    with pytest.raises(validator.ValidationError, match="requires"):
        validator._resolve_model_bundle(model_dir)

    contract_path = model_dir / "plate_detection_contract.json"
    contract_path.write_text(json.dumps(_plate_detection_contract()), encoding="utf-8")

    assert validator._resolve_model_bundle(model_dir) == {
        "model_type": "plate_detection",
        "model": model.resolve(),
        "contract": contract_path.resolve(),
    }


def test_normalize_results_maps_class_names_and_counts():
    validator = _load_validator()
    rows = [
        {
            "image_name": "one.jpg",
            "bboxes": [
                {"category_id": 1, "score": 0.91, "bbox": [10, 20, 30, 40]},
                {"category_id": 3, "score": 0.52, "bbox": [1, 2, 3, 4]},
            ],
        },
        {"image_name": "two.jpg", "bboxes": []},
    ]

    normalized, summary = validator._normalize_results(rows, ["person", "helmet"])

    assert normalized[0]["detections"][0]["class_name"] == "helmet"
    assert normalized[0]["detections"][1]["class_name"] == "class_3"
    assert summary == {
        "result_image_count": 2,
        "images_with_detections": 1,
        "total_detections": 2,
        "class_counts": {"helmet": 1, "class_3": 1},
    }


def test_normalize_plate_results_preserves_business_fields():
    validator = _load_validator()
    payload = {
        "images": [
            {
                "image": "/data/images/plate.jpg",
                "detections": [
                    {
                        "plate_number": "A12345",
                        "plate_number_score": 0.93,
                        "plate_type": "greenPlate",
                        "plate_type_score": 0.91,
                        "color": "green",
                        "color_score": 0.89,
                        "score": 0.95,
                        "box": [1, 2, 30, 40],
                        "corners": [[1, 2], [30, 2], [30, 40], [1, 40]],
                    }
                ],
            }
        ]
    }

    normalized, summary = validator._normalize_plate_results(payload)

    assert normalized[0]["detections"][0]["plate_number"] == "A12345"
    assert normalized[0]["detections"][0]["bbox_xyxy"] == [1, 2, 30, 40]
    assert summary["total_detections"] == 1
    assert summary["plate_numbers"] == ["A12345"]
    assert summary["plate_type_counts"] == {"greenPlate": 1}
    assert summary["color_counts"] == {"green": 1}


def test_main_runs_with_default_replace_model_and_images_workflow(
    monkeypatch, tmp_path
):
    validator = _load_validator()
    monkeypatch.setattr(validator, "EDGE_ROOT", tmp_path)
    model_dir = tmp_path / "model"
    images_dir = tmp_path / "images"
    demo_root = tmp_path / "sophon-demo" / "sample" / "YOLOv8_plus_det"
    output_dir = tmp_path / "output" / "current"
    model_dir.mkdir()
    images_dir.mkdir()
    (demo_root / "python").mkdir(parents=True)
    (demo_root / "python" / "yolov8_bmcv.py").write_text("# fake", encoding="utf-8")
    model = model_dir / "model_int8_1b.bmodel"
    model.write_bytes(b"model")
    (images_dir / "sample.jpg").write_bytes(b"image")

    monkeypatch.setattr(validator, "__file__", str(tmp_path / "validate.py"))
    monkeypatch.setenv("SOPHON_DEMO_ROOT", str(demo_root))
    monkeypatch.setattr(validator.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        validator,
        "_render_images",
        lambda images, normalized, output: {"status": "ok", "count": 1},
    )

    def fake_run(command, cwd, log_path):
        results = demo_root / "results"
        results.mkdir(parents=True, exist_ok=True)
        (results / "model_int8_1b.bmodel_images_bmcv_python_result.json").write_text(
            json.dumps(
                [
                    {
                        "image_name": "sample.jpg",
                        "bboxes": [
                            {
                                "category_id": 0,
                                "score": 0.88,
                                "bbox": [1, 2, 30, 40],
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok", encoding="utf-8")
        return {"return_code": 0, "elapsed_seconds": 0.1}

    monkeypatch.setattr(validator, "_run_command", fake_run)

    exit_code = validator.main([])

    assert exit_code == 0
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["runtime_ok"] is True
    assert summary["objects_detected"] is True
    assert summary["input_image_count"] == 1
    assert summary["total_detections"] == 1
    assert (output_dir / "detections.json").is_file()
    assert (output_dir / "raw_detections.json").is_file()


def test_main_auto_dispatches_plate_recognition_without_sophon_demo(
    monkeypatch, tmp_path
):
    validator = _load_validator()
    monkeypatch.setattr(validator, "EDGE_ROOT", tmp_path)
    model_dir = tmp_path / "model"
    images_dir = tmp_path / "images"
    output_dir = tmp_path / "output" / "current"
    model_dir.mkdir()
    images_dir.mkdir()
    (model_dir / "plate_recognition_fp16_1b.bmodel").write_bytes(b"model")
    (model_dir / "plate_recognition_contract.json").write_text(
        json.dumps(_plate_contract()), encoding="utf-8"
    )
    (images_dir / "plate.jpg").write_bytes(b"image")
    (tmp_path / "plate_recognition_bm1688_infer.py").write_text(
        "# fake runner", encoding="utf-8"
    )

    monkeypatch.setattr(validator, "__file__", str(tmp_path / "validate.py"))
    monkeypatch.setattr(validator.shutil, "which", lambda name: None)

    def fake_run(command, cwd, log_path):
        plate_output = Path(command[command.index("--output") + 1])
        plate_output.mkdir(parents=True, exist_ok=True)
        (plate_output / "plate.jpg").write_bytes(b"annotated")
        (plate_output / "results.json").write_text(
            json.dumps(
                {
                    "format": "labelu_plate_recognition_bm1688_result_v1",
                    "images": [
                        {
                            "image": str(images_dir / "plate.jpg"),
                            "detections": [
                                {
                                    "plate_number": "A12345",
                                    "plate_type": "greenPlate",
                                    "color": "green",
                                    "score": 0.9,
                                    "box": [1, 2, 30, 40],
                                    "corners": [],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok", encoding="utf-8")
        return {"return_code": 0, "elapsed_seconds": 0.2}

    monkeypatch.setattr(validator, "_run_command", fake_run)

    exit_code = validator.main([])

    assert exit_code == 0
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["model_type"] == "plate_recognition"
    assert summary["runtime_ok"] is True
    assert summary["plate_numbers"] == ["A12345"]
    assert summary["rendered_images"]["count"] == 1


def test_main_auto_dispatches_plate_detection_without_sophon_demo(
    monkeypatch, tmp_path
):
    validator = _load_validator()
    monkeypatch.setattr(validator, "EDGE_ROOT", tmp_path)
    model_dir = tmp_path / "model"
    images_dir = tmp_path / "images"
    output_dir = tmp_path / "output" / "current"
    model_dir.mkdir()
    images_dir.mkdir()
    (model_dir / "plate_detection_fp16_1b.bmodel").write_bytes(b"model")
    (model_dir / "plate_detection_contract.json").write_text(
        json.dumps(_plate_detection_contract()), encoding="utf-8"
    )
    (images_dir / "plate.jpg").write_bytes(b"image")
    (tmp_path / "plate_detection_bm1688_infer.py").write_text(
        "# fake runner", encoding="utf-8"
    )

    monkeypatch.setattr(validator, "__file__", str(tmp_path / "validate.py"))
    monkeypatch.setattr(validator.shutil, "which", lambda name: None)

    def fake_run(command, cwd, log_path):
        plate_output = Path(command[command.index("--output") + 1])
        plate_output.mkdir(parents=True, exist_ok=True)
        (plate_output / "plate.jpg").write_bytes(b"annotated")
        (plate_output / "results.json").write_text(
            json.dumps(
                {
                    "format": "labelu_plate_detection_bm1688_result_v1",
                    "images": [
                        {
                            "image": str(images_dir / "plate.jpg"),
                            "detections": [
                                {
                                    "label": "doubleEP",
                                    "score": 0.92,
                                    "box": [1, 2, 30, 40],
                                    "corners": [[1, 2], [30, 2], [30, 40], [1, 40]],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok", encoding="utf-8")
        return {"return_code": 0, "elapsed_seconds": 0.2}

    monkeypatch.setattr(validator, "_run_command", fake_run)

    assert validator.main([]) == 0
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    detections = json.loads(
        (output_dir / "detections.json").read_text(encoding="utf-8")
    )
    assert summary["model_type"] == "plate_detection"
    assert summary["runtime_ok"] is True
    assert summary["total_detections"] == 1
    assert detections[0]["detections"][0]["label"] == "doubleEP"


def test_package_builder_includes_canonical_plate_runner(tmp_path):
    builder_path = (
        Path(__file__).parents[3]
        / "scripts"
        / "edge_bmodel_validator"
        / "build_package.py"
    )
    spec = importlib.util.spec_from_file_location(
        "edge_validator_builder_test", builder_path
    )
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)
    archive_path = tmp_path / "validator.tar.gz"

    builder.build_archive(archive_path)

    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "edge_bmodel_validator/plate_recognition_bm1688_infer.py" in names
    assert "edge_bmodel_validator/plate_detection_bm1688_infer.py" in names
    assert "edge_bmodel_validator/validate.py" in names
    assert "edge_bmodel_validator/build_package.py" not in names
