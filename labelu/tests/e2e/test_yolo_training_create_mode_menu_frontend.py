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


def test_create_modal_keeps_runtime_template_as_hidden_compatibility_field():
    source = TRAINING_PAGE_FILE.read_text(encoding="utf-8")

    assert (
        '<select id="modalRuntimeTemplate" hidden aria-hidden="true" tabindex="-1"></select>'
        in source
    )
    assert '<label for="modalRuntimeTemplate">' not in source
    assert 'id="modalRuntimeTemplateHint"' not in source
    assert "if (!selectedTrainingRecipe() || !selectedRuntimeTemplate())" not in source
    assert "if (!selectedTrainingRecipe()) {" in source
    assert 'footerHint.textContent = "请选择有效的训练配方";' in source
    assert 'runtime_profile: runtime ? runtime.runtime_profile : "yolov11_detection"' in source


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


def test_create_button_opens_task_mode_modal_without_runtime_template_field():
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
                        }
                    ]
                },
            )
            return
        if api_path.startswith(
            (
                    "/annotation-taxonomy/labels",
                    "/annotation-taxonomy/global-tag-dimensions",
                    "/annotation-taxonomy/dataset-categories",
                "/annotation-taxonomy/training-recipes",
                "/annotation-taxonomy/runtime-templates",
            )
        ):
            _json_response(route, {"data": []})
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
        expect(page.locator("#pageLoadingOverlay")).to_be_hidden()
        expect(page.locator("#modalTrainingTaskName")).to_be_visible(timeout=15_000)
        expect(page.locator("#modalRuntimeTemplate")).to_be_hidden()
        expect(page.locator('label[for="modalRuntimeTemplate"]')).to_have_count(0)
        assert page.locator("#modalTrainingTaskName").is_enabled()

        browser.close()
