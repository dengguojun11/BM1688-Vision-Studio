"""SOP 服务端评估路径解析。"""

from pathlib import Path

from labelu.internal.application.service.yolo_sop_model_evaluation import (
    _compare_pt_bmodel,
    _compare_pt_onnx,
    _load_eval_summary,
    _normalize_eval_metrics,
    _read_class_names_from_data_yaml,
    enrich_sop_eval_summary,
)


def test_read_class_names_from_data_yaml(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\nnc: 2\nnames:\n  0: cat\n  1: dog\n",
        encoding="utf-8",
    )
    names = _read_class_names_from_data_yaml(root)
    assert names == ["cat", "dog"]


def test_compare_pt_onnx_within_threshold():
    cmp = _compare_pt_onnx({"map50": 0.5}, {"map50": 0.496})
    assert cmp["status"] == "ok"
    assert cmp["verdict"] == "pass"
    assert cmp["drop_map50"] == 0.004
    assert cmp["threshold_unit"] == "map50_absolute_drop"


def test_compare_pt_onnx_over_threshold():
    cmp = _compare_pt_onnx({"map50": 0.5}, {"map50": 0.489})
    assert cmp["status"] == "fail"
    assert cmp["verdict"] == "fail"
    assert cmp["reason_code"] == "drop_exceeds_max"


def test_compare_pt_onnx_review_band():
    cmp = _compare_pt_onnx({"map50": 0.5}, {"map50": 0.492})

    assert cmp["status"] == "warn"
    assert cmp["verdict"] == "review"
    assert cmp["drop_map50"] == 0.008


def test_compare_pt_onnx_significant_improvement_requires_review_not_failure():
    cmp = _compare_pt_onnx({"map50": 0.5}, {"map50": 0.6})

    assert cmp["status"] == "warn"
    assert cmp["verdict"] == "review"
    assert cmp["reason_code"] == "candidate_metric_improved_significantly"
    assert cmp["signed_delta_map50"] == 0.1
    assert cmp["drop_map50"] == 0.0
    assert cmp["delta_map50"] == 0.1
    assert cmp["delta_ratio"] == 0.2


def test_compare_pt_bmodel_supports_explicit_absolute_drop_thresholds():
    cmp = _compare_pt_bmodel(
        {"map50": 0.8},
        {"map50": 0.77},
        pass_max_drop=0.01,
        max_drop=0.04,
    )

    assert cmp["status"] == "warn"
    assert cmp["verdict"] == "review"
    assert cmp["drop_map50"] == 0.03
    assert cmp["max_drop"] == 0.04


def test_compare_pt_bmodel_uses_fp16_deployment_conversion_wording():
    cmp = _compare_pt_bmodel(
        {"map50": 0.99},
        {"map50": 0.985},
        precision="fp16",
    )

    assert cmp["status"] == "ok"
    assert cmp["precision"] == "fp16"
    assert cmp["loss_type"] == "deployment_conversion"
    assert "FP16 BModel" in cmp["detail"]
    assert "INT8" not in cmp["detail"]


def test_compare_pt_bmodel_keeps_int8_default_wording():
    cmp = _compare_pt_bmodel({"map50": 0.80}, {"map50": 0.79})

    assert "precision" not in cmp
    assert "INT8 BModel" in cmp["detail"]


def test_enrich_sop_eval_summary_adds_conversion(tmp_path):
    root = tmp_path / "export"
    cloud = root / "server_eval" / "results_cloud"
    cloud.mkdir(parents=True)
    (cloud / "evaluation_report.md").write_text("# pt", encoding="utf-8")
    raw = {
        "status": "ok",
        "pt": {"status": "ok", "results_dir": str(cloud)},
        "comparison_pt_onnx": _compare_pt_onnx({"map50": 0.5}, {"map50": 0.501}),
    }
    out = enrich_sop_eval_summary(raw, root)
    assert out["conversion_pt_onnx"]["success"] is True
    assert out["pt"]["report_md_exists"] is True


def test_normalize_eval_metrics_maps_overall_fields():
    out = _normalize_eval_metrics(
        {
            "map50": 0.5,
            "overall_precision": 0.85,
            "overall_recall": 0.72,
            "map5095": 0.48,
        }
    )
    assert out["precision"] == 0.85
    assert out["recall"] == 0.72
    assert out["map50_95"] == 0.48


def test_normalize_eval_metrics_clears_invalid_map50_95_approximation():
    out = _normalize_eval_metrics(
        {
            "map50": 0.71,
            "map50_95": 0.71,
            "map5095": 0.71,
            "metric_validity": {
                "map50_95": {
                    "status": "not_computed",
                    "reason": "evaluator_only_matches_at_iou_0.50",
                }
            },
        }
    )

    assert out["map50_95"] is None
    assert out["map50_95_status"] == "not_computed"


