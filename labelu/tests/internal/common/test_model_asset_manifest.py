from datetime import datetime

from labelu.internal.common.model_asset_manifest import (
    build_dataset_manifest,
    build_export_artifact_manifest,
)
from labelu.internal.common.bm1688_compile_contract import build_bm1688_profile_snapshot


def test_dataset_manifest_contains_split_source_and_file_hash(tmp_path):
    dataset_root = tmp_path / "dataset"
    image = dataset_root / "images" / "train" / "sample.jpg"
    label = dataset_root / "labels" / "train" / "sample.txt"
    image.parent.mkdir(parents=True)
    label.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    manifest = build_dataset_manifest(
        dataset_root,
        source_task_ids=[3, 2, 3],
        split_counts={"train": 1, "val": 0, "test": 0},
        class_names=["bike"],
        per_task_counts={2: 1, 3: 1},
    )

    assert manifest["source_task_ids"] == [2, 3]
    assert manifest["split_counts"]["train"] == 1
    assert manifest["file_count"] == 2
    assert any(item["relative_path"] == "images/train/sample.jpg" for item in manifest["files"])
    assert manifest["content_sha256"]


def test_export_manifest_tracks_artifacts_and_calibration(tmp_path):
    export_root = tmp_path / "export"
    export_root.mkdir()
    pt = tmp_path / "weights" / "best.pt"
    pt.parent.mkdir()
    pt.write_bytes(b"pt")
    onnx = export_root / "models" / "onnx" / "model.onnx"
    bmodel = export_root / "models" / "BM1688" / "model_int8_1b.bmodel"
    onnx.parent.mkdir(parents=True)
    bmodel.parent.mkdir(parents=True)
    onnx.write_bytes(b"onnx")
    bmodel.write_bytes(b"bmodel")
    cali = tmp_path / "calibration"
    cali.mkdir()
    (cali / "one.jpg").write_bytes(b"cali")

    manifest = build_export_artifact_manifest(
        export_root,
        train_job_id=10,
        export_id=11,
        source_dataset_manifest={"content_sha256": "dataset"},
        pt_path=pt,
        onnx_path=onnx,
        bmodel_paths=[bmodel],
        calibration_dir=cali,
        compiler_config={"tpu_mlir_root": "/opt/tpu-mlir", "quantize": "full"},
        compile_log="compile ok",
        status="COMPLETED",
    )

    roles = {item["role"] for item in manifest["artifacts"]}
    assert roles == {"weights_pt", "model_onnx", "model_bmodel"}
    assert manifest["calibration"]["file_count"] == 1
    assert manifest["source_dataset"]["content_sha256"] == "dataset"
    assert manifest["content_sha256"]
    assert manifest["execution_signature"]
    assert manifest["evidence_manifest_sha256"] == manifest["content_sha256"]


def test_export_manifest_preserves_effective_onnx_export_contract(tmp_path):
    export_root = tmp_path / "export"
    export_root.mkdir()
    pt = tmp_path / "best.pt"
    pt.write_bytes(b"pt")
    onnx = export_root / "model.onnx"
    bmodel = export_root / "model_fp32.bmodel"
    onnx.write_bytes(b"onnx")
    bmodel.write_bytes(b"bmodel")

    manifest = build_export_artifact_manifest(
        export_root,
        train_job_id=10,
        export_id=11,
        source_dataset_manifest={"dataset_content_signature": "dataset"},
        pt_path=pt,
        onnx_path=onnx,
        bmodel_paths=[bmodel],
        calibration_dir=None,
        compiler_config={
            "compiler_version": "tpu-mlir-1",
            "onnx_input_imgsz": 640,
            "onnx_opset": 12,
            "onnx_dynamic": False,
            "onnx_simplify": True,
            "onnx_export_backend": "sophon",
        },
        compile_log="compile ok",
        status="COMPLETED",
    )

    assert manifest["compiler"]["config"]["onnx_input_imgsz"] == 640
    assert manifest["compiler"]["config"]["onnx_opset"] == 12
    assert manifest["compiler"]["config"]["onnx_dynamic"] is False
    assert manifest["compiler"]["config"]["onnx_simplify"] is True
    assert manifest["compiler"]["config"]["onnx_export_backend"] == "sophon"


