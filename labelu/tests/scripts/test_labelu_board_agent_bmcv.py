"""labelu_board_agent bmcv 检测汇总（边端脚本，本机单测）。"""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest
from labelu.internal.common.edge_script_paths import resolve_board_agent_script


_FP16_SOPHON_TIMING_LOG = """\
INFO:root:decode_time(ms): 18.19
INFO:root:preprocess_time(ms): 9.80
INFO:root:inference_time(ms): 19.39
INFO:root:postprocess_time(ms): 6.91
"""

_FP32_SOPHON_TIMING_LOG = """\
INFO:root:decode_time(ms): 17.98
INFO:root:preprocess_time(ms): 10.16
INFO:root:inference_time(ms): 51.40
INFO:root:postprocess_time(ms): 6.70
"""


def _load_board_agent():
    path = resolve_board_agent_script()
    spec = importlib.util.spec_from_file_location("labelu_board_agent_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_parse_infer_stdout_recovers_export_8_sophon_stage_averages():
    mod = _load_board_agent()

    fp16 = mod._parse_infer_stdout(_FP16_SOPHON_TIMING_LOG)
    assert fp16["decode_ms_avg"] == 18.19
    assert fp16["preprocess_ms_avg"] == 9.8
    assert fp16["infer_ms_avg"] == 19.39
    assert fp16["postprocess_ms_avg"] == 6.91
    assert fp16["pipeline_ms_sum"] == 54.29

    fp32 = mod._parse_infer_stdout(_FP32_SOPHON_TIMING_LOG)
    assert fp32["decode_ms_avg"] == 17.98
    assert fp32["preprocess_ms_avg"] == 10.16
    assert fp32["infer_ms_avg"] == 51.4
    assert fp32["postprocess_ms_avg"] == 6.7
    assert fp32["pipeline_ms_sum"] == 86.24


def test_parse_infer_stdout_does_not_sum_incomplete_pipeline():
    mod = _load_board_agent()

    parsed = mod._parse_infer_stdout(
        "decode_time(ms): 18.19\ninference_time(ms): 19.39\n"
    )

    assert parsed["decode_ms_avg"] == 18.19
    assert parsed["infer_ms_avg"] == 19.39
    assert "pipeline_ms_sum" not in parsed


def test_load_threshold_contract_rejects_percent_units_and_values():
    mod = _load_board_agent()

    try:
        mod._load_threshold_contract(
            {
                "threshold_contract": {
                    "threshold_unit": "percent",
                    "conf_thres": 35,
                }
            }
        )
    except RuntimeError as exc:
        assert "threshold unit" in str(exc)
    else:
        raise AssertionError("percent threshold contract must be rejected")

    try:
        mod._load_threshold_contract(
            {"threshold_contract": {"threshold_unit": "ratio_0_1", "conf_thres": 35}}
        )
    except RuntimeError as exc:
        assert "outside 0..1" in str(exc)
    else:
        raise AssertionError("percent-like ratio value must be rejected")


def test_load_threshold_contract_rejects_legacy_unit_alias_in_any_layer():
    mod = _load_board_agent()

    with pytest.raises(RuntimeError, match="threshold unit"):
        mod._load_threshold_contract(
            {
                "unit": "percent",
                "threshold_contract": {"conf_thres": 0.35},
            }
        )

    with pytest.raises(RuntimeError, match="threshold unit"):
        mod._load_threshold_contract(
            {
                "threshold_contract": {
                    "threshold_unit": "ratio_0_1",
                    "unit": "percent",
                    "conf_thres": 0.35,
                }
            }
        )

    accepted = mod._load_threshold_contract(
        {"unit": "ratio_0_1", "conf_thres": 0.35}
    )
    assert accepted["threshold_unit"] == "ratio_0_1"


def test_extract_models_zip_rejects_path_traversal(tmp_path):
    mod = _load_board_agent()
    zip_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("models/BM1688/should-not-extract.bmodel", b"partial")
        zf.writestr("../outside.txt", b"must not escape")

    with pytest.raises(RuntimeError, match="unsafe zip"):
        mod._extract_models_zip(zip_path, tmp_path / "work")
    assert not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "work" / "models").exists()


