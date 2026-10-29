import json
import zipfile
from types import SimpleNamespace

from labelu.internal.application.service import board_agent, board_eval_pack


def test_build_models_zip_reuses_eval_snapshot_and_unchanged_run_config(
    tmp_path, monkeypatch
):
    export_root = tmp_path / "exports" / "42"
    model_dir = export_root / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    (model_dir / "model_int8.bmodel").write_bytes(b"bmodel")

    export_row = SimpleNamespace(
        task_id=7,
        yolo_train_job_id=8,
        imgsz=640,
        validation_thresholds_json=json.dumps(
            {
                "effective": {
                    "conf_thres": 0.31,
                    "nms_thres": 0.66,
                    "match_iou_thres": 0.55,
                },
                "source": "test",
            }
        ),
        threshold_binding_json=None,
    )
    monkeypatch.setattr(board_agent, "_export_dir_for_row", lambda _id: export_root)
    monkeypatch.setattr(
        board_agent.crud_yolo_bm1688_export,
        "get",
        lambda _db, _id: export_row,
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_task.get",
        lambda **_kwargs: SimpleNamespace(config=None),
    )

    pack_calls = []

    def fake_pack(_db, **kwargs):
        pack_calls.append(kwargs)
        board_eval = export_root / "board_eval"
        (board_eval / "images").mkdir(parents=True, exist_ok=True)
        (board_eval / "images" / "0000_eval.jpg").write_bytes(b"image")
        (board_eval / "labels").mkdir(parents=True, exist_ok=True)
        label_bytes = b"0 0.5 0.5 0.5 0.5\n"
        (board_eval / "labels" / "0000_eval.txt").write_bytes(label_bytes)
        import hashlib

        files = [
            {
                "path": "images/0000_eval.jpg",
                "size": 5,
                "sha256": hashlib.sha256(b"image").hexdigest(),
            },
            {
                "path": "labels/0000_eval.txt",
                "size": len(label_bytes),
                "sha256": hashlib.sha256(label_bytes).hexdigest(),
            },
        ]
        manifest = {
            "schema_version": "fixed_eval.v1",
            "image_count": 1,
            "labeled_image_count": 1,
            "label_mappings": [
                {"image": "images/0000_eval.jpg", "path": "labels/0000_eval.txt"}
            ],
            "files": files,
        }
        manifest["manifest_sha256"] = hashlib.sha256(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        (board_eval / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return board_eval

    monkeypatch.setattr(board_eval_pack, "pack_board_eval_images_for_export", fake_pack)

    first_zip = board_agent.build_models_zip_path(42, db=object())
    run_config = export_root / "run_config.json"
    first_config_mtime = run_config.stat().st_mtime_ns
    first_zip_bytes = first_zip.read_bytes()
    with zipfile.ZipFile(first_zip) as archive:
        assert "run_config.json" in archive.namelist()
        assert "manifest.json" in archive.namelist()
        assert "board_eval/labels/0000_eval.txt" in archive.namelist()
    config = json.loads(run_config.read_text(encoding="utf-8"))
    assert config["fixed_eval_signature"]
    assert config["eval_image_count"] == 1
    assert config["labeled_image_count"] == 1
    assert config["require_fixed_eval_binding"] is True

    second_zip = board_agent.build_models_zip_path(42, db=object())
    assert second_zip == first_zip
    assert len(pack_calls) == 1
    assert run_config.stat().st_mtime_ns == first_config_mtime
    assert second_zip.read_bytes() == first_zip_bytes

    export_row.validation_thresholds_json = json.dumps(
        {
            "effective": {
                "conf_thres": 0.42,
                "nms_thres": 0.66,
                "match_iou_thres": 0.55,
            },
            "source": "test-updated",
        }
    )
    updated_zip = board_agent.build_models_zip_path(42, db=object())
    assert len(pack_calls) == 1
    assert updated_zip.read_bytes() != first_zip_bytes
    updated_config = json.loads(run_config.read_text(encoding="utf-8"))
    assert updated_config["conf_thres"] == 0.42
    assert updated_config["threshold_contract"]["source"] == "test-updated"
