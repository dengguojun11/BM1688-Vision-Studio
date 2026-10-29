import importlib.util
from pathlib import Path


def _load_utils():
    path = Path("labelu/scripts/sop_eval/evaluation_utils.py").resolve()
    spec = importlib.util.spec_from_file_location("evaluation_utils_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_compute_map50_95_averages_all_ten_iou_thresholds():
    module = _load_utils()
    target = module.Detection(0, 0, 10, 10, 0, 1.0)
    prediction = module.Detection(0, 0, 6, 10, 0, 0.9)

    result = module.compute_map50_95([([prediction], [target])], class_count=1)

    assert result["iou_thresholds"] == [
        0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95
    ]
    assert round(result["map50_95"], 4) == 0.3
    assert round(result["per_class"][0], 4) == 0.3


def test_summarize_metrics_keeps_map50_gate_and_adds_real_map50_95():
    module = _load_utils()
    target = module.Detection(0, 0, 10, 10, 0, 1.0)
    prediction = module.Detection(0, 0, 6, 10, 0, 0.9)
    matched_at_50 = module.match_detections([prediction], [target], 1, 0.5)

    summary = module.summarize_metrics(
        ["object"],
        matched_at_50,
        [1],
        total_images=1,
        total_time_seconds=0.1,
        evaluation_samples=[([prediction], [target])],
    )

    assert summary["map50"] == 1.0
    assert summary["map5095"] == 0.3
    assert summary["map5095_status"] == "computed"
    assert summary["metric_validity"]["map50_95"]["status"] == "computed"