def test_extract_models_zip_rejects_windows_normalized_parent(tmp_path):
    mod = _load_board_agent()
    zip_path = tmp_path / "malicious-windows.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(".. /outside.txt", b"must not escape")

    with pytest.raises(RuntimeError, match="unsafe zip"):
        mod._extract_models_zip(zip_path, tmp_path / "work")
    assert not (tmp_path / "outside.txt").exists()


def test_extract_models_zip_accepts_normal_model_package(tmp_path):
    mod = _load_board_agent()
    zip_path = tmp_path / "models.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("run_config.json", "{}")
        zf.writestr("models/BM1688/model_int8.bmodel", b"bmodel")

    work = tmp_path / "work"
    result = mod._extract_models_zip(zip_path, work)
    assert result == work
    assert (work / "run_config.json").is_file()
    assert (work / "models" / "BM1688" / "model_int8.bmodel").read_bytes() == b"bmodel"


def test_bmcv_int8_detection_is_included_in_infer_runs():
    mod = _load_board_agent()
    step = {
        "step": "bmcv_int8_detection",
        "bmodel": "yolov11n_int8_1b.bmodel",
        "returncode": 0,
        "stdout": _FP16_SOPHON_TIMING_LOG,
        "stderr": "",
    }

    mod._attach_step_metrics(step)
    report_metrics = mod._build_report_metrics({"steps": [step]}, {})

    assert step["metrics"]["pipeline_ms_sum"] == 54.29
    assert report_metrics["infer_runs"] == [
        {
            "tag": "bmcv_int8_detection",
            "bmodel": "yolov11n_int8_1b.bmodel",
            "ok": True,
            **step["metrics"],
        }
    ]


def test_run_bmcv_int8_detection_compat_wrapper_keeps_legacy_step(monkeypatch):
    mod = _load_board_agent()
    captured = {}

    def fake_bmcv_detection(*args, **kwargs):
        captured.update(kwargs)
        return {"step": "unexpected", "returncode": 0}

    monkeypatch.setattr(mod, "run_bmcv_detection", fake_bmcv_detection)

    result = mod.run_bmcv_int8_detection(
        Path("demo"),
        input_dir=Path("images"),
        bmodel_path=Path("model_int8_1b.bmodel"),
    )

    assert captured["precision"] == "int8"
    assert result["step"] == "bmcv_int8_detection"


