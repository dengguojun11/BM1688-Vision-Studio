from unittest.mock import patch

from labelu.internal.common.bm1688_quality_report import (
    merge_manual_edge_validation_into_report,
    parse_compile_deploy_hints,
    quality_summary_one_liner,
    run_onnx_coco128_val,
    train_val_from_job,
)


def test_parse_compile_deploy_hints_extracts_similarity():
    log = """
exit_code=0
cosine similarity: 0.9912
INT8 compare PASSED tolerance 0.95
"""
    out = parse_compile_deploy_hints(log)
    assert out["exit_code"] == 0
    assert len(out["lines"]) >= 1
    assert out["scores"]


def test_train_val_from_metrics_summary():
    raw = '{"final_metrics": {"map50": 0.55, "map50_95": 0.42}}'
    tv = train_val_from_job(raw)
    assert tv["status"] == "ok"
    assert abs(tv["map50_95"] - 0.42) < 0.001


def test_run_onnx_val_uses_same_interpreter(tmp_path):
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    fake = {"status": "ok", "map50_95": 0.5, "source": "ultralytics.val(coco128.yaml)"}

    with patch(
        "labelu.internal.common.optional_dependencies.ensure_ultralytics_installed",
        return_value=True,
    ), patch(
        "labelu.internal.common.bm1688_quality_report._run_onnx_coco128_val_inprocess",
        return_value=fake,
    ) as run_val:
        out = run_onnx_coco128_val(onnx, imgsz=640, timeout_sec=30)
    assert out["status"] == "ok"
    assert out["map50_95"] == 0.5
    run_val.assert_called_once()


def test_run_onnx_val_missing_ultralytics(tmp_path):
    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"x")
    with patch(
        "labelu.internal.common.optional_dependencies.ensure_ultralytics_installed",
        return_value=False,
    ):
        out = run_onnx_coco128_val(onnx, imgsz=640)
    assert out["status"] == "fail"
    assert "ultralytics" in out["detail"]


def test_quality_summary_one_liner():
    report = {
        "train_val": {"status": "ok", "map50_95": 0.42},
        "onnx_val": {"status": "ok", "map50_95": 0.40},
        "sophon_layout": {"passed": True},
    }
    s = quality_summary_one_liner(report)
    assert "0.42" in s
    assert "0.40" in s
    assert "通过" in s