def test_load_eval_summary_backfills_precision_from_report_md(tmp_path):
    results = tmp_path / "results_cloud"
    results.mkdir(parents=True)
    (results / "eval_summary.json").write_text(
        '{"map50": 0.7666, "map50_95": 0.7666, "precision": null, "recall": null, "images": 12}',
        encoding="utf-8",
    )
    (results / "evaluation_report.md").write_text(
        "\n".join(
            [
                "# report",
                "- 评估图片数: 12",
                "- mAP50: 0.7666",
                "- mAP50-95: 0.7666",
                "- 整体精确率: 0.8525",
                "- 整体召回率: 0.7172",
            ]
        ),
        encoding="utf-8",
    )
    loaded = _load_eval_summary(results)
    assert loaded["precision"] == 0.8525
    assert loaded["recall"] == 0.7172
    assert loaded["images"] == 12


def test_load_eval_summary_backfills_when_metrics_are_zero(tmp_path):
    root = tmp_path / "server_eval" / "results_cloud"
    root.mkdir(parents=True)
    (root / "eval_summary.json").write_text(
        '{"map50": 0.0769, "precision": 0, "recall": 0, "images": 12}',
        encoding="utf-8",
    )
    (root / "evaluation_report.md").write_text(
        "\n".join(
            [
                "- mAP50: 0.0769",
                "- 整体精确率: 1.0",
                "- 整体召回率: 0.0069",
            ]
        ),
        encoding="utf-8",
    )
    loaded = _load_eval_summary(root)
    assert loaded["precision"] == 1.0
    assert loaded["recall"] == 0.0069


def test_run_eval_script_recovers_when_eval_summary_exists(tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_sop_model_evaluation as mod

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "fake_eval.py").write_text("print('done')\n", encoding="utf-8")
    save_dir = tmp_path / "out"
    save_dir.mkdir()
    (save_dir / "eval_summary.json").write_text('{"map50": 0.9}', encoding="utf-8")

    class FakeProc:
        returncode = 1
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(mod, "_sop_eval_scripts_dir", lambda: script_dir)
    monkeypatch.setattr(mod, "_pick_python", lambda: "python")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeProc())

    ok, log = mod._run_eval_script(
        "fake_eval.py",
        env_extra={"EVAL_SAVE_DIR": str(save_dir)},
        timeout_sec=30,
    )
    assert ok is True
    assert "recovered" in log


def test_enrich_sop_eval_summary_loads_metrics_from_eval_summary(tmp_path):
    import json

    root = tmp_path / "export"
    cloud = root / "server_eval" / "results_cloud"
    onnx = root / "server_eval" / "results_onnx"
    cloud.mkdir(parents=True)
    onnx.mkdir(parents=True)
    (cloud / "eval_summary.json").write_text(
        json.dumps(
            {
                "map50": 0.42,
                "map50_95": 0.31,
                "precision": 0.8,
                "recall": 0.7,
                "images": 10,
            }
        ),
        encoding="utf-8",
    )
    (onnx / "eval_summary.json").write_text(
        json.dumps({"map50": 0.421, "map50_95": 0.32, "images": 10}),
        encoding="utf-8",
    )
    raw = {
        "status": "ok",
        "pt": {"status": "ok", "results_dir": str(cloud)},
        "onnx": {"status": "ok", "results_dir": str(onnx)},
    }
    out = enrich_sop_eval_summary(raw, root)
    assert out["pt"]["metrics"]["map50"] == 0.42
    assert out["onnx"]["metrics"]["map50"] == 0.421
    assert out["comparison_pt_onnx"]["status"] == "ok"
    assert out["criteria"]["version"] == "map50-absolute-drop-v2"
    assert out["criteria"]["legacy_reference"]["active"] is False


def test_resolve_sop_eval_report_path_bmodel_manual_fallback(tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_sop_model_evaluation as mod

    export_root = tmp_path / "export-3"
    manual = (
        export_root
        / "manual_edge_validation"
        / "job-3"
        / "server_eval"
        / "results_bmodel"
    )
    manual.mkdir(parents=True)
    (manual / "evaluation_report.md").write_text(
        "# bmodel edge report", encoding="utf-8"
    )

    monkeypatch.setattr(mod, "_export_root", lambda export_id: export_root)

    path = mod.resolve_sop_eval_report_path(3, "bmodel")
    assert path == manual / "evaluation_report.md"
    assert path.read_text(encoding="utf-8").startswith("# bmodel")


def test_resolve_sop_eval_asset_path_bmcv_manual_fallback(tmp_path, monkeypatch):
    from labelu.internal.application.service import yolo_sop_model_evaluation as mod

    export_root = tmp_path / "export-3"
    preview = (
        export_root
        / "manual_edge_validation"
        / "job-3"
        / "server_eval"
        / "bmcv_preview"
    )
    preview.mkdir(parents=True)
    (preview / "sample.png").write_bytes(b"png")

    monkeypatch.setattr(mod, "_export_root", lambda export_id: export_root)

    path = mod.resolve_sop_eval_asset_path(3, "bmcv", "sample.png")
    assert path == preview / "sample.png"
