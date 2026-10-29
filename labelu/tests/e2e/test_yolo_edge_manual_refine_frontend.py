"""
Playwright 前端回归：
BM1688 导出页自动打开手动边端验证弹窗，复用历史任务并完成
“AI 二次修正阈值 -> 应用修正值 -> 重新提交”整条链路。

运行前置：
  pip install playwright pytest-playwright
  playwright install chromium
  启动 LabelU 本地服务，或配合 scripts/with_server.py 启动：
    python scripts/with_server.py ^
      --server "powershell -ExecutionPolicy Bypass -File scripts/run_labelu_e2e.ps1 -Port 9888" ^
      --port 9888 --timeout 120 ^
      -- python -m pytest labelu/tests/e2e/test_yolo_edge_manual_refine_frontend.py -v -s
"""
from __future__ import annotations

import json
import os
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
    f"{BASE_URL}/yolo-bm1688-exports?export_id=123&manual_edge=1",
)
REPO_ROOT = Path(__file__).resolve().parents[3]
BM1688_PAGE_FILE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_bm1688_exports.html"


def _json_response(route: Route, data: Any, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False),
    )


def _sample_row(sample_id: int, filename: str, annotated_count: int) -> dict[str, Any]:
    return {
        "id": sample_id,
        "inner_id": sample_id + 1000,
        "state": "DONE" if annotated_count else "NEW",
        "annotated_count": annotated_count,
        "updated_at": "2026-06-28T09:30:00",
        "data": {"filename": filename},
        "file": {
            "id": sample_id + 5000,
            "filename": filename,
            "url": "/favicon.svg",
            "thumbnail_url": "/favicon.svg",
            "annotation_display_url": "/favicon.svg",
        },
    }


def _export_detail() -> dict[str, Any]:
    return {
        "id": 123,
        "task_id": 88,
        "task_name": "边端验证任务",
        "yolo_train_job_id": 66,
        "status": "COMPLETED",
        "imgsz": 640,
        "can_recompile_bmodel": True,
        "export_dir": "D:/labelu_media/yolo_bm1688_exports/123",
        "quality_summary": "PT/ONNX/bmodel 评估已完成",
        "board_validation": {
            "status": "COMPLETED",
            "report_summary": "最近一次边端验证已完成",
            "report": {"source": "manual_edge_validation"},
        },
    }


def _pending_manual_export_detail() -> dict[str, Any]:
    return {
        "id": 123,
        "task_id": 88,
        "task_name": "边端验证任务",
        "yolo_train_job_id": 66,
        "status": "COMPLETED",
        "imgsz": 640,
        "can_recompile_bmodel": True,
        "export_dir": "D:/labelu_media/yolo_bm1688_exports/123",
        "quality_summary": "PT/ONNX/bmodel 评估已完成",
        "board_validation": {
            "validation_id": 701,
            "status": "PENDING",
            "report_summary": "manual:run_and_eval; images=1, labeled=0; 已创建任务，等待启动",
            "report": {
                "source": "manual_edge_validation",
                "job_id": 701,
                "mode": "run_and_eval",
                "image_count": 1,
                "labeled_image_count": 0,
                "unlabeled_image_count": 1,
                "updated_at": "2026-07-04T09:24:06",
            },
        },
    }


def _history_job_summary() -> dict[str, Any]:
    return {
        "id": 501,
        "status": "COMPLETED",
        "edge_result_status": "fail",
        "mode": "run_and_eval",
        "threshold_source": "ai_recommended",
        "image_count": 2,
        "labeled_image_count": 2,
        "sample_ids": [1, 2],
        "attachment_ids": [],
        "report_summary": "历史任务评估完成，可用于二次修正",
        "created_at": "2026-06-28T09:40:00",
    }


