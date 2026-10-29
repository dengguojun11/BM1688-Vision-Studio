import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

from labelu.internal.application.service import plate_detection_bm1688 as service
from labelu.internal.application.service import yolo_bm1688_export as base_export
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus


class _Writer:
    def __init__(self):
        self.blocks = []
        self.closed = False

    def write_block(self, name, value):
        self.blocks.append((name, value))

    def close(self):
        self.closed = True


class _Guard:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


def test_runtime_contract_declares_single_pose_network():
    contract = service._runtime_contract(
        SimpleNamespace(imgsz=640),
        {
            "imgsz": 640,
            "class_names": ["doubleEP"],
            "keypoint_order": [
                "top_left",
                "top_right",
                "bottom_right",
                "bottom_left",
            ],
        },
    )

    assert contract["format"] == "labelu_plate_detection_bm1688_v1"
    assert contract["job_type"] == "plate_detection"
    assert contract["bmodel"] == "plate_detection_fp16_1b.bmodel"
    assert contract["network"]["name"] == "plate_detection"
    assert contract["network"]["input"]["shape"] == [1, 3, 640, 640]
    assert contract["network"]["class_names"] == ["doubleEP"]


def test_compiler_script_builds_one_f16_bmodel_and_cleans_temp_directory():
    script = (
        Path(service.__file__).resolve().parents[3]
        / "scripts"
        / "bm1688_plate_detection_to_bmodel.sh"
    ).read_text(encoding="utf-8")

    assert "tpu_mlir_setup_native_runtime" in script
    assert "tpu_mlir_require_chip_kernel_so bm1688" in script
    assert "--model_name plate_detection" in script
    assert "--quantize F16" in script
    assert "plate_detection_fp16_1b.bmodel" in script
    assert "trap 'rm -rf \"${WORK_DIR}\"' EXIT" in script


def test_export_persists_single_bmodel_and_detection_contract(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    job_root = media_root / "yolo_train" / "job-42" / "plate_detection"
    job_root.mkdir(parents=True)
    pt_path = job_root / "plate_detection_best.pt"
    onnx_path = job_root / "plate_detection.onnx"
    pt_path.write_bytes(b"pt")
    onnx_path.write_bytes(b"onnx")
    artifacts = {
        "pose": {
            "pt": str(pt_path),
            "onnx": str(onnx_path),
            "network": "plate_detection",
            "input_size": [640, 640],
            "class_names": ["doubleEP"],
        }
    }
    job = SimpleNamespace(
        id=42,
        job_type="plate_detection",
        status=YoloTrainStatus.COMPLETED.value,
        imgsz=640,
        component_artifacts_json=json.dumps(artifacts),
    )
    row = SimpleNamespace(id=9, yolo_train_job_id=42)
    updates = []
    writer = _Writer()
    quantize_writer = _Writer()
    guard = _Guard()
    export_root = media_root / "yolo_bm1688_exports" / "9"

    @contextmanager
    def fake_session_scope():
        yield object()

    def fake_update(_db, target, **values):
        updates.append(values)
        for key, value in values.items():
            setattr(target, key, value)

    def fake_compile(_command, **_kwargs):
        bmodel = export_root / "models" / "BM1688" / service.BMODEL_NAME
        bmodel.parent.mkdir(parents=True, exist_ok=True)
        bmodel.write_bytes(b"bmodel")
        return 0

    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(service.settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    monkeypatch.setattr(service, "session_scope", fake_session_scope)
    monkeypatch.setattr(service, "begin_transaction", lambda _db: nullcontext())
    monkeypatch.setattr(service.crud_yolo_bm1688_export, "get", lambda _db, _id: row)
    monkeypatch.setattr(service.crud_yolo_bm1688_export, "update_row", fake_update)
    monkeypatch.setattr(service.crud_yolo_train_job, "get", lambda _db, _id: job)
    monkeypatch.setattr(
        service.cfg_io,
        "load_bm1688_compiler_config",
        lambda: {
            "auto_compile": True,
            "tpu_mlir_root": "/opt/tpu-mlir",
            "tpu_mlir_python": "/opt/tpu-venv/bin/python3",
        },
    )
    monkeypatch.setattr(
        base_export, "_acquire_export_execution_guard", lambda _id: guard
    )
    monkeypatch.setattr(
        base_export,
        "_start_export_execution_monitor",
        lambda _id, **_kwargs: (None, True, False),
    )
    monkeypatch.setattr(
        base_export,
        "_start_export_execution_monitor",
        lambda _id, **_kwargs: (None, True, False),
    )
    monkeypatch.setattr(base_export, "_stop_export_execution_monitor", lambda _m: None)
    monkeypatch.setattr(
        base_export,
        "_open_bm1688_process_log_writers",
        lambda _id: (writer, quantize_writer),
    )
    monkeypatch.setattr(base_export, "_export_dir_for_row", lambda _id: export_root)
    monkeypatch.setattr(base_export, "_preflight_tpu_mlir_python", lambda _path: None)
    monkeypatch.setattr(base_export, "_compile_subprocess_env", lambda _cfg: {})
    monkeypatch.setattr(base_export, "_compile_timeout_seconds", lambda: 60)
    monkeypatch.setattr(base_export, "_run_compile_shell_command", fake_compile)

    service.run_plate_detection_bm1688_export(9)

    assert updates[-1]["status"] == "COMPLETED"
    assert updates[-1]["bmodel_rel_path"] == (
        "models/BM1688/plate_detection_fp16_1b.bmodel"
    )
    contract = json.loads(
        (export_root / "plate_detection_contract.json").read_text(encoding="utf-8")
    )
    assert contract["network"]["name"] == "plate_detection"
    manifest = json.loads(updates[-1]["artifact_manifest_json"])
    assert manifest["network_names"] == ["plate_detection"]
    assert manifest["bmodel"] == "models/BM1688/plate_detection_fp16_1b.bmodel"
    assert guard.released is True
    assert writer.closed is True
    assert quantize_writer.closed is True
