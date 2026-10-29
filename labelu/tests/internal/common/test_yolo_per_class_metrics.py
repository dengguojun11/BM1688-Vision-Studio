"""各类别训练指标解析。"""
import json

from labelu.internal.common.yolo_training_metrics import (
    build_class_metrics_detail,
    read_classes_from_metrics_summary,
    read_per_class_metrics_from_summary,
)
from labelu.internal.common.yolo_ultralytics_split_val import (
    extract_per_class_box_metrics,
    read_class_names_from_data_yaml,
)


class _FakeBox:
    def __init__(self):
        self.nc = 2
        self.ap_class_index = [0, 1]
        self.ap = [0.55, 0.66]
        self.p = [0.81, 0.72]
        self.r = [0.79, 0.68]
        self.ap50 = [0.88, 0.77]

    @property
    def maps(self):
        return [0.55, 0.66]

    def class_result(self, i):
        return self.p[i], self.r[i], self.ap50[i], self.ap[i]


class _FakeMetrics:
    box = None

    def __init__(self):
        self.box = _FakeBox()


def test_extract_per_class_box_metrics_order():
    rows = extract_per_class_box_metrics(_FakeMetrics(), ["person", "garbage"])
    assert len(rows) == 2
    assert rows[0]["label"] == "person"
    assert rows[0]["index"] == 0
    assert abs(rows[0]["map50"] - 0.88) < 1e-6
    assert abs(rows[1]["map50_95"] - 0.66) < 1e-6


def test_extract_per_class_box_metrics_keeps_zero_instance_class_empty():
    metrics = _FakeMetrics()
    metrics.box.ap_class_index = [0, 2]
    rows = extract_per_class_box_metrics(
        metrics,
        ["garbage_street_dump", "non_garbage", "garbage_bulky"],
    )

    assert rows[0]["map50"] == 0.88
    assert rows[1] == {"index": 1, "label": "non_garbage"}
    assert rows[2]["map50"] == 0.77
    assert rows[2]["map50_95"] == 0.66


def test_build_class_metrics_detail_fills_missing_classes():
    raw = json.dumps(
        {
            "classes": ["a", "b", "c"],
            "class_instance_counts": [
                {"index": 0, "label": "a", "train_instances": 10, "val_instances": 2, "test_instances": 1},
                {"index": 1, "label": "b", "train_instances": 0, "val_instances": 0, "test_instances": 0},
                {"index": 2, "label": "c", "train_instances": 5, "val_instances": 1, "test_instances": 0},
            ],
            "per_class_val_metrics": [
                {"index": 0, "label": "a", "map50": 0.9, "map50_95": 0.5, "precision": 0.8, "recall": 0.7},
                {"index": 2, "label": "c", "map50": 0.6, "map50_95": 0.3, "precision": 0.5, "recall": 0.4},
            ],
            "test_val_metrics": {
                "status": "ok",
                "per_class_metrics": [
                    {"index": 1, "label": "b", "map50": 0.75, "map50_95": 0.4, "precision": 0.6, "recall": 0.55},
                ],
            },
        },
        ensure_ascii=False,
    )
    detail = build_class_metrics_detail(raw)
    assert detail is not None
    assert detail["classes"] == ["a", "b", "c"]
    assert detail["val_metrics"][1]["label"] == "b"
    assert detail["val_metrics"][1]["map50"] is None
    assert detail["val_metrics"][1]["train_instances"] == 0
    assert detail["rows"][0]["train_instances"] == 10
    assert detail["rows"][1]["test_map50"] == 0.75


def test_merge_val_per_class_into_metrics_summary():
    from labelu.internal.common.yolo_ultralytics_split_val import (
        merge_val_per_class_into_metrics_summary,
    )

    raw = '{"final_metrics": {"map50": 0.9}}'
    val = {
        "status": "ok",
        "classes": ["a", "b"],
        "per_class_metrics": [
            {"index": 0, "label": "a", "map50": 0.8, "map50_95": 0.5, "precision": 0.7, "recall": 0.6},
        ],
    }
    merged = json.loads(merge_val_per_class_into_metrics_summary(raw, val))
    assert merged["classes"] == ["a", "b"]
    assert len(merged["per_class_val_metrics"]) == 1


def test_read_classes_and_per_class_from_summary():
    raw = '{"classes":["x","y"],"per_class_val_metrics":[{"index":0,"label":"x","map50":0.1}]}'
    assert read_classes_from_metrics_summary(raw) == ["x", "y"]
    assert len(read_per_class_metrics_from_summary(raw, split="val")) == 1


def test_read_class_names_from_data_yaml(tmp_path):
    p = tmp_path / "data.yaml"
    p.write_text(
        "names:\n  0: cat\n  1: dog\n",
        encoding="utf-8",
    )
    assert read_class_names_from_data_yaml(p) == ["cat", "dog"]
