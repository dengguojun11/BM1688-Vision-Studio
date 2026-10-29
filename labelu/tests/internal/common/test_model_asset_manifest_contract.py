from labelu.internal.common.model_asset_manifest import build_dataset_manifest


def _write_dataset(root):
    (root / "images" / "train").mkdir(parents=True)
    (root / "labels" / "train").mkdir(parents=True)
    (root / "images" / "train" / "sample.jpg").write_bytes(b"same-image")
    (root / "labels" / "train" / "sample.txt").write_text(
        "0 0.5 0.5 0.2 0.2\n", encoding="utf-8"
    )
    (root / "data.yaml").write_text(
        f"path: {root.as_posix()}\ntrain: images/train\nval: images/train\nnames:\n  0: person\n",
        encoding="utf-8",
    )


def test_dataset_content_signature_is_stable_across_build_directories(tmp_path):
    first = tmp_path / "first-build"
    second = tmp_path / "second-build"
    _write_dataset(first)
    _write_dataset(second)

    kwargs = {
        "source_task_ids": [9],
        "split_counts": {"train": 1, "val": 0, "test": 0},
        "class_names": ["person"],
    }
    first_manifest = build_dataset_manifest(first, **kwargs)
    second_manifest = build_dataset_manifest(second, **kwargs)

    assert (
        first_manifest["dataset_content_signature"]
        == second_manifest["dataset_content_signature"]
    )
    assert first_manifest["files"] != second_manifest["files"]
