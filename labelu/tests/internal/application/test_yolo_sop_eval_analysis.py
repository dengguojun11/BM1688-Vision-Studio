from pathlib import Path

from labelu.internal.application.service.yolo_sop_eval_analysis import (
    build_sop_eval_analysis,
    parse_class_metrics_from_report_md,
)

_SAMPLE_CLASS_SECTION = """
## 类别指标

| 类别 | 标签数 | TP | FP | FN | 精确率 | 召回率 | F1 | AP50 |
|------|--------|----|----|----|--------|--------|----|------|
| garbage_street_can | 10 | 7 | 0 | 3 | 1.0000 | 0.7000 | 0.8235 | 0.7000 |
| garbage_street_pack | 58 | 49 | 4 | 9 | 0.9245 | 0.8448 | 0.8829 | 0.8381 |
| object_box | 8 | 8 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
"""

_SAMPLE_BMODEL_SECTION = """
## 类别指标

| 类别 | 标签数 | TP | FP | FN | 精确率 | 召回率 | F1 | AP50 |
|------|--------|----|----|----|--------|--------|----|------|
| garbage_street_can | 10 | 2 | 0 | 8 | 1.0000 | 0.2000 | 0.3333 | 0.2000 |
| garbage_street_pack | 58 | 45 | 4 | 13 | 0.9184 | 0.7759 | 0.8411 | 0.7744 |
| object_box | 8 | 8 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
"""


def _write_reports(root: Path) -> None:
    pt_dir = root / "server_eval" / "results_cloud"
    bm_dir = root / "server_eval" / "results_bmodel"
    pt_dir.mkdir(parents=True)
    bm_dir.mkdir(parents=True)
    (pt_dir / "evaluation_report.md").write_text(
        "# PT\n- mAP50: 0.8443\n- 整体召回率: 0.8142\n" + _SAMPLE_CLASS_SECTION,
        encoding="utf-8",
    )
    (bm_dir / "evaluation_report.md").write_text(
        "# BM\n- mAP50: 0.7234\n- 整体召回率: 0.7432\n" + _SAMPLE_BMODEL_SECTION,
        encoding="utf-8",
    )


def test_parse_class_metrics_from_report_md(tmp_path):
    p = tmp_path / "r.md"
    p.write_text(_SAMPLE_CLASS_SECTION, encoding="utf-8")
    rows = parse_class_metrics_from_report_md(p)
    assert len(rows) == 3
    assert rows[0]["name"] == "garbage_street_can"
    assert rows[0]["ap50"] == 0.7


def test_build_sop_eval_analysis_class_drops_and_root_causes(tmp_path):
    _write_reports(tmp_path)
    sop = {
        "pt": {
            "status": "ok",
            "results_dir": str(tmp_path / "server_eval" / "results_cloud"),
            "metrics": {"map50": 0.8443, "recall": 0.8142, "images": 41},
        },
        "bmodel": {
            "status": "ok",
            "results_dir": str(tmp_path / "server_eval" / "results_bmodel"),
            "metrics": {"map50": 0.7234, "recall": 0.7432, "images": 41},
        },
        "comparison_pt_onnx": {
            "status": "ok",
            "delta_ratio": 0.0014,
        },
        "comparison_pt_bmodel": {
            "status": "fail",
            "delta_ratio": 0.1432,
            "detail": "bmodel vs pt mAP50 差 0.1209 (14.32%)，文档阈值 ≤5%",
        },
    }
    out = build_sop_eval_analysis(tmp_path, sop)
    assert out is not None
    assert out["status"] == "fail"
    can = next(r for r in out["class_drops"] if r["name"] == "garbage_street_can")
    assert can["ap50_delta"] == -0.5
    assert can["fn_delta"] == 5
    assert can["severity"] == "critical"
    assert out["global_delta"]["total_fn_delta"] == 9
    assert any("INT8" in c["title"] for c in out["root_causes"])


def test_build_sop_eval_analysis_maps_warn_to_review(tmp_path):
    _write_reports(tmp_path)
    sop = {
        "pt": {
            "status": "ok",
            "results_dir": str(tmp_path / "server_eval" / "results_cloud"),
            "metrics": {"map50": 0.80, "recall": 0.81, "images": 41},
        },
        "bmodel": {
            "status": "ok",
            "results_dir": str(tmp_path / "server_eval" / "results_bmodel"),
            "metrics": {"map50": 0.785, "recall": 0.79, "images": 41},
        },
        "comparison_pt_onnx": {"status": "ok", "verdict": "pass"},
        "comparison_pt_bmodel": {
            "status": "warn",
            "verdict": "review",
            "drop_map50": 0.015,
            "max_drop": 0.02,
            "detail": "INT8 BModel 掉点位于复核区间",
        },
    }

    out = build_sop_eval_analysis(tmp_path, sop)

    assert out is not None
    assert out["status"] == "review"
    assert any(cause["severity"] == "medium" for cause in out["root_causes"])
    assert not any("≤5%" in cause["evidence"] for cause in out["root_causes"])
