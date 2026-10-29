from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:9888")
API_PREFIX = "/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = REPO_ROOT / "frontend" / "extensions" / "assets"
TRAINING_PAGE_FILE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"
TRAINING_PAGE_URL = f"{BASE_URL}/yolo-training?embedded=1&panel=list"


def _json_response(route: Route, data, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False),
    )


def _fulfill_asset(route: Route, path: str) -> bool:
    if path.startswith("/assets/"):
        asset = ASSETS_DIR / path.split("/assets/", 1)[1]
        if asset.exists():
            suffix = asset.suffix.lower()
            content_type = "text/plain; charset=utf-8"
            if suffix == ".js":
                content_type = "application/javascript; charset=utf-8"
            elif suffix == ".css":
                content_type = "text/css; charset=utf-8"
            route.fulfill(
                status=200,
                content_type=content_type,
                body=asset.read_text(encoding="utf-8"),
            )
            return True
        route.fulfill(status=404, body="")
        return True
    if "marked.min.js" in path:
        route.fulfill(
            status=200,
            content_type="application/javascript; charset=utf-8",
            body="window.marked = { parse: function (s) { return String(s || ''); } };",
        )
        return True
    if "purify.min.js" in path:
        route.fulfill(
            status=200,
            content_type="application/javascript; charset=utf-8",
            body="window.DOMPurify = { sanitize: function (s) { return String(s || ''); } };",
        )
        return True
    if path in ("/favicon.ico", "/favicon.svg"):
        route.fulfill(status=204, body="")
        return True
    return False