def _history_job_detail() -> dict[str, Any]:
    return {
        "id": 501,
        "status": "COMPLETED",
        "edge_result_status": "fail",
        "mode": "run_and_eval",
        "threshold_source": "ai_recommended",
        "thresholds": {
            "conf_thres": 0.33,
            "nms_thres": 0.68,
            "match_iou_thres": 0.50,
        },
        "ai_thresholds": {
            "source": "ai_recommended",
            "conf_thres": 0.33,
            "nms_thres": 0.68,
            "match_iou_thres": 0.50,
            "reason": "首轮 AI 推荐偏向稳妥召回",
            "confidence_level": "medium",
            "provider_key": "mock-llm",
        },
        "sample_ids": [1, 2],
        "attachment_ids": [],
        "external_attachments": [],
        "image_count": 2,
        "labeled_image_count": 2,
        "report_summary": "历史任务评估完成，可用于二次修正",
        "created_at": "2026-06-28T09:40:00",
        "result": {
            "execution_summary": {
                "status": "COMPLETED",
                "phase": "completed",
            },
            "prediction_summary": {
                "total_boxes": 6,
                "image_count": 2,
            },
            "eval_summary": {
                "map50": 0.621,
            },
        },
    }


def _pending_history_job_summary() -> dict[str, Any]:
    return {
        "id": 701,
        "status": "PENDING",
        "mode": "run_and_eval",
        "threshold_source": "system_default",
        "image_count": 1,
        "labeled_image_count": 0,
        "sample_ids": [1],
        "attachment_ids": [],
        "report_summary": "已创建任务，等待启动",
        "created_at": "2026-07-04T09:24:06",
    }


def _pending_history_job_detail() -> dict[str, Any]:
    return {
        "id": 701,
        "status": "PENDING",
        "mode": "run_and_eval",
        "threshold_source": "system_default",
        "thresholds": {
            "conf_thres": 0.25,
            "nms_thres": 0.70,
            "match_iou_thres": 0.50,
        },
        "sample_ids": [1],
        "attachment_ids": [],
        "external_attachments": [],
        "image_count": 1,
        "labeled_image_count": 0,
        "unlabeled_image_count": 1,
        "report_summary": "已创建任务，等待启动",
        "created_at": "2026-07-04T09:24:06",
        "result": {
            "request": {"sample_ids": [1], "attachment_ids": [], "external_attachments": []},
            "pack": {"copied_count": 1, "labeled_count": 0, "unlabeled_count": 1},
        },
    }


