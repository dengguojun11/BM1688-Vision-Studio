import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

from labelu.internal.application.service import plate_recognition_bm1688 as service
from labelu.internal.application.service import yolo_bm1688_export as base_export
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus


class _Writer:
    def __init__(self):
        self.blocks = []
        self.closed = False

    def write_block(self, name, value):
        self.blocks.append((name, value))

    def write(self, _value):
        return None

    def close(self):
        self.closed = True


class _Guard:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


def _component_artifacts(job_root: Path) -> dict:
    component_root = job_root / "plate_components"
    component_root.mkdir(parents=True)
    result = {}
    for component, network in (
        ("pose", "plate_pose"),
        ("ocr", "plate_ocr"),
        ("color", "plate_color"),
        ("plate_type", "plate_type"),
    ):
        pt = component_root / f"{component}.pt"
        onnx = component_root / f"{component}.onnx"
        pt.write_bytes(b"pt")
        onnx.write_bytes(b"onnx")
        result[component] = {
            "pt": str(pt),
            "onnx": str(onnx),
            "network": network,
            "classes": ["green"] if component == "color" else ["greenPlate"],
            "characters": ["A", "1"] if component == "ocr" else None,
            "input_size": [640, 640] if component == "pose" else None,
        }
    return result


def test_runtime_contract_declares_four_named_networks():
    components = {
        "pose": {"input_size": [640, 640]},
        "ocr": {
            "characters": ["陕", "A", "1"],
            "length_counts": {"6": 10},
            "preferred_length": 6,
            "crop_ratios": [0.45, 0.5, 0.55],
            "fallback_crop_ratios": [0.47],
            "length_anchor_crop_ratio": 0.5,
            "length_preference_tolerance": 0.1,
            "non_preferred_length_preserve_confidence": 0.99,
            "guarded_consensus_enabled": True,
            "anchor_group_min_confidence": 0.90,
            "non_anchor_group_min_confidence": 0.94,
        },
        "color": {"classes": ["green"]},
        "plate_type": {"classes": ["greenPlate"]},
    }
    job = SimpleNamespace(imgsz=640)

    contract = service._runtime_contract(job=job, components=components)

    assert list(contract["networks"]) == [
        "plate_pose",
        "plate_ocr",
        "plate_color",
        "plate_type",
    ]
    assert contract["networks"]["plate_ocr"]["input"]["shape"] == [1, 3, 48, 192]
    assert contract["networks"]["plate_pose"]["input"]["shape"] == [1, 3, 640, 640]
    assert contract["networks"]["plate_pose"]["keypoint_order"] == [
        "top_left",
        "top_right",
        "bottom_right",
        "bottom_left",
    ]
    assert contract["networks"]["plate_ocr"]["preferred_length"] == 6
    assert contract["networks"]["plate_ocr"]["crop_ratios"] == [0.45, 0.5, 0.55]
    assert contract["networks"]["plate_ocr"]["fallback_crop_ratios"] == [0.47]
    assert contract["networks"]["plate_ocr"]["length_anchor_crop_ratio"] == 0.5
    assert (
        contract["networks"]["plate_ocr"]["non_preferred_length_preserve_confidence"]
        == 0.99
    )
    assert contract["networks"]["plate_ocr"]["guarded_consensus_enabled"] is True
    assert contract["networks"]["plate_ocr"]["anchor_group_min_confidence"] == 0.90
    assert contract["networks"]["plate_ocr"]["non_anchor_group_min_confidence"] == 0.94


