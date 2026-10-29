"""YOLO 数据集各类别框数统计。"""
from labelu.internal.application.service.yolo_dataset import count_class_instances_in_dataset


def test_count_class_instances_in_dataset(tmp_path):
    root = tmp_path / "dataset"
    classes = ["cat", "dog"]
    for split, lines in (
        ("train", ["0 0.5 0.5 0.1 0.1", "1 0.2 0.2 0.1 0.1", "0 0.3 0.3 0.1 0.1"]),
        ("val", ["1 0.5 0.5 0.1 0.1"]),
        ("test", []),
    ):
        d = root / "labels" / split
        d.mkdir(parents=True)
        if lines:
            (d / "a.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows = count_class_instances_in_dataset(root, classes)
    assert len(rows) == 2
    assert rows[0]["label"] == "cat"
    assert rows[0]["train_instances"] == 2
    assert rows[0]["val_instances"] == 0
    assert rows[0]["test_instances"] == 0
    assert rows[1]["train_instances"] == 1
    assert rows[1]["val_instances"] == 1
