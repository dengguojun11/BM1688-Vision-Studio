"""
Playwright 前端回归：
验证训练页模型库、发布页可发布模型中的质量判定展示，
并校验表格容器不会因为少量数据被异常撑出大块空白。
"""
from __future__ import annotations

import json
import os
from typing import Any

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:9888")
pytestmark = pytest.mark.skipif(
    not os.environ.get("LABELU_E2E_BASE"),
    reason="set LABELU_E2E_BASE to run legacy extension E2E tests",
)
API_PREFIX = "/api/v1"
PUBLISH_PAGE_URL = os.environ.get(
    "LABELU_E2E_PUBLISH_PAGE",
    f"{BASE_URL}/yolo-model-publish",
)
TRAINING_PAGE_URL = os.environ.get(
    "LABELU_E2E_TRAINING_PAGE",
    f"{BASE_URL}/yolo-training?embedded=1&panel=models",
)


def _json_response(route: Route, data: Any, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False),
    )


def _publish_rows() -> list[dict[str, Any]]:
    return [
        {
            "export_id": 48,
            "yolo_train_job_id": 191,
            "task_id": 12,
            "task_name": "垃圾暴露训练任务",
            "rect_preset_name": "垃圾暴露",
            "rect_preset_slug": "garbageStreet",
            "category": "垃圾暴露",
            "dataset_category_id": 5,
            "dataset_category_key": "street_scene",
            "dataset_category_name": "街面治理",
            "class_names": ["垃圾桶", "垃圾袋"],
            "class_order_signature": "class-order-review",
            "data_yaml_sha256": "data-yaml-review",
            "publish_template_key": "yolov11_detection",
            "publish_template_name": "街面检测运行模板",
            "publish_template_version": 2,
            "validation_pass": {
                "status": "不达标",
                "detail": "mAP50-95=0.4796，达标线≥0.5000",
            },
            "training_assessment": {
                "status_code": "conditional_passed",
                "status_label": "条件达标",
                "reason_codes": ["map5095_below_pass_line"],
                "reason_summary": "mAP50-95=0.4796 低于场景训练达标线 0.70",
            },
            "edge_validation_assessment": {
                "status_code": "usable",
                "status_label": "可用",
                "reason_codes": [],
                "reason_summary": "最近边端验证已完成",
            },
            "release_gate_assessment": {
                "status_code": "review_required",
                "status_label": "需复核",
                "reason_codes": ["generalization_missing_review"],
                "reason_summary": "泛化质量仍需人工复核",
            },
            "quality_assessment": {
                "training_quality": {
                    "status": "达标",
                    "reasons": ["旧兼容字段不应再主导页面结论"],
                },
                "generalization_quality": {
                    "status": "未验证",
                    "reasons": ["未执行 test 集验证"],
                },
                "edge_usability": {"status": "可用", "reasons": []},
                "release_readiness": {
                    "status": "需复核",
                    "reasons": ["泛化质量仍需人工复核"],
                },
            },
            "map50": 0.63,
            "map50_95": 0.4796,
            "bmodel_options": [
                {
                    "rel_path": "BM1688/review_int8.bmodel",
                    "filename": "review_int8.bmodel",
                    "precision": "int8",
                    "label": "INT8",
                    "recommended": True,
                    "validation_status": "pass",
                    "publishable": True,
                    "pt_map50": 0.63,
                    "bmodel_map50": 0.625,
                    "drop_map50": 0.005,
                }
            ],
            "template_cached": True,
            "publish_blocked_reason": None,
            "edge_validation": {
                "status": "COMPLETED",
                "report_summary": "最近一次边端验证已完成",
                "report": {
                    "source": "manual_edge_validation",
                    "mode": "run_and_eval",
                },
            },
            "board_validation": {
                "status": "COMPLETED",
                "report_summary": "最近一次边端验证已完成",
                "report": {
                    "source": "manual_edge_validation",
                    "mode": "run_and_eval",
                },
            },
        },
        {
            "export_id": 46,
            "yolo_train_job_id": 192,
            "task_id": 13,
            "task_name": "占道经营训练任务",
            "rect_preset_name": "占道经营",
            "rect_preset_slug": "streetVendorOccupation",
            "category": "占道经营",
            "dataset_category_id": 5,
            "dataset_category_key": "street_scene",
            "dataset_category_name": "街面治理",
            "class_names": ["摊贩", "遮阳棚"],
            "class_order_signature": "class-order-ready",
            "data_yaml_sha256": "data-yaml-ready",
            "publish_template_key": "yolov11_detection",
            "publish_template_name": "街面检测运行模板",
            "publish_template_version": 2,
            "validation_pass": {
                "status": "达标",
                "detail": "mAP50-95=0.7333，达标线≥0.5000",
            },
            "training_assessment": {
                "status_code": "passed",
                "status_label": "达标",
                "reason_codes": [],
                "reason_summary": "训练指标满足要求",
            },
            "edge_validation_assessment": {
                "status_code": "usable",
                "status_label": "可用",
                "reason_codes": [],
                "reason_summary": "板端验证通过",
            },
            "release_gate_assessment": {
                "status_code": "publishable",
                "status_label": "可发布",
                "reason_codes": [],
                "reason_summary": "训练与边端验证结论满足发布要求",
            },
            "quality_assessment": {
                "training_quality": {"status": "达标", "reasons": []},
                "generalization_quality": {"status": "稳定", "reasons": []},
                "edge_usability": {"status": "可用", "reasons": []},
                "release_readiness": {"status": "可发布", "reasons": []},
            },
            "map50": 0.88,
            "map50_95": 0.7333,
            "bmodel_options": [
                {
                    "rel_path": "BM1688/ready_int8.bmodel",
                    "filename": "ready_int8.bmodel",
                    "precision": "int8",
                    "label": "INT8",
                    "recommended": False,
                    "validation_status": "exceeded",
                    "publishable": True,
                    "pt_map50": 0.88,
                    "bmodel_map50": 0.0,
                    "drop_map50": 0.88,
                    "quality_warning": "INT8 BModel 相对 PT 的 mAP50 掉点超过当前场景上限",
                },
                {
                    "rel_path": "BM1688/ready_fp16.bmodel",
                    "filename": "ready_fp16.bmodel",
                    "precision": "fp16",
                    "label": "FP16",
                    "recommended": True,
                    "validation_status": "pass",
                    "publishable": True,
                    "pt_map50": 0.88,
                    "bmodel_map50": 0.878,
                    "drop_map50": 0.002,
                },
                {
                    "rel_path": "BM1688/ready_fp32.bmodel",
                    "filename": "ready_fp32.bmodel",
                    "precision": "fp32",
                    "label": "FP32",
                    "recommended": False,
                    "validation_status": "pass",
                    "publishable": True,
                    "pt_map50": 0.88,
                    "bmodel_map50": 0.879,
                    "drop_map50": 0.001,
                },
            ],
            "template_cached": False,
            "publish_blocked_reason": None,
            "edge_validation": {
                "status": "COMPLETED",
                "report_summary": "板端验证通过",
                "report": {"source": "board_agent", "mode": "run_and_eval"},
            },
            "board_validation": {
                "status": "COMPLETED",
                "report_summary": "板端验证通过",
                "report": {"source": "board_agent", "mode": "run_and_eval"},
            },
        },
    ]