def test_bm1688_modal_transitions_keep_only_one_primary_modal_open():
    page_origin = "http://labelu.test"
    page_url = f"{page_origin}/yolo-bm1688-exports?export_id=123"
    page_html = BM1688_PAGE_FILE.read_text(encoding="utf-8")

    def handle_api(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path

        if path == "/yolo-bm1688-exports":
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=page_html,
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

        api_path = path[len(API_PREFIX) :]
        method = request.method

        if api_path == "/local-dev/session" and method == "GET":
            _json_response(route, {"data": {"session_id": "playwright"}})
            return
        if api_path.startswith("/yolo-bm1688-exports/123/runtime-log/") and method == "GET":
            route.fulfill(status=404, body="")
            return
        if api_path.startswith("/yolo-bm1688-exports/123/sop-eval-asset/") and method == "GET":
            route.fulfill(status=404, body="")
            return

        if api_path == "/yolo-bm1688-exports/compiler-settings" and method == "GET":
            _json_response(route, {"data": {"auto_compile": True, "builtin_script_path": "labelu/scripts/export_bm1688.py"}})
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

        if api_path == "/yolo-bm1688-exports/edge-ssh-settings" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "enabled": True,
                        "host": "192.168.0.10",
                        "port": 22,
                        "user": "linaro",
                        "password_configured": True,
                        "auto_after_export": True,
                    }
                },
            )
            return

        if api_path == "/annotation-taxonomy/dataset-categories" and method == "GET":
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/label-presets" and method == "GET":
            _json_response(route, {"data": []})
            return

        if api_path == "/users/me" and method == "GET":
            _json_response(route, {"data": {"id": 1, "username": "mock-user"}})
            return

        if api_path == "/yolo-bm1688-exports" and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 123,
                            "task_id": 88,
                            "task_name": "边端验证任务",
                            "yolo_train_job_id": 66,
                            "status": "COMPLETED",
                            "imgsz": 640,
                            "onnx_filename": "yolov11n.onnx",
                            "quality_summary": "PT/ONNX/bmodel 评估已完成",
                            "validation_gate_label": "通过",
                            "edge_validation_label": "通过",
                            "board_validation": {
                                "status": "COMPLETED",
                                "report_summary": "最近一次边端验证已完成",
                            },
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return

        if api_path == "/yolo-bm1688-exports/123" and method == "GET":
            _json_response(route, {"data": _export_detail()})
            return

        # The production page now prefers the lightweight summary contract and
        # falls back to the full detail endpoint only on 404.
        if api_path == "/yolo-bm1688-exports/123/summary" and method == "GET":
            _json_response(route, {"data": _export_detail()})
            return
        if api_path == "/yolo-bm1688-exports/123/evaluation" and method == "GET":
            _json_response(route, {"data": _export_detail()})
            return

        if api_path.startswith("/yolo-bm1688-exports/123/sop-eval-report/") and method == "GET":
            route.fulfill(
                status=200,
                content_type="text/markdown; charset=utf-8",
                body="## mock report\n\nok\n",
            )
            return

        if api_path == "/tasks/88/samples" and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        _sample_row(1, "sample_001.jpg", 1),
                        _sample_row(2, "sample_002.jpg", 1),
                    ]
                },
            )
            return

        if api_path == "/yolo-edge-validation/jobs" and method == "GET":
            _json_response(route, {"data": []})
            return

        raise AssertionError(f"Unexpected API call: {method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"{page_origin}/**", handle_api)
        page.goto(page_url)

        expect(page.locator("#exportDetailModal")).to_be_visible(timeout=20_000)
        expect(page.locator("#evalModal")).not_to_be_visible()
        expect(page.locator("#edgeManualModal")).not_to_be_visible()

        page.locator("#btnDetailOpenEval").click()

        expect(page.locator("#evalModal")).to_be_visible(timeout=10_000)
        expect(page.locator("#exportDetailModal")).not_to_be_visible()
        expect(page.locator("#edgeManualModal")).not_to_be_visible()

        page.locator("#btnEvalManualEdge").click()

        expect(page.locator("#edgeManualModal")).to_be_visible(timeout=10_000)
        expect(page.locator("#evalModal")).not_to_be_visible()
        expect(page.locator("#exportDetailModal")).not_to_be_visible()

        browser.close()


def test_export_detail_pending_manual_edge_shows_waiting_not_running():
    page_origin = "http://labelu.test"
    page_url = f"{page_origin}/yolo-bm1688-exports?export_id=123"
    page_html = BM1688_PAGE_FILE.read_text(encoding="utf-8")

    def handle_api(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path

        if path == "/yolo-bm1688-exports":
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=page_html,
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

        api_path = path[len(API_PREFIX) :]
        method = request.method

        if api_path == "/local-dev/session" and method == "GET":
            _json_response(route, {"data": {"session_id": "playwright"}})
            return
        if api_path.startswith("/yolo-bm1688-exports/123/runtime-log/") and method == "GET":
            route.fulfill(status=404, body="")
            return
        if api_path.startswith("/yolo-bm1688-exports/123/sop-eval-asset/") and method == "GET":
            route.fulfill(status=404, body="")
            return

        if api_path == "/yolo-bm1688-exports/compiler-settings" and method == "GET":
            _json_response(route, {"data": {"auto_compile": True, "builtin_script_path": "labelu/scripts/export_bm1688.py"}})
            return
        if api_path == "/yolo-bm1688-exports/catalog-summary" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "total": 1,
                        "running": 0,
                        "blocked": 0,
                        "publish_ready": 0,
                        "pending_edge": 1,
                        "failed": 0,
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
                        "port": 22,
                        "user": "linaro",
                        "password_configured": True,
                        "auto_after_export": True,
                    }
                },
            )
            return

        if api_path == "/annotation-taxonomy/dataset-categories" and method == "GET":
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/label-presets" and method == "GET":
            _json_response(route, {"data": []})
            return

        if api_path == "/users/me" and method == "GET":
            _json_response(route, {"data": {"id": 1, "username": "mock-user"}})
            return

        if api_path == "/yolo-bm1688-exports" and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 123,
                            "task_id": 88,
                            "task_name": "边端验证任务",
                            "yolo_train_job_id": 66,
                            "status": "COMPLETED",
                            "imgsz": 640,
                            "onnx_filename": "yolov11n.onnx",
                            "quality_summary": "PT/ONNX/bmodel 评估已完成",
                            "validation_gate_label": "通过",
                            "edge_validation_label": "待验证",
                            "edge_execution_label": "待执行",
                            "edge_int8_eval_label": "评估中",
                            "current_verdict_label": "待完成边端验证",
                            "board_validation": {
                                "status": "PENDING",
                                "report_summary": "manual:run_and_eval; images=1, labeled=0; 已创建任务，等待启动",
                                "report": {"source": "manual_edge_validation"},
                            },
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return

        if api_path == "/yolo-bm1688-exports/123" and method == "GET":
            _json_response(route, {"data": _pending_manual_export_detail()})
            return

        if api_path == "/yolo-bm1688-exports/123/summary" and method == "GET":
            _json_response(route, {"data": _pending_manual_export_detail()})
            return

        if api_path == "/yolo-bm1688-exports/123/board-validation" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "validation_id": 701,
                        "status": "PENDING",
                        "report_summary": "manual:run_and_eval; images=1, labeled=0; 已创建任务，等待启动",
                        "report": {"source": "manual_edge_validation"},
                    }
                },
            )
            return

        if api_path == "/tasks/88/samples" and method == "GET":
            _json_response(route, {"data": [_sample_row(1, "sample_001.jpg", 0)]})
            return

        if api_path == "/yolo-edge-validation/jobs" and method == "GET":
            _json_response(route, {"data": [_pending_history_job_summary()]})
            return

        if api_path == "/yolo-edge-validation/jobs/701" and method == "GET":
            _json_response(route, {"data": _pending_history_job_detail()})
            return

        if api_path.startswith("/yolo-bm1688-exports/123/sop-eval-report/") and method == "GET":
            route.fulfill(
                status=200,
                content_type="text/markdown; charset=utf-8",
                body="## mock report\n\nok\n",
            )
            return

        raise AssertionError(f"Unexpected API call: {method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"{page_origin}/**", handle_api)
        page.goto(page_url)

        expect(page.locator("#detailPipelineCurrent")).to_have_text("等待边端启动")
        expect(page.locator("#detailPipelineSummary")).to_contain_text("等待启动")

        page.locator("[data-detail-tab='manual']").click()
        page.locator("#btnDetailManualEdgeProxy").click()
        expect(page.locator("#edgeManualModal")).to_be_visible(timeout=20_000)
        expect(page.locator("#edgeManualJobLog")).to_contain_text("任务 #701")
        expect(page.locator("#edgeManualJobLog")).to_contain_text("PENDING")

        browser.close()


def test_manual_edge_refine_threshold_frontend_flow():
    page_origin = "http://labelu.test"
    page_url = f"{page_origin}/yolo-bm1688-exports?export_id=123"
    page_html = BM1688_PAGE_FILE.read_text(encoding="utf-8")
    created_payloads: list[dict[str, Any]] = []
    refine_payloads: list[dict[str, Any]] = []
    job601_poll_count = 0

    def handle_api(route: Route) -> None:
        nonlocal job601_poll_count
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/yolo-bm1688-exports":
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=page_html,
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

        api_path = path[len(API_PREFIX) :]
        method = request.method

        if api_path == "/local-dev/session" and method == "GET":
            _json_response(route, {"data": {"session_id": "playwright"}})
            return
        if api_path.startswith("/yolo-bm1688-exports/123/runtime-log/") and method == "GET":
            route.fulfill(status=404, body="")
            return
        if api_path.startswith("/yolo-bm1688-exports/123/sop-eval-asset/") and method == "GET":
            route.fulfill(status=404, body="")
            return

        if api_path == "/yolo-bm1688-exports/compiler-settings" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "tpu_mlir_root": "D:/toolchains/tpu-mlir",
                        "tpu_mlir_python": "python",
                        "auto_compile": True,
                        "quantize": "full",
                        "compile_platforms": "bm1688",
                        "bmodel_basename": "yolov11n_int8_1b",
                        "sophon_export": True,
                        "calibration_dataset_dir": "D:/datasets/calib",
                        "builtin_script_path": "labelu/scripts/export_bm1688.py",
                    }
                },
            )
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

        if api_path == "/yolo-bm1688-exports/edge-ssh-settings" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "enabled": True,
                        "host": "192.168.0.10",
                        "port": 22,
                        "user": "linaro",
                        "password_configured": True,
                        "auto_after_export": True,
                        "remote_base": "/data/labelu_edge/jobs",
                        "sophon_demo_root": "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
                        "docker_container": "bm1688",
                        "docker_use_sudo": True,
                    }
                },
            )
            return

        if api_path == "/annotation-taxonomy/dataset-categories" and method == "GET":
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/label-presets" and method == "GET":
            _json_response(route, {"data": []})
            return

        if api_path == "/users/me" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "id": 1,
                        "email": "mock@example.com",
                        "username": "mock-user",
                    }
                },
            )
            return

        if api_path == "/yolo-bm1688-exports" and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 123,
                            "task_id": 88,
                            "task_name": "边端验证任务",
                            "yolo_train_job_id": 66,
                            "status": "COMPLETED",
                            "imgsz": 640,
                            "onnx_filename": "yolov11n.onnx",
                            "quality_summary": "PT/ONNX/bmodel 评估已完成",
                            "validation_gate_label": "通过",
                            "edge_validation_label": "通过",
                            "board_validation": {
                                "status": "COMPLETED",
                                "report_summary": "最近一次边端验证已完成",
                            },
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return

        if api_path == "/yolo-bm1688-exports/123" and method == "GET":
            _json_response(route, {"data": _export_detail()})
            return

        if api_path == "/yolo-bm1688-exports/123/summary" and method == "GET":
            _json_response(route, {"data": _export_detail()})
            return

        if api_path == "/tasks/88/samples" and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        _sample_row(1, "sample_001.jpg", 1),
                        _sample_row(2, "sample_002.jpg", 1),
                    ]
                },
            )
            return

        if api_path == "/yolo-edge-validation/jobs" and method == "GET":
            assert query.get("export_id") == ["123"]
            if query.get("size") != ["1"]:
                assert query.get("task_id") == ["88"]
            _json_response(route, {"data": [_history_job_summary()]})
            return

        if api_path == "/yolo-edge-validation/jobs/501" and method == "GET":
            _json_response(route, {"data": _history_job_detail()})
            return

        if api_path == "/yolo-edge-validation/jobs/501/refine-thresholds" and method == "POST":
            refine_payloads.append(request.post_data_json or {})
            _json_response(
                route,
                {
                    "data": {
                        "base_thresholds": {
                            "conf_thres": 0.33,
                            "nms_thres": 0.68,
                            "match_iou_thres": 0.50,
                        },
                        "refined_thresholds": {
                            "source": "ai_refined",
                            "conf_thres": 0.31,
                            "nms_thres": 0.66,
                            "match_iou_thres": 0.52,
                            "reason": "根据本次边端误检情况适度下调 conf，并微调 NMS/匹配 IoU。",
                            "confidence_level": "high",
                            "provider_key": "mock-llm",
                        },
                        "delta_summary": {
                            "conf_thres": -0.02,
                            "nms_thres": -0.02,
                            "match_iou_thres": 0.02,
                        },
                        "fallback_used": False,
                    }
                },
            )
            return

        if api_path == "/yolo-edge-validation/jobs" and method == "POST":
            payload = request.post_data_json or {}
            created_payloads.append(payload)
            _json_response(
                route,
                {
                    "data": {
                        "id": 601,
                        "status": "PENDING",
                        "mode": payload.get("mode", "run_and_eval"),
                        "threshold_source": payload.get("threshold_source"),
                        "thresholds": payload.get("thresholds") or {},
                        "ai_thresholds": payload.get("ai_thresholds"),
                        "sample_ids": payload.get("sample_ids") or [],
                        "attachment_ids": payload.get("attachment_ids") or [],
                        "image_count": 2,
                        "labeled_image_count": 2,
                        "report_summary": "任务已创建，等待下发边端",
                    }
                },
            )
            return

        if api_path == "/yolo-edge-validation/jobs/601/run" and method == "POST":
            _json_response(
                route,
                {
                    "data": {
                        "id": 601,
                        "status": "PROCESSING",
                        "report_summary": "边端执行中",
                    }
                },
            )
            return

        if api_path == "/yolo-edge-validation/jobs/601" and method == "GET":
            job601_poll_count += 1
            _json_response(
                route,
                {
                    "data": {
                        "id": 601,
                        "status": "COMPLETED",
                        "mode": "run_and_eval",
                        "threshold_source": "ai_refined",
                        "thresholds": {
                            "conf_thres": 0.31,
                            "nms_thres": 0.66,
                            "match_iou_thres": 0.52,
                        },
                        "sample_ids": [1, 2],
                        "attachment_ids": [],
                        "image_count": 2,
                        "labeled_image_count": 2,
                        "report_summary": "边端验证完成，建议使用修正阈值",
                        "started_at": "2026-06-28T10:05:00",
                        "completed_at": "2026-06-28T10:06:00",
                        "result": {
                            "execution_summary": {"status": "COMPLETED"},
                            "prediction_summary": {"image_count": 2},
                            "eval_summary": {"map50": 0.637},
                        },
                    }
                },
            )
            return

        raise AssertionError(f"Unexpected API call: {method} {api_path}?{parsed.query}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script(
            """
                localStorage.setItem('token', 'Bearer mock-token');
                window.confirm = () => true;
                """
        )
        page.route(f"{page_origin}/**", handle_api)
        page.goto(page_url)

        page.locator("[data-detail-tab='manual']").click()
        page.locator("#btnDetailManualEdgeProxy").click()
        expect(page.locator("#edgeManualModal .modal-dialog")).to_be_visible(timeout=20_000)
        expect(page.locator("#edgeManualSampleList .edge-manual-sample-card")).to_have_count(2)
        expect(page.locator("#edgeManualHistoryList .edge-manual-history-item")).to_have_count(1)
        expect(
            page.locator("#edgeManualHistoryList .edge-manual-history-item .tag-no")
        ).to_have_text("未通过")
        expect(
            page.locator("#edgeManualHistoryList .edge-manual-history-item .tag-ok")
        ).to_have_count(0)

        page.locator('[data-edge-history-refine="501"]').click()

        expect(page.locator("#edgeManualRefineBox")).to_be_visible(timeout=10_000)
        expect(page.locator("#edgeManualRefineResult")).to_contain_text("conf_thres: 0.31")
        expect(page.locator("#edgeManualRefineDelta")).to_contain_text("match_iou_thres: 0.02")
        expect(page.locator("#edgeManualRefineReason")).to_contain_text("mock-llm")

        page.locator("#btnEdgeManualApplyRefine").click()

        expect(page.locator('input[name="edgeThresholdSource"][value="ai_refined"]')).to_be_checked()
        expect(page.locator("#edgeConfThres")).to_have_value("0.31")
        expect(page.locator("#edgeNmsThres")).to_have_value("0.66")
        expect(page.locator("#edgeMatchIouThres")).to_have_value("0.52")
        expect(page.locator("#edgeManualAiReason")).to_contain_text("AI 二次修正")

        page.locator("#btnEdgeManualSubmit").click()

        expect(page.locator("#edgeManualJobLog")).to_contain_text("任务 #601", timeout=10_000)
        expect(page.locator("#edgeManualJobLog")).to_contain_text("COMPLETED", timeout=10_000)
        browser.close()

    assert refine_payloads == [{"max_images": 4, "include_visual_evidence": True}]
    assert job601_poll_count >= 1
    assert len(created_payloads) == 1

    payload = created_payloads[0]
    assert payload["export_id"] == 123
    assert payload["task_id"] == 88
    assert payload["sample_ids"] == [1, 2]
    assert payload["attachment_ids"] == []
    assert payload["threshold_source"] == "ai_refined"
    assert payload["thresholds"]["conf_thres"] == 0.31
    assert payload["thresholds"]["nms_thres"] == 0.66
    assert payload["thresholds"]["match_iou_thres"] == 0.52
    assert payload["ai_thresholds"]["source"] == "ai_refined"
    assert payload["ai_thresholds"]["provider_key"] == "mock-llm"
    assert payload["ai_thresholds"]["reason"].startswith("根据本次边端误检情况")
