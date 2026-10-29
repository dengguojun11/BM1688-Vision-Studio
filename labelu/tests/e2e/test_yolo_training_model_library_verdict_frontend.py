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
TRAINING_PAGE_URL = f"{BASE_URL}/yolo-training?embedded=1&panel=models"


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


def _trained_rows():
    return [
        {
            "job_id": 301,
            "task_id": 23,
            "task_name": "非机动车违停训练任务",
            "category": "非机动车违停",
            "model_weights": "yolo11n.pt",
            "epochs": 120,
            "imgsz": 640,
            "total_sample_count": 490,
            "train_sample_count": 343,
            "val_sample_count": 98,
            "test_sample_count": 49,
            "training_val_metrics": {
                "map50": 0.9554,
                "map50_95": 0.7566,
                "precision": 0.9365,
                "recall": 0.9778,
                "summary_line": "mAP50-95=0.7566 mAP50=0.9554 P=0.9365 R=0.9778",
            },
            "test_val_metrics": {
                "status": "completed",
                "map50": 0.9726,
                "map50_95": 0.7658,
                "precision": 0.9520,
                "recall": 0.9604,
                "summary_line": "mAP50-95=0.7658 mAP50=0.9726 P=0.9520 R=0.9604",
            },
            "val_test_comparison": {
                "delta_map50": 0.0172,
                "delta_map50_95": 0.0092,
            },
            "validation_pass": {
                "status": "达标",
                "detail": "mAP50-95=0.7566，达标线≥0.5000",
            },
            "bm1688_export_id": 88,
            "bm1688_compile_status": "completed",
            "bm1688_compile_status_label": "已编译",
            "quality_assessment": {
                "training_quality": {
                    "status": "达标",
                    "summary": "mAP50-95=0.7566 mAP50=0.9554 P=0.9365 R=0.9778",
                    "reasons": [],
                },
                "generalization_quality": {
                    "status": "未验证",
                    "summary": "缺少 test 集验证结果",
                    "reasons": ["未执行 test 集验证"],
                },
                "edge_usability": {
                    "status": "未要求",
                    "summary": "当前场景标准未要求边端验证",
                    "reasons": ["当前启用标准允许先完成训练判定"],
                },
                "release_readiness": {
                    "status": "需复核",
                    "summary": "泛化质量仍需人工复核",
                    "reasons": ["未执行 test 集验证"],
                },
            },
            "creator_email": "admin@123.com",
            "updated_at": "2026-07-06T11:57:33",
        },
        {
            "job_id": 302,
            "task_id": 77,
            "task_name": "华润占道经营",
            "category": "占道经营",
            "model_weights": "yolo11n.pt",
            "epochs": 1,
            "imgsz": 640,
            "total_sample_count": 13,
            "train_sample_count": 10,
            "val_sample_count": 2,
            "test_sample_count": 1,
            "training_val_metrics": {
                "map50": 0.0,
                "map50_95": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "summary_line": "mAP50-95=0.0000 mAP50=0.0000 P=0.0000 R=0.0000",
            },
            "test_val_metrics": {
                "status": "completed",
                "map50": 0.0553,
                "map50_95": 0.0276,
                "precision": 0.0056,
                "recall": 0.3333,
                "summary_line": "mAP50-95=0.0276 mAP50=0.0553 P=0.0056 R=0.3333",
            },
            "val_test_comparison": {
                "delta_map50": 0.0553,
                "delta_map50_95": 0.0276,
            },
            "validation_pass": {
                "status": "不达标",
                "detail": "mAP50-95=0.0000，达标线≥0.5000",
            },
            "bm1688_export_id": 89,
            "bm1688_compile_status": "not_started",
            "bm1688_compile_status_label": "待继续",
            "quality_assessment": {
                "training_quality": {
                    "status": "不达标",
                    "summary": "mAP50-95=0.0000 mAP50=0.0000 P=0.0000 R=0.0000",
                    "reasons": ["训练样本总数较少(13)", "mAP50-95=0.0000 低于预期"],
                },
                "generalization_quality": {
                    "status": "未验证",
                    "summary": "缺少 test 集验证结果",
                    "reasons": ["未执行 test 集验证"],
                },
                "edge_usability": {
                    "status": "未要求",
                    "summary": "当前场景标准未要求边端验证",
                    "reasons": ["当前启用标准允许先完成训练判定"],
                },
                "release_readiness": {
                    "status": "不可发布",
                    "summary": "训练质量未达标",
                    "reasons": ["训练样本总数较少(13)", "mAP50-95=0.0000 低于预期"],
                },
            },
            "creator_email": "admin@123.com",
            "updated_at": "2026-07-06T11:48:55",
        },
    ]