def test_export_execution_signature_is_stable_across_roots_and_time(tmp_path, monkeypatch):
    def build(root, *, created_at):
        export_root = root / "export"
        export_root.mkdir(parents=True)
        pt = root / "weights" / "best.pt"
        pt.parent.mkdir()
        pt.write_bytes(b"pt")
        onnx = export_root / "models" / "model.onnx"
        bmodel = export_root / "models" / "model_int8.bmodel"
        onnx.parent.mkdir(parents=True)
        onnx.write_bytes(b"onnx")
        bmodel.write_bytes(b"bmodel")
        monkeypatch.setattr(
            "labelu.internal.common.model_asset_manifest.datetime",
            type("FixedDateTime", (), {"utcnow": staticmethod(lambda: created_at)}),
        )
        return build_export_artifact_manifest(
            export_root,
            train_job_id=10,
            export_id=11,
            source_dataset_manifest={"dataset_content_signature": "dataset"},
            pt_path=pt,
            onnx_path=onnx,
            bmodel_paths=[bmodel],
            calibration_dir=None,
            compiler_config={
                "compiler_version": "tpu-mlir-1",
                "tpu_mlir_root": str(root / "toolchain"),
                "quantize": "full",
            },
            compile_log="compile ok",
            status="COMPLETED",
            class_names=["plate"],
            label_set_signature="labels",
            class_order_signature="order",
            dataset_content_signature="dataset",
        )

    first = build(tmp_path / "first", created_at=datetime(2026, 9, 18, 10, 0, 0))
    second = build(tmp_path / "second", created_at=datetime(2026, 9, 19, 10, 0, 0))
    assert first["execution_signature"] == second["execution_signature"]
    assert first["evidence_manifest_sha256"] != second["evidence_manifest_sha256"]


def test_export_execution_signature_changes_when_artifact_changes(tmp_path):
    export_root = tmp_path / "export"
    export_root.mkdir()
    pt = tmp_path / "best.pt"
    pt.write_bytes(b"pt")
    onnx = export_root / "model.onnx"
    bmodel = export_root / "model_int8.bmodel"
    onnx.write_bytes(b"onnx")
    bmodel.write_bytes(b"bmodel")
    kwargs = dict(
        train_job_id=10,
        export_id=11,
        source_dataset_manifest={"dataset_content_signature": "dataset"},
        pt_path=pt,
        onnx_path=onnx,
        bmodel_paths=[bmodel],
        calibration_dir=None,
        compiler_config={"compiler_version": "tpu-mlir-1"},
        compile_log="compile ok",
        status="COMPLETED",
        class_names=["plate"],
        label_set_signature="labels",
        class_order_signature="order",
        dataset_content_signature="dataset",
    )
    first = build_export_artifact_manifest(export_root, **kwargs)
    bmodel.write_bytes(b"changed")
    second = build_export_artifact_manifest(export_root, **kwargs)
    assert first["execution_signature"] != second["execution_signature"]


def test_export_manifest_embeds_bm1688_profile_snapshot(tmp_path):
    export_root = tmp_path / "export"
    export_root.mkdir()
    pt = tmp_path / "best.pt"
    pt.write_bytes(b"pt")
    onnx = export_root / "model.onnx"
    onnx.write_bytes(b"onnx")
    profile = build_bm1688_profile_snapshot(
        model_stem="yolov11n",
        imgsz=640,
        compiler_config={"compiler_version": "tpu-mlir-test"},
        class_names=["plate"],
    )

    manifest = build_export_artifact_manifest(
        export_root,
        train_job_id=10,
        export_id=11,
        source_dataset_manifest={"dataset_content_signature": "dataset"},
        pt_path=pt,
        onnx_path=onnx,
        bmodel_paths=[],
        calibration_dir=None,
        compiler_config={"compiler_version": "tpu-mlir-test"},
        compile_log="compile ok",
        status="COMPLETED",
        bm1688_profile=profile,
    )

    assert manifest["bm1688_profile"]["profile_id"] == "bm1688-yolov11-detection-v1"
    assert manifest["profile_digest"] == manifest["bm1688_profile"]["profile_digest"]
    assert manifest["compiler"]["profile_digest"] == manifest["profile_digest"]