def _trained_rows() -> list[dict[str, Any]]:
    return [
        {
            "job_id": 191,
            "id": 191,
            "task_id": 12,
            "task_name": "垃圾暴露训练任务",
            "category": "垃圾暴露",
            "model_weights": "yolo11n.pt",
            "epochs": 100,
            "imgsz": 640,
            "total_sample_count": 320,
            "train_sample_count": 260,
            "val_sample_count": 40,
            "test_sample_count": 20,
            "training_val_metrics": {
                "map50": 0.63,
                "map50_95": 0.4796,
                "summary_line": "val mAP50=0.63 / mAP50-95=0.4796",
            },
            "test_val_metrics": {
                "status": "missing",
                "summary_line": "暂无 test 验证",
            },
            "val_test_comparison": {"summary_line": "test 未执行，无法比较"},
            "validation_pass": {
                "status": "不达标",
                "detail": "mAP50-95=0.4796，达标线≥0.5000",
            },
            "training_assessment": {
                "status_code": "conditional_passed",
                "status_label": "条件达标",
                "reason_codes": ["map5095_below_pass_line"],
                "reason_summary": "mAP50-95=0.4796 低于场景训练达标线 0.70",
            },
            "quality_assessment": {
                "training_quality": {
                    "status": "达标",
                    "reasons": ["旧兼容字段不应再主导训练页主结论"],
                },
                "generalization_quality": {
                    "status": "未验证",
                    "reasons": ["未执行 test 集验证"],
                },
                "edge_usability": {"status": "可用", "reasons": []},
                "release_readiness": {
                    "status": "需复核",
                    "reasons": ["泛化质量仍需人工复核"],
                },
            },
            "creator_email": "admin@123.com",
            "updated_at": "2026-06-29T00:00:00",
        },
        {
            "job_id": 192,
            "id": 192,
            "task_id": 13,
            "task_name": "占道经营训练任务",
            "category": "占道经营",
            "model_weights": "yolo11n.pt",
            "epochs": 120,
            "imgsz": 640,
            "total_sample_count": 640,
            "train_sample_count": 500,
            "val_sample_count": 80,
            "test_sample_count": 60,
            "training_val_metrics": {
                "map50": 0.84,
                "map50_95": 0.7333,
                "summary_line": "val mAP50=0.84 / mAP50-95=0.7333",
            },
            "test_val_metrics": {
                "status": "completed",
                "map50": 0.82,
                "map50_95": 0.7011,
                "summary_line": "test mAP50=0.82 / mAP50-95=0.7011",
            },
            "val_test_comparison": {
                "summary_line": "val/test 差异可接受",
                "delta_map50": -0.02,
                "delta_map50_95": -0.0322,
            },
            "validation_pass": {
                "status": "达标",
                "detail": "mAP50-95=0.7333，达标线≥0.5000",
            },
            "training_assessment": {
                "status_code": "passed",
                "status_label": "达标",
                "reason_codes": [],
                "reason_summary": "训练指标满足要求",
            },
            "quality_assessment": {
                "training_quality": {"status": "达标", "reasons": []},
                "generalization_quality": {"status": "稳定", "reasons": []},
                "edge_usability": {"status": "可用", "reasons": []},
                "release_readiness": {"status": "可发布", "reasons": []},
            },
            "creator_email": "admin@123.com",
            "updated_at": "2026-06-29T00:05:00",
        },
    ]


