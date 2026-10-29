import importlib.util
import json

from pathlib import Path
import sys
from types import SimpleNamespace

import cv2
import numpy as np


def _load_sophon_eval():
    path = Path("labelu/scripts/sop_eval/sophon_bmodel_evaluation.py").resolve()
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("sophon_bmodel_evaluation_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_edge_runner():
    path = Path("labelu/scripts/edge_board_validate_runner.py").resolve()
    spec = importlib.util.spec_from_file_location(
        "edge_board_validate_runner_test", path
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_find_fresh_bmcv_result_json_ignores_stale_files(tmp_path):
    mod = _load_sophon_eval()
    demo = tmp_path / "demo"
    results = demo / "results"
    results.mkdir(parents=True)

    stale = results / "yolov11n_int8_1b.bmodel_test_bmcv_python_result.json"
    stale.write_text(json.dumps([{"image_name": "stale.jpg", "bboxes": []}]), encoding="utf-8")

    import os
    import time

    now = time.time()
    os.utime(stale, (now - 120, now - 120))

    fresh = results / "yolov11n_int8_1b.bmodel_images_bmcv_python_result.json"
    fresh.write_text(json.dumps([{"image_name": "fresh.jpg", "bboxes": []}]), encoding="utf-8")
    os.utime(fresh, (now + 2, now + 2))

    picked = mod._find_fresh_bmcv_result_json(
        demo, Path("yolov11n_int8_1b.bmodel"), started_at=now
    )
    assert picked == fresh


def test_cleanup_stale_bmcv_outputs_removes_old_json_and_images(tmp_path):
    mod = _load_sophon_eval()
    demo = tmp_path / "demo"
    results = demo / "results"
    images = results / "images"
    images.mkdir(parents=True)
    (results / "yolov11n_int8_1b.bmodel_test_bmcv_python_result.json").write_text("[]", encoding="utf-8")
    (images / "preview.jpg").write_bytes(b"x")

    mod._cleanup_stale_bmcv_outputs(demo, "yolov11n_int8_1b.bmodel")

    assert not (results / "yolov11n_int8_1b.bmodel_test_bmcv_python_result.json").exists()
    assert not images.exists()


def test_business_threshold_analysis_returns_strict_t1_t2_t3():
    mod = _load_sophon_eval()
    Detection = mod.Detection
    targets = [
        Detection(0, 0, 10, 10, 0, 1.0),
        Detection(20, 20, 30, 30, 0, 1.0),
    ]
    predictions = [
        Detection(0, 0, 10, 10, 0, 0.95),
        Detection(40, 40, 50, 50, 0, 0.90),
        Detection(20, 20, 30, 30, 0, 0.60),
    ]

    analysis = mod.build_business_threshold_analysis(
        [(predictions, targets)],
        ["electric_bike"],
        iou_threshold=0.5,
        min_conf=0.21,
        max_conf=0.99,
        step=0.01,
    )

    assert analysis["status"] == "ok"
    assert analysis["recommended_thresholds"] == {
        "edge_low_threshold": 0.59,
        "edge_high_threshold": 0.95,
        "flink_filter_threshold": 0.6,
    }
    selected = analysis["per_class"][0]
    assert selected["conf_threshold_flink_pass"] > selected["conf_threshold_alert_f1"]
    assert selected["conf_threshold_alert_f1"] > selected["conf_threshold_gray_recall"]
    assert selected["conf_threshold_gray_recall"] > 0.2
    assert selected["t1_precision_target_met"] is True


def test_edge_runtime_dependencies_fall_back_to_bundle_wheel(tmp_path, monkeypatch):
    mod = _load_edge_runner()
    (tmp_path / "edge_requirements.txt").write_text(
        "tqdm==4.64.1\n", encoding="utf-8"
    )
    wheel_dir = tmp_path / "edge_wheels"
    wheel_dir.mkdir()
    (wheel_dir / "tqdm-4.64.1-py2.py3-none-any.whl").write_bytes(b"wheel")

    available = iter((False, True))
    calls = []
    monkeypatch.setattr(mod, "_module_available", lambda name: next(available))

    def fake_pip(args, timeout_sec=300):
        calls.append(args)
        return (1, "online failed") if len(calls) == 1 else (0, "offline ok")

    monkeypatch.setattr(mod, "_run_pip_install", fake_pip)
    result = mod._ensure_edge_runtime_dependencies(tmp_path)

    assert result["status"] == "ok"
    assert result["required"][0]["status"] == "installed_offline"
    assert calls[0][0:2] == ["install", "--disable-pip-version-check"]
    assert "--no-index" in calls[1]
    assert (tmp_path / "output" / "dependency_setup.json").is_file()


def test_bmodel_eval_output_publishes_computed_map50_95(tmp_path, monkeypatch):
    mod = _load_sophon_eval()
    monkeypatch.setattr(mod, "EVAL_SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "plot_f1_summary_curve", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "plot_scene_metrics", lambda *args, **kwargs: {})
    monkeypatch.setattr(mod, "plot_size_metrics", lambda *args, **kwargs: None)

    report_path = tmp_path / "evaluation_report.md"

    def fake_report(*args, **kwargs):
        report_path.write_text("report", encoding="utf-8")
        return str(report_path)

    monkeypatch.setattr(mod, "write_markdown_report", fake_report)
    summary = {
        "class_names": ["electric_bike"],
        "per_class_rows": [],
        "map50": 0.71,
        "map5095": 0.43,
        "map5095_status": "computed",
        "overall_precision": 0.75,
        "overall_recall": 0.68,
        "overall_f1": 0.71,
        "speed_avg_ms": 12.0,
    }

    mod._write_outputs(
        summary,
        10,
        {},
        {},
        backend="bmcv",
        bmodel=tmp_path / "model.bmodel",
    )

    payload = json.loads((tmp_path / "eval_summary.json").read_text(encoding="utf-8"))
    assert payload["map50_95"] == 0.43
    assert payload["map50_95_status"] == "computed"
    assert "map50_95_approximation" not in payload


def test_bmodel_eval_output_preserves_performance_probe_summary(tmp_path, monkeypatch):
    mod = _load_sophon_eval()
    monkeypatch.setattr(mod, "EVAL_SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "plot_f1_summary_curve", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "plot_scene_metrics", lambda *args, **kwargs: {})
    monkeypatch.setattr(mod, "plot_size_metrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mod,
        "write_markdown_report",
        lambda *args, **kwargs: str(tmp_path / "evaluation_report.md"),
    )
    summary = {
        "class_names": ["car"],
        "per_class_rows": [],
        "map50": 0.8,
        "map5095": 0.8,
        "overall_precision": 0.81,
        "overall_recall": 0.79,
        "overall_f1": 0.8,
    }
    performance = {
        "status": "ok",
        "runtime_metrics": {
            "warmup_count_completed": 50,
            "repeat_count_completed": 1000,
            "p99_inference_ms": 9.8,
        },
        "resource_usage": {"sample_count": 20, "series_available": True},
        "environment": {"device_serial": "BM1688-1"},
        "stability": {"enabled": False},
        "performance_artifacts": {"latency_samples": "performance/latency_samples.jsonl.gz"},
    }

    mod._write_outputs(
        summary,
        10,
        {},
        {},
        backend="sophon.sail",
        bmodel=tmp_path / "model.bmodel",
        performance=performance,
    )

    payload = json.loads((tmp_path / "eval_summary.json").read_text(encoding="utf-8"))
    assert payload["performance_probe_status"] == "ok"
    assert payload["runtime_metrics"]["p99_inference_ms"] == 9.8
    assert payload["environment"]["device_serial"] == "BM1688-1"


def test_runner_passes_performance_settings_and_limits_stability_to_int8(
    tmp_path, monkeypatch
):
    mod = _load_edge_runner()
    scripts = tmp_path / "scripts" / "sop_eval"
    scripts.mkdir(parents=True)
    (scripts / "sophon_bmodel_evaluation.py").write_text("", encoding="utf-8")
    (tmp_path / "eval_data" / "images" / "test").mkdir(parents=True)
    model_dir = tmp_path / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    model = model_dir / "model_fp16_1b.bmodel"
    model.write_bytes(b"bmodel")
    captured = {}

    def fake_run(cmd, cwd, env, timeout_sec):
        captured.update(env)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_subprocess_run_compat", fake_run)
    mod._run_sophon_bmodel_eval(
        tmp_path,
        {
            "performance_warmup_count": 7,
            "performance_repeat_count": 11,
            "performance_resource_interval_seconds": 2.5,
            "performance_stability_seconds": 60,
        },
        model_file=model,
        model_tag="fp16",
    )

    assert captured["EVAL_PERFORMANCE_WARMUP_COUNT"] == "7"
    assert captured["EVAL_PERFORMANCE_REPEAT_COUNT"] == "11"
    assert captured["EVAL_RESOURCE_INTERVAL_SECONDS"] == "2.5"
    assert captured["EVAL_STABILITY_SECONDS"] == "0"


def test_evaluate_loop_renders_detected_and_no_detection_images(tmp_path, monkeypatch):
    mod = _load_sophon_eval()
    monkeypatch.setattr(mod, "EVAL_SAVE_DIR", str(tmp_path / "results"))
    (tmp_path / "results").mkdir()
    image_a = tmp_path / "detected.jpg"
    image_b = tmp_path / "empty.jpg"
    blank = np.full((80, 120, 3), 240, dtype=np.uint8)
    assert cv2.imwrite(str(image_a), blank)
    assert cv2.imwrite(str(image_b), blank)
    samples = [
        SimpleNamespace(
            image_path=str(image_a), label_path=None, xml_label_path=None, scene_attrs={}
        ),
        SimpleNamespace(
            image_path=str(image_b), label_path=None, xml_label_path=None, scene_attrs={}
        ),
    ]
    predictions = {
        image_a.name: [mod.Detection(10, 10, 60, 50, 0, 0.91)],
        image_b.name: [],
    }
    monkeypatch.setattr(mod, "load_yolo_labels", lambda *_args, **_kwargs: [])

    def grouped(predictions_arg, *_args, **_kwargs):
        stats = mod._empty_stats()
        stats["fp"] = len(predictions_arg)
        return {
            "per_class": {0: stats},
            "scene": {},
            "size": {
                item[0]: {0: mod._empty_stats()} for item in mod.SIZE_BINS
            },
        }

    monkeypatch.setattr(mod, "match_detections_grouped", grouped)
    monkeypatch.setattr(
        mod,
        "summarize_metrics",
        lambda **_kwargs: {
            "map50": 0.0,
            "per_class_rows": [],
            "overall_precision": 0.0,
            "overall_recall": 0.0,
            "overall_f1": 0.0,
        },
    )
    monkeypatch.setattr(mod, "summarize_size_metrics", lambda *_args: {})
    monkeypatch.setattr(mod, "build_business_threshold_analysis", lambda *_args, **_kwargs: {})

    _summary, evaluated, _scene, _size = mod._evaluate_loop(
        samples=samples,
        class_names=["bike"],
        class_count=1,
        backend="test",
        bmodel=tmp_path / "model.bmodel",
        pred_by_basename=predictions,
    )

    assert evaluated == 2
    assert (tmp_path / "results" / "images" / image_a.name).is_file()
    assert (tmp_path / "results" / "images" / image_b.name).is_file()
    metadata = json.loads(
        (tmp_path / "results" / "visualization_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["image_count"] == 2
    assert metadata["with_boxes"] == 1
    assert metadata["total_boxes"] == 1
    assert metadata["unlabeled_images"] == 2
    assert metadata["false_positive_images"] == 1
    assert metadata["true_negative_images"] == 1
    assert metadata["fp"] == 1
    assert metadata["fn"] == 0
    assert [row["detection_state"] for row in metadata["images"]] == [
        "detected",
        "no_detection",
    ]
    assert [row["status"] for row in metadata["images"]] == [
        "false_positive",
        "true_negative",
    ]


def test_runner_normalizes_all_rendered_images_to_bmcv_preview(tmp_path):
    mod = _load_edge_runner()
    images = tmp_path / "output" / "results_bmodel" / "images"
    images.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        image = np.full((1200, 1600, 3), 220, dtype=np.uint8)
        assert cv2.imwrite(str(images / name), image)
    (images.parent / "visualization_summary.json").write_text(
        json.dumps(
            {
                "image_count": 3,
                "with_boxes": 2,
                "total_boxes": 4,
                "images": [],
            }
        ),
        encoding="utf-8",
    )

    result = mod._normalize_bmodel_visualizations(tmp_path)

    assert result["preview"]["preview_files"] == ["a.jpg", "b.jpg", "c.jpg"]
    assert result["preview"]["viz_image_count"] == 3
    assert result["detection_summary"]["with_boxes"] == 2
    assert len(list((tmp_path / "output" / "bmcv_preview").iterdir())) == 3
    preview = cv2.imread(str(tmp_path / "output" / "bmcv_preview" / "a.jpg"))
    assert max(preview.shape[:2]) == 960
    assert (tmp_path / "output" / "bmcv_preview" / "a.jpg").stat().st_size < (
        images / "a.jpg"
    ).stat().st_size
