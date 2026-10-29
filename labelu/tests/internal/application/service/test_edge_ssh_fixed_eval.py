import hashlib
import json
from pathlib import Path

from labelu.internal.application.service.edge_ssh_validation import (
    _copy_board_eval_dataset,
)
from labelu.internal.common.fixed_eval_signature import fixed_eval_content_signature


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_ssh_dataset_copy_reuses_board_agent_images_and_labels(tmp_path: Path) -> None:
    board_eval = tmp_path / "board_eval"
    (board_eval / "images").mkdir(parents=True)
    (board_eval / "labels").mkdir(parents=True)
    image = b"same-image"
    label = b"0 0.5 0.5 0.4 0.2\n"
    (board_eval / "images" / "0000.jpg").write_bytes(image)
    (board_eval / "labels" / "0000.txt").write_bytes(label)
    manifest = {
        "schema_version": "fixed_eval.v1",
        "image_count": 1,
        "labeled_image_count": 1,
        "class_names": ["车牌"],
        "files": [
            {"path": "images/0000.jpg", "size": len(image), "sha256": _sha(image)},
            {"path": "labels/0000.txt", "size": len(label), "sha256": _sha(label)},
        ],
    }
    manifest["fixed_eval_signature"] = fixed_eval_content_signature(manifest)
    (board_eval / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    destination = tmp_path / "eval_data"
    source_manifest = _copy_board_eval_dataset(board_eval, destination)

    assert source_manifest is not None
    assert source_manifest["fixed_eval_signature"] == manifest["fixed_eval_signature"]
    assert (destination / "images" / "test" / "0000.jpg").read_bytes() == image
    assert (destination / "labels" / "test" / "0000.txt").read_bytes() == label


def test_ssh_dataset_copy_rejects_non_fixed_eval_manifest(tmp_path: Path) -> None:
    board_eval = tmp_path / "board_eval"
    (board_eval / "images").mkdir(parents=True)
    (board_eval / "manifest.json").write_text(
        json.dumps({"schema_version": "legacy.v0"}), encoding="utf-8"
    )

    assert _copy_board_eval_dataset(board_eval, tmp_path / "eval_data") is None
