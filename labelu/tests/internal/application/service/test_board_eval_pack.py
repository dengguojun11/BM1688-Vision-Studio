import json
from pathlib import Path

from labelu.internal.application.service import board_eval_pack
from labelu.internal.common.fixed_eval_signature import fixed_eval_content_signature


def test_safe_board_eval_filename_keeps_ascii_and_extension() -> None:
    src = Path(r"D:\tmp\16382_中文 空格-2026-05-22T112441.850.png")
    packed = board_eval_pack._safe_board_eval_filename(7, src)
    assert packed == "0007_16382__-2026-05-22T112441.850.png"


def test_pack_board_eval_manifest_includes_original_name_mapping(
    db, tmp_path, monkeypatch
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    source = src_dir / "16382_中文 空格-2026-05-22T112441.850.png"
    source.write_bytes(b"fake-image")

    export_root = tmp_path / "export-43"
    monkeypatch.setattr(board_eval_pack, "_export_dir_for_row", lambda export_id: export_root)
    monkeypatch.setattr(board_eval_pack, "_collect_from_train_dataset", lambda job_id, limit: [source])
    monkeypatch.setattr(board_eval_pack, "_collect_from_task_samples", lambda db, task_id, limit: [])

    eval_root = board_eval_pack.pack_board_eval_images_for_export(
        db,
        export_id=43,
        task_id=14,
        yolo_train_job_id=166,
        imgsz=1280,
        task_config_json='{"tools":[{"tool":"rectTool","config":{"attributes":[{"key":"RT","value":"RT"}]}}]}',
    )

    packed_files = sorted((eval_root / "images").iterdir())
    assert len(packed_files) == 1
    assert packed_files[0].name == "0000_16382__-2026-05-22T112441.850.png"

    manifest = json.loads((eval_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["image_count"] == 1
    assert manifest["file_mappings"] == [
        {
            "packed_name": "0000_16382__-2026-05-22T112441.850.png",
            "original_name": "16382_中文 空格-2026-05-22T112441.850.png",
            "source_path": str(source),
        }
    ]
    assert len(manifest["fixed_eval_signature"]) == 64


def test_fixed_eval_signature_ignores_transport_layout_but_binds_content() -> None:
    image_sha = "a" * 64
    label_sha = "b" * 64
    board_manifest = {
        "image_count": 1,
        "labeled_image_count": 1,
        "class_names": ["车牌"],
        "files": [
            {"path": "images/0000_plate.jpg", "size": 10, "sha256": image_sha},
            {"path": "labels/0000_plate.txt", "size": 12, "sha256": label_sha},
        ],
    }
    ssh_manifest = {
        "image_count": 1,
        "labeled_image_count": 1,
        "class_names": ["车牌"],
        "files": [
            {"path": "images/test/0000_plate.jpg", "size": 10, "sha256": image_sha},
            {"path": "labels/test/0000_plate.txt", "size": 12, "sha256": label_sha},
        ],
    }

    assert fixed_eval_content_signature(board_manifest) == fixed_eval_content_signature(
        ssh_manifest
    )
    changed_label = dict(ssh_manifest)
    changed_label["files"] = [
        *ssh_manifest["files"][:-1],
        {"path": "labels/test/0000_plate.txt", "size": 13, "sha256": label_sha},
    ]
    assert fixed_eval_content_signature(board_manifest) != fixed_eval_content_signature(
        changed_label
    )


def test_pack_board_eval_marks_each_label_mapping_as_annotation(tmp_path, monkeypatch) -> None:
    source_root = tmp_path / "dataset" / "images" / "val"
    labels_root = tmp_path / "dataset" / "labels" / "val"
    source_root.mkdir(parents=True)
    labels_root.mkdir(parents=True)
    source = source_root / "sample.jpg"
    source.write_bytes(b"fake-image")
    (labels_root / "sample.txt").write_text("0 0.5 0.5 0.4 0.2\n", encoding="utf-8")

    export_root = tmp_path / "export"
    monkeypatch.setattr(board_eval_pack, "_export_dir_for_row", lambda _id: export_root)
    monkeypatch.setattr(board_eval_pack, "_collect_from_train_dataset", lambda _job, _limit: [source])
    monkeypatch.setattr(board_eval_pack, "_collect_from_task_samples", lambda _db, **_kwargs: [])

    eval_root = board_eval_pack.pack_board_eval_images_for_export(
        object(),
        export_id=1,
        task_id=2,
        yolo_train_job_id=3,
        imgsz=640,
        task_config_json='{"tools": [{"tool": "rectTool", "config": {"attributes": [{"key": "car", "value": "car"}]}}]}',
    )

    manifest = json.loads((eval_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["labeled_image_count"] == 1
    assert len(manifest["label_mappings"]) == 1
    assert manifest["label_mappings"][0]["has_annotation"] is True
    assert manifest["label_mappings"][0]["has_annotation_file"] is True