def test_resolve_component_sources_recovers_ocr_vocabulary_from_disk(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(service.settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    job_root = tmp_path / "yolo_train" / "job-42"
    artifacts = _component_artifacts(job_root)
    artifacts["ocr"].pop("characters")
    vocabulary_path = job_root / "plate_components" / "plate_ocr_vocab.json"
    vocabulary_path.write_text(
        json.dumps(
            {"blank_index": 0, "characters": ["0", "1", "A", "B"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    job = SimpleNamespace(
        id=42,
        component_artifacts_json=json.dumps(artifacts, ensure_ascii=False),
    )

    resolved = service._resolve_component_sources(job)

    assert resolved["ocr"]["characters"] == ["0", "1", "A", "B"]
    assert resolved["ocr"]["vocabulary"] == str(vocabulary_path.resolve())


def test_compiler_script_initializes_bm1688_native_runtime_before_codegen():
    script = (
        Path(service.__file__).resolve().parents[3]
        / "scripts"
        / "bm1688_plate_recognition_to_bmodel.sh"
    )
    content = script.read_text(encoding="utf-8")

    setup_index = content.index("tpu_mlir_setup_native_runtime")
    kernel_check_index = content.index("tpu_mlir_require_chip_kernel_so bm1688")
    pose_compile_index = content.index('compile_network plate_pose "${POSE_ONNX}"')

    assert 'source "${RUNTIME_HELPER}"' in content
    assert setup_index < kernel_check_index < pose_compile_index
    assert 'local bmodel="${WORK_DIR}/${name}_fp16_1b.bmodel"' in content
    assert '"${WORK_DIR}/plate_pose_fp16_1b.bmodel"' in content


def test_composite_export_combines_four_networks_and_persists_contract(
    tmp_path, monkeypatch
):
    media_root = tmp_path / "media"
    job_root = media_root / "yolo_train" / "job-42"
    artifacts = _component_artifacts(job_root)
    job = SimpleNamespace(
        id=42,
        job_type="plate_recognition",
        status=YoloTrainStatus.COMPLETED.value,
        imgsz=320,
        component_artifacts_json=json.dumps(artifacts, ensure_ascii=False),
    )
    row = SimpleNamespace(id=9, yolo_train_job_id=42)
    updates = []
    writer = _Writer()
    quantize_writer = _Writer()
    guard = _Guard()
    export_root = media_root / "yolo_bm1688_exports" / "9"
    edge_validation_calls = []
    quality_report_calls = []

    @contextmanager
    def fake_session_scope():
        yield object()

    def fake_update(_db, target, **values):
        updates.append(values)
        for key, value in values.items():
            setattr(target, key, value)

    def fake_compile(_command, **_kwargs):
        combined = export_root / "models" / "BM1688" / service.COMBINED_BMODEL_NAME
        combined.parent.mkdir(parents=True, exist_ok=True)
        combined.write_bytes(b"combined-bmodel")
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
    monkeypatch.setattr(base_export, "_stop_export_execution_monitor", lambda _m: None)
    monkeypatch.setattr(
        base_export,
        "_start_export_execution_monitor",
        lambda _id, **_kwargs: (None, True, False),
    )
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
    monkeypatch.setattr(
        base_export,
        "_maybe_run_edge_ssh_validation_sync",
        lambda export_id, **kwargs: edge_validation_calls.append((export_id, kwargs)),
    )
    monkeypatch.setattr(
        base_export,
        "_generate_quality_report_sync",
        lambda export_id, **kwargs: quality_report_calls.append((export_id, kwargs)),
    )

    service.run_plate_recognition_bm1688_export(9)

    assert updates[-1]["status"] == "COMPLETED"
    assert updates[-1]["bmodel_rel_path"] == (
        "models/BM1688/plate_recognition_fp16_1b.bmodel"
    )
    contract = json.loads(
        (export_root / "plate_recognition_contract.json").read_text(encoding="utf-8")
    )
    assert set(contract["networks"]) == {
        "plate_pose",
        "plate_ocr",
        "plate_color",
        "plate_type",
    }
    manifest = json.loads(updates[-1]["artifact_manifest_json"])
    assert manifest["network_names"] == [
        "plate_pose",
        "plate_ocr",
        "plate_color",
        "plate_type",
    ]
    assert guard.released is True
    assert writer.closed is True
    assert quantize_writer.closed is True
    assert edge_validation_calls == [(9, {"yolo_train_job_id": 42, "imgsz": 640})]
    assert quality_report_calls == [(9, {"run_onnx_val": False})]


def test_pose_input_size_uses_component_artifact_instead_of_legacy_job_size():
    job = SimpleNamespace(imgsz=320)

    assert service._pose_input_size(job, {"pose": {"input_size": [640, 640]}}) == 640
    assert service._pose_input_size(job, {"pose": {}}) == 320