def test_run_validation_keeps_all_bmcv_models_and_int8_alias(tmp_path, monkeypatch):
    mod = _load_board_agent()
    demo_root = tmp_path / "demo"
    export_work = tmp_path / "export"
    demo_root.mkdir()
    model_dir = export_work / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    model_names = [
        "yolov11n_int8_1b.bmodel",
        "yolov11n_fp16_1b.bmodel",
        "yolov11n_fp32_1b.bmodel",
    ]
    for name in model_names:
        (model_dir / name).write_bytes(b"bmodel")
    eval_dir = export_work / "eval_data" / "images" / "test"
    eval_dir.mkdir(parents=True)
    (eval_dir / "sample.jpg").write_bytes(b"image")

    monkeypatch.setattr(
        mod,
        "_run_cmd",
        lambda *args, **kwargs: {
            "cmd": args[0],
            "returncode": 0,
            "stdout": "calculate time = 0.005",
            "stderr": "",
        },
    )
    calls = []
    threshold_calls = []

    def fake_bmcv(*args, **kwargs):
        name = kwargs["bmodel_path"].name
        calls.append(name)
        threshold_calls.append((kwargs["conf_thresh"], kwargs["nms_thresh"]))
        return {
            "step": "bmcv_int8_detection",
            "bmodel": name,
            "returncode": 0,
            "metrics": {"infer_ms_avg": float(len(calls))},
            "detection_summary": {
                "image_count": 1,
                "with_boxes": 1,
                "total_boxes": 1,
            },
        }

    monkeypatch.setattr(mod, "run_bmcv_detection", fake_bmcv)

    report = mod.run_validation(
        demo_root,
        export_work,
        run_coco=False,
        conf_thresh=0.31,
        nms_thresh=0.66,
        match_iou_thres=0.55,
        threshold_source="ai_recommended",
    )
    finalized = mod.finalize_board_report(report, export_work)

    expected_order = [
        "yolov11n_fp32_1b.bmodel",
        "yolov11n_fp16_1b.bmodel",
        "yolov11n_int8_1b.bmodel",
    ]
    assert calls == expected_order
    assert threshold_calls == [(0.31, 0.66)] * 3
    assert report["thresholds"] == {
        "threshold_unit": "ratio_0_1",
        "source": "ai_recommended",
        "conf_thres": 0.31,
        "nms_thres": 0.66,
        "match_iou_thres": 0.55,
    }
    assert report["thresholds_applied"] == report["thresholds"]
    assert report["inference_applied"] == {
        "threshold_unit": "ratio_0_1",
        "source": "ai_recommended",
        "conf_thres": 0.31,
        "nms_thres": 0.66,
    }
    assert report["evaluation_match_config"]["match_iou_thres"] == 0.55
    assert report["evaluation_match_config"]["status"] == "configured_for_formal_evaluator"
    assert [row["bmodel"] for row in report["bmcv_infer_runs"]] == expected_order
    assert [row["step"] for row in report["bmcv_infer_runs"]] == [
        "bmcv_fp32_detection",
        "bmcv_fp16_detection",
        "bmcv_int8_detection",
    ]
    assert report["bmcv_infer"]["bmodel"] == "yolov11n_int8_1b.bmodel"
    assert [row["bmodel"] for row in finalized["metrics"]["infer_runs"]] == expected_order


