"""Frontend coverage for the independent system label management page."""

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
PAGE_URL = f"{BASE_URL}/annotation-settings"


def _json_response(route: Route, data: Any) -> None:
    route.fulfill(
        status=200,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False),
    )


def _handle_api(route: Route) -> None:
    path = route.request.url.split(API_PREFIX, 1)[1]
    if path.startswith("/local-dev/session"):
        _json_response(route, {"data": {"session_id": "playwright"}})
    elif path == "/users/me":
        _json_response(
            route,
            {"data": {"id": 1, "username": "admin@123.com", "is_task_superuser": True}},
        )
    elif path.startswith("/annotation-taxonomy/labels"):
        _json_response(
            route,
            {
                "meta_data": {"total": 1, "page": 0, "size": 1},
                "data": [
                    {
                        "id": 101,
                        "label_key": "行人",
                        "label_value": "person",
                        "annotation_kind": "bbox",
                        "color": "#1677ff",
                        "attributes": [
                            {
                                "key": "状态",
                                "value": "state",
                                "type": "enum",
                                "required": True,
                                "options": [
                                    {
                                        "key": "正常",
                                        "value": "normal",
                                        "isDefault": True,
                                    }
                                ],
                            }
                        ],
                        "description": None,
                        "status": "active",
                        "version": 3,
                        "reference_count": 2,
                    }
                ],
            },
        )
    elif path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": []})
    elif path.startswith("/annotation-taxonomy/global-tag-dimensions"):
        _json_response(route, {"data": []})
    elif path.startswith("/annotation-taxonomy/dataset-categories"):
        _json_response(
            route,
            {
                "meta_data": {"total": 1, "page": 0, "size": 20},
                "data": [
                    {
                        "id": 88,
                        "category_key": "training_consistent_pool",
                        "name": "训练一致数据集",
                        "description": "系统自动维护",
                        "sort_order": 9000,
                        "status": "active",
                        "version": 1,
                        "reference_count": 3,
                        "is_system": True,
                    }
                ],
            },
        )
    elif path.startswith("/annotation-taxonomy/label-aliases"):
        _json_response(route, {"data": []})
    elif path.startswith("/annotation-taxonomy/training-recipes"):
        _json_response(route, {"data": []})
    elif path.startswith("/annotation-taxonomy/runtime-templates"):
        _json_response(
            route,
            {
                "data": [
                    {
                        "id": 1,
                        "template_key": "yolov11_detection",
                        "name": "YOLOv11 检测运行模板",
                        "environment_id": "env-test",
                        "runtime_profile": "yolov11_detection",
                        "interface_version": 1,
                        "aicm_model_set_id": "530",
                        "expected_model_alg_type": "yoloV11",
                        "capability_contract": {
                            "supported_job_types": ["yolo_detection"],
                            "max_classes": 80,
                        },
                        "template_sha256": None,
                        "is_default": True,
                        "status": "active",
                        "version": 2,
                    }
                ]
            },
        )
    elif path.startswith("/yolo-aicm-publish/environments"):
        _json_response(
            route,
            {
                "data": {
                    "active_environment_id": "env-test",
                    "environments": [
                        {
                            "id": "env-test",
                            "name": "测试发布环境",
                            "base_url": "http://aicm.test:8102",
                            "username": "admin",
                            "password_configured": True,
                            "ready_to_publish": True,
                            "missing_fields": [],
                            "is_active": True,
                        }
                    ],
                }
            },
        )
    elif path.startswith("/yolo-aicm-publish/runtime-template-candidates"):
        _json_response(
            route,
            {
                "meta_data": {"total": 1, "page": 1, "size": 1},
                "data": [
                    {
                        "model_set_id": 530,
                        "name": "YOLOv11 detection runtime",
                        "model_alg_type": "yoloV11",
                        "status": "ready",
                        "created_at": "2026-08-06T10:00:00",
                    }
                ],
            },
        )
    else:
        raise AssertionError(f"Unexpected API call: {route.request.method} {path}")


def test_independent_label_list_and_edit_modal_load_on_first_visit() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_api)

        page.goto(PAGE_URL)

        label_rows = page.locator("#tblLabels tbody tr")
        expect(label_rows).to_have_count(1, timeout=15_000)
        expect(label_rows).to_contain_text("行人")
        expect(label_rows).to_contain_text("person")
        expect(page.locator("#labelPageSummary")).to_have_text("共 1 条")
        expect(page.locator("#filterLabelStatus")).to_be_visible()
        expect(page.locator("#labelPageSize")).to_have_value("20")
        edit_button = page.locator("#tblLabels .edit-label")
        expect(edit_button).to_have_count(1)
        edit_button.click()
        expect(page.locator("#modalLabel")).to_have_class("modal-panel open")
        expect(page.locator("#modalLabelTitle")).to_have_text("编辑标签 #101")
        expect(page.locator("#labelKey")).to_have_value("行人")
        expect(page.locator("#labelValue")).to_have_value("person")
        expect(page.locator("#labelValue")).to_have_attribute("readonly", "")
        expect(page.locator("#labelAttributesEditor .attribute-card")).to_have_count(1)
        expect(page.locator('[data-field="value"]')).to_have_value("state")
        page.locator("#btnAddTextAttribute").click()
        expect(page.locator("#labelAttributesEditor .attribute-card")).to_have_count(2)
        page.keyboard.press("Escape")
        expect(page.locator("#modalLabel")).not_to_have_class("modal-panel open")

        browser.close()


def test_runtime_template_query_opens_editor_and_loads_aicm_model_sets() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_api)

        page.goto(f"{PAGE_URL}?tab=runtime&template_key=yolov11_detection")

        expect(page.locator("#panel-runtime")).to_have_class("panel active")
        expect(page.locator("#tblRuntime tbody tr")).to_have_count(1, timeout=15_000)
        expect(page.locator("#tblRuntime")).to_contain_text("#530")
        expect(page.locator("#modalRuntime")).to_have_class("modal-panel open")
        expect(page.locator("#modalRuntimeTitle")).to_have_text("编辑运行模板 #1")
        expect(page.locator("#runtimeEnvironmentState")).to_contain_text("环境已就绪")
        expect(page.locator("#runtimeModelSetId")).to_have_value("530")
        expect(page.locator("#runtimeCandidateSummary")).to_contain_text("已加载 1 个模型集")

        browser.close()


def test_system_dataset_category_is_read_only_in_the_management_table() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handle_api)

        page.goto(f"{PAGE_URL}?tab=categories")

        row = page.locator("#tblCategories tbody tr")
        expect(row).to_have_count(1, timeout=15_000)
        expect(row).to_contain_text("训练一致数据集")
        expect(row).to_contain_text("系统分类")
        expect(row).to_contain_text("系统维护")
        expect(row.locator(".edit-category")).to_have_count(0)
        expect(row.locator(".delete-category")).to_have_count(0)

        browser.close()
