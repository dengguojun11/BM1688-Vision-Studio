import importlib.util
import json
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _plate_contract(model_name="plate_recognition_fp16_1b.bmodel"):
    return {
        "format": "labelu_plate_recognition_bm1688_v1",
        "job_type": "plate_recognition",
        "combined_bmodel": model_name,
        "networks": {
            "plate_pose": {},
            "plate_ocr": {},
            "plate_color": {},
            "plate_type": {},
        },
    }


def _plate_detection_contract(model_name="plate_detection_fp16_1b.bmodel"):
    return {
        "format": "labelu_plate_detection_bm1688_v1",
        "job_type": "plate_detection",
        "bmodel": model_name,
        "network": {"name": "plate_detection", "class_names": ["doubleEP"]},
    }


def test_build_yolo_validation_job(tmp_path):
    builder = _load_module(
        REPO_ROOT / "scripts" / "edge_validation_job" / "build_job.py",
        "edge_validation_job_builder_yolo",
    )
    model = tmp_path / "model_fp16_1b.bmodel"
    classes = tmp_path / "classes.txt"
    images = tmp_path / "images"
    output = tmp_path / "job.tar.gz"
    model.write_bytes(b"bmodel")
    classes.write_text("person\ncar\n", encoding="utf-8")
    images.mkdir()
    (images / "sample.jpg").write_bytes(b"image")

    builder.build_job(model, images, output, "job-1", classes=classes)

    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
        manifest = json.load(archive.extractfile("validation_job/manifest.json"))
    assert manifest["model_type"] == "yolo_detection"
    assert manifest["image_count"] == 1
    assert "validation_job/model/model_fp16_1b.bmodel" in names
    assert "validation_job/model/classes.txt" in names
    assert "validation_job/images/sample.jpg" in names


def test_build_plate_validation_job(tmp_path):
    builder = _load_module(
        REPO_ROOT / "scripts" / "edge_validation_job" / "build_job.py",
        "edge_validation_job_builder_plate",
    )
    model = tmp_path / "plate_recognition_fp16_1b.bmodel"
    contract = tmp_path / "plate_recognition_contract.json"
    images = tmp_path / "images"
    output = tmp_path / "plate-job.tar.gz"
    model.write_bytes(b"bmodel")
    contract.write_text(json.dumps(_plate_contract()), encoding="utf-8")
    images.mkdir()
    (images / "plate.png").write_bytes(b"image")

    builder.build_job(model, images, output, "plate-job", contract=contract)

    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
        manifest = json.load(archive.extractfile("validation_job/manifest.json"))
    assert manifest["model_type"] == "plate_recognition"
    assert "validation_job/model/plate_recognition_contract.json" in names
    assert "validation_job/images/plate.png" in names


def test_build_plate_detection_validation_job(tmp_path):
    builder = _load_module(
        REPO_ROOT / "scripts" / "edge_validation_job" / "build_job.py",
        "edge_validation_job_builder_plate_detection",
    )
    model = tmp_path / "plate_detection_fp16_1b.bmodel"
    contract = tmp_path / "plate_detection_contract.json"
    images = tmp_path / "images"
    output = tmp_path / "plate-detection-job.tar.gz"
    model.write_bytes(b"bmodel")
    contract.write_text(json.dumps(_plate_detection_contract()), encoding="utf-8")
    images.mkdir()
    (images / "plate.png").write_bytes(b"image")

    builder.build_job(model, images, output, "plate-detection-job", contract=contract)

    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
        manifest = json.load(archive.extractfile("validation_job/manifest.json"))
    assert manifest["model_type"] == "plate_detection"
    assert "validation_job/model/plate_detection_contract.json" in names
    assert "validation_job/images/plate.png" in names


def test_runtime_package_contains_offline_payload_and_validator(tmp_path):
    builder = _load_module(
        REPO_ROOT / "scripts" / "edge_runtime_bootstrap" / "build_package.py",
        "edge_runtime_package_builder",
    )
    docker_image = tmp_path / "runtime.tar.gz"
    sophon_demo = tmp_path / "demo.tar.gz"
    output = tmp_path / "runtime-package.tar"
    docker_image.write_bytes(b"docker-image")
    sophon_demo.write_bytes(b"sophon-demo")

    builder.build_package(docker_image, sophon_demo, output)

    with tarfile.open(output, "r") as archive:
        names = set(archive.getnames())
        setup_mode = archive.getmember("bm1688_edge_runtime/setup.sh").mode
        setup_script = archive.extractfile(
            "bm1688_edge_runtime/setup.sh"
        ).read().decode("utf-8")
        check_script = archive.extractfile(
            "bm1688_edge_runtime/check_env.sh"
        ).read().decode("utf-8")
    assert "bm1688_edge_runtime/setup.sh" in names
    assert "bm1688_edge_runtime/check_env.sh" in names
    assert "bm1688_edge_runtime/bmodel-validate" in names
    assert "bm1688_edge_runtime/app/validate.py" in names
    assert "bm1688_edge_runtime/app/plate_recognition_bm1688_infer.py" in names
    assert "bm1688_edge_runtime/app/plate_detection_bm1688_infer.py" in names
    assert "bm1688_edge_runtime/payload/bm1688-7g-runtime-image.tar.gz" in names
    assert "bm1688_edge_runtime/payload/sophon-demo-yolov8-plus-det.tar.gz" in names
    assert "bm1688_edge_runtime/payload/SHA256SUMS" in names
    assert setup_mode == 0o755
    assert 'BIN_DIR="${LABELU_EDGE_BIN_DIR:-${EDGE_ROOT}/bin}"' in setup_script
    assert 'install -m 0755 "${SCRIPT_DIR}/bmodel-validate" "${BIN_DIR}/bmodel-validate"' in setup_script
    assert '-x "${BIN_DIR}/bmodel-validate"' in check_script
    assert "/usr/local/bin/bmodel-validate" not in setup_script
    assert "/usr/local/bin/bmodel-validate" not in check_script


def test_delivery_shell_scripts_use_lf_line_endings():
    source_dir = REPO_ROOT / "scripts" / "edge_runtime_bootstrap"
    for name in ("setup.sh", "check_env.sh", "bmodel-validate"):
        assert b"\r\n" not in (source_dir / name).read_bytes()