def test_label_dimension_create_flow_checks_name_previews_and_submits():
    name_check_bodies: list[dict] = []
    preview_bodies: list[dict] = []
    create_bodies: list[dict] = []

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path

        if path == "/yolo-training":
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=TRAINING_PAGE_FILE.read_text(encoding="utf-8"),
            )
            return
        if _fulfill_asset(route, path):
            return
        if not path.startswith(API_PREFIX):
            route.fulfill(status=404, body="")
            return

        api_path = path[len(API_PREFIX) :]
        if api_path in {"/users/me", "/users/about"}:
            _json_response(route, {"data": {"id": 1, "username": "admin@123.com"}})
            return
        if api_path == "/annotation-taxonomy/labels":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 1,
                            "label_key": "零散垃圾",
                            "label_value": "scatter_a",
                            "color": "#1677ff",
                            "status": "active",
                            "image_count": 9,
                        }
                    ]
                },
            )
            return
        if api_path == "/annotation-taxonomy/global-tag-dimensions":
            _json_response(route, {"data": []})
            return
        if api_path in {
            "/annotation-taxonomy/dataset-categories",
            "/annotation-taxonomy/runtime-templates",
        }:
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/training-recipes":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 1,
                            "recipe_key": "yolov11_detection",
                            "name": "YOLOv11 detection",
                            "model_family": "yolov11",
                            "annotation_kind": "bbox",
                            "version": 1,
                            "status": "active",
                        }
                    ]
                },
            )
            return
        if api_path.startswith("/annotation-taxonomy/label-presets"):
            _json_response(
                route,
                {
                    "data": [
                        {
                            "slug": "preset_a",
                            "name": "预设A",
                            "items": [
                                {"id": 1, "label_key": "零散垃圾", "label_value": "scatter_a", "sort_order": 0},
                                {"id": 2, "label_key": "打包垃圾", "label_value": "pack_a", "sort_order": 1},
                            ],
                        },
                        {
                            "slug": "preset_b",
                            "name": "预设B",
                            "items": [
                                {"id": 3, "label_key": "零散垃圾", "label_value": "scatter_b", "sort_order": 0},
                            ],
                        },
                    ]
                },
            )
            return
        if api_path == "/yolo-training-tasks/name-check":
            body = json.loads(request.post_data or "{}")
            name_check_bodies.append(body)
            _json_response(
                route,
                {
                    "data": {
                        "available": True,
                        "normalized_name": body.get("name", ""),
                        "reason": None,
                    }
                },
            )
            return
        if api_path == "/yolo-training-tasks/preview":
            body = json.loads(request.post_data or "{}")
            preview_bodies.append(body)
            _json_response(
                route,
                {
                    "data": {
                        "selected_preset_count": 1,
                        "selected_label_item_count": 1,
                        "merged_class_count": 1,
                        "included_task_count": 1,
                        "included_image_count": 9,
                        "ignored_task_count": 1,
                        "merged_classes": [
                            {
                                "class_name": "零散垃圾",
                                "class_order": 0,
                                "selected_from": ["预设A/零散垃圾"],
                                "image_count": 9,
                            }
                        ],
                        "included_tasks": [
                            {
                                "task_id": 101,
                                "task_name": "街道垃圾任务A",
                                "included_image_count": 9,
                                "matched_done_sample_count": 12,
                                "match_sources": ["预设A/零散垃圾"],
                            }
                        ],
                        "ignored_tasks": [
                            {
                                "task_id": 102,
                                "task_name": "街道垃圾任务B",
                                "reason_code": "NO_VALID_SAMPLES_AFTER_CLEAN",
                                "reason_text": "按标签清洗后无有效图片",
                            }
                        ],
                    }
                },
            )
            return
        if api_path == "/yolo-training-tasks":
            body = json.loads(request.post_data or "{}")
            create_bodies.append(body)
            _json_response(
                route,
                {
                    "data": {
                        "training_task_id": 88,
                        "name": body["name"],
                        "mode": "label_dimension",
                        "primary_task_id": 101,
                        "latest_job_id": 501,
                        "included_task_count": 1,
                        "included_image_count": 9,
                        "ignored_task_count": 1,
                        "message": "训练任务已创建。纳入任务 1 个，纳入图片 9 张，忽略任务 1 个。",
                    }
                },
            )
            return
        if api_path.startswith("/yolo-train-jobs/"):
            _json_response(
                route,
                {
                    "data": {
                        "id": 501,
                        "task_id": 101,
                        "task_name": "街道垃圾任务A",
                        "status": "PENDING",
                        "epochs": 50,
                        "imgsz": 640,
                        "model_weights": "yolo11n.pt",
                        "hyperparams": {},
                    }
                },
            )
            return
        if api_path.startswith("/yolo-train-jobs"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-trained-models"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-imported-models"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-eval-reports"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path == "/yolo-bm1688-exports/compiler-settings":
            _json_response(route, {"data": {"auto_compile": True}})
            return
        if api_path == "/yolo-train/remote-nodes/admin":
            _json_response(route, {"data": []})
            return
        if api_path.startswith("/yolo-train/remote-nodes"):
            _json_response(route, {"data": []})
            return
        if api_path == "/yolo-train/hyperparam-fields":
            _json_response(route, {"data": []})
            return
        if api_path.startswith("/yolo-train/eligible-tasks"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-train/"):
            _json_response(route, {"data": []})
            return
        if api_path.startswith("/tasks"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        raise AssertionError(f"Unexpected API call: {request.method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        page.locator("#btnOpenCreate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        page.locator("#btnSwitchToLabelMode").click()
        expect(page.locator("#modalTrainingTaskName")).to_be_visible(timeout=15_000)
        expect(page.locator("#pageLoadingOverlay")).to_be_hidden()
        assert page.locator("#modalTrainingTaskName").is_enabled()

        page.evaluate(
            """
            window.__holdNameCheck = true;
            window.__holdPreview = true;
            window.__originalTrainingFetch = window.fetch;
            window.fetch = function (input, init) {
                if (window.__holdNameCheck && String(input).indexOf('/yolo-training-tasks/name-check') >= 0) {
                    return new Promise(function () {});
                }
                if (window.__holdPreview && String(input).indexOf('/yolo-training-tasks/preview') >= 0) {
                    return new Promise(function () {});
                }
                return window.__originalTrainingFetch.call(this, input, init);
            };
            """
        )

        page.fill("#modalTrainingTaskName", "零散垃圾训练集")
        page.locator("#modalCreateTitle").click()
        page.wait_for_timeout(100)
        expect(page.locator("#pageLoadingOverlay")).to_be_hidden()
        page.evaluate("window.__holdNameCheck = false;")
        page.locator("#modalTrainingTaskName").focus()
        page.locator("#modalCreateTitle").click()
        expect(page.locator("#modalTrainingTaskNameStatus")).to_contain_text("可创建", timeout=15_000)

        label_checkbox = page.locator('#labelModeLabelList input[type="checkbox"]').first
        expect(page.locator("#labelModeLabelList")).to_contain_text("scatter_a", timeout=15_000)
        label_checkbox.check()
        page.wait_for_timeout(100)
        expect(page.locator("#pageLoadingOverlay")).to_be_hidden()
        page.evaluate("window.__holdPreview = false;")
        label_checkbox.uncheck()
        label_checkbox.check()

        expect(page.locator("#labelModePreview")).to_contain_text("纳入任务 1 个", timeout=15_000)
        expect(page.locator("#labelModePreview")).to_contain_text("忽略任务 1 个", timeout=15_000)

        page.locator("#btnCreateWizardNext").click()
        expect(page.locator('[data-create-wizard-panel="2"]')).to_be_visible()
        page.locator("#btnCreateWizardNext").click()
        expect(page.locator('[data-create-wizard-panel="3"]')).to_be_visible()
        expect(page.locator("#createJobReviewList")).to_contain_text("纳入 1 个任务，9 张图片")
        page.locator("#btnModalSubmit").click()
        expect(page.locator("#toast")).to_contain_text("训练任务已创建", timeout=15_000)

        assert name_check_bodies == [{"name": "零散垃圾训练集"}]
        assert preview_bodies[-1] == {
            "label_ids": [1],
            "dataset_category_id": None,
            "dataset_category_ids": [],
            "training_recipe_key": "yolov11_detection",
            "training_recipe_version": 1,
            "runtime_profile": "yolov11_detection",
            "runtime_interface_version": 1,
            "sample_conditions": {},
        }
        assert create_bodies == [
            {
                "name": "零散垃圾训练集",
                "label_ids": [1],
                "training_recipe_key": "yolov11_detection",
                "training_recipe_version": 1,
                "runtime_profile": "yolov11_detection",
                "runtime_interface_version": 1,
                "sample_conditions": {},
                "model_weights": "yolo11n.pt",
                "weights_source": "preset",
                "epochs": 50,
                "imgsz": 640,
                "hyperparams": {},
            }
        ]
        browser.close()
