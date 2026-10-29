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


def test_new_training_modal_resets_iteration_prefill_after_close():
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
        if api_path in {
                "/annotation-taxonomy/labels",
                "/annotation-taxonomy/global-tag-dimensions",
                "/annotation-taxonomy/dataset-categories",
            "/annotation-taxonomy/training-recipes",
            "/annotation-taxonomy/runtime-templates",
        }:
            _json_response(route, {"data": []})
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
                                    "label_key": "垃圾堆",
                                    "label_value": "garbage_street_dump",
                                    "sort_order": 0,
                                }
                            ],
                        }
                    ]
                },
            )
            return
        if api_path == "/yolo-train/eligible-tasks":
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
        if api_path.startswith("/yolo-train-jobs/"):
            _json_response(
                route,
                {
                    "data": {
                        "id": 65,
                        "task_id": 18,
                        "task_name": "垃圾暴露训练任务",
                        "status": "COMPLETED",
                        "epochs": 123,
                        "imgsz": 960,
                        "model_weights": "yolo11n.pt",
                        "hyperparams": {"batch": 8},
                    }
                },
            )
            return
        if api_path.startswith("/yolo-train-jobs"):
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 65,
                            "task_id": 18,
                            "task_name": "垃圾暴露训练任务",
                            "status": "COMPLETED",
                            "runtime_status": "DONE",
                            "model_weights": "runs/train/exp65/weights/best.pt",
                            "epochs": 123,
                            "current_epoch": 123,
                            "imgsz": 960,
                            "category": "垃圾暴露",
                            "hyperparams": {"batch": 8},
                            "source_task_ids": [18],
                            "creator_email": "admin@123.com",
                            "created_at": "2026-07-06T10:00:00",
                            "updated_at": "2026-07-06T10:10:00",
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return
        if api_path.startswith("/yolo-trained-models"):
            _json_response(
                route,
                {
                    "data": [
                        {
                            "job_id": 65,
                            "task_id": 18,
                            "task_name": "垃圾暴露训练任务",
                            "task_rect_preset_slug": "garbageStreet",
                            "weights_path": "runs/train/exp65/weights/best.pt",
                            "status": "COMPLETED",
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
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
            _json_response(
                route,
                {
                    "data": [
                        {"key": "batch", "label": "batch", "type": "int", "default": 16},
                        {"key": "lr0", "label": "lr0", "type": "float", "default": 0.01},
                        {"key": "mosaic", "label": "mosaic", "type": "float", "default": 1.0},
                    ]
                },
            )
            return
        if api_path.startswith("/yolo-train/iteration-baseline"):
            _json_response(route, {"data": {"hyperparams": {}, "recommended_train_params": {}}})
            return
        if (
            api_path
            == "/tasks/18/yolo-train-jobs/65/evaluate-reports"
        ):
            report_kind = query.get("report_kind", [""])[0]
            if report_kind == "full_analysis":
                _json_response(
                    route,
                    {
                        "data": [
                            {
                                "id": 901,
                                "provider_key": "qwen",
                                "body": "full analysis",
                                "suggested_params": {
                                    "recommended_train_params": {
                                        "imgsz": 1280,
                                        "lr0": 0.005,
                                        "notes": "hard-negative focus",
                                    }
                                },
                            }
                        ],
                        "meta_data": {"total": 1},
                    },
                )
                return
            if report_kind == "next_iteration":
                _json_response(
                    route,
                    {
                        "data": [
                            {
                                "id": 902,
                                "provider_key": "qwen",
                                "body": "# optimize\nhard-negative oversample",
                                "suggested_params": {
                                    "recommended_train_params": {
                                        "epochs": 160,
                                        "batch": 2,
                                        "mosaic": 0.6,
                                    }
                                },
                            }
                        ],
                        "meta_data": {"total": 1},
                    },
                )
                return
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
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        page.locator(".btn-job-iterate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        expect(page.locator("#modalEpochs")).to_have_value("160", timeout=15_000)
        expect(page.locator("#modalImgsz")).to_have_value("1280", timeout=15_000)
        expect(page.locator("#hp-batch")).to_have_value("2", timeout=15_000)
        expect(page.locator("#hp-lr0")).to_have_value("0.005", timeout=15_000)
        expect(page.locator("#modalIterProvider")).to_have_value("qwen", timeout=15_000)
        expect(page.locator("#trainParamOptimizeNotes")).to_contain_text("hard-negative", timeout=15_000)
        expect(page.locator("#modalHyperSourceHint")).to_contain_text("65", timeout=15_000)
        expect(page.locator("#modalHyperSourceHint")).to_contain_text("902", timeout=15_000)

        page.locator("#btnModalCancel").click()
        expect(page.locator("#modalCreate")).not_to_be_visible(timeout=15_000)

        page.locator("#btnOpenCreate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        page.locator("#btnSwitchToPresetMode").click()
        expect(page.locator("#modalEpochs")).to_have_value("50", timeout=15_000)
        expect(page.locator("#modalImgsz")).to_have_value("640", timeout=15_000)
        expect(page.locator("#modalWeightsUnified")).to_have_value(
            "preset:yolo11n.pt", timeout=15_000
        )
        browser.close()