def test_model_library_verdict_cell_uses_real_summary_instead_of_empty_fallback():
    requested_job_detail_paths: list[str] = []

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
            _json_response(
                route,
                {"data": {"id": 1, "email": "admin@123.com", "username": "admin"}},
            )
            return
        if api_path.startswith("/annotation-taxonomy/label-presets"):
            _json_response(route, {"data": []})
            return
        if api_path.startswith("/annotation-taxonomy/"):
            _json_response(route, {"data": []})
            return
        if api_path.startswith("/yolo-trained-models"):
            _json_response(route, {"data": _trained_rows(), "meta_data": {"total": 2}})
            return
        if api_path.startswith("/yolo-imported-models"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-train-jobs"):
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
        if api_path.startswith("/yolo-train/"):
            _json_response(route, {"data": []})
            return
        if api_path == "/tasks/77/yolo-train-jobs/302":
            requested_job_detail_paths.append(api_path)
            _json_response(
                route,
                {
                    "data": {
                        "id": 302,
                        "task_id": 77,
                        "task_name": "华润占道经营",
                        "status": "COMPLETED",
                        "category": "占道经营",
                        "model_weights": "yolo11n.pt",
                        "epochs": 1,
                        "imgsz": 640,
                        "hyperparams": {},
                        "task_rect_preset_slug": "streetVendorOccupation",
                    }
                },
            )
            return
        if api_path.startswith("/tasks"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return

        raise AssertionError(f"Unexpected API call: {request.method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1180, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#modelsTbody tr")).to_have_count(2, timeout=15_000)

        desktop_filter_geometry = page.evaluate(
            """
            () => {
              const ids = [
                "filterModelTaskName",
                "filterModelDatasetCategory",
                "filterModelQualityStatus",
                "sortModelsBy",
              ];
              const boxes = ids.map((id) => document.getElementById(id).getBoundingClientRect());
              const actions = document.querySelector("#modelsFiltersSystem .models-filter-actions").getBoundingClientRect();
              const bar = document.getElementById("modelsFiltersSystem").getBoundingClientRect();
              return {
                tops: boxes.map((box) => Math.round(box.top)).concat(Math.round(actions.top)),
                widths: boxes.map((box) => Math.round(box.width)),
                barHeight: Math.round(bar.height),
              };
            }
            """
        )
        assert max(desktop_filter_geometry["tops"]) - min(desktop_filter_geometry["tops"]) <= 2
        assert desktop_filter_geometry["barHeight"] <= 40
        assert max(desktop_filter_geometry["widths"]) < 360

        pass_cell = page.locator("#modelsTbody tr").first.locator("td").nth(3)
        expect(pass_cell).to_contain_text("达标")
        expect(pass_cell).to_contain_text("0.7566")
        expect(pass_cell).to_contain_text("0.5000")
        expect(pass_cell).not_to_contain_text("未要求")
        expect(pass_cell).not_to_contain_text("可发布")

        compile_cell = page.locator("#modelsTbody tr").first.locator("td").nth(4)
        expect(compile_cell).to_contain_text("已编译")

        first_row_more = page.locator("#modelsTbody tr").first.get_by_role(
            "button", name="更多", exact=True
        )
        first_row_more.click()
        first_row = page.locator("#modelsTbody tr").first
        expect(first_row.get_by_role("button", name="查看最近一次 BM1688 编译")).to_be_visible()
        expect(first_row.get_by_role("button", name="再次 BM1688 编译")).to_be_visible()
        expect(first_row.get_by_role("button", name="删除模型")).to_be_visible()
        expect(first_row).not_to_contain_text("删除作业")

        second_row = page.locator("#modelsTbody tr").nth(1)
        second_row.get_by_role("button", name="更多", exact=True).click()
        expect(
            second_row.get_by_role("button", name="继续 BM1688 编译")
        ).to_be_visible()
        expect(second_row.get_by_role("button", name="删除模型")).to_be_visible()

        page_source = TRAINING_PAGE_FILE.read_text(encoding="utf-8")
        assert '"未执行"' in page_source
        assert "尚未执行；当前先展示训练过程最佳轮次" in page_source
        assert "历史作业尚未生成" not in page_source
        assert (
            '<button type="button" class="btn primary" id="btnTestVal">'
            "执行 best.pt 独立复验</button>"
        ) in page_source

        performance_cell = page.locator("#modelsTbody tr").first.locator("td").nth(2)
        expect(performance_cell).to_contain_text("VAL · 门禁口径")
        expect(performance_cell).to_contain_text("TEST · 泛化观察")
        expect(performance_cell).to_contain_text("Test 49 张")
        expect(performance_cell).to_contain_text("波动正常")
        expect(performance_cell).not_to_contain_text("Test 待补齐")

        fail_cell = page.locator("#modelsTbody tr").nth(1).locator("td").nth(3)
        expect(fail_cell).to_contain_text("不达标")
        expect(fail_cell).not_to_contain_text("不可发布")

        expect(page.locator("#modelsTable")).not_to_contain_text("边端")
        expect(page.locator("#modelsTable")).not_to_contain_text("发布资格")

        expect(page.locator("#modelsTbody tr").first.locator("td").nth(1)).to_contain_text("类别未冻结")
        assert page.locator("#modelsTbody tr").first.evaluate(
            "element => element.getBoundingClientRect().height"
        ) <= 102

        page.set_viewport_size({"width": 760, "height": 1200})
        mobile_filter_geometry = page.evaluate(
            """
            () => {
              const bar = document.getElementById("modelsFiltersSystem").getBoundingClientRect();
              const search = document.getElementById("filterModelTaskName").getBoundingClientRect();
              const status = document.getElementById("filterModelQualityStatus").getBoundingClientRect();
              const actions = document.querySelector("#modelsFiltersSystem .models-filter-actions").getBoundingClientRect();
              return {
                barWidth: Math.round(bar.width),
                searchWidth: Math.round(search.width),
                statusTop: Math.round(status.top),
                actionsTop: Math.round(actions.top),
                pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
              };
            }
            """
        )
        assert mobile_filter_geometry["searchWidth"] >= mobile_filter_geometry["barWidth"] - 2
        assert mobile_filter_geometry["actionsTop"] > mobile_filter_geometry["statusTop"]
        assert mobile_filter_geometry["pageOverflow"] <= 0

        second_row.get_by_role("button", name="迭代优化", exact=True).click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        expect(page.locator("#modalWeightsUnified")).to_have_value(
            "trained:302", timeout=15_000
        )
        assert requested_job_detail_paths
        assert set(requested_job_detail_paths) == {"/tasks/77/yolo-train-jobs/302"}
        expect(page.locator("#toast")).not_to_contain_text(
            "仅有训练完成的作业可发起迭代训练"
        )

        browser.close()
