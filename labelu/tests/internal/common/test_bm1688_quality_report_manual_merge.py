import json

from labelu.internal.common.config import settings
from labelu.internal.common.bm1688_quality_report import (
    merge_manual_edge_validation_into_report,
)


def test_merge_manual_edge_validation_into_report_backfills_bmodel_metrics():
    report = {
        "sop_eval": {
            "status": "ok",
            "pt": {
                "metrics": {
                    "map50": 0.6721,
                }
            },
        }
    }
    edge_validation = type(
        "BV",
        (),
        {
            "report": {
                "result": {
                    "eval_summary": {
                        "status": "ok",
                        "map50": 0.6795,
                        "map50_95": 0.6795,
                        "precision": 0.7361,
                        "recall": 0.6578,
                        "images": 957,
                    },
                    "analysis": {
                        "summary": "bmodel vs pt mAP50 差 0.0074 (1.10%)，文档阈值 ≤ 5%",
                        "sources": {
                            "bmodel_report": "/tmp/results_bmodel/evaluation_report.md",
                        },
                    },
                }
            }
        },
    )()

    merged = merge_manual_edge_validation_into_report(report, edge_validation)

    assert merged["sop_eval"]["bmodel"]["status"] == "ok"
    assert merged["sop_eval"]["bmodel"]["metrics"]["map50"] == 0.6795
    assert merged["sop_eval"]["comparison_pt_bmodel"]["status"] == "ok"
    assert merged["sop_eval"]["comparison_pt_bmodel"]["bmodel_map50"] == 0.6795
    assert "INT8 BModel" in merged["sop_eval"]["comparison_pt_bmodel"]["detail"]


def test_merge_manual_edge_validation_prefers_same_batch_fp16_comparison():
    direct_comparison = {
        "status": "ok",
        "verdict": "pass",
        "precision": "fp16",
        "loss_type": "deployment_conversion",
        "same_batch": True,
        "pt_map50": 0.992,
        "bmodel_map50": 0.99,
        "drop_map50": 0.002,
        "detail": "FP16 BModel 相对 PT 的 mAP50 掉点 0.0020",
    }
    edge_validation = type(
        "BV",
        (),
        {
            "report": {
                "result": {
                    "pt_eval": {
                        "status": "ok",
                        "metrics": {"map50": 0.992, "images": 50},
                        "dataset_evidence": {"pack_signature": "same-pack"},
                    },
                    "eval_summary": {"status": "ok", "map50": 0.99, "images": 50},
                    "eval_models": {
                        "fp16": {
                            "model_tag": "fp16",
                            "status": "ok",
                            "map50": 0.99,
                            "images": 50,
                        }
                    },
                    "comparison_pt_bmodel": direct_comparison,
                }
            }
        },
    )()
    report = {
        "train_val": {"status": "ok", "map50": 0.88},
        "sop_eval": {"pt": {"metrics": {"map50": 0.88}}},
    }

    merged = merge_manual_edge_validation_into_report(report, edge_validation)

    assert merged["sop_eval"]["pt"]["metrics"]["map50"] == 0.992
    assert merged["sop_eval"]["bmodel"]["metrics"]["map50"] == 0.99
    assert merged["sop_eval"]["comparison_pt_bmodel"] == direct_comparison


def test_merge_manual_edge_validation_into_report_keeps_plate_recognition_eval():
    edge_validation = type(
        "BV",
        (),
        {
            "report": {
                "result": {
                    "plate_recognition_eval": {
                        "status": "ok",
                        "model_type": "plate_recognition",
                        "model_tag": "fp16",
                        "model_filename": "plate_recognition_fp16_1b.bmodel",
                        "runtime_ok": True,
                        "input_image_count": 351,
                        "images_with_detections": 351,
                        "total_detections": 437,
                        "plate_type_counts": {"greenPlate": 300},
                        "color_counts": {"green": 310},
                        "inference": {"elapsed_seconds": 78.653},
                    }
                }
            }
        },
    )()

    merged = merge_manual_edge_validation_into_report({}, edge_validation)

    plate = merged["sop_eval"]["plate_recognition_eval"]
    assert plate["runtime_ok"] is True
    assert plate["model_tag"] == "fp16"
    assert plate["total_detections"] == 437
    assert plate["inference"]["elapsed_seconds"] == 78.653


def test_merge_manual_edge_validation_recovers_historical_plate_summary_from_disk(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "YOLO_BM1688_EXPORT_SUBDIR", "exports")
    summary_path = (
        tmp_path
        / "exports"
        / "146"
        / "manual_edge_validation"
        / "job-126"
        / "edge_ssh_pull"
        / "output"
        / "results_bmodel"
        / "summary.json"
    )
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "runtime_ok": True,
                "model_type": "plate_recognition",
                "model_filename": "plate_recognition_fp16_1b.bmodel",
                "input_image_count": 351,
                "result_image_count": 351,
                "images_with_detections": 351,
                "total_detections": 437,
            }
        ),
        encoding="utf-8",
    )
    edge_validation = type(
        "BV",
        (),
        {
            "export_id": 146,
            "report": {
                "export_id": 146,
                "job_id": 126,
                "result": {"eval_summary": {}},
            },
        },
    )()

    merged = merge_manual_edge_validation_into_report({}, edge_validation)

    plate = merged["sop_eval"]["plate_recognition_eval"]
    assert plate["runtime_ok"] is True
    assert plate["input_image_count"] == 351
    assert plate["total_detections"] == 437
    assert plate["source_path"] == str(summary_path)


def test_maybe_align_pt_onnx_comparison_with_test_val():
    from labelu.internal.common.bm1688_quality_report import (
        _maybe_align_pt_onnx_comparison_with_test_val,
    )

    sop_eval = {
        "status": "warn",
        "pt": {"status": "ok", "metrics": {"map50": 0.9345}},
        "onnx": {"status": "ok", "metrics": {"map50": 0.9659}},
        "comparison_pt_onnx": {
            "status": "fail",
            "detail": "onnx vs pt mAP50 差 0.0314 (3.36%)",
            "pt_map50": 0.9345,
            "onnx_map50": 0.9659,
            "delta_ratio": 0.0336,
        },
        "comparison_pt_bmodel": {"status": "ok"},
    }
    report = {
        "test_val": {
            "status": "ok",
            "map50": 0.9636898147510538,
        }
    }
    out = _maybe_align_pt_onnx_comparison_with_test_val(sop_eval, report)
    assert out["comparison_pt_onnx"]["status"] == "ok"
    assert out["comparison_pt_onnx"]["baseline"] == "test_val"
    assert out["conversion_pt_onnx"]["success"] is True
    assert out["status"] == "ok"