def test_run_validation_uses_configured_coco_inference_thresholds(tmp_path, monkeypatch):
    mod = _load_board_agent()
    demo_root = tmp_path / "demo"
    export_work = tmp_path / "export"
    model_dir = export_work / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    for name in (
        "yolov11n_fp32_1b.bmodel",
        "yolov11n_fp16_1b.bmodel",
        "yolov11n_int8_1b.bmodel",
    ):
        (model_dir / name).write_bytes(b"bmodel")
    eval_dir = export_work / "board_eval" / "images"
    eval_dir.mkdir(parents=True)
    (eval_dir / "sample.jpg").write_bytes(b"image")
    coco_dir = demo_root / "datasets" / "coco" / "val2017_1000"
    coco_dir.mkdir(parents=True)
    (coco_dir / "sample.jpg").write_bytes(b"image")
    (demo_root / "datasets" / "coco" / "instances_val2017_1000.json").write_text(
        "{}", encoding="utf-8"
    )

    commands = []

    def fake_run_cmd(cmd, *args, **kwargs):
        commands.append(list(cmd))
        if "yolov8_bmcv.py" in cmd:
            results = demo_root / "results"
            results.mkdir(parents=True, exist_ok=True)
            (results / "coco_result.json").write_text("[]", encoding="utf-8")
        return {"cmd": list(cmd), "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(mod, "_run_cmd", fake_run_cmd)
    monkeypatch.setattr(
        mod,
        "run_bmcv_detection",
        lambda *args, **kwargs: {
            "bmodel": kwargs["bmodel_path"].name,
            "returncode": 0,
            "metrics": {},
            "detection_summary": {"image_count": 1},
        },
    )

    report = mod.run_validation(
        demo_root,
        export_work,
        run_coco=True,
        conf_thresh=0.31,
        nms_thresh=0.66,
        match_iou_thres=0.55,
        threshold_source="manual",
    )

    coco_cmd = next(cmd for cmd in commands if "val2017_1000" in " ".join(cmd))
    assert coco_cmd[coco_cmd.index("--conf_thresh") + 1] == "0.31"
    assert coco_cmd[coco_cmd.index("--nms_thresh") + 1] == "0.66"
    assert report["inference_applied"]["conf_thres"] == 0.31
    assert report["inference_applied"]["nms_thres"] == 0.66
    assert report["evaluation_match_config"]["match_iou_thres"] == 0.55


def test_run_validation_falls_back_to_first_bmcv_run_without_int8(
    tmp_path, monkeypatch
):
    mod = _load_board_agent()
    demo_root = tmp_path / "demo"
    export_work = tmp_path / "export"
    demo_root.mkdir()
    model_dir = export_work / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    for name in ("model_fp16_1b.bmodel", "model_fp32_1b.bmodel"):
        (model_dir / name).write_bytes(b"bmodel")
    eval_dir = export_work / "eval_data" / "images" / "test"
    eval_dir.mkdir(parents=True)
    (eval_dir / "sample.jpg").write_bytes(b"image")

    monkeypatch.setattr(
        mod,
        "_run_cmd",
        lambda *args, **kwargs: {
            "cmd": args[0],
            "returncode": 0,
            "stdout": "calculate time = 0.005",
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        mod,
        "run_bmcv_detection",
        lambda *args, **kwargs: {
            "bmodel": kwargs["bmodel_path"].name,
            "returncode": 0,
            "metrics": {},
            "detection_summary": {},
        },
    )

    report = mod.run_validation(demo_root, export_work, run_coco=False)

    assert report["bmcv_infer_runs"][0]["bmodel"] == "model_fp32_1b.bmodel"
    assert report["bmcv_infer"] is report["bmcv_infer_runs"][0]


def test_summarize_bmcv_result_json_counts(tmp_path):
    mod = _load_board_agent()
    jp = tmp_path / "result.json"
    jp.write_text(
        json.dumps(
            [
                {"image_name": "a.jpg", "bboxes": [[0, 0, 1, 1, 0.9, 0]]},
                {"filename": "b.jpg", "bboxes": []},
                {"image_name": "c.jpg", "bboxes": [[0, 0, 1, 1, 0.8, 1], [0, 0, 2, 2, 0.7, 1]]},
            ]
        ),
        encoding="utf-8",
    )
    out = mod.summarize_bmcv_result_json(jp)
    assert out["image_count"] == 3
    assert out["with_boxes"] == 2
    assert out["total_boxes"] == 3
    assert out["sample"]["image"] == "a.jpg"
    assert out["sample"]["boxes"] == 1


def test_package_bmcv_preview_images_copies_latest(tmp_path):
    mod = _load_board_agent()
    demo = tmp_path / "demo"
    src = demo / "results" / "images"
    src.mkdir(parents=True)
    (src / "old.jpg").write_bytes(b"old")
    (src / "new.jpg").write_bytes(b"new")
    old = src / "old.jpg"
    new = src / "new.jpg"
    import os
    import time

    os.utime(old, (time.time() - 100, time.time() - 100))
    os.utime(new, (time.time(), time.time()))
    dest = tmp_path / "out" / "bmcv_preview"
    info = mod.package_bmcv_preview_images(demo, dest, max_images=1)
    assert info["viz_image_count"] == 2
    assert info["preview_files"] == ["new.jpg"]
    assert (dest / "new.jpg").is_file()


def test_find_fresh_bmcv_result_json_ignores_stale_files(tmp_path):
    mod = _load_board_agent()
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
        demo, "yolov11n_int8_1b.bmodel", started_at=now
    )
    assert picked == fresh


def test_cleanup_stale_bmcv_outputs_removes_old_json_and_images(tmp_path):
    mod = _load_board_agent()
    demo = tmp_path / "demo"
    results = demo / "results"
    images = results / "images"
    images.mkdir(parents=True)
    (results / "yolov11n_int8_1b.bmodel_test_bmcv_python_result.json").write_text("[]", encoding="utf-8")
    (images / "preview.jpg").write_bytes(b"x")

    mod._cleanup_stale_bmcv_outputs(demo, "yolov11n_int8_1b.bmodel")

    assert not (results / "yolov11n_int8_1b.bmodel_test_bmcv_python_result.json").exists()
    assert not images.exists()