def _handle_publish_api(route: Route) -> None:
    request = route.request
    path = request.url.split(API_PREFIX, 1)[1]
    method = request.method

    if path.startswith("/local-dev/session") and method == "GET":
        _json_response(route, {"data": {"session_id": "playwright"}})
        return

    if path == "/users/me" and method == "GET":
        _json_response(
            route,
            {"data": {"id": 1, "email": "admin@123.com", "username": "admin"}},
        )
        return

    if path.startswith("/annotation-taxonomy/dataset-categories") and method == "GET":
        _json_response(
            route,
            {
                "data": [
                    {
                        "id": 5,
                        "category_key": "street_scene",
                        "name": "街面治理",
                        "status": "active",
                        "sort_order": 1,
                    },
                ]
            },
        )
        return

    if path.startswith("/yolo-aicm-publish/publishable") and method == "GET":
        _json_response(route, {"data": _publish_rows(), "meta_data": {"total": 2}})
        return

    if path == "/yolo-aicm-publish/publish-jobs/mock-fp16" and method == "GET":
        _json_response(
            route,
            {
                "data": {
                    "job_id": "mock-fp16",
                    "export_id": 46,
                    "status": "COMPLETED",
                    "result": {"ok": True, "model_set_id": 901},
                }
            },
        )
        return

    if path.startswith("/yolo-aicm-publish/publish-jobs") and method == "GET":
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    if path == "/yolo-aicm-publish/publish" and method == "POST":
        _json_response(
            route,
            {
                "data": {
                    "job_id": "mock-fp16",
                    "export_id": 46,
                    "status": "PROCESSING",
                }
            },
        )
        return

    if path.startswith("/yolo-aicm-publish/environments") and method == "GET":
        _json_response(
            route,
            {
                "data": {
                    "active_environment_id": "prod-aicm",
                    "environments": [
                        {
                            "id": "prod-aicm",
                            "name": "生产 AICM",
                            "ready_to_publish": True,
                            "is_active": True,
                        }
                    ],
                }
            },
        )
        return

    raise AssertionError(f"Unexpected publish API call: {method} {path}")


