from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


def test_training_create_modal_uses_scoped_tasks_and_supports_bulk_selection():
    generic_task_calls: list[str] = []
    eligible_calls: list[str] = []
    trained_model_calls: list[str] = []
    preview_calls: list[list[int]] = []

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        query = parse_qs(parsed.query)

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
        if api_path == "/users/me":
            _json_response(route, {"data": {"id": 1, "email": "admin@123.com"}})
            return
        if api_path.startswith("/annotation-taxonomy/label-presets"):
            _json_response(
                route,
                {
                    "data": [
                        {
                            "slug": "garbageStreet",
                            "name": "垃圾暴露",
                            "items": [
                                {
                                    "id": 1,
                                    "label_key": "垃圾",
                                    "label_value": "garbage",
                                    "sort_order": 0,
                                }
                            ],
                        }
                    ]
                },
            )
            return
        if api_path == "/annotation-taxonomy/labels":
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/global-tag-dimensions":
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/dataset-categories":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 7,
                            "category_key": "garbage_exposure",
                            "name": "垃圾暴露",
                            "status": "active",
                        }
                    ]
                },
            )
            return
        if api_path == "/annotation-taxonomy/training-recipes":
            _json_response(route, {"data": []})
            return
        if api_path == "/annotation-taxonomy/runtime-templates":
            _json_response(route, {"data": []})
            return
        if api_path == "/yolo-train/eligible-tasks":
            eligible_calls.append(request.url)
            if query.get("task_dimension", [""])[0] == "true":
                rows = [
                    {
                        "task_id": 21,
                        "task_name": "分组任务一",
                        "training_ready": True,
                        "done_count": 8,
                        "label_set_signature": "signature-a",
                        "contract_labels": [
                            {"label_key": "垃圾", "label_value": "garbage"}
                        ],
                    },
                    {
                        "task_id": 22,
                        "task_name": "分组任务二",
                        "training_ready": True,
                        "done_count": 9,
                        "label_set_signature": "signature-a",
                        "contract_labels": [
                            {"label_key": "垃圾", "label_value": "garbage"}
                        ],
                    },
                    {
                        "task_id": 23,
                        "task_name": "契约不一致任务",
                        "training_ready": True,
                        "done_count": 5,
                        "label_set_signature": "signature-b",
                        "contract_labels": [
                            {"label_key": "车辆", "label_value": "vehicle"}
                        ],
                    },
                    {
                        "task_id": 24,
                        "task_name": "其他兼容任务",
                        "training_ready": True,
                        "done_count": 7,
                        "label_set_signature": "signature-a",
                        "contract_labels": [
                            {"label_key": "垃圾", "label_value": "garbage"}
                        ],
                    },
                ]
                keyword = query.get("q", [""])[0].strip().lower()
                if keyword:
                    rows = [
                        row
                        for row in rows
                        if keyword in str(row["task_id"]).lower()
                        or keyword in str(row["task_name"]).lower()
                    ]
                total = len(rows)
                page = int(query.get("page", ["0"])[0])
                size = int(query.get("size", ["20"])[0])
                rows = rows[page * size : page * size + size]
                _json_response(route, {"data": rows, "meta_data": {"total": total}})
                return
            slug = query.get("rect_preset_slug", [""])[0]
            rows = []
            if slug == "garbageStreet":
                rows = [
                    {
                        "task_id": 18,
                        "task_name": "垃圾暴露训练任务",
                        "training_ready": True,
                        "done_count": 12,
                        "created_at": "2026-07-06T10:00:00",
                        "resolved_rect_preset_slug": "garbageStreet",
                        "match_source": "explicit_preset",
                    }
                ]
            _json_response(route, {"data": rows, "meta_data": {"total": len(rows)}})
            return
        if api_path == "/yolo-training-tasks/preview":
            payload = request.post_data_json
            selected_task_ids = [int(value) for value in payload["selected_task_ids"]]
            preview_calls.append(selected_task_ids)
            _json_response(
                route,
                {
                    "data": {
                        "label_set_signature": "signature-a",
                        "class_order_signature": "class-order-a",
                        "included_task_count": len(selected_task_ids),
                        "included_image_count": len(selected_task_ids) * 8,
                        "ignored_task_count": 0,
                        "included_tasks": [],
                        "ignored_tasks": [],
                        "merged_classes": [],
                    }
                },
            )
            return
        if api_path.startswith("/tasks"):
            generic_task_calls.append(request.url)
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 18,
                            "name": "垃圾暴露训练任务",
                            "config": json.dumps({"defaultRectPresetId": "garbageStreet"}, ensure_ascii=False),
                            "stats": {"done": 12},
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return
        if api_path.startswith("/yolo-trained-models"):
            trained_model_calls.append(request.url)
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
        if api_path.startswith("/yolo-train-jobs"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-train/"):
            _json_response(route, {"data": []})
            return

        raise AssertionError(f"Unexpected API call: {request.method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        page.locator("#btnOpenCreate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        assert page.locator("#modalDatasetCategory").get_attribute("multiple") is not None
        page.locator("#modalDatasetCategoryTrigger").click()
        expect(page.locator("#modalDatasetCategoryMenu")).to_be_visible()
        page.locator("#btnModalCloseX").click()
        expect(page.locator("#modalCreate")).to_be_hidden()

        page.locator("#btnOpenCreate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        page.locator("#btnModalCancel").click()
        expect(page.locator("#modalCreate")).to_be_hidden()

        page.locator("#btnOpenCreate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        expect(page.locator("#taskModeTaskList")).to_contain_text("请先选择数据集分类", timeout=15_000)
        assert not eligible_calls

        page.select_option("#modalDatasetCategory", ["7"])
        expect(page.locator("#taskModeTaskList")).to_contain_text("分组任务一", timeout=15_000)
        bulk_toggle = page.locator("#btnTaskModeSelectAll")
        expect(bulk_toggle).to_be_enabled()
        expect(bulk_toggle).to_have_text("批量全选")

        page.locator("#taskModeTaskSearch").fill("分组")
        expect(page.locator(".task-mode-task-row")).to_have_count(2)
        task_dimension_queries = [
            parse_qs(urlparse(url).query)
            for url in eligible_calls
            if parse_qs(urlparse(url).query).get("task_dimension", [""])[0] == "true"
        ]
        assert any(item.get("q") == ["分组"] for item in task_dimension_queries)
        assert all(item.get("size") == ["20"] for item in task_dimension_queries)
        bulk_toggle.click()
        expect(page.locator("#taskModeSelectionCount")).to_contain_text("已选 2 个")
        expect(page.locator("#labelModePreview")).to_contain_text("纳入任务 2 个")
        expect(
            page.locator(".task-mode-task-row", has_text="分组任务一").locator("input")
        ).to_be_checked()
        expect(
            page.locator(".task-mode-task-row", has_text="分组任务二").locator("input")
        ).to_be_checked()
        assert preview_calls == [[21, 22]]

        page.locator("#taskModeTaskSearch").fill("")
        incompatible_checkbox = page.locator(
            ".task-mode-task-row", has_text="契约不一致任务"
        ).locator("input")
        expect(incompatible_checkbox).to_be_disabled()
        expect(incompatible_checkbox).not_to_be_checked()

        page.locator("#taskModeTaskSearch").fill("其他兼容")
        expect(bulk_toggle).to_have_text("批量全选")
        bulk_toggle.click()
        expect(page.locator("#taskModeSelectionCount")).to_contain_text("已选 3 个")
        expect(page.locator("#labelModePreview")).to_contain_text("纳入任务 3 个")
        assert preview_calls == [[21, 22], [21, 22, 24]]

        expect(bulk_toggle).to_have_text("取消当前选择")
        bulk_toggle.click()
        expect(page.locator("#taskModeSelectionCount")).to_contain_text("已选 2 个")
        expect(page.locator("#labelModePreview")).to_contain_text("纳入任务 2 个")
        assert preview_calls == [[21, 22], [21, 22, 24], [21, 22]]

        page.locator("#taskModeTaskSearch").fill("分组")
        expect(bulk_toggle).to_have_text("取消当前选择")
        bulk_toggle.click()
        expect(page.locator("#taskModeSelectionCount")).to_contain_text("已选 0 个")
        assert preview_calls == [[21, 22], [21, 22, 24], [21, 22]]

        # A page change must retain the task selected on the previous page.
        page.locator("#taskModeTaskSearch").fill("")
        page.select_option("#taskModeTaskPageSize", "2")
        expect(page.locator(".task-mode-task-row")).to_have_count(2)
        page.locator(".task-mode-task-row", has_text="分组任务一").locator("input").check()
        # Use the stable pager structure instead of localized accessible text.
        # The first button is the previous-page control and the second is next.
        page.locator("#taskModeTaskPager button").nth(1).click()
        expect(page.locator(".task-mode-task-row")).to_have_count(2)
        page.locator(".task-mode-task-row", has_text="其他兼容任务").locator("input").check()
        expect(page.locator("#taskModeSelectionCount")).to_contain_text("已选 2 个")
        assert preview_calls[-1] == [21, 24]

        page.locator("#btnSwitchToPresetMode").click()
        expect(page.locator("#modalCreateTitle")).to_have_text("新建训练作业", timeout=15_000)
        expect(page.locator("#pageLoadingOverlay")).to_be_hidden()
        assert page.locator("#modalCategory").is_enabled()
        assert all("task_dimension=true" in url for url in eligible_calls)
        assert not trained_model_calls

        page.select_option("#modalRectPresetSelect", "garbageStreet")
        expect(page.locator("#modalTaskSelect")).to_contain_text("垃圾暴露训练任务", timeout=15_000)
        assert not trained_model_calls

        page.locator("#btnCreateWizardNext").click()
        expect(page.locator('[data-create-wizard-panel="2"]')).to_be_visible()
        page.select_option("#modalWeightsUnified", "__load_trained_models__")
        expect(page.locator("#toast")).to_contain_text("该拉框预设下暂无已完成训练模型", timeout=15_000)

        assert not generic_task_calls
        assert eligible_calls
        assert trained_model_calls
        assert any("rect_preset_slug=garbageStreet" in url for url in eligible_calls)
        browser.close()
