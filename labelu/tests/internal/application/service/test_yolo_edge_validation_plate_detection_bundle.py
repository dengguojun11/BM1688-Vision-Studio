import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from labelu.internal.application.service import board_agent
from labelu.internal.application.service import edge_ssh_validation
from labelu.internal.application.service import yolo_edge_validation as service
from labelu.internal.common import edge_device_registry


@pytest.mark.parametrize(
    ("execution_profile", "performance_enabled", "performance_model_tags"),
    [
        ("quick", False, []),
        ("certification", True, ["fp32", "fp16", "int8"]),
    ],
)
def test_manual_bundle_includes_plate_detection_contract_and_runner(
    monkeypatch,
    tmp_path,
    execution_profile,
    performance_enabled,
    performance_model_tags,
):
    export_root = tmp_path / "export"
    export_root.mkdir()
    (export_root / "plate_detection_contract.json").write_text(
        json.dumps(
            {
                "format": "labelu_plate_detection_bm1688_v1",
                "job_type": "plate_detection",
                "bmodel": "plate_detection_fp16_1b.bmodel",
            }
        ),
        encoding="utf-8",
    )

    models_zip = export_root / "board_models.zip"
    with zipfile.ZipFile(models_zip, "w") as archive:
        archive.writestr("models/BM1688/plate_detection_fp16_1b.bmodel", b"bmodel")

    job_root = tmp_path / "job"
    (job_root / "eval_data" / "images" / "test").mkdir(parents=True)
    (job_root / "eval_data" / "images" / "test" / "sample.jpg").write_bytes(b"image")
    (job_root / "manifest.json").write_text(
        json.dumps({"class_names": ["doubleEP"]}), encoding="utf-8"
    )

    monkeypatch.setattr(service, "edge_validation_job_root", lambda **kwargs: job_root)
    monkeypatch.setattr(service, "_export_dir_for_row", lambda export_id: export_root)
    monkeypatch.setattr(board_agent, "build_models_zip_path", lambda *args: models_zip)
    monkeypatch.setattr(
        board_agent,
        "build_board_threshold_contract",
        lambda *args, **kwargs: {
            "schema_version": "v1",
            "threshold_unit": "ratio_0_1",
            "source": "system_default",
            "defaulted_fields": [],
            "conf_thres": 0.35,
            "nms_thres": 0.7,
            "match_iou_thres": 0.5,
        },
    )
    monkeypatch.setattr(
        edge_device_registry,
        "load_device_config",
        lambda device_id: {"run_coco_eval": False, "validation_timeout_sec": 7200},
    )
    monkeypatch.setattr(
        edge_ssh_validation,
        "_prepare_edge_runtime_dependency_bundle",
        lambda staging: None,
    )

    row = SimpleNamespace(
        edge_device_id="edge-test",
        mode="run_and_eval",
        result_json=json.dumps(
            {
                "pack": {"copied_count": 1, "labeled_count": 1},
                "request": {"execution_profile": execution_profile},
            }
        ),
    )
    export_row = SimpleNamespace(id=148, imgsz=640)

    _, staging = service._build_manual_edge_validation_bundle(
        object(),
        job_id=150,
        export_row=export_row,
        row=row,
        thresholds={},
    )

    model_dir = staging / "models" / "BM1688"
    validation_dir = staging / "scripts" / "plate_validation"
    assert (model_dir / "plate_detection_contract.json").is_file()
    assert (validation_dir / "validate.py").is_file()
    assert (validation_dir / "plate_detection_bm1688_infer.py").is_file()
    assert (validation_dir / "plate_recognition_bm1688_infer.py").is_file()
    run_config = json.loads((staging / "run_config.json").read_text(encoding="utf-8"))
    assert run_config["execution_profile"] == execution_profile
    assert run_config["performance_enabled"] is performance_enabled
    assert run_config["performance_model_tags"] == performance_model_tags