def _handle_training_api(route: Route) -> None:
    request = route.request
    path = request.url.split(API_PREFIX, 1)[1]
    method = request.method

    if path.startswith("/local-dev/session") and method == "GET":
        _json_response(route, {"data": {"session_id": "playwright"}})
        return

    if path == "/users/me" and method == "GET":
        _json_response(
            route,
            {"data": {"id": 1, "email": "admin@123.com", "username": "admin"}},
        )
        return

    if path.startswith("/annotation-taxonomy/label-presets") and method == "GET":
        _json_response(
            route,
            {
                "data": [
                    {"slug": "garbageStreet", "name": "垃圾暴露"},
                    {"slug": "streetVendorOccupation", "name": "占道经营"},
                ]
            },
        )
        return

    if path.startswith("/annotation-taxonomy/") and method == "GET":
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    if path.startswith("/yolo-trained-models") and method == "GET":
        _json_response(route, {"data": _trained_rows(), "meta_data": {"total": 2}})
        return

    if path.startswith("/yolo-imported-models") and method == "GET":
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    if path.startswith("/yolo-train-jobs") and method == "GET":
        _json_response(route, {"data": _trained_rows(), "meta_data": {"total": 2}})
        return

    if path.startswith("/yolo-eval-reports") and method == "GET":
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    if path == "/yolo-bm1688-exports/compiler-settings" and method == "GET":
        _json_response(route, {"data": {"auto_compile": True}})
        return

    if path == "/yolo-train/remote-nodes/admin" and method == "GET":
        _json_response(route, {"data": []})
        return

    if path.startswith("/yolo-train/remote-nodes") and method == "GET":
        _json_response(route, {"data": []})
        return

    if path == "/yolo-train/hyperparam-fields" and method == "GET":
        _json_response(route, {"data": []})
        return

    if path.startswith("/yolo-train/") and method == "GET":
        _json_response(route, {"data": []})
        return

    if path.startswith("/tasks") and method == "GET":
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    raise AssertionError(f"Unexpected training API call: {method} {path}")


def test_publish_page_quality_assessment_block_is_compact_and_readable():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_publish_api)
        page.goto(PUBLISH_PAGE_URL)

        expect(page.locator("#publishTbody tr")).to_have_count(2, timeout=15_000)
        qa_cell = page.locator("#publishTbody tr").first.locator("td").nth(3)
        expect(qa_cell).to_contain_text("需复核")
        expect(qa_cell).to_contain_text("泛化质量仍需人工复核")
        expect(qa_cell.locator("text=泛化质量仍需人工复核")).to_have_count(1)
        expect(page.locator("#statPublishTotal")).to_have_text("2")
        expect(page.locator("#statPublishPassed")).to_have_text("1")
        expect(page.locator("#statPublishPage")).to_have_text("1")
        expect(page.locator('[data-queue-count="ready"]')).to_have_text("1")
        expect(page.get_by_text("补齐运行模板配置")).to_have_count(0)
        expect(page.get_by_role("button", name="检查模板")).to_have_count(0)

        ready_row = page.locator('#publishTbody tr[data-gate-group="ready"]')
        expect(ready_row).to_have_count(1)
        ready_row.get_by_role("button", name="发布").click()
        expect(page.locator("#publishConfirmModal")).to_be_visible()
        expect(page.locator("#publishConfirmModal")).to_contain_text(
            "ready_fp16.bmodel"
        )
        expect(page.locator('input[name="publishBmodel"]')).to_have_count(3)
        expect(page.locator('input[name="publishBmodel"][value="0"]')).to_be_enabled()
        expect(page.locator('input[name="publishBmodel"][value="1"]')).to_be_checked()
        expect(page.locator("#publishConfirmModal")).to_contain_text("FP16（推荐发布）")
        expect(page.locator("#publishConfirmModal")).to_contain_text("INT8 BModel 相对 PT")
        expect(page.locator("#publishConfirmRisk")).to_be_hidden()
        expect(page.locator("#chkKeepTemplateCache")).to_have_count(0)

        table_box = page.locator("#panelPublishable .train-table-wrap").bounding_box()
        assert table_box is not None
        assert 180 <= table_box["height"] <= 620

        with page.expect_request(
            lambda request: request.method == "POST"
            and request.url.endswith("/api/v1/yolo-aicm-publish/publish")
        ) as publish_request:
            expect(
                page.get_by_role("button", name="确认人工发布 FP16")
            ).to_have_css("background-color", "rgb(22, 119, 255)")
            page.get_by_role("button", name="确认人工发布 FP16").click()
        payload = publish_request.value.post_data_json
        assert payload["bmodel_precision"] == "fp16"
        assert payload["bmodel_rel_path"] == "BM1688/ready_fp16.bmodel"
        browser.close()


