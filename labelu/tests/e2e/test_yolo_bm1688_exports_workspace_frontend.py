"""
Playwright 前端回归：
BM1688 导出页应提供列表下方常驻详情工作台，支持在同页查看概览、评估、
执行步骤、手动验证结果与配置快照，而不是只依赖旧弹窗切换。
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:9888")
API_PREFIX = "/api/v1"
PAGE_URL = os.environ.get(
    "LABELU_E2E_BM1688_PAGE",
    f"{BASE_URL}/yolo-bm1688-exports",
)
REPO_ROOT = Path(__file__).resolve().parents[3]
BM1688_PAGE_FILE = (
    REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_bm1688_exports.html"
)


def _json_response(route: Route, data: Any, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False),
    )


def _export_row() -> dict[str, Any]:
    return {
        "id": 123,
        "task_id": 88,
        "task_name": "边端验证任务",
        "yolo_train_job_id": 66,
        "status": "COMPLETED",
        "imgsz": 640,
        "onnx_filename": "model.onnx",
        "bmodel_filename": "yolov11n_int8_1b.bmodel",
        "bmodel_filenames": [
            "BM1688/yolov11n_fp32_1b.bmodel",
            "BM1688/yolov11n_fp16_1b.bmodel",
            "BM1688/yolov11n_int8_1b.bmodel",
        ],
        "error_message": None,
        "quality_summary": "训练达标，边端结果已回传",
        "validation_gate_label": "达标",
        "edge_validation_label": "结果已回传",
        "edge_execution_label": "执行完成",
        "edge_int8_eval_label": "需复核",
        "current_verdict_label": "需复核",
        "training_assessment": {
            "status_code": "passed",
            "status_label": "达标",
            "reason_codes": [],
            "reason_summary": "训练质量满足当前场景标准",
        },
        "edge_validation_assessment": {
            "status_code": "usable_needs_review",
            "status_label": "可运行但效果待确认",
            "reason_codes": ["pt_bmodel_comparison_review"],
            "reason_summary": "BModel 绝对掉点进入人工复核区间",
        },
        "release_gate_assessment": {
            "status_code": "review_required",
            "status_label": "需复核",
            "reason_codes": ["edge_review_required"],
            "reason_summary": "边端 INT8 业务精度需要人工复核",
        },
        "artifact_manifest": {
            "manifest_version": "v1",
            "source_dataset": {
                "content_sha256": "dataset-sha256-1234567890",
                "class_names": ["person", "vehicle"],
                "split_counts": {"test": 120},
            },
            "artifacts": [
                {"role": "weights_pt", "sha256": "pt-sha256-1234567890"},
                {"role": "model_onnx", "sha256": "onnx-sha256-1234567890"},
                {"role": "model_bmodel", "sha256": "bmodel-sha256-1234567890"},
            ],
        },
        "doc_hint": "对齐 SOP 3.2",
        "created_at": "2026-07-04T10:00:00",
        "updated_at": "2026-07-04T10:10:00",
        "board_validation": {
            "validation_id": 9,
            "export_id": 123,
            "status": "COMPLETED",
            "report_summary": "边端验证通过",
            "completed_at": "2026-07-04T10:18:00",
            "report": {
                "source": "manual_edge_validation",
                "job_id": 9,
                "mode": "run_and_eval",
                "threshold_source": "manual",
                "thresholds": {
                    "threshold_unit": "ratio_0_1",
                    "conf_thres": 0.35,
                    "nms_thres": 0.7,
                    "match_iou_thres": 0.5,
                },
                "image_count": 32,
                "labeled_image_count": 24,
                "unlabeled_image_count": 8,
                "started_at": "2026-07-04T10:11:00",
                "result": {
                    "execution": {
                        "avg_inference_ms": 22.4,
                        "p50_inference_ms": 21.8,
                        "p95_inference_ms": 27.6,
                        "fps": 41.7,
                        "memory_mb": 486.5,
                        "npu_usage": 72.4,
                        "power_w": 8.6,
                        "temperature_c": 58.3,
                    },
                    "execution_summary": {
                        "status": "ok",
                        "remote_exit_code": 0,
                        "files_pulled": 7,
                        "remote_host": "192.168.10.8",
                        "has_board_report": True,
                        "has_bmcv_infer": True,
                        "has_bmodel_eval": True,
                    },
                    "prediction_summary": {
                        "image_count": 32,
                        "with_boxes": 24,
                        "total_boxes": 72,
                        "avg_boxes_per_image": 2.25,
                        "sample": {"image": "street-001.jpg", "boxes": 3},
                    },
                    "validation_dimensions": {
                        "environment": {"status": "pass", "ready": True},
                        "inference": {
                            "status": "pass",
                            "remote_exit_code": 0,
                            "observed": True,
                        },
                        "detection_output": {
                            "status": "detected",
                            "image_count": 32,
                            "with_boxes": 24,
                            "total_boxes": 72,
                        },
                        "precision_evaluation": {
                            "status": "computed",
                            "ground_truth_source": "existing_training_annotations",
                            "labeled_image_count": 24,
                        },
                        "class_coverage": {
                            "status": "partial",
                            "strategy": "class_coverage_then_uniform",
                            "covered_class_count": 1,
                            "total_class_count": 2,
                            "covered_classes": ["person"],
                            "missing_classes": ["vehicle"],
                        },
                    },
                    "pt_eval": {
                        "status": "ok",
                        "metrics": {
                            "images": 32,
                            "map50": 0.82,
                            "precision": 0.81,
                            "recall": 0.76,
                        },
                        "eval_params": {
                            "imgsz": 640,
                            "conf_thres": 0.35,
                            "nms_thres": 0.7,
                            "match_iou_thres": 0.5,
                            "preprocessing_contract": {
                                "resize": "letterbox",
                                "color_order": "RGB",
                            },
                        },
                    },
                    "eval_models": {
                        "fp32": {
                            "model_filename": "yolov11n_fp32_1b.bmodel",
                            "images": 32,
                            "map50": 0.81,
                            "precision": 0.8,
                            "recall": 0.75,
                        },
                        "fp16": {
                            "model_filename": "yolov11n_fp16_1b.bmodel",
                            "images": 32,
                            "map50": 0.808,
                            "precision": 0.79,
                            "recall": 0.74,
                        },
                        "int8": {
                            "model_filename": "yolov11n_int8_1b.bmodel",
                            "images": 32,
                            "map50": 0.801,
                            "precision": 0.78,
                            "recall": 0.72,
                        },
                    },
                    "comparison_pt_bmodel": {
                        "status": "warn",
                        "same_batch": True,
                        "primary_precision": "int8",
                        "by_precision": {
                            "fp32": {"same_batch": True, "signed_delta_map50": -0.01},
                            "fp16": {"same_batch": True, "signed_delta_map50": -0.012},
                            "int8": {"same_batch": True, "signed_delta_map50": -0.019},
                        },
                        "int8_vs_fp32": {
                            "same_batch": True,
                            "fp32_map50": 0.81,
                            "int8_map50": 0.801,
                            "drop_map50": 0.009,
                        },
                    },
                },
                "metrics": {
                    "eval_dataset": {
                        "source": "selected_task_samples",
                        "image_count": 32,
                        "imgsz": 640,
                        "class_names": ["person", "vehicle"],
                        "yolo_train_job_id": 66,
                        "task_id": 88,
                        "same_as_training": False,
                    },
                    "bmrt_tests": [
                        {
                            "bmodel": "yolov11n_fp32_1b.bmodel",
                            "ok": True,
                            "calculate_time_ms": 50.644,
                        },
                        {
                            "bmodel": "yolov11n_fp16_1b.bmodel",
                            "ok": True,
                            "calculate_time_ms": 18.725,
                        },
                        {
                            "bmodel": "yolov11n_int8_1b.bmodel",
                            "ok": True,
                            "calculate_time_ms": 5.539,
                        },
                    ],
                    "infer_runs": [
                        {
                            "bmodel": "yolov11n_fp32_1b.bmodel",
                            "tag": "infer_fp32",
                            "ok": True,
                            "decode_ms_avg": 17.98,
                            "preprocess_ms_avg": 10.16,
                            "infer_ms_avg": 51.40,
                            "postprocess_ms_avg": 6.70,
                            "pipeline_ms_sum": 86.24,
                            "timing_source": "sophon_demo_stage_summary",
                            "timing_scope": "per_image_stage_average",
                        },
                        {
                            "bmodel": "yolov11n_fp16_1b.bmodel",
                            "tag": "infer_fp16",
                            "ok": True,
                            "decode_ms_avg": 18.19,
                            "preprocess_ms_avg": 9.80,
                            "infer_ms_avg": 19.39,
                            "postprocess_ms_avg": 6.91,
                            "pipeline_ms_sum": 54.29,
                            "timing_source": "sophon_demo_stage_summary",
                            "timing_scope": "per_image_stage_average",
                        },
                    ],
                    "bmcv_detection": {
                        "image_count": 32,
                        "with_boxes": 24,
                        "total_boxes": 72,
                        "sample": {"image": "street-001.jpg", "boxes": 3},
                    },
                    "bmcv_preview": {
                        "viz_image_count": 12,
                        "preview_files": [],
                    },
                },
                "bmcv_infer": {
                    "bmodel": "yolov11n_fp16_1b.bmodel",
                    "conf_thresh": 0.35,
                    "nms_thresh": 0.7,
                    "returncode": 0,
                },
            },
        },
    }


def _performance_models() -> list[dict[str, Any]]:
    rows = []
    for tag, avg, minimum, maximum, fps in (
        ("fp32", 18.4, 17.3, 20.8, 31.5),
        ("fp16", 10.2, 9.1, 12.6, 44.8),
        ("int8", 7.2, 6.4, 9.1, 52.4),
    ):
        rows.append(
            {
                "tag": tag,
                "bmodel": f"yolov11n_{tag}_1b.bmodel",
                "status": "ok",
                "runtime_metrics": {
                    "throughput_fps": fps,
                    "stage_metrics": {
                        "inference_ms": {
                            "sample_count": 1000,
                            "avg": avg,
                            "min": minimum,
                            "max": maximum,
                            "p50": avg - 0.2,
                            "p95": avg + 1.4,
                            "p99": avg + 2.0 if tag != "int8" else 8.8,
                        }
                    },
                },
                "resource_usage": {
                    "sample_count": 20,
                    "series_available": True,
                    "available_fields": [
                        "cpu_usage",
                        "memory_mb",
                        "npu_usage",
                        "temperature_c",
                    ],
                    "unavailable_fields": ["power_w"],
                    "unavailable_reasons": {
                        "power_w": "device_power_telemetry_not_exposed"
                    },
                    "cpu_usage": {
                        "sample_count": 20,
                        "avg": 28.0,
                        "p95": 39.0,
                        "max": 43.0,
                    },
                    "memory_mb": {
                        "sample_count": 20,
                        "avg": 470.0,
                        "p95": 480.0,
                        "max": 484.0,
                    },
                    "npu_usage": {
                        "sample_count": 20,
                        "avg": 64.0,
                        "p95": 82.0,
                        "max": 88.0,
                    },
                    "temperature_c": {
                        "sample_count": 20,
                        "avg": 57.0,
                        "p95": 60.0,
                        "max": 61.0,
                    },
                },
            }
        )
    return rows


def _export_detail() -> dict[str, Any]:
    row = _export_row()
    row.update(
        {
            "compile_log": "compile ok",
            "export_dir": "D:/labelu_media/yolo_bm1688_exports/123",
            "export_dir_abs": "D:/labelu_media/yolo_bm1688_exports/123",
            "can_recompile_bmodel": True,
            "quality_report": {
                "sop_eval": {
                    "status": "warn",
                    "split": "test",
                    "criteria": {
                        "version": "map50-absolute-drop-v2",
                        "primary_metric": "mAP50",
                        "threshold_unit": "map50_absolute_drop",
                        "onnx_vs_pt": {
                            "pass_max_drop": 0.005,
                            "review_max_drop": 0.01,
                            "max_drop": 0.01,
                        },
                        "bmodel_vs_pt": {
                            "pass_max_drop": 0.01,
                            "review_max_drop": 0.02,
                            "max_drop": 0.02,
                        },
                        "legacy_reference": {
                            "active": False,
                            "onnx_vs_pt_max_ratio": 0.03,
                            "bmodel_vs_pt_max_ratio": 0.05,
                        },
                    },
                    "eval_params": {
                        "imgsz": 640,
                        "conf_thres": 0.35,
                        "iou_thres": 0.5,
                    },
                    "pt": {
                        "status": "ok",
                        "metrics": {
                            "map50_95": 0.71,
                            "map50_95_computed": True,
                            "map50": 0.82,
                            "precision": 0.81,
                            "recall": 0.76,
                            "images": 120,
                        },
                    },
                    "onnx": {
                        "status": "ok",
                        "metrics": {
                            "map50_95": 0.708,
                            "map50_95_computed": True,
                            "map50": 0.816,
                            "precision": None,
                            "recall": 0.0,
                            "images": 120,
                        },
                    },
                    "bmodel": {
                        "status": "ok",
                        "metrics": {
                            "map50_95": 0.79,
                            "map50_95_status": "not_computed",
                            "approximation": "map50_alias",
                            "map50": 0.805,
                            "precision": 0.78,
                            "recall": 0.72,
                            "images": 144,
                            "cpu_memory_mb": 512.0,
                            "class_metrics": [
                                {"name": "person", "tp": 61, "fp": 5, "fn": 7},
                                {"name": "vehicle", "tp": 44, "fp": 4, "fn": 5},
                            ],
                        },
                    },
                    "comparison_pt_onnx": {
                        "status": "ok",
                        "verdict": "pass",
                        "signed_delta_map50": -0.004,
                        "drop_map50": 0.004,
                        "drop_ratio": 0.004878,
                        "pass_max_drop": 0.005,
                        "max_drop": 0.01,
                    },
                    "comparison_pt_bmodel": {
                        "status": "warn",
                        "verdict": "review",
                        "signed_delta_map50": -0.015,
                        "drop_map50": 0.015,
                        "drop_ratio": 0.018293,
                        "pass_max_drop": 0.01,
                        "max_drop": 0.02,
                        "detail": "BModel 掉点进入复核区间",
                    },
                    "analysis": {
                        "status": "warn",
                        "summary": "两类指标均已生成",
                        "class_drops": [
                            {
                                "name": "person",
                                "pt_ap50": 0.8,
                                "bmodel_ap50": 0.78,
                            },
                            {
                                "name": "vehicle",
                                "pt_ap50": 0.84,
                                "bmodel_ap50": 0.83,
                            },
                        ],
                        "size_tier_drops": [],
                        "root_causes": [],
                    },
                }
            },
            "quality_report_refresh": {"status": "COMPLETED"},
        }
    )
    row["board_validation"]["report"]["metrics"][
        "performance_models"
    ] = _performance_models()
    return row


def _export_detail_edge_pending() -> dict[str, Any]:
    row = _export_detail()
    row["board_validation"] = {
        "validation_id": 10,
        "export_id": 123,
        "status": "PROCESSING",
        "report_summary": "边端评估运行中，等待指标回传",
        "report": {"source": "manual_edge_validation"},
    }
    row["edge_validation_assessment"] = {
        "status_code": "pending",
        "status_label": "未验证",
        "reason_codes": ["edge_validation_running"],
        "reason_summary": "边端验证尚未完成",
    }
    sop_eval = row["quality_report"]["sop_eval"]
    sop_eval["bmodel"] = {
        "status": "pending",
        "metrics": {"map50_95_status": "not_computed"},
    }
    sop_eval["comparison_pt_bmodel"] = {
        "status": "skip",
        "verdict": "not_evaluable",
        "drop_map50": None,
        "drop_ratio": None,
        "detail": "BModel 指标未回传",
    }
    return row


def _export_detail_missing_eval_values() -> dict[str, Any]:
    row = _export_detail()
    row["imgsz"] = None
    sop_eval = row["quality_report"]["sop_eval"]
    sop_eval["split"] = None
    sop_eval["status"] = None
    sop_eval["eval_params"] = {}
    row["board_validation"]["report"]["thresholds"] = {}
    return row


def _plate_detection_result() -> dict[str, Any]:
    model = {
        "model_tag": "fp16",
        "model_filename": "plate_detection_fp16_1b.bmodel",
        "status": "ok",
        "backend": "plate_detection_bm1688",
        "runtime_ok": True,
        "objects_detected": True,
        "model_type": "plate_detection",
        "input_image_count": 351,
        "result_image_count": 351,
        "images_with_detections": 351,
        "total_detections": 438,
        "results_dir": "/data/labelu_edge/jobs/manual-job-150/work/output/results_bmodel",
        "bmrt_test": {"return_code": 0, "elapsed_seconds": 1.058},
        "inference": {"return_code": 0, "elapsed_seconds": 67.553},
        "precision_eval_status": "computed",
        "precision_eval_type": "bbox",
        "map50": 0.9876,
        "map50_95": 0.8765,
        "precision": 0.98,
        "recall": 0.97,
        "f1": 0.9749,
        "images": 50,
        "rendered_images": {"status": "ok", "count": 351},
    }
    comparison = {
        "status": "ok",
        "verdict": "pass",
        "precision": "fp16",
        "loss_type": "deployment_conversion",
        "same_batch": True,
        "pt_map50": 0.986,
        "bmodel_map50": 0.9876,
        "signed_delta_map50": 0.0016,
        "drop_map50": 0.0,
        "drop_ratio": 0.0,
        "delta_map50": 0.0016,
        "delta_ratio": 0.0016,
        "dataset_evidence": {"pack_signature": "same-pack-50", "image_count": 50},
        "detail": "FP16 BModel 相对 PT 的 mAP50 掉点 0.0000，通过标准 ≤0.0100",
    }
    return {
        "execution_summary": {
            "status": "ok",
            "remote_exit_code": 0,
            "files_pulled": 714,
            "remote_host": "10.10.11.90",
        },
        "prediction_summary": {
            "image_count": 351,
            "with_boxes": 351,
            "total_boxes": 438,
            "avg_boxes_per_image": 1.2479,
        },
        "eval_summary": {
            "status": "ok",
            "map50": 0.9876,
            "map50_95": 0.8765,
            "map50_95_status": "computed",
            "precision": 0.98,
            "recall": 0.97,
            "images": 50,
            "models": {"fp16": model},
        },
        "eval_models": {"fp16": model},
        "pt_eval": {
            "status": "ok",
            "model_type": "plate_detection",
            "metrics": {
                "map50": 0.986,
                "map50_95": 0.875,
                "precision": 0.979,
                "recall": 0.968,
                "images": 50,
            },
            "dataset_evidence": {
                "source": "manual_edge_validation_pack",
                "pack_signature": "same-pack-50",
                "image_count": 50,
            },
        },
        "comparison_pt_bmodel": comparison,
    }


def _export_detail_plate_detection_runtime() -> dict[str, Any]:
    row = _export_detail()
    row.update(
        {
            "id": 148,
            "task_id": 122,
            "task_name": "lxl电动车车牌识别",
            "yolo_train_job_id": 271,
            "bmodel_filename": "plate_detection_fp16_1b.bmodel",
            "bmodel_filenames": ["plate_detection_fp16_1b.bmodel"],
            "edge_int8_eval_label": "待回传",
            "edge_result_status": "result_pending",
            "current_verdict_label": "待完成边端验证",
            "compatibility_summary": {
                "schema_version": "plate_bm1688_compatibility_v1",
                "model_scope": "plate_detection_component",
                "model_scope_label": "车牌检测 Pose 组件",
                "training_recipe_key": "plate_recognition_v1",
                "network_names": ["plate_detection"],
                "precision_variants": ["fp16"],
                "compiled_components": ["pose"],
                "expected_components": ["pose", "ocr", "color", "plate_type"],
                "missing_components": ["ocr", "color", "plate_type"],
                "compile_status": "pass",
                "generic_validation_status": "not_applicable",
                "specialized_runtime_status": "pass",
                "bmrt_test": {"return_code": 0, "elapsed_seconds": 0.535},
                "inference": {"return_code": 0, "elapsed_seconds": 13.336},
                "input_image_count": 50,
                "result_image_count": 50,
                "images_with_detections": 50,
                "total_detections": 64,
                "precision_evaluation": {
                    "status": "computed",
                    "type": "bbox",
                    "map50": 0.9876,
                    "map50_95": 0.8765,
                    "precision": 0.98,
                    "recall": 0.97,
                    "f1": 0.9749,
                    "images": 50,
                    "reason": None,
                },
                "verdict": "component_compatible",
                "verdict_label": "车牌检测组件可在 BM1688 运行",
                "release_scope_label": "仅检测组件，不代表完整车牌复合识别",
            },
        }
    )
    report = row["board_validation"]["report"]
    report.update(
        {
            "job_id": 150,
            "image_count": 351,
            "labeled_image_count": 351,
            "result": _plate_detection_result(),
        }
    )
    report["metrics"]["eval_dataset"]["image_count"] = 32
    report["metrics"]["bmrt_tests"] = [
        {
            "bmodel": "plate_detection_fp16_1b.bmodel",
            "ok": True,
            "calculate_time_ms": 21.521,
        }
    ]
    report["metrics"]["infer_runs"] = [
        {
            "bmodel": "plate_detection_fp16_1b.bmodel",
            "tag": "bmcv_fp16_detection",
            "ok": False,
        }
    ]
    report["metrics"]["bmcv_detection"] = {
        "image_count": None,
        "with_boxes": None,
        "total_boxes": None,
    }
    row["board_validation"].update(
        {
            "validation_id": 150,
            "report_summary": "manual:run_and_eval; images=351, labeled=351; 边端样本包上传 45%",
        }
    )
    fp16_model = _plate_detection_result()["eval_models"]["fp16"]
    comparison = _plate_detection_result()["comparison_pt_bmodel"]
    row["quality_report"]["sop_eval"].update(
        {
            "pt": _plate_detection_result()["pt_eval"],
            "bmodel": {"status": "ok", "metrics": fp16_model},
            "bmodels": {"fp16": fp16_model},
            "comparison_pt_bmodel": comparison,
        }
    )
    return row


def _export_detail_plate_recognition() -> dict[str, Any]:
    row = _export_detail()
    row.update(
        {
            "task_name": "lxl电动车车牌识别",
            "bmodel_filename": "plate_recognition_fp16_1b.bmodel",
            "bmodel_filenames": ["plate_recognition_fp16_1b.bmodel"],
            "edge_int8_eval_label": "需复核",
            "edge_result_status": "review",
            "verdict_code": "training_failed",
            "current_verdict_label": "训练未达标",
            "training_assessment": {
                "status_code": "conditional_passed",
                "status_label": "条件达标",
                "reason_codes": ["ocr_test_accuracy"],
                "reason_summary": "2 项关键指标缺失，需要补充验证",
                "evidence": {
                    "summary": "2 项关键指标缺失，需要补充验证",
                    "checks": [
                        {
                            "key": "pose_box_map50",
                            "label": "Pose 车牌框 mAP50",
                            "status": "达标",
                            "value": 0.99114,
                            "threshold": 0.9,
                        },
                        {
                            "key": "pose_box_map50_95",
                            "label": "Pose 车牌框 mAP50-95",
                            "status": "达标",
                            "value": 0.88343,
                            "threshold": 0.6,
                        },
                        {
                            "key": "pose_keypoint_map50",
                            "label": "Pose 四角点 mAP50",
                            "status": "达标",
                            "value": 0.9798,
                            "threshold": 0.9,
                        },
                        {
                            "key": "color_val_accuracy",
                            "label": "车牌颜色 val 准确率",
                            "status": "达标",
                            "value": 0.9688,
                            "threshold": 0.9,
                        },
                        {
                            "key": "plate_type_test_accuracy",
                            "label": "车牌类型 test 准确率",
                            "status": "达标",
                            "value": 0.9074,
                            "threshold": 0.9,
                        },
                        {
                            "key": "ocr_test_accuracy",
                            "label": "车牌号码 OCR test 准确率",
                            "status": "待评估",
                            "value": None,
                            "threshold": 0.8,
                        },
                    ],
                    "warnings": [],
                },
            },
            "edge_validation_assessment": {
                "status_code": "usable_needs_review",
                "status_label": "可运行但效果待确认",
                "reason_codes": ["plate_end_to_end_accuracy_missing"],
                "reason_summary": "板端复合模型运行通过，缺少同口径端到端精度",
            },
            "release_gate_assessment": {
                "status_code": "training_failed",
                "status_label": "不可发布",
                "reason_codes": ["training_conditional_passed"],
                "reason_summary": "OCR 测试指标缺失",
            },
        }
    )
    plate_eval = {
        "status": "ok",
        "model_type": "plate_recognition",
        "model_tag": "fp16",
        "model_filename": "plate_recognition_fp16_1b.bmodel",
        "backend": "plate_recognition_bm1688",
        "runtime_ok": True,
        "objects_detected": True,
        "input_image_count": 351,
        "result_image_count": 351,
        "images_with_detections": 351,
        "total_detections": 437,
        "recognized_plate_count": 437,
        "unique_plate_count": 320,
        "plate_number_samples": ["粤B12345", "粤B67890"],
        "plate_type_counts": {"greenPlate": 300, "doubleYellow": 137},
        "color_counts": {"green": 310, "yellow": 127},
        "inference": {"elapsed_seconds": 78.653},
        "bmrt_test": {"calculate_time_ms": 13.25},
        "rendered_images": {"status": "ok", "count": 351},
        "source_path": "/data/labelu/yolo_bm1688_exports/146/results_bmodel/summary.json",
    }
    row["quality_report"] = {
        "train_val": {"status": "unavailable"},
        "test_val": {"status": "unavailable"},
        "onnx_val": {"status": "skip"},
        "sop_eval": {
            "status": "not_run",
            "plate_recognition_eval": plate_eval,
        },
    }
    row["board_validation"]["report"]["result"] = {
        "execution_summary": {
            "status": "ok",
            "remote_exit_code": 0,
            "files_pulled": 361,
            "remote_host": "10.10.11.92",
            "has_board_report": True,
            "has_bmcv_infer": False,
            "has_bmodel_eval": True,
        },
        "plate_recognition_eval": plate_eval,
    }
    row["board_validation"]["report_summary"] = "边端验证执行完成"
    return row


def _export_detail_legacy_criteria() -> dict[str, Any]:
    row = _export_detail()
    row["edge_validation_assessment"] = {
        "status_code": "usable",
        "status_label": "可用",
        "reason_codes": [],
        "reason_summary": "边端任务执行完成",
    }
    sop_eval = row["quality_report"]["sop_eval"]
    sop_eval["criteria"] = {
        "primary_metric": "mAP50",
        "onnx_vs_pt_max_ratio": 0.03,
        "bmodel_vs_pt_max_ratio": 0.05,
    }
    sop_eval["comparison_pt_onnx"] = {
        "status": "ok",
        "delta_map50": 0.0085,
        "delta_ratio": 0.0172,
        "detail": "相对掉点 1.72%，历史阈值 3%",
    }
    sop_eval["comparison_pt_bmodel"] = {
        "status": "fail",
        "delta_map50": 0.0701,
        "delta_ratio": 0.1417,
        "detail": "相对掉点 14.17%，文档阈值 ≤5%",
    }
    return row


def _export_detail_evaluator_wall_clock_only() -> dict[str, Any]:
    row = _export_detail()
    execution = row["board_validation"]["report"]["result"]["execution"]
    for key in (
        "avg_inference_ms",
        "p50_inference_ms",
        "p95_inference_ms",
        "fps",
    ):
        execution.pop(key, None)
    bmodel_metrics = row["quality_report"]["sop_eval"]["bmodel"]["metrics"]
    bmodel_metrics["speed_avg_ms"] = 1558.0
    bmodel_metrics.pop("extra_speed_metrics", None)
    return row


def _export_detail_failed_with_malformed_metric_lists() -> dict[str, Any]:
    row = _export_detail()
    board_validation = row["board_validation"]
    board_validation["status"] = "FAILED"
    board_validation["error_message"] = "边端 Agent 执行失败：bmrt_test 未生成结果"
    board_validation["report_summary"] = "边端执行失败，未形成可用性能报告"
    metrics = board_validation["report"]["metrics"]
    metrics["bmrt_tests"] = {"unexpected": "object"}
    metrics["infer_runs"] = {"unexpected": "object"}
    bmodel = row["quality_report"]["sop_eval"]["bmodel"]
    bmodel["status"] = "fail"
    bmodel["log_tail"] = "bmodel evaluator exited with code 2"
    bmodel["metrics"] = {}
    return row


def _export_detail_partial_edge_result() -> dict[str, Any]:
    row = _export_detail_failed_with_malformed_metric_lists()
    board_validation = row["board_validation"]
    board_validation["status"] = "PARTIAL_COMPLETED"
    board_validation["error_message"] = None
    board_validation["report_summary"] = "仅回传执行日志，精度报告尚未回传"
    return row


def _export_detail_failed_with_residual_pass_metrics() -> dict[str, Any]:
    row = _export_detail()
    row["verdict_code"] = "release_ready"
    row["current_verdict_label"] = "可进入发布评估"
    row["release_gate_assessment"] = {
        "status_code": "publishable",
        "status_label": "可发布",
        "reason_codes": [],
        "reason_summary": "历史门禁结果仍为通过",
    }
    row["edge_validation_assessment"] = {
        "status_code": "usable",
        "status_label": "可用",
        "reason_codes": [],
        "reason_summary": "历史边端结果仍为可用",
    }
    board_validation = row["board_validation"]
    board_validation["status"] = "FAILED"
    board_validation["error_message"] = "本次边端执行失败"
    board_validation["report_summary"] = "本次失败，但响应仍残留上一次精度指标"
    return row


def _export_detail_failed_onnx_with_residual_pass_metrics() -> dict[str, Any]:
    row = _export_detail()
    row["verdict_code"] = "release_ready"
    row["current_verdict_label"] = "可进入发布评估"
    row["release_gate_assessment"] = {
        "status_code": "publishable",
        "status_label": "可发布",
        "reason_codes": [],
        "reason_summary": "历史门禁结果仍为通过",
    }
    onnx = row["quality_report"]["sop_eval"]["onnx"]
    onnx["status"] = "fail"
    onnx["error"] = "本次 ONNX 评估进程退出，但响应仍残留上一次指标"
    return row


def _manual_job_row() -> dict[str, Any]:
    return {
        "id": 15,
        "export_id": 123,
        "task_id": 88,
        "task_name": "边端验证任务",
        "status": "FAILED",
        "mode": "run_and_eval",
        "image_count": 5,
        "labeled_image_count": 4,
        "unlabeled_image_count": 1,
        "threshold_source": "manual",
        "report_summary": "边端执行完成，但 bmodel SOP 未通过",
        "progress": {
            "stage": "finalize",
            "message": "边端执行结束，正在回填最终结果",
            "updated_at": "2026-07-04T10:13:00",
            "history": [
                {
                    "stage": "prepare",
                    "message": "任务已启动",
                    "at": "2026-07-04T10:10:00",
                },
                {
                    "stage": "ssh_connect",
                    "message": "已连接边端设备",
                    "at": "2026-07-04T10:11:00",
                },
                {
                    "stage": "remote_run",
                    "message": "边端脚本执行中",
                    "at": "2026-07-04T10:12:00",
                },
                {
                    "stage": "finalize",
                    "message": "已回填最终结果",
                    "at": "2026-07-04T10:13:00",
                },
            ],
        },
        "created_at": "2026-07-04T10:10:00",
        "updated_at": "2026-07-04T10:13:00",
    }


def _manual_job_detail() -> dict[str, Any]:
    row = _manual_job_row()
    row.update(
        {
            "sample_ids": [1, 2, 3, 4, 5],
            "attachment_ids": [],
            "external_attachments": [],
            "thresholds": {
                "conf_thres": 0.3,
                "nms_thres": 0.7,
                "match_iou_thres": 0.5,
                "source": "manual",
            },
            "ai_thresholds": None,
            "result": {
                "execution_summary": {
                    "remote_host": "10.10.11.26",
                    "remote_job_dir": "/data/labelu_edge/jobs/manual-job-15",
                    "remote_exit_code": 0,
                    "files_pulled": 17,
                    "sophon_demo_root": "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
                },
                "status": "fail",
                "error": "bmodel SOP 未通过",
            },
            "error_message": "bmodel SOP 未通过",
            "started_at": "2026-07-04T10:10:00",
            "completed_at": "2026-07-04T10:13:00",
        }
    )
    return row


def _plate_detection_manual_job() -> dict[str, Any]:
    return {
        "id": 150,
        "export_id": 148,
        "task_id": 122,
        "task_name": "lxl电动车车牌识别",
        "status": "COMPLETED",
        "mode": "run_and_eval",
        "image_count": 351,
        "labeled_image_count": 351,
        "unlabeled_image_count": 0,
        "threshold_source": "system_default",
        "edge_device_name": "龙岗项目3号三面板",
        "edge_host": "10.10.11.90",
        "edge_result_status": "pass",
        "report_summary": "边端样本包上传 45%",
        "result": _plate_detection_result(),
        "created_at": "2026-08-04T13:40:00",
        "updated_at": "2026-08-04T13:59:23",
        "completed_at": "2026-08-04T13:52:02",
        "thresholds": {
            "conf_thres": 0.35,
            "nms_thres": 0.7,
            "match_iou_thres": 0.5,
        },
    }


def _manual_visual_job(job_id: int) -> dict[str, Any]:
    latest = job_id == 28
    image_count = 10 if latest else 8
    row = {
        "id": job_id,
        "export_id": 123,
        "task_id": 88,
        "task_name": "边端验证任务",
        "status": "COMPLETED",
        "mode": "run_and_eval",
        "image_count": image_count,
        "labeled_image_count": image_count,
        "unlabeled_image_count": 0,
        "threshold_source": "manual",
        "edge_device_name": "算能BM1688",
        "edge_host": "192.168.10.8",
        "edge_result_status": "pass",
        "report_summary": "边端验证执行完成",
        "created_at": "2026-08-03T17:46:24" if latest else "2026-08-02T16:20:00",
        "updated_at": "2026-08-03T17:49:24" if latest else "2026-08-02T16:23:00",
    }
    map50 = 0.81 if latest else 0.76
    precision = 0.84 if latest else 0.79
    recall = 0.78 if latest else 0.72
    f1 = 0.81 if latest else 0.75
    speed = 12.5 if latest else 14.5
    preview_name = f"task-{job_id}-boxed.png"

    def model_metrics(tag: str, offset: float) -> dict[str, Any]:
        metrics = {
            "model_filename": f"yolov11n_{tag}_1b.bmodel",
            "status": "ok",
            "map50_95": map50 - 0.2 + offset,
            "map50": map50 + offset,
            "precision": precision + offset,
            "recall": recall + offset,
            "f1": f1 + offset,
            "speed_avg_ms": speed
            + (1.5 if tag == "fp32" else (0.8 if tag == "fp16" else 0)),
            "cpu_memory_mb": 128.0 if latest else 132.0,
            "images": image_count,
            "backend": "sophon-bmcv",
        }
        if tag == "int8":
            metrics["class_metrics"] = [
                {"id": 0, "name": "car", "labels": 6, "tp": 5, "fp": 1, "fn": 1},
                {
                    "id": 1,
                    "name": "plate",
                    "labels": 4,
                    "tp": 4 if latest else 3,
                    "fp": 1,
                    "fn": 0 if latest else 1,
                },
            ]
            if latest:
                metrics["performance_probe_status"] = "ok"
                metrics["runtime_metrics"] = {
                    "warmup_count_requested": 50,
                    "warmup_count_completed": 50,
                    "repeat_count_requested": 1000,
                    "repeat_count_completed": 1000,
                    "avg_inference_ms": 7.2,
                    "p50_inference_ms": 7.0,
                    "p95_inference_ms": 8.1,
                    "p99_inference_ms": 8.8,
                    "throughput_fps": 52.4,
                    "latency_series_available": True,
                    "stage_series_available": True,
                    "stage_metrics": {
                        "decode_ms": {
                            "sample_count": 1000,
                            "avg": 1.1,
                            "p50": 1.0,
                            "p95": 1.4,
                            "p99": 1.6,
                            "stddev": 0.2,
                            "outlier_count": 3,
                        },
                        "preprocess_ms": {
                            "sample_count": 1000,
                            "avg": 2.2,
                            "p50": 2.1,
                            "p95": 2.8,
                            "p99": 3.0,
                            "stddev": 0.3,
                            "outlier_count": 4,
                        },
                        "inference_ms": {
                            "sample_count": 1000,
                            "avg": 7.2,
                            "min": 6.4,
                            "max": 9.1,
                            "p50": 7.0,
                            "p95": 8.1,
                            "p99": 8.8,
                            "stddev": 0.5,
                            "outlier_count": 8,
                        },
                        "postprocess_ms": {
                            "sample_count": 1000,
                            "avg": 1.8,
                            "p50": 1.7,
                            "p95": 2.2,
                            "p99": 2.5,
                            "stddev": 0.25,
                            "outlier_count": 5,
                        },
                        "total_ms": {
                            "sample_count": 1000,
                            "avg": 12.3,
                            "p50": 12.0,
                            "p95": 13.9,
                            "p99": 15.0,
                            "stddev": 0.9,
                            "outlier_count": 10,
                        },
                    },
                }
                metrics["resource_usage"] = {
                    "sample_count": 25,
                    "series_available": True,
                    "cpu_usage": {
                        "sample_count": 25,
                        "avg": 31.2,
                        "p95": 44.0,
                        "max": 48.0,
                    },
                    "process_cpu_usage": {
                        "sample_count": 25,
                        "avg": 17.0,
                        "p95": 24.0,
                        "max": 26.0,
                    },
                    "memory_mb": {
                        "sample_count": 25,
                        "avg": 486.5,
                        "p95": 492.0,
                        "max": 495.0,
                    },
                    "system_memory_usage": {
                        "sample_count": 25,
                        "avg": 54.0,
                        "p95": 56.0,
                        "max": 57.0,
                    },
                    "npu_usage": {
                        "sample_count": 25,
                        "avg": 72.4,
                        "p95": 86.0,
                        "max": 91.0,
                    },
                    "temperature_c": {
                        "sample_count": 25,
                        "avg": 58.3,
                        "p95": 61.0,
                        "max": 62.0,
                    },
                    "power_w": {"sample_count": 25, "avg": 8.6, "p95": 9.2, "max": 9.5},
                }
                metrics["environment"] = {
                    "device_serial": "BM1688-EDGE-001",
                    "hostname": "bm1688-box",
                    "platform": "Linux-5.10-aarch64",
                    "kernel": "5.10.4",
                    "python": "3.8.10",
                    "sophon_sail_version": "3.8.0",
                    "firmware_version": "1.9.2",
                    "bm_smi_version": {"stdout": "bm-smi 1.0"},
                    "libsophon_packages": {"stdout": "libsophon 0.5.0"},
                    "model_sha256": "abc123",
                }
                metrics["stability"] = {
                    "enabled": True,
                    "requested_duration_seconds": 60,
                    "duration_seconds": 60.1,
                    "successful_frames": 3120,
                    "failed_frames": 1,
                    "failure_rate": 0.0003204,
                    "restart_count": None,
                    "series_available": True,
                }
        if latest and tag != "int8":
            probe_avg = 18.4 if tag == "fp32" else 10.2
            metrics["performance_probe_status"] = "ok"
            metrics["runtime_metrics"] = {
                "warmup_count_requested": 50,
                "warmup_count_completed": 50,
                "repeat_count_requested": 1000,
                "repeat_count_completed": 1000,
                "throughput_fps": 31.5 if tag == "fp32" else 44.8,
                "latency_series_available": True,
                "stage_series_available": True,
                "stage_metrics": {
                    "inference_ms": {
                        "sample_count": 1000,
                        "avg": probe_avg,
                        "min": probe_avg - 1.1,
                        "max": probe_avg + 2.4,
                        "p50": probe_avg - 0.2,
                        "p95": probe_avg + 1.4,
                        "p99": probe_avg + 2.0,
                        "stddev": 0.6,
                        "outlier_count": 5,
                    }
                },
            }
            metrics["resource_usage"] = {
                "sample_count": 20,
                "series_available": True,
                "available_fields": [
                    "cpu_usage",
                    "memory_mb",
                    "npu_usage",
                    "temperature_c",
                ],
                "unavailable_fields": ["power_w"],
                "unavailable_reasons": {
                    "power_w": "device_power_telemetry_not_exposed"
                },
                "cpu_usage": {
                    "sample_count": 20,
                    "avg": 28.0,
                    "p95": 39.0,
                    "max": 43.0,
                },
                "memory_mb": {
                    "sample_count": 20,
                    "avg": 470.0,
                    "p95": 480.0,
                    "max": 484.0,
                },
                "npu_usage": {
                    "sample_count": 20,
                    "avg": 64.0,
                    "p95": 82.0,
                    "max": 88.0,
                },
                "temperature_c": {
                    "sample_count": 20,
                    "avg": 57.0,
                    "p95": 60.0,
                    "max": 61.0,
                },
            }
        return metrics

    row.update(
        {
            "sample_ids": list(range(1, image_count + 1)),
            "attachment_ids": [],
            "external_attachments": [],
            "thresholds": {
                "conf_thres": 0.3 if latest else 0.25,
                "nms_thres": 0.7,
                "match_iou_thres": 0.5,
                "source": "manual",
            },
            "ai_thresholds": None,
            "result": {
                "status": "pass",
                "analysis": {"status": "pass"},
                "prediction_summary": {
                    "image_count": image_count,
                    "with_boxes": image_count - 1,
                    "total_boxes": 14 if latest else 10,
                    "avg_boxes_per_image": 1.4 if latest else 1.25,
                },
                "preview": {"preview_files": [preview_name]},
                "pack": {"file_mappings": [{"packed_name": preview_name}]},
                "eval_models": {
                    "fp32": {"metrics": model_metrics("fp32", 0.02)},
                    "fp16": {"metrics": model_metrics("fp16", 0.01)},
                    "int8": {"metrics": model_metrics("int8", 0.0)},
                },
                "execution_summary": {
                    "remote_host": "192.168.10.8",
                    "remote_exit_code": 0,
                    "files_pulled": 18,
                },
                "execution": {
                    "remote_host": "192.168.10.8",
                    "remote_exit_code": 0,
                    "files_pulled": 18,
                    "environment": {
                        "after": {
                            "environment_ready": True,
                            "sophon_demo_ok": True,
                            "validator_ok": True,
                        }
                    },
                    "artifacts": {"summary": "summary.json", "preview": preview_name},
                },
            },
            "error_message": None,
            "started_at": row["created_at"],
            "completed_at": row["updated_at"],
        }
    )
    return row


def _normalize_on_demand_export_path(path: str) -> str:
    for suffix in ("/summary", "/evaluation"):
        if path.endswith(suffix) and "/yolo-bm1688-exports/" in path:
            return path[: -len(suffix)]
    return path


def _handle_api(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = parsed.path
    query = parse_qs(parsed.query)
    method = request.method

    if path == "/yolo-bm1688-exports":
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=BM1688_PAGE_FILE.read_text(encoding="utf-8"),
        )
        return
    if path.startswith("/assets/"):
        content_type = "text/plain; charset=utf-8"
        body = ""
        if path.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif path.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        route.fulfill(status=200, content_type=content_type, body=body)
        return
    if path == "/favicon.svg":
        route.fulfill(
            status=200,
            content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"></svg>',
        )
        return
    if path == "/favicon.ico":
        route.fulfill(status=204, body="")
        return
    if not path.startswith(API_PREFIX):
        route.fulfill(status=404, body="")
        return

    api_path = _normalize_on_demand_export_path(path[len(API_PREFIX) :])
    if api_path.startswith("/annotation-taxonomy/label-presets") and method == "GET":
        _json_response(route, {"data": []})
        return
    if (
        api_path.startswith("/annotation-taxonomy/dataset-categories")
        and method == "GET"
    ):
        _json_response(route, {"data": []})
        return
    if api_path == "/yolo-bm1688-exports/compiler-settings" and method == "GET":
        _json_response(
            route,
            {
                "data": {
                    "auto_compile": True,
                    "compile_platforms": "bm1688",
                    "bmodel_basename": "yolov11n",
                }
            },
        )
        return
    if api_path == "/yolo-bm1688-exports/edge-ssh-settings" and method == "GET":
        _json_response(
            route,
            {
                "data": {
                    "enabled": True,
                    "host": "10.10.11.26",
                    "user": "linaro",
                    "port": 22,
                    "password_configured": True,
                    "password_source_type": "mysql:encrypted",
                    "password_masked_hint": "****1818",
                    "auto_after_export": True,
                    "docker_container": "bm1688",
                    "remote_base": "/data/labelu_edge/jobs",
                }
            },
        )
        return
    if api_path == "/yolo-bm1688-exports" and method == "GET" and "page" in query:
        _json_response(route, {"data": [_export_row()], "meta_data": {"total": 1}})
        return
    if api_path == "/yolo-bm1688-exports/catalog-summary" and method == "GET":
        _json_response(
            route,
            {
                "data": {
                    "total": 1,
                    "running": 0,
                    "blocked": 0,
                    "publish_ready": 1,
                    "pending_edge": 0,
                    "failed": 0,
                }
            },
        )
        return
    if api_path == "/tasks/88/samples" and method == "GET":
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return
    if api_path == "/yolo-bm1688-exports/123" and method == "GET":
        _json_response(route, {"data": _export_detail()})
        return
    if api_path == "/yolo-bm1688-exports/123/runtime-log/compile" and method == "GET":
        route.fulfill(
            status=200,
            content_type="text/plain; charset=utf-8",
            body="compile.log full line 1\ncompile.log full line 2\n",
        )
        return
    if (
        api_path == "/yolo-bm1688-exports/123/runtime-log/edge_remote_run"
        and method == "GET"
    ):
        route.fulfill(
            status=200,
            content_type="text/plain; charset=utf-8",
            body="edge remote line 1\nedge remote line 2\n",
        )
        return
    if api_path == "/yolo-bm1688-exports/123/board-validation" and method == "GET":
        _json_response(route, {"data": _export_row()["board_validation"]})
        return
    if api_path == "/yolo-edge-validation/jobs" and method == "GET":
        _json_response(route, {"data": [_manual_job_row()], "meta_data": {"total": 1}})
        return
    if api_path == "/yolo-edge-validation/jobs/15" and method == "GET":
        _json_response(route, {"data": _manual_job_detail()})
        return
    if (
        api_path.startswith("/yolo-edge-validation/jobs/")
        and api_path.endswith("/visual-evidence")
        and method == "GET"
    ):
        validation_id = int(api_path.split("/")[3])
        _json_response(
            route,
            {
                "data": {
                    "schema_version": "bm1688-visual-evidence-v2",
                    "validation_id": validation_id,
                    "export_id": 123,
                    "page_size": 12,
                    "available": False,
                    "counts": {},
                    "items": [],
                }
            },
        )
        return
    if (
        api_path
        in (
            "/yolo-bm1688-exports/123/sop-eval-report/pt",
            "/yolo-bm1688-exports/123/sop-eval-report/onnx",
            "/yolo-bm1688-exports/123/sop-eval-report/bmodel",
        )
        and method == "GET"
    ):
        route.fulfill(
            status=200,
            content_type="text/markdown; charset=utf-8",
            body="# mock sop report\n\n- ok\n",
        )
        return
    if (
        api_path.startswith("/yolo-bm1688-exports/123/sop-eval-asset/")
        and method == "GET"
    ):
        route.fulfill(status=404, body="")
        return

    raise AssertionError(f"Unexpected BM1688 API call: {method} {api_path}")


def _manual_visual_handler(preview_requests: list[str]):
    jobs = [_manual_visual_job(28), _manual_visual_job(27)]
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def handle(route: Route) -> None:
        request = route.request
        path = urlparse(request.url).path
        method = request.method
        if path == f"{API_PREFIX}/yolo-edge-validation/jobs" and method == "GET":
            _json_response(route, {"data": jobs, "meta_data": {"total": len(jobs)}})
            return
        for job in jobs:
            detail_path = f"{API_PREFIX}/yolo-edge-validation/jobs/{job['id']}"
            preview_prefix = f"{detail_path}/preview/"
            if path == detail_path and method == "GET":
                _json_response(route, {"data": job})
                return
            if path.startswith(preview_prefix) and method == "GET":
                preview_requests.append(path)
                route.fulfill(status=200, content_type="image/png", body=tiny_png)
                return
        _handle_api(route)

    return handle


def _handle_api_edge_pending(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = _normalize_on_demand_export_path(parsed.path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(route, {"data": _export_detail_edge_pending()})
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/123/board-validation"
        and request.method == "GET"
    ):
        _json_response(
            route, {"data": _export_detail_edge_pending()["board_validation"]}
        )
        return
    _handle_api(route)


def _handle_api_missing_eval_values(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(route, {"data": _export_detail_missing_eval_values()})
        return
    _handle_api(route)


def _handle_api_plate_recognition(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = _normalize_on_demand_export_path(parsed.path)
    query = parse_qs(parsed.query)
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports"
        and request.method == "GET"
        and "page" in query
    ):
        _json_response(
            route,
            {"data": [_export_detail_plate_recognition()], "meta_data": {"total": 1}},
        )
        return
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(route, {"data": _export_detail_plate_recognition()})
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/123/board-validation"
        and request.method == "GET"
    ):
        _json_response(
            route,
            {"data": _export_detail_plate_recognition()["board_validation"]},
        )
        return
    _handle_api(route)


def _handle_api_legacy_criteria(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(route, {"data": _export_detail_legacy_criteria()})
        return
    _handle_api(route)


def _handle_api_evaluator_wall_clock_only(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(route, {"data": _export_detail_evaluator_wall_clock_only()})
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/123/board-validation"
        and request.method == "GET"
    ):
        _json_response(
            route,
            {"data": _export_detail_evaluator_wall_clock_only()["board_validation"]},
        )
        return
    _handle_api(route)


def _handle_api_failed_malformed_metrics(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(
            route, {"data": _export_detail_failed_with_malformed_metric_lists()}
        )
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/123/board-validation"
        and request.method == "GET"
    ):
        _json_response(
            route,
            {
                "data": _export_detail_failed_with_malformed_metric_lists()[
                    "board_validation"
                ]
            },
        )
        return
    _handle_api(route)


def _handle_api_partial_edge_result(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(route, {"data": _export_detail_partial_edge_result()})
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/123/board-validation"
        and request.method == "GET"
    ):
        _json_response(
            route,
            {"data": _export_detail_partial_edge_result()["board_validation"]},
        )
        return
    _handle_api(route)


def _handle_api_failed_residual_pass_metrics(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(
            route, {"data": _export_detail_failed_with_residual_pass_metrics()}
        )
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/123/board-validation"
        and request.method == "GET"
    ):
        _json_response(
            route,
            {
                "data": _export_detail_failed_with_residual_pass_metrics()[
                    "board_validation"
                ]
            },
        )
        return
    _handle_api(route)


def _handle_api_failed_onnx_residual_pass_metrics(route: Route) -> None:
    request = route.request
    path = _normalize_on_demand_export_path(urlparse(request.url).path)
    if path == f"{API_PREFIX}/yolo-bm1688-exports/123" and request.method == "GET":
        _json_response(
            route,
            {"data": _export_detail_failed_onnx_with_residual_pass_metrics()},
        )
        return
    _handle_api(route)


def _handle_api_plate_detection_runtime(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = _normalize_on_demand_export_path(parsed.path)
    query = parse_qs(parsed.query)
    method = request.method
    detail = _export_detail_plate_detection_runtime()
    job = _plate_detection_manual_job()
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports"
        and method == "GET"
        and "page" in query
    ):
        _json_response(route, {"data": [detail], "meta_data": {"total": 1}})
        return
    if path == f"{API_PREFIX}/yolo-bm1688-exports/148" and method == "GET":
        if parsed.path.endswith("/summary"):
            detail["quality_report"] = None
            detail["board_validation"]["execution_device_host"] = "10.10.11.90"
            detail["board_validation"]["report"] = None
        _json_response(route, {"data": detail})
        return
    if (
        path == f"{API_PREFIX}/yolo-bm1688-exports/148/board-validation"
        and method == "GET"
    ):
        _json_response(route, {"data": detail["board_validation"]})
        return
    if (
        path
        in (
            f"{API_PREFIX}/yolo-bm1688-exports/148/runtime-log/compile",
            f"{API_PREFIX}/yolo-bm1688-exports/148/runtime-log/edge_remote_run",
        )
        and method == "GET"
    ):
        route.fulfill(
            status=200, content_type="text/plain; charset=utf-8", body="runtime ok\n"
        )
        return
    if (
        path
        in (
            f"{API_PREFIX}/yolo-bm1688-exports/148/sop-eval-report/pt",
            f"{API_PREFIX}/yolo-bm1688-exports/148/sop-eval-report/onnx",
            f"{API_PREFIX}/yolo-bm1688-exports/148/sop-eval-report/bmodel",
        )
        and method == "GET"
    ):
        route.fulfill(
            status=200,
            content_type="text/markdown; charset=utf-8",
            body="# plate detection evaluation\n",
        )
        return
    if (
        path.startswith(f"{API_PREFIX}/yolo-bm1688-exports/148/sop-eval-asset/")
        and method == "GET"
    ):
        route.fulfill(status=404, body="")
        return
    if path == f"{API_PREFIX}/yolo-edge-validation/jobs" and method == "GET":
        rows = [] if "task_id" in query else [job]
        _json_response(route, {"data": rows, "meta_data": {"total": len(rows)}})
        return
    if path == f"{API_PREFIX}/yolo-edge-validation/jobs/150" and method == "GET":
        _json_response(route, {"data": job})
        return
    if (
        path == f"{API_PREFIX}/yolo-edge-validation/jobs/150/visual-evidence"
        and method == "GET"
    ):
        _json_response(route, {"data": {"items": []}})
        return
    _handle_api(route)


def test_bm1688_page_shows_detail_modal_after_selecting_row():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(f"{PAGE_URL}?embedded=1")

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='detail']").click()

        expect(page.locator("#exportDetailModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#detailSummaryTitle")).to_contain_text("#123")
        dialog_box = page.locator("#exportDetailPanel").bounding_box()
        assert dialog_box is not None
        assert dialog_box["x"] > 0
        assert dialog_box["y"] > 0
        assert dialog_box["x"] + dialog_box["width"] < 1600
        assert dialog_box["y"] + dialog_box["height"] < 1400
        expect(page.locator("[data-detail-tab='overview']")).to_be_visible()
        expect(page.locator("[data-detail-tab='eval']")).to_be_visible()
        expect(page.locator("[data-detail-tab='steps']")).to_be_visible()
        expect(page.locator("[data-detail-tab='manual']")).to_be_visible()
        expect(page.locator("[data-detail-tab='config']")).to_be_visible()
        expect(page).to_have_url(
            re.compile(r"embedded=1.*export_id=123|export_id=123.*embedded=1")
        )

        page.locator("#btnDetailCloseX").click()
        expect(page.locator("#exportDetailModal.show")).to_have_count(0)
        expect(page).to_have_url(re.compile(r"[?&]embedded=1(?:&|$)"))
        assert "export_id=" not in page.url

        browser.close()


def test_bm1688_list_filters_are_forwarded_to_the_catalog_api():
    list_queries: list[dict[str, list[str]]] = []

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        if (
            parsed.path == f"{API_PREFIX}/yolo-bm1688-exports"
            and request.method == "GET"
            and "page" in parse_qs(parsed.query)
        ):
            list_queries.append(parse_qs(parsed.query))
        _handle_api(route)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(f"{PAGE_URL}?embedded=1")

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#filterExportTaskName").fill("车牌")
        page.locator("#filterExportStatus").select_option("COMPLETED")
        page.evaluate("""
            () => {
              const option = document.createElement("option");
              option.value = "7";
              option.textContent = "车牌数据";
              document.getElementById("filterExportDatasetCategory").appendChild(option);
            }
            """)
        page.locator("#filterExportDatasetCategory").select_option("7")
        page.locator("#btnExportSearch").click()

        expect(page.locator("#bmListSectionHint")).to_contain_text("当前筛选共 1 条")
        page.wait_for_timeout(100)
        assert any(
            query.get("task_name") == ["车牌"]
            and query.get("dataset_category_id") == ["7"]
            and query.get("export_status") == ["COMPLETED"]
            for query in list_queries
        )
        browser.close()


def test_bm1688_steps_tab_shows_runtime_stage_and_dual_logs():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='detail']").click()
        page.locator("[data-detail-tab='steps']").click()

        expect(page.locator("#detailRuntimeCurrentStage")).to_contain_text(
            "回填最终结果", timeout=15_000
        )
        expect(page.locator("#detailRuntimeOutcome")).to_contain_text(
            "bmodel SOP 未通过"
        )
        expect(page.locator("#detailRuntimeCompileLog")).to_contain_text(
            "compile.log full line 2"
        )
        expect(page.locator("#detailRuntimeEdgeLog")).to_contain_text(
            "edge remote line 2"
        )
        expect(page.locator("#detailRuntimeTimeline")).to_contain_text("执行边端脚本")

        browser.close()


def test_bm1688_row_eval_action_opens_eval_modal_without_switching_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()

        expect(page).to_have_url(PAGE_URL)
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalExportId")).to_contain_text("#123")
        expect(page.locator("#evalStepsModal.show")).to_have_count(0)
        expect(page.locator("#edgeManualModal.show")).to_have_count(0)

        browser.close()


def test_bm1688_eval_modal_uses_v2_absolute_drop_gate_and_signal_first_tabs():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalContextLine")).to_contain_text("INT8 业务质量")

        signal_cards = page.locator("#evalVerdictBar .eval-signal-card")
        expect(signal_cards).to_have_count(5)
        expect(page.locator("#evalVerdictBar")).to_contain_text("最终发布门禁")
        expect(page.locator("#evalVerdictBar")).to_contain_text("边端执行")
        expect(page.locator("#evalVerdictBar")).to_contain_text("ONNX 转换保真")
        expect(page.locator("#evalVerdictBar")).to_contain_text("INT8 量化保真")
        expect(page.locator("#evalVerdictBar")).to_contain_text("证据完整度")
        expect(page.locator("#evalVerdictBar")).to_contain_text("需复核")
        onnx_signal = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("ONNX 转换保真"))'
        )
        int8_signal = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("INT8 量化保真"))'
        )
        expect(onnx_signal).to_contain_text("绝对掉点 0.0040 · 相对掉点 0.0049")
        expect(int8_signal).to_contain_text("绝对掉点 0.0150 · 相对掉点 0.0183")

        eval_tabs = page.locator("#evalModal [data-eval-workspace-tab]")
        expect(eval_tabs).to_have_count(4)
        expect(eval_tabs).to_have_text(
            ["精度与门禁", "分类诊断", "边端性能", "证据与报告"]
        )
        active_tab_style = page.locator('[data-eval-workspace-tab="gates"]').evaluate(
            "el => ({background: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderColor, weight: getComputedStyle(el).fontWeight})"
        )
        inactive_tab_style = page.locator(
            '[data-eval-workspace-tab="diagnosis"]'
        ).evaluate(
            "el => ({background: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderColor, weight: getComputedStyle(el).fontWeight})"
        )
        assert active_tab_style != inactive_tab_style
        assert int(active_tab_style["weight"]) >= 700

        gate_panel = page.locator('[data-eval-workspace-panel="gates"]')
        expect(gate_panel).to_be_visible()
        main_table = gate_panel.locator(".bm-eval-table").first
        expect(main_table.locator("thead")).to_contain_text("当前生效标准")
        expect(main_table.locator("thead")).to_contain_text("相对掉点比值（0-1）")

        pt_row = main_table.locator("tbody tr").filter(has_text="云端基准")
        expect(pt_row.locator("td").nth(5)).to_have_text("不适用")
        expect(pt_row.locator("td").nth(6)).to_have_text("不适用")

        onnx_row = main_table.locator("tbody tr").filter(has_text="云端转换模型")
        expect(onnx_row.locator("td").nth(3)).to_contain_text("未采集")
        expect(onnx_row.locator("td").nth(4)).to_contain_text("0.0000")
        expect(onnx_row.locator("td").nth(6)).to_have_text("0.0049")
        onnx_standard = onnx_row.locator("td").nth(7)
        expect(onnx_standard).to_contain_text("通过 ≤0.0050")
        expect(onnx_standard).to_contain_text("复核上限 0.0100")
        assert "%" not in onnx_standard.inner_text()

        bmodel_row = main_table.locator("tbody tr").filter(has_text="边端量化模型")
        expect(bmodel_row.locator("td").nth(1)).to_contain_text("未计算")
        expect(bmodel_row.locator("td").nth(1)).not_to_contain_text("0.7900")
        expect(bmodel_row.locator("td").nth(6)).to_have_text("0.0183")
        expect(bmodel_row.locator("td").nth(7)).to_contain_text("通过 ≤0.0100")
        expect(bmodel_row.locator("td").nth(9)).to_contain_text("需复核")
        assert "%" not in main_table.inner_text()

        for tab_name, panel_name in (
            ("diagnosis", "diagnosis"),
            ("performance", "performance"),
            ("evidence", "evidence"),
        ):
            page.locator(f'[data-eval-workspace-tab="{tab_name}"]').click()
            expect(
                page.locator(f'[data-eval-workspace-panel="{panel_name}"]')
            ).to_be_visible()

        page.locator('[data-eval-workspace-tab="diagnosis"]').click()
        class_rows = page.locator("#sopClassDropTable tbody tr")
        expect(class_rows).to_have_count(2)
        expect(class_rows.nth(0)).to_contain_text("person")
        expect(class_rows.nth(0)).to_contain_text("0.8000")
        expect(class_rows.nth(0)).to_contain_text("0.7800")

        evidence_panel = page.locator('[data-eval-workspace-panel="evidence"]')
        expect(evidence_panel).to_contain_text("同口径数据集 / split")
        expect(evidence_panel).to_contain_text("评估参数")
        expect(evidence_panel).to_contain_text("图片数一致性")
        expect(evidence_panel).to_contain_text("类别覆盖")
        expect(evidence_panel).to_contain_text("模型版本")
        expect(evidence_panel).to_contain_text("数据版本")
        expect(evidence_panel).to_contain_text("map50-absolute-drop-v2")
        expect(evidence_panel).to_contain_text("threshold_unit")
        expect(evidence_panel).to_contain_text("map50_absolute_drop")
        expect(evidence_panel).to_contain_text("模型转换门禁 map50-absolute-drop-v2")

        browser.close()


def test_bm1688_eval_workspace_tabs_are_all_visible_on_mobile():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        tabs = page.locator("#evalModal [data-eval-workspace-tab]")
        expect(tabs).to_have_count(4)
        boxes = tabs.evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {left:r.left, right:r.right, top:r.top, bottom:r.bottom}; })"
        )

        assert all(box["left"] >= 0 and box["right"] <= 390 for box in boxes)
        assert len({round(box["top"]) for box in boxes}) == 2
        browser.close()


def test_bm1688_plate_detection_uses_business_runtime_values_and_history_fallback():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_plate_detection_runtime)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='detail']").click()
        page.locator("[data-detail-tab='steps']").click()

        cards = page.locator("#boardValMetricsCards")
        expect(
            cards.locator(".mini-card").filter(has_text="验证图片数").locator(".v")
        ).to_have_text("351")
        expect(
            cards.locator(".mini-card").filter(has_text="BMRT 总耗时")
        ).to_contain_text("1.058 s")
        expect(
            cards.locator(".mini-card").filter(has_text="推理总耗时")
        ).to_contain_text("67.553 s")
        expect(
            cards.locator(".mini-card").filter(has_text="FP16 运行状态")
        ).to_contain_text("运行通过")
        expect(
            cards.locator(".mini-card").filter(has_text="推理结果图片")
        ).to_contain_text("351 / 351")
        expect(
            cards.locator(".mini-card").filter(has_text="bmcv 图片数")
        ).to_contain_text("平均 1.2479 个/图")
        expect(cards.locator(".mini-card").filter(has_text="结果回传")).to_contain_text(
            "714 个文件"
        )
        expect(cards).not_to_contain_text("21.521 ms")
        expect(cards).not_to_contain_text("FAIL")
        expect(page.locator("#detailRuntimeOutcome")).to_contain_text(
            "边端验证执行完成"
        )
        expect(page.locator("#detailRuntimeOutcome")).not_to_contain_text("上传 45%")

        page.locator("[data-detail-tab='manual']").click()
        host = page.locator("#detailManualValidationHost")
        expect(host).to_contain_text("任务 #150 结果", timeout=15_000)
        expect(host).to_contain_text("推理图片")
        expect(host).to_contain_text("351 张")
        expect(host).not_to_contain_text("当前还没有手动边端验证记录")
        assert page_errors == []

        browser.close()


def test_bm1688_plate_detection_renders_layered_compatibility_evidence():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_plate_detection_runtime)
        page.goto(f"{PAGE_URL}?embedded=1&export_id=148")

        panel = page.locator("#detailCompatibilityPanel")
        expect(panel).to_be_visible(timeout=15_000)
        expect(page.locator("#detailEdgeSshLine")).to_contain_text(
            "本次实际执行设备：10.10.11.90"
        )
        expect(panel).to_contain_text("BM1688 兼容性检查")
        expect(panel).to_contain_text("车牌检测组件可在 BM1688 运行")
        expect(panel).to_contain_text("FP16")
        expect(panel).to_contain_text("50 / 50 张")
        expect(panel).to_contain_text("64 个检测结果")
        expect(panel).to_contain_text("通用 Sophon Demo")
        expect(panel).to_contain_text("不适用")
        expect(panel).to_contain_text("仅检测组件，不代表完整车牌复合识别")

        browser.close()


def test_bm1688_plate_detection_eval_uses_fp16_and_real_bbox_metrics():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_plate_detection_runtime)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        expect(page.locator("#tbody tr")).to_contain_text("FP16 精度已评估")
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalContextLine")).to_contain_text("FP16 车牌检测模型")
        expect(page.locator("#evalContextLine")).not_to_contain_text("INT8")
        expect(page.locator("#evalEdgeSshLine")).to_contain_text(
            "本次实际执行设备：10.10.11.90"
        )

        signal = page.locator("#evalVerdictBar .eval-signal-card").filter(
            has_text="FP16 车牌检测"
        )
        expect(signal).to_contain_text("通过")
        expect(signal).to_contain_text("部署转换精度损失")
        expect(signal).to_contain_text("同批次 50 张图片")
        expect(page.locator("#evalVerdictBar")).not_to_contain_text("INT8 量化保真")

        assessment = page.locator("#plateDetectionAssessment")
        expect(assessment).to_contain_text("车牌检测 FP16 工程评估")
        expect(assessment).to_contain_text("plate_detection_fp16_1b.bmodel")
        expect(assessment).to_contain_text("0.9876")
        expect(assessment).to_contain_text("0.8765")
        expect(assessment).to_contain_text("0.9800 / 0.9700 / 0.9749")
        expect(assessment).to_contain_text("关键点指标暂不参与")

        bmodel_row = page.locator("#evalTablesHost tbody tr").filter(
            has_text="FP16 车牌检测 BModel"
        )
        expect(bmodel_row).to_contain_text("0.8765")
        expect(bmodel_row).not_to_contain_text("mAP50-95 未计算")
        expect(bmodel_row).to_contain_text("通过")

        page.locator('[data-eval-workspace-tab="performance"]').click()
        performance = page.locator("#plateDetectionPerformance")
        expect(performance).to_contain_text("plate_detection_fp16_1b.bmodel")
        expect(performance).to_contain_text("67.553 s")
        expect(performance).to_contain_text("192.46 ms/图")
        assert page_errors == []

        browser.close()


def test_bm1688_eval_modal_renders_plate_recognition_business_assessment():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_plate_recognition)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        expect(page.locator(".data-table thead")).to_contain_text("当前结论 / 阻塞")
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalContextLine")).to_contain_text("复合模型业务质量")
        expect(page.locator("#evalContextLine")).not_to_contain_text("INT8")

        expect(page.locator("#plateCompositeTrainingAssessment")).to_be_visible()
        expect(page.locator("#plateCompositeTrainingAssessment")).to_contain_text(
            "Pose 车牌框 mAP50"
        )
        expect(page.locator("#plateCompositeTrainingAssessment")).to_contain_text(
            "0.9911"
        )
        expect(page.locator("#plateCompositeTrainingAssessment")).to_contain_text(
            "车牌颜色 val 准确率"
        )
        expect(page.locator("#plateCompositeTrainingAssessment")).to_contain_text(
            "车牌号码 OCR test 准确率"
        )
        expect(page.locator("#plateEdgeBusinessResult")).to_contain_text("351 / 351")
        expect(page.locator("#plateEdgeBusinessResult")).to_contain_text("437 个")
        expect(page.locator("#plateEdgeBusinessResult")).to_contain_text("78.653 s")
        expect(page.locator("#plateEdgeBusinessResult")).to_contain_text("green 310")
        expect(page.locator("#plateEdgeBusinessResult")).to_contain_text(
            "greenPlate 300"
        )

        fp16_card = page.locator("#evalVerdictBar .eval-signal-card").filter(
            has_text="FP16 复合模型"
        )
        expect(fp16_card).to_contain_text("运行通过，需精度复核")
        generic_table = page.locator(
            '[data-eval-workspace-panel="gates"] .bm-eval-table'
        ).filter(has_text="FP16 复合 BModel")
        expect(generic_table).to_contain_text("四网络板端容器")
        expect(generic_table).not_to_contain_text("INT8 BModel")

        page.locator('[data-eval-workspace-tab="diagnosis"]').click()
        expect(page.locator("#plateRecognitionDiagnosis")).to_contain_text("粤B12345")
        expect(page.locator("#plateRecognitionDiagnosis")).to_contain_text("green 310")

        page.locator('[data-eval-workspace-tab="performance"]').click()
        expect(page.locator("#plateRecognitionPerformance")).to_contain_text(
            "224.08 ms/图"
        )
        expect(page.locator("#plateRecognitionPerformance")).to_contain_text("13.25 ms")

        page.locator('[data-eval-workspace-tab="evidence"]').click()
        evidence_rows = page.locator(
            "#evalEvidenceMetaHost > .bm-eval-table-scroll tbody tr"
        )
        expect(evidence_rows).to_have_count(5)
        expect(page.locator('[data-eval-workspace-panel="evidence"]')).to_contain_text(
            "results_bmodel/summary.json"
        )
        assert page_errors == []

        browser.close()


def test_bm1688_plate_eval_modal_keeps_business_table_scroll_local_on_mobile():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_plate_recognition)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalContextLine")).to_contain_text("复合模型业务质量")

        training_table_scroll = page.locator(
            "#plateCompositeTrainingAssessment .bm-eval-table-scroll"
        )
        expect(training_table_scroll).to_be_visible()
        assert training_table_scroll.evaluate("el => el.scrollWidth > el.clientWidth")
        assert page.locator("#evalModalBody").evaluate(
            "el => el.scrollWidth <= el.clientWidth + 1"
        )

        browser.close()


def test_bm1688_eval_modal_keeps_diagnosis_table_scroll_local_on_mobile():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalContextLine")).to_contain_text("INT8 业务质量")
        page.locator('[data-eval-workspace-tab="diagnosis"]').click()

        diagnosis = page.locator('[data-eval-workspace-panel="diagnosis"]')
        expect(diagnosis).to_be_visible()
        class_table_scroll = diagnosis.locator(
            ".bm-eval-table-scroll:has(#sopClassDropTable)"
        )
        expect(class_table_scroll).to_be_visible()
        assert class_table_scroll.evaluate("el => el.scrollWidth > el.clientWidth")
        assert page.locator("#evalModalBody").evaluate(
            "el => el.scrollWidth <= el.clientWidth + 1"
        )

        browser.close()


def test_bm1688_eval_modal_keeps_legacy_ratio_gate_numeric_for_old_exports():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_legacy_criteria)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)

        table = page.locator('[data-eval-workspace-panel="gates"] .bm-eval-table').first
        onnx_row = table.locator("tbody tr").filter(has_text="云端转换模型")
        bmodel_row = table.locator("tbody tr").filter(has_text="边端量化模型")
        expect(onnx_row.locator("td").nth(6)).to_have_text("0.0172旧SOP相对偏差")
        expect(onnx_row.locator("td").nth(7)).to_contain_text("旧版相对掉点 ≤0.0300")
        expect(bmodel_row.locator("td").nth(6)).to_have_text("0.1417旧SOP相对偏差")
        expect(bmodel_row.locator("td").nth(7)).to_contain_text("旧版相对掉点 ≤0.0500")
        expect(page.locator(".eval-table-footnote")).to_contain_text(
            "历史 ratio_0_1 相对掉点标准"
        )
        int8_signal = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("INT8 量化保真"))'
        )
        expect(int8_signal.locator(".value")).to_have_text("未通过")
        expect(int8_signal).to_have_class(re.compile(r"\bfail\b"))
        expect(int8_signal).not_to_contain_text("可用")
        expect(int8_signal).to_contain_text(
            "绝对掉点 0.0701 · 相对掉点 0.1417 · 历史门限 ≤0.0500"
        )
        onnx_signal = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("ONNX 转换保真"))'
        )
        expect(onnx_signal).to_contain_text(
            "绝对掉点 0.0085 · 相对掉点 0.0172 · 历史门限 ≤0.0300"
        )
        assert "%" not in page.locator("#evalVerdictBar").inner_text()
        page.locator('[data-eval-workspace-tab="diagnosis"]').click()
        diagnosis = page.locator('[data-eval-workspace-panel="diagnosis"]')
        analysis = diagnosis.locator(".bm-eval-section").filter(has_text="精度损失分析")
        expect(analysis).to_contain_text(
            "绝对掉点 0.0701 · 相对掉点比值（0-1） 0.1417 · 历史门限 ≤0.0500"
        )
        expect(analysis).to_contain_text("相对掉点比值（0-1）0.1417")
        expect(analysis).not_to_contain_text("14.17%")
        expect(analysis).not_to_contain_text("≤5%")
        page.locator('[data-eval-workspace-tab="evidence"]').click()
        threshold_row = page.locator(
            '[data-eval-workspace-panel="evidence"] tbody tr'
        ).filter(has_text="conversion_gate_unit")
        expect(threshold_row.locator("td").nth(1)).to_have_text("ratio_0_1")
        expect(threshold_row.locator("td").nth(2)).to_have_text("历史模型转换门禁")
        assert "%" not in table.inner_text()

        browser.close()


def test_bm1688_eval_modal_edge_performance_is_source_aware_and_explicit():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        page.locator('[data-eval-workspace-tab="performance"]').click()

        panel = page.locator('[data-eval-workspace-panel="performance"]')
        expect(panel).to_be_visible()
        dimensions = panel.locator("#edgeValidationDimensions")
        expect(dimensions.locator(".eval-signal-card")).to_have_count(5)
        expect(dimensions).to_contain_text("运行环境通过")
        expect(dimensions).to_contain_text("模型推理通过")
        expect(dimensions).to_contain_text("检测输出已产生检测框")
        expect(dimensions).to_contain_text("精度评估已计算")
        expect(dimensions).to_contain_text("24 张复用已有真值")
        expect(dimensions).to_contain_text("类别覆盖1 / 2")
        expect(dimensions).to_contain_text("缺少：vehicle")
        same_batch = panel.locator("#edgeSameBatchComparison")
        expect(same_batch.locator("tbody tr")).to_have_count(4)
        expect(same_batch).to_contain_text("同批同口径模型比较")
        expect(same_batch).to_contain_text("letterbox / RGB")
        expect(same_batch).to_contain_text("INT8 相对 FP32 AP50 掉点 0.0090")
        expect(same_batch.locator('tr[data-same-batch-model="int8"]')).to_contain_text(
            "-0.0190"
        )
        summary = panel.locator("#edgePerformanceSummary")
        expect(summary.locator(".eval-signal-card")).to_have_count(4)
        expect(summary).to_contain_text("边端任务状态")
        expect(summary).to_contain_text("执行流程已结束，结果完整度见相邻覆盖项")
        coverage_card = summary.locator(".eval-signal-card").filter(
            has_text="实图 BMCV 覆盖"
        )
        expect(coverage_card).to_contain_text("2 / 3 模型")
        expect(coverage_card).to_contain_text("INT8 未执行")
        expect(coverage_card).to_have_class(re.compile(r"\bwarn\b"))
        expect(summary).not_to_contain_text("推理阶段平均耗时")
        expect(summary).not_to_contain_text("51.4 ms/帧")
        expect(summary).to_contain_text("5.539 ms")
        expect(summary).to_contain_text("工程验证样本")
        expect(summary).to_contain_text("32 张")

        matrix = panel.locator("#edgePerformanceMatrix")
        expect(matrix.locator("tbody tr")).to_have_count(3)
        expect(matrix.locator("thead")).to_contain_text("阶段最小")
        expect(matrix.locator("thead")).to_contain_text("阶段最大")
        expect(matrix.locator("thead")).to_contain_text("样本数")
        expect(matrix.locator("thead")).to_contain_text("端到端 FPS")
        expect(panel).to_contain_text(
            "Sophon Demo 分段均值在下表独立展示，不与本表混算"
        )
        assert "真实推理平均" not in panel.inner_text()

        fp32_row = matrix.locator(
            'tbody tr[data-edge-bmodel="yolov11n_fp32_1b.bmodel"]'
        )
        expect(fp32_row.locator("td").nth(1)).to_contain_text("已采集")
        for column, value in enumerate(
            ("18.4", "18.2", "19.8", "20.4", "17.3", "20.8", "1000", "31.5"), 2
        ):
            expect(fp32_row.locator("td").nth(column)).to_contain_text(value)
        expect(fp32_row.locator(".eval-source-path")).to_have_attribute(
            "data-source-path", re.compile(r"yolov11n_fp32_1b\.bmodel$")
        )

        int8_row = matrix.locator(
            'tbody tr[data-edge-bmodel="yolov11n_int8_1b.bmodel"]'
        )
        expect(int8_row.locator("td").nth(1)).to_contain_text("已采集")
        for column, value in enumerate(
            ("7.2", "7", "8.6", "8.8", "6.4", "9.1", "1000", "52.4"), 2
        ):
            expect(int8_row.locator("td").nth(column)).to_contain_text(value)
        expect(int8_row.locator(".eval-source-path")).to_have_attribute(
            "data-source-path", re.compile(r"yolov11n_int8_1b\.bmodel$")
        )

        resource_matrix = panel.locator("#edgeProbeResourceMatrix")
        expect(resource_matrix.locator("tbody tr")).to_have_count(15)
        fp32_power = resource_matrix.locator(
            'tbody tr[data-edge-resource-model="yolov11n_fp32_1b.bmodel"]'
        ).filter(has_text="功耗")
        expect(fp32_power).to_contain_text("设备不支持功耗遥测")
        fp32_npu = resource_matrix.locator(
            'tbody tr[data-edge-resource-model="yolov11n_fp32_1b.bmodel"]'
        ).filter(has_text="NPU")
        expect(fp32_npu).to_contain_text("64%")

        stage_matrix = panel.locator("#edgeBmcvStageMatrix")
        expect(stage_matrix.locator("tbody tr")).to_have_count(3)
        expect(stage_matrix.locator("thead")).to_have_text(
            "BModelBMCV 状态解码平均预处理平均TPU 推理平均后处理平均四段求和流水线换算 FPS"
        )
        fp16_stages = stage_matrix.locator(
            'tbody tr[data-edge-bmcv-model="yolov11n_fp16_1b.bmodel"]'
        )
        expect(fp16_stages.locator("td").nth(1)).to_have_text("已采集")
        for column, value in enumerate(("18.19", "9.8", "19.39", "6.91", "54.29"), 2):
            expect(fp16_stages.locator("td").nth(column)).to_contain_text(value)
        expect(fp16_stages.locator("td").nth(7)).to_contain_text("18.4")

        fp32_stages = stage_matrix.locator(
            'tbody tr[data-edge-bmcv-model="yolov11n_fp32_1b.bmodel"]'
        )
        for column, value in enumerate(("17.98", "10.16", "51.4", "6.7", "86.24"), 2):
            expect(fp32_stages.locator("td").nth(column)).to_contain_text(value)
        expect(fp32_stages.locator("td").nth(7)).to_contain_text("11.6")

        int8_stages = stage_matrix.locator(
            'tbody tr[data-edge-bmcv-model="yolov11n_int8_1b.bmodel"]'
        )
        expect(int8_stages.locator("td").nth(1)).to_have_text("未执行")
        for column in range(2, 8):
            expect(int8_stages.locator("td").nth(column)).to_have_text("未执行")
        stage_timing = panel.locator("#edgeBmcvStageTiming")
        expect(stage_timing).to_contain_text("1000 ÷ 四段平均和")
        expect(stage_timing).to_contain_text("不是长稳吞吐或端到端墙钟实测")

        resources = panel.locator("#edgeRuntimeResources")
        expect(resources).to_contain_text("运行资源与延迟分布")
        for label, value in (
            ("实测平均推理耗时", "22.4 ms/图"),
            ("P50 推理耗时", "21.8 ms"),
            ("P95 推理耗时", "27.6 ms"),
            ("P99 推理耗时", "未采集"),
            ("实测吞吐 FPS", "41.7 帧/s"),
            ("运行内存", "486.5 MB"),
            ("NPU 利用率", "72.4%"),
            ("设备功耗", "8.6 W"),
            ("设备温度", "58.3 °C"),
        ):
            resource = resources.locator(".edge-condition-item").filter(has_text=label)
            expect(resource.locator(".value")).to_have_text(value)

        conditions = panel.locator("#edgeEvaluationConditions")
        expect(conditions).to_contain_text("selected_task_samples")
        expect(conditions).to_contain_text("不一致")
        board_images = conditions.locator(".edge-condition-item").filter(
            has_text="板端工程验证图片"
        )
        precision_images = conditions.locator(".edge-condition-item").filter(
            has_text="INT8 精度评估图片"
        )
        expect(board_images).to_contain_text("32 张")
        expect(precision_images).to_contain_text("144 张")
        for label, value in (
            ("置信度阈值 conf", "0.35"),
            ("NMS IoU 阈值", "0.7"),
            ("匹配 IoU 阈值", "0.5"),
        ):
            item = conditions.locator(".edge-condition-item").filter(has_text=label)
            expect(item.locator(".value")).to_have_text(value)
            assert "%" not in item.inner_text()

        detection = panel.locator("#edgeDetectionOutput")
        expect(detection).to_contain_text("模型输出与标注检查")
        expect(detection).to_contain_text("不等于 Precision、Recall 或 mAP")
        expect(detection).to_contain_text("空 XML 作为已确认负样本单独统计")
        expect(detection).to_contain_text("本次边端任务没有可用的图片证据")
        expect(detection.get_by_text("有标注图片", exact=True)).to_have_count(0)

        evidence = panel.locator("#edgeExecutionEvidence")
        expect(evidence).to_contain_text("手动边端验证 · 运行并评估")
        expect(evidence).to_contain_text("192.168.10.8")
        expect(evidence).to_contain_text("2026-07-04 10:18:00")
        expect(evidence).to_contain_text("无错误")
        unavailable = evidence.locator("#edgeUnavailableEvidence")
        expect(unavailable).to_contain_text("本次仍未采集")
        expect(unavailable).to_contain_text("各分段逐帧波动、抖动与离群点")
        expect(unavailable).not_to_contain_text("预处理 / 推理 / 后处理分段耗时")
        expect(unavailable).to_contain_text("完整逐帧耗时原始序列")
        expect(unavailable).to_contain_text("P99 推理耗时")
        expect(unavailable).to_contain_text("完整 CPU / NPU / 内存利用率序列")
        expect(unavailable).to_contain_text("完整温度与功耗序列")
        expect(unavailable).to_contain_text("长稳运行时长与失败率")

        browser.close()


def test_bm1688_eval_modal_names_pending_edge_metrics_as_not_returned():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_edge_pending)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)

        main_table = page.locator(
            '[data-eval-workspace-panel="gates"] .bm-eval-table'
        ).first
        bmodel_row = main_table.locator("tbody tr").filter(has_text="边端量化模型")
        expect(bmodel_row.locator("td").nth(1)).to_contain_text("未回传")
        expect(bmodel_row.locator("td").nth(2)).to_contain_text("未回传")
        expect(bmodel_row.locator("td").nth(8)).to_contain_text("未回传")
        expect(bmodel_row.locator("td").nth(9)).to_contain_text("待完成")

        page.locator('[data-eval-workspace-tab="performance"]').click()
        performance_panel = page.locator('[data-eval-workspace-panel="performance"]')
        expect(performance_panel.locator("#edgePerformanceSummary")).to_contain_text(
            "执行中"
        )
        expect(performance_panel.locator("#edgePerformanceMatrix")).to_contain_text(
            "未执行"
        )
        expect(performance_panel.locator("#edgeEvaluationConditions")).to_contain_text(
            "未采集"
        )
        expect(performance_panel.locator("#edgeExecutionEvidence")).to_contain_text(
            "未回传"
        )
        expect(performance_panel.locator("#edgeExecutionEvidence")).not_to_contain_text(
            "无错误"
        )

        browser.close()


def test_bm1688_eval_modal_keeps_missing_parameters_visible_without_zero_fill():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_missing_eval_values)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        toolbar_buttons = page.locator("#evalModal .bm-detail-toolbar-right > *")
        expect(toolbar_buttons).to_have_count(6)
        assert all(
            item["left"] >= 0 and item["right"] <= 390
            for item in toolbar_buttons.evaluate_all(
                "els => els.map(el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right}; })"
            )
        )
        page.locator('[data-eval-workspace-tab="evidence"]').click()

        evidence = page.locator('[data-eval-workspace-panel="evidence"]')
        params = evidence.locator(".bm-eval-section").filter(
            has_text="评估与边端阈值参数"
        )
        expect(params).to_be_visible()
        expect(params.locator("tbody tr")).to_have_count(13)
        expect(params).to_contain_text("map50_absolute_drop")
        for name in (
            "split",
            "imgsz",
            "sop_conf_thres",
            "sop_iou_thres",
            "images_dir",
            "xml_dir",
            "dataset_root",
            "sop_eval_status",
        ):
            row = params.locator("tbody tr").filter(has_text=name)
            expect(row.locator("td").nth(1)).to_have_text("未提供")
            expect(row.locator("td").nth(1)).not_to_have_text("0")
        for name in (
            "edge_threshold_unit",
            "edge_conf_thres",
            "edge_nms_thres",
            "edge_match_iou_thres",
        ):
            row = params.locator("tbody tr").filter(has_text=name)
            expect(row.locator("td").nth(1)).to_have_text("未回传")
            expect(row.locator("td").nth(1)).not_to_have_text("0")

        browser.close()


def test_bm1688_eval_modal_failed_run_ignores_residual_pass_metrics():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_failed_residual_pass_metrics)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)

        release_card = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("最终发布门禁"))'
        )
        execution_card = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("边端执行"))'
        )
        fidelity_card = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("INT8 量化保真"))'
        )
        expect(release_card.locator(".value")).to_have_text("不可进入发布评估")
        expect(execution_card.locator(".value")).to_have_text("失败")
        expect(fidelity_card.locator(".value")).to_have_text("不可评估")

        table = page.locator('[data-eval-workspace-panel="gates"] .bm-eval-table').first
        bmodel_row = table.locator("tbody tr").filter(has_text="边端量化模型")
        expect(bmodel_row.locator("td").nth(8)).to_contain_text("执行失败")
        expect(bmodel_row.locator("td").nth(9)).to_contain_text("不可评估")
        expect(bmodel_row).not_to_contain_text("0.8050")
        expect(bmodel_row).not_to_contain_text("0.0150")
        expect(bmodel_row).not_to_contain_text("0.0183")
        assert page_errors == []

        browser.close()


def test_bm1688_eval_modal_failed_onnx_ignores_residual_pass_metrics():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_failed_onnx_residual_pass_metrics)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)

        release_card = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("最终发布门禁"))'
        )
        onnx_card = page.locator(
            '#evalVerdictBar .eval-signal-card:has(> .label:text-is("ONNX 转换保真"))'
        )
        expect(release_card.locator(".value")).to_have_text("不可进入发布评估")
        expect(onnx_card.locator(".value")).to_have_text("不可评估")

        table = page.locator('[data-eval-workspace-panel="gates"] .bm-eval-table').first
        onnx_row = table.locator("tbody tr").filter(has_text="云端转换模型")
        expect(onnx_row.locator("td").nth(8)).to_contain_text("评估失败")
        expect(onnx_row.locator("td").nth(9)).to_contain_text("不可评估")
        expect(onnx_row).not_to_contain_text("0.8160")
        expect(onnx_row).not_to_contain_text("0.0040")
        expect(onnx_row).not_to_contain_text("0.0049")
        assert page_errors == []

        browser.close()


def test_bm1688_eval_modal_separates_inference_from_evaluator_wall_clock():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_evaluator_wall_clock_only)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        page.locator('[data-eval-workspace-tab="performance"]').click()

        resources = page.locator("#edgeRuntimeResources")
        inference = resources.locator(".edge-condition-item").filter(
            has_text="实测平均推理耗时"
        )
        evaluator = resources.locator(".edge-condition-item").filter(
            has_text="评估端到端平均耗时"
        )
        expect(inference.locator(".value")).to_have_text("未采集")
        expect(inference).not_to_contain_text("1558")
        expect(evaluator.locator(".value")).to_have_text("1558 ms/图")
        expect(evaluator).to_contain_text("包含读图、预处理、推理、后处理与指标匹配")
        assert page_errors == []

        browser.close()


def test_bm1688_eval_modal_handles_failed_malformed_third_party_metrics():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_failed_malformed_metrics)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)

        table = page.locator('[data-eval-workspace-panel="gates"] .bm-eval-table').first
        bmodel_row = table.locator("tbody tr").filter(has_text="边端量化模型")
        expect(bmodel_row.locator("td").nth(8)).to_contain_text("执行失败")
        expect(bmodel_row.locator("td").nth(8)).to_contain_text("bmrt_test 未生成结果")

        page.locator('[data-eval-workspace-tab="performance"]').click()
        performance = page.locator('[data-eval-workspace-panel="performance"]')
        expect(performance.locator("#edgePerformanceMatrix tbody tr")).to_have_count(3)
        expect(performance.locator("#edgeExecutionEvidence")).to_contain_text(
            "边端 Agent 执行失败"
        )
        assert page_errors == []

        browser.close()


def test_bm1688_eval_modal_names_partial_edge_result_as_partial_return():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api_partial_edge_result)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#tbody tr button[data-act='eval']").click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)

        table = page.locator('[data-eval-workspace-panel="gates"] .bm-eval-table').first
        bmodel_row = table.locator("tbody tr").filter(has_text="边端量化模型")
        expect(bmodel_row.locator("td").nth(8)).to_contain_text("部分回传")
        expect(bmodel_row.locator("td").nth(8)).to_contain_text("精度报告尚未回传")
        assert page_errors == []

        browser.close()


def test_bm1688_manual_edge_query_opens_detail_manual_tab_without_legacy_modal():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(f"{PAGE_URL}?export_id=123&manual_edge=1")

        expect(page.locator("#exportDetailModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("[data-detail-tab='manual'].active")).to_have_count(
            1, timeout=15_000
        )
        expect(page.locator("#edgeManualModal.show")).to_have_count(0)
        expect(page.locator("#evalModal.show")).to_have_count(0)
        expect(page.locator("#evalStepsModal.show")).to_have_count(0)

        browser.close()


def test_publish_blocker_query_opens_contextual_bm1688_detail():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_api)
        page.goto(
            f"{PAGE_URL}?embedded=1&export_id=123&manual_edge=1"
            "&from=publish&blocker_status=FAILED"
        )

        expect(page.locator("#exportDetailModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#bmPublishBlockerContext")).to_be_visible()
        expect(page.locator("#exportDetailTitle")).to_contain_text("处理发布阻断")
        expect(page.locator("#btnBmReturn")).to_have_text("返回模型发布")
        expect(page.locator("#btnPublishContextBack")).to_have_attribute(
            "href", "/yolo-model-publish?embedded=1"
        )

        browser.close()


def test_bm1688_manual_result_workspace_switches_jobs_images_tabs_and_metrics_compare():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page_errors: list[str] = []
        preview_requests: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _manual_visual_handler(preview_requests))
        page.goto(f"{PAGE_URL}?export_id=123&manual_edge=1")

        host = page.locator("#detailManualValidationHost")
        expect(page.locator("[data-detail-tab='manual'].active")).to_have_count(
            1, timeout=15_000
        )
        expect(host.locator(".manual-result-title-row h5").first).to_have_text(
            "任务 #28 结果"
        )
        expect(host).to_contain_text("共 2 条")
        expect(host).to_contain_text("最新")
        expect(host).to_contain_text("检测框")
        expect(host).to_contain_text("14 个")
        latest_image = host.locator('img[data-edge-job-id="28"]')
        expect(latest_image).to_have_attribute(
            "src", re.compile(r"^blob:"), timeout=15_000
        )
        assert any(
            "/jobs/28/preview/task-28-boxed.png" in path for path in preview_requests
        )

        host.locator('[data-detail-manual-job="27"]').click()
        expect(host.locator(".manual-result-title-row h5").first).to_have_text(
            "任务 #27 结果"
        )
        expect(host).to_contain_text("10 个")
        older_image = host.locator('img[data-edge-job-id="27"]')
        expect(older_image).to_have_attribute(
            "src", re.compile(r"^blob:"), timeout=15_000
        )
        assert any(
            "/jobs/27/preview/task-27-boxed.png" in path for path in preview_requests
        )

        host.locator('[data-detail-manual-result-tab="accuracy"]').click()
        expect(host).to_contain_text("FP32")
        expect(host).to_contain_text("INT8")
        expect(host).to_contain_text("0.7600")
        expect(host).to_contain_text("类别覆盖 2/2")

        host.locator('[data-detail-manual-result-tab="performance"]').click()
        expect(host).to_contain_text("14.50 ms/图")
        expect(host).to_contain_text("sophon-bmcv")

        host.locator('[data-detail-manual-job="28"]').click()
        expect(host).to_contain_text("8.800 ms")
        expect(host).to_contain_text("50 / 50")
        expect(host).to_contain_text("逐帧阶段波动")
        expect(host).to_contain_text("NPU 推理")
        expect(host).to_contain_text("BM1688-EDGE-001")
        expect(host).to_contain_text("60.1 s")
        expect(host).to_contain_text("0.0320%")

        host.locator('[data-detail-manual-result-tab="execution"]').click()
        expect(host).to_contain_text("192.168.10.8")
        expect(host).to_contain_text("运行环境")
        expect(host).to_contain_text("检查通过")
        expect(host).to_contain_text("Sophon Demo")

        host.locator('[data-detail-manual-compare="27"]').check()
        host.locator('[data-detail-manual-compare="28"]').check()
        compare = host.locator(".manual-result-compare")
        expect(compare).to_be_visible()
        expect(compare).to_contain_text("任务 #27 与 #28 对比")
        map_row = compare.locator("tbody tr").filter(has_text="INT8 mAP50")
        expect(map_row.locator("td").nth(1)).to_have_text("0.76")
        expect(map_row.locator("td").nth(2)).to_have_text("0.81")
        expect(map_row.locator("td").nth(3)).to_have_text("+0.0500")
        expect(compare).to_contain_text("INT8 TP")
        assert page_errors == []

        browser.close()


def test_manual_edge_execution_profile_defaults_quick_and_posts_certification():
    created_payloads: list[dict[str, Any]] = []

    def handle_api(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        method = request.method

        if path == f"{API_PREFIX}/tasks/88/samples" and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 41,
                            "inner_id": 1,
                            "state": "DONE",
                            "annotated_count": 1,
                            "data": {"result": {"rectTool": {"result": [{}]}}},
                            "file": {"filename": "edge-profile.jpg", "url": ""},
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return
        if path == f"{API_PREFIX}/yolo-edge-validation/jobs" and method == "GET":
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if path == f"{API_PREFIX}/yolo-edge-validation/jobs" and method == "POST":
            payload = request.post_data_json or {}
            created_payloads.append(payload)
            _json_response(
                route,
                {
                    "data": {
                        "id": 901,
                        "export_id": 123,
                        "task_id": 88,
                        "status": "PENDING",
                        "mode": payload.get("mode"),
                        "execution_profile": payload.get("execution_profile"),
                        "sample_ids": payload.get("sample_ids") or [],
                        "attachment_ids": [],
                        "image_count": 1,
                        "labeled_image_count": 1,
                        "unlabeled_image_count": 0,
                    }
                },
                status=201,
            )
            return
        if (
            path == f"{API_PREFIX}/yolo-edge-validation/jobs/901/run"
            and method == "POST"
        ):
            _json_response(
                route,
                {
                    "data": {
                        "id": 901,
                        "export_id": 123,
                        "status": "PROCESSING",
                        "execution_profile": "certification",
                    }
                },
            )
            return
        if path == f"{API_PREFIX}/yolo-edge-validation/jobs/901" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "id": 901,
                        "export_id": 123,
                        "status": "COMPLETED",
                        "mode": "run_and_eval",
                        "execution_profile": "certification",
                        "sample_ids": [41],
                        "attachment_ids": [],
                        "image_count": 1,
                        "labeled_image_count": 1,
                        "unlabeled_image_count": 0,
                        "result": {},
                    }
                },
            )
            return
        _handle_api(route)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handle_api)
        page.goto(PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator('button[data-ops-more-toggle="export"]').click()
        page.locator('button[data-act="manual-edge"]').click()
        expect(page.locator("#edgeManualModal.show")).to_be_visible(timeout=15_000)
        expect(
            page.locator('input[name="edgeExecutionProfile"][value="quick"]')
        ).to_be_checked()

        page.locator(
            'input[name="edgeExecutionProfile"][value="certification"]'
        ).check()
        page.locator('input[data-edge-sample="sample:41"]').check()
        page.locator("#btnEdgeManualSubmit").click()
        expect(page.locator("#edgeManualJobLog")).to_contain_text(
            "#901", timeout=15_000
        )

        assert len(created_payloads) == 1
        assert created_payloads[0]["execution_profile"] == "certification"
        assert created_payloads[0]["sample_ids"] == [41]
        browser.close()