def test_publish_page_review_row_can_be_manually_published():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_publish_api)
        page.goto(PUBLISH_PAGE_URL)

        review_row = page.locator('#publishTbody tr[data-gate-group="quality"]')
        expect(review_row).to_have_count(1, timeout=15_000)
        expect(review_row).to_contain_text("需复核，可人工发布")
        expect(review_row.get_by_role("button", name="人工发布")).to_have_css(
            "background-color", "rgb(212, 107, 8)"
        )
        review_row.get_by_role("button", name="人工发布").click()
        expect(page.locator("#publishConfirmModal")).to_be_visible()
        expect(page.locator("#publishConfirmRisk")).to_contain_text(
            "泛化质量仍需人工复核"
        )
        expect(
            page.get_by_role("button", name="确认人工发布 INT8")
        ).to_be_enabled()
        expect(
            page.get_by_role("button", name="确认人工发布 INT8")
        ).to_have_css("background-color", "rgb(212, 107, 8)")
        browser.close()


def test_publish_page_mobile_uses_record_cards_without_horizontal_overflow():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_publish_api)
        page.goto(f"{PUBLISH_PAGE_URL}?embedded=1")

        expect(page.locator("#publishTbody tr")).to_have_count(2, timeout=15_000)
        expect(page.locator("#publishTbody tr").first).to_be_visible()
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1
        browser.close()


def test_training_models_page_quality_assessment_block_is_compact_and_readable():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_training_api)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#modelsTbody tr")).to_have_count(2, timeout=15_000)
        qa_cell = page.locator("#modelsTbody tr").first.locator("td").nth(3)
        expect(qa_cell).to_contain_text("待复核")
        expect(qa_cell).to_contain_text("Val mAP50-95=0.4796")
        expect(qa_cell).not_to_contain_text("泛化")
        expect(qa_cell).not_to_contain_text("发布")

        table_box = page.locator("#panelModelsSystem .train-table-wrap").bounding_box()
        assert table_box is not None
        assert 180 <= table_box["height"] <= 980
        browser.close()


def test_training_job_list_validation_explanation_can_expand():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_training_api)
        page.goto(f"{BASE_URL}/yolo-training?embedded=1&panel=list")

        expect(page.locator("#globalJobTbody tr")).to_have_count(2, timeout=15_000)
        trigger = page.locator("#globalJobTbody tr").first.locator("[data-val-pass-toggle]")
        expect(trigger).to_be_visible()
        detail = page.locator("#globalJobTbody tr").first.locator("[data-val-pass-detail]")
        expect(detail).to_be_hidden()
        trigger.click()
        expect(detail).to_be_visible()
        expect(detail).to_contain_text("mAP50-95=0.4796")
        browser.close()
