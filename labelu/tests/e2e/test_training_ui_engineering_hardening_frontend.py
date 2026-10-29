from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:9888")
API_PREFIX = "/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = REPO_ROOT / "frontend" / "extensions" / "assets"
TRAINING_PAGE_FILE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"
BM1688_PAGE_FILE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_bm1688_exports.html"
PORTAL_JS_FILE = ASSETS_DIR / "labelu_portal.js"

TRAINING_PAGE_URL = f"{BASE_URL}/yolo-training?embedded=1&panel=list"
BM1688_PAGE_URL = f"{BASE_URL}/yolo-bm1688-exports"


def _json_response(route: Route, data: Any, status: int = 200) -> None:
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
            route.fulfill(status=200, content_type=content_type, body=asset.read_text(encoding="utf-8"))
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


def _train_job_row() -> dict[str, Any]:
    return {
        "id": 65,
        "task_id": 18,
        "task_name": "垃圾暴露训练任务",
        "status": "COMPLETED",
        "runtime_status": "DONE",
        "model_weights": "runs/train/exp65/weights/best.pt",
        "epochs": 100,
        "current_epoch": 100,
        "imgsz": 640,
        "total_sample_count": 320,
        "train_sample_count": 260,
        "val_sample_count": 40,
        "test_sample_count": 20,
        "creator_email": "admin@123.com",
        "created_at": "2026-07-05T10:00:00",
        "updated_at": "2026-07-05T10:10:00",
        "validation_pass": {"status": "达标", "detail": "mAP50-95=0.7333"},
        "quality_assessment": {
            "training_quality": {"status": "达标", "reasons": []},
            "generalization_quality": {"status": "稳定", "reasons": []},
            "edge_usability": {"status": "可用", "reasons": []},
            "release_readiness": {"status": "可发布", "reasons": []},
        },
    }


def _bm_export_row() -> dict[str, Any]:
    return {
        "id": 123,
        "task_id": 18,
        "task_name": "垃圾暴露训练任务",
        "yolo_train_job_id": 65,
        "status": "COMPLETED",
        "imgsz": 640,
        "onnx_filename": "model.onnx",
        "bmodel_filename": "model.bmodel",
        "bmodel_filenames": ["BM1688/model_int8_1b.bmodel"],
        "error_message": None,
        "quality_summary": "训练达标，边端结果已回传",
        "validation_gate_label": "达标",
        "edge_validation_label": "结果已回传",
        "edge_execution_label": "执行完成",
        "edge_int8_eval_label": "通过",
        "current_verdict_label": "可进入发布评估",
        "doc_hint": "对齐 SOP 3.2",
        "created_at": "2026-07-05T10:00:00",
        "updated_at": "2026-07-05T10:10:00",
        "board_validation": {
            "validation_id": 9,
            "export_id": 123,
            "status": "COMPLETED",
            "report_summary": "边端验证通过",
            "report": {"source": "manual_edge_validation"},
        },
    }


def _handle_training_success(route: Route) -> None:
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
    if api_path == "/users/me":
        _json_response(route, {"data": {"id": 1, "email": "admin@123.com"}})
        return
    if api_path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": [{"slug": "garbageStreet", "name": "垃圾暴露"}]})
        return
    if api_path in {
        "/annotation-taxonomy/labels",
        "/annotation-taxonomy/dataset-categories",
        "/annotation-taxonomy/training-recipes",
        "/annotation-taxonomy/runtime-templates",
    }:
        _json_response(route, {"data": []})
        return
    if api_path.startswith("/tasks"):
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
    if api_path.startswith("/yolo-train-jobs"):
        _json_response(route, {"data": [_train_job_row()], "meta_data": {"total": 1}})
        return
    if api_path.startswith("/yolo-train/"):
        _json_response(route, {"data": []})
        return

    raise AssertionError(f"Unexpected training API call: {request.method} {api_path}")


def _handle_training_failure(route: Route) -> None:
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
    if api_path == "/users/me":
        _json_response(route, {"data": {"id": 1, "email": "admin@123.com"}})
        return
    if api_path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": []})
        return
    if api_path in {
        "/annotation-taxonomy/labels",
        "/annotation-taxonomy/dataset-categories",
        "/annotation-taxonomy/training-recipes",
        "/annotation-taxonomy/runtime-templates",
    }:
        _json_response(route, {"data": []})
        return
    if api_path.startswith("/tasks"):
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
    if api_path.startswith("/yolo-train-jobs"):
        _json_response(route, {"msg": "训练列表接口异常"}, status=500)
        return
    if api_path.startswith("/yolo-train/"):
        _json_response(route, {"data": []})
        return

    raise AssertionError(f"Unexpected training API call: {request.method} {api_path}")


def _handle_bm_success(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = parsed.path
    query = parse_qs(parsed.query)

    if path == "/yolo-bm1688-exports":
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=BM1688_PAGE_FILE.read_text(encoding="utf-8"),
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
    if api_path == "/annotation-taxonomy/dataset-categories":
        _json_response(route, {"data": []})
        return
    if api_path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": [{"slug": "garbageStreet", "name": "垃圾暴露"}]})
        return
    if api_path == "/yolo-bm1688-exports/compiler-settings":
        _json_response(route, {"data": {"auto_compile": True, "compile_platforms": "bm1688"}})
        return
    if api_path == "/yolo-bm1688-exports/edge-ssh-settings":
        _json_response(
            route,
            {
                "data": {
                    "enabled": True,
                    "host": "10.10.11.26",
                    "port": 22,
                    "user": "linaro",
                    "password_configured": True,
                    "password_source_type": "env",
                    "password_env_name": "EDGE_BOX1_PASSWORD",
                    "password_masked_hint": "****1818",
                    "auto_after_export": True,
                    "probe_status": "ok",
                    "probe_checked_at": "2026-07-08T09:30:00+08:00",
                    "device_id": "edge_box_1",
                    "device_name": "产线边端 1 号机",
                    "last_validation_status": "ok",
                    "last_validation_at": "2026-07-08T09:35:00+08:00",
                    "last_used_at": "2026-07-08T09:35:00+08:00",
                    "remote_base": "/data/labelu_edge/jobs",
                    "docker_container": "bm1688",
                }
            },
        )
        return
    if api_path == "/yolo-bm1688-exports" and "page" in query:
        _json_response(route, {"data": [_bm_export_row()], "meta_data": {"total": 33}})
        return
    if api_path.startswith("/tasks/"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return
    if api_path.startswith("/yolo-edge-validation/jobs"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    raise AssertionError(f"Unexpected bm1688 API call: {request.method} {api_path}")


def _handle_bm_failure(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = parsed.path
    query = parse_qs(parsed.query)

    if path == "/yolo-bm1688-exports":
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=BM1688_PAGE_FILE.read_text(encoding="utf-8"),
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
    if api_path == "/annotation-taxonomy/dataset-categories":
        _json_response(route, {"data": []})
        return
    if api_path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": []})
        return
    if api_path == "/yolo-bm1688-exports/compiler-settings":
        _json_response(route, {"data": {"auto_compile": True}})
        return
    if api_path == "/yolo-bm1688-exports/edge-ssh-settings":
        _json_response(route, {"data": {"enabled": True, "password_configured": True}})
        return
    if api_path == "/yolo-bm1688-exports" and "page" in query:
        _json_response(route, {"msg": "BM1688 列表接口异常"}, status=500)
        return
    if api_path.startswith("/tasks/"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return
    if api_path.startswith("/yolo-edge-validation/jobs"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    raise AssertionError(f"Unexpected bm1688 API call: {request.method} {api_path}")


def test_training_ui_contracts_export_single_source_of_truth():
    script = f"""
const {{ trainingUi }} = require({json.dumps(PORTAL_JS_FILE.as_posix())});
const summary = trainingUi.summarizeBmExportRows([{{
  status: "COMPLETED",
  board_validation: {{ status: "COMPLETED" }},
  edge_int8_eval_label: "通过",
  current_verdict_label: "可进入发布评估"
}}], 33);
const failed = trainingUi.summarizeBmExportRows([], 0, "HTTP 500");
console.log(JSON.stringify({{
  auxEntryLabel: trainingUi.text.auxEntryLabel,
  searchFeedback: trainingUi.buildTrainListActionFeedback("search", {{
    total: 2,
    filtered: true,
    criteria: "任务名称“垃圾暴露”"
  }}),
  trainFailure: trainingUi.buildTrainListLoadFailureMessage("接口超时"),
  bmTotal: summary.cards.total.value,
  bmReady: summary.cards.publishReady.value,
  bmFailedValue: failed.cards.total.value,
  bmFailedHint: failed.cards.total.hint
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(result.stdout.strip())
    assert payload["auxEntryLabel"] == "辅助入口 / 配置"
    assert payload["searchFeedback"] == "已搜索，2 条；任务名称“垃圾暴露”"
    assert payload["trainFailure"] == "列表加载失败：接口超时，请重试或点「重置」。"
    assert payload["bmTotal"] == 33
    assert payload["bmReady"] == 1
    assert payload["bmFailedValue"] == "—"
    assert payload["bmFailedHint"] == "列表加载失败：HTTP 500，请点击「刷新」重试。"


def test_bm1688_page_kpi_uses_same_total_as_list_source():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_bm_success)
        page.goto(BM1688_PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        expect(page.locator("#statBmTotal")).to_have_text("33")
        expect(page.locator("#statBmPublishReady")).to_have_text("1")
        browser.close()


def test_bm1688_page_failure_clears_kpi_and_surfaces_failure_hint():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_bm_failure)
        page.goto(BM1688_PAGE_URL)

        expect(page.locator("#tbody")).to_contain_text("加载失败：BM1688 列表接口异常", timeout=15_000)
        expect(page.locator("#statBmTotal")).to_have_text("—")
        expect(page.locator("#bmStatsGrid .train-stat-hint").nth(1)).to_contain_text("列表加载失败：BM1688 列表接口异常")
        browser.close()


def _legacy_test_training_page_feedback_and_aux_label_are_consistent():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_training_success)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#globalJobTbody tr")).to_have_count(1, timeout=15_000)
        expect(page.locator("#luTrainingHubMoreBtn")).to_have_text("辅助入口 / 配置")
        expect(page.locator("body")).not_to_contain_text("训练作业控制台")
        expect(page.locator("body")).not_to_contain_text("作业列表")
        expect(page.locator("#trainFilterTitle")).to_have_text("当前筛选")
        page.locator("#luTrainingHubMoreBtn").click()
        expect(page.locator('[data-hub-more-tab="consistent"]')).to_have_text("筛选一致汇总")
        expect(page.locator('#luTrainingHubMoreMenu a[href="http://127.0.0.1:9888/yolo-quality-policies#trainingStandards"]')).to_have_text("训练质量标准")
        expect(page.locator('#luTrainingHubMoreMenu a[href="http://127.0.0.1:9888/yolo-quality-policies#edgeStandards"]')).to_have_text("边端验证标准")
        expect(page.locator('#luTrainingHubMoreMenu a[href="http://127.0.0.1:9888/yolo-quality-policies#releaseStandards"]')).to_have_text("发布门禁规则")
        page.locator('[data-hub-more-tab="consistent"]').click()
        expect(page.locator("#viewConsistentPool")).to_have_class("view-panel active", timeout=15_000)
        expect(page.locator("#viewConsistentPool")).to_contain_text("筛选一致汇总")
        page.locator("#navTrainList").click()
        expect(page.locator("#viewTrainList")).to_have_class("view-panel active", timeout=15_000)

        page.locator("#filterTaskName").fill("垃圾暴露")
        page.locator("#btnSearch").click()
        expect(page.locator("#toast")).to_contain_text("训练任务搜索已生效，共 1 条结果；任务名称“垃圾暴露”。")
        expect(page.locator("#trainListSectionHint")).to_contain_text("当前筛选：任务名称“垃圾暴露” · 共 1 条结果")

        page.locator("#btnRefreshJobList").click()
        expect(page.locator("#toast")).to_contain_text("训练任务列表已刷新，当前筛选结果共 1 条；任务名称“垃圾暴露”。")

        page.locator("#btnResetFilter").click()
        expect(page.locator("#toast")).to_contain_text("训练任务筛选已重置，已恢复默认列表（共 1 条）。")
        expect(page.locator("#trainListSectionHint")).to_have_text("点击行选中 · 详情查看进度 · 权重见「模型库」")
        browser.close()


def test_training_page_failure_does_not_masquerade_as_zero_results():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_training_failure)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#globalJobTbody")).to_contain_text("加载失败：训练列表接口异常", timeout=15_000)
        expect(page.locator("#statTotalJobs")).to_have_text("—")
        hint_text = page.locator("#trainListSectionHint").inner_text(timeout=15_000)
        assert "列表加载失败：训练列表接口异常" in hint_text
        assert "共 0 条结果" not in hint_text
        browser.close()


def test_training_page_feedback_and_aux_label_are_consistent():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _handle_training_success)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#globalJobTbody tr")).to_have_count(1, timeout=15_000)
        expect(page.locator("#luTrainingHubMoreBtn")).to_have_text("辅助入口 / 配置")
        expect(page.locator("body")).not_to_contain_text("训练作业控制台")
        expect(page.locator("body")).not_to_contain_text("作业列表")
        expect(page.locator("#trainFilterTitle")).to_have_text("当前筛选")

        page.locator("#luTrainingHubMoreBtn").click()
        expect(page.locator('[data-hub-more-tab="consistent"]')).to_have_text("筛选一致汇总")
        expect(
            page.locator('#luTrainingHubMoreMenu a[href="http://127.0.0.1:9888/yolo-quality-policies#trainingStandards"]')
        ).to_have_text("训练质量标准")

        page.locator("#filterTaskName").fill("垃圾暴露")
        page.locator("#btnSearch").click()
        expect(page.locator("#toast")).to_contain_text("已搜索，1 条；任务名称“垃圾暴露”")
        expect(page.locator("#trainListSectionHint")).to_contain_text("任务名称“垃圾暴露” · 1 条")

        page.locator("#btnRefreshJobList").click()
        expect(page.locator("#toast")).to_contain_text("已刷新，1 条；任务名称“垃圾暴露”")

        page.locator("#btnResetFilter").click()
        expect(page.locator("#toast")).to_contain_text("筛选已重置，1 条")
        expect(page.locator("#trainListSectionHint")).to_have_text("")

        page.locator("#luTrainingHubMoreBtn").click()
        page.locator('[data-hub-more-tab="consistent"]').click()
        expect(page.locator("#viewConsistentPool")).to_have_class("view-panel active", timeout=15_000)
        browser.close()


def test_plate_training_analysis_switches_between_all_component_models():
    plate_row = _train_job_row()
    plate_row.update(
        {
            "job_type": "plate_recognition",
            "component_metrics": {
                "pose": {
                    "label": "车牌定位与四角",
                    "status": "COMPLETED",
                    "train_sample_count": 80,
                    "metrics": {},
                },
                "ocr": {
                    "label": "车牌号码 OCR",
                    "status": "COMPLETED",
                    "train_sample_count": 80,
                    "metrics": {
                        "val": {"accuracy": 0.875, "correct": 7, "total": 8},
                        "test": {"accuracy": 0.9, "correct": 9, "total": 10},
                    },
                },
                "color": {
                    "label": "车牌颜色",
                    "status": "COMPLETED",
                    "train_sample_count": 80,
                    "metrics": {},
                },
                "plate_type": {
                    "label": "车牌类型",
                    "status": "COMPLETED",
                    "train_sample_count": 80,
                    "metrics": {},
                },
            },
        }
    )
    component_rows = []
    for key, label, filename in (
        ("pose", "车牌定位与四角", "plate_pose_best.pt"),
        ("ocr", "车牌号码 OCR", "plate_ocr_best.pt"),
        ("color", "车牌颜色", "plate_color_best.pt"),
        ("plate_type", "车牌类型", "plate_type_best.pt"),
    ):
        component_rows.append(
            {
                "key": key,
                "label": label,
                "status": "COMPLETED",
                "model_filename": filename,
                "model_available": True,
                "train_sample_count": 80,
                "final_loss": 0.123456,
                "current_epoch": 10,
                "target_epochs": 10,
                "metrics": (
                    {
                        "val": {"accuracy": 0.875, "correct": 7, "total": 8},
                        "test": {"accuracy": 0.9, "correct": 9, "total": 10},
                    }
                    if key != "pose"
                    else {}
                ),
                "series": {
                    "train_loss": [
                        {"epoch": 1, "value": 0.9},
                        {"epoch": 10, "value": 0.123456},
                    ],
                    "learning_rate": [{"epoch": 10, "value": 0.001}],
                },
                "preview_images": [
                    {
                        "asset_id": f"plate_{key}:confusion_matrix.png",
                        "filename": "confusion_matrix.png",
                        "kind": "confusion_matrix",
                        "url": f"/api/v1/yolo-train-jobs/65/analysis/assets/plate-{key}",
                    }
                ],
                "analysis_source": {
                    "key": f"plate_{key}",
                    "relative_path": f"plate_components/evaluation/{key}/analysis",
                },
            }
        )
    analysis_payload = {
        "job": plate_row,
        "progress": {"current_epoch": 10, "target_epochs": 10, "percent": 100},
        "series": {},
        "status_cards": {},
        "logs": [],
        "preview_images": [],
        "component_analyses": component_rows,
        "updated_at": "2026-07-31T10:00:00",
    }
    pixel_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
        b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def handler(route: Route) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path
        api_path = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else ""
        if api_path == "/yolo-train-jobs/65/analysis":
            _json_response(route, {"data": analysis_payload})
            return
        if api_path.startswith("/yolo-train-jobs/65/analysis/assets/"):
            route.fulfill(status=200, content_type="image/png", body=pixel_png)
            return
        if api_path == "/tasks/18/yolo-train-jobs/65":
            _json_response(route, {"data": plate_row})
            return
        if api_path.startswith("/yolo-train-jobs"):
            _json_response(
                route,
                {"data": [plate_row], "meta_data": {"total": 1}},
            )
            return
        if api_path.startswith("/yolo-bm1688-exports"):
            _json_response(
                route,
                {"data": [], "meta_data": {"total": 0}},
            )
            return
        _handle_training_success(route)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#globalJobTbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#globalJobTbody .btn-job-detail").click()
        expect(page.locator("#modalJobDetail")).to_be_visible(timeout=15_000)
        page.locator('[data-job-tab="analysis"]').click()
        tabs = page.locator("#jobTrainAnalysisComponentTabs [data-analysis-component]")
        expect(tabs).to_have_count(4, timeout=15_000)
        page.locator('[data-analysis-component="ocr"]').click()
        expect(page.locator("#jobTrainAnalysisMetricTitle")).to_have_text(
            "车牌号码 OCR · 训练指标"
        )
        expect(page.locator("#jobTrainAnalysisStats")).to_contain_text(
            "plate_ocr_best.pt"
        )
        expect(page.locator("#jobTrainAnalysisStats")).to_contain_text("0.8750")
        expect(page.locator("#jobTrainAnalysisCharts .job-analysis-chart")).to_have_count(2)
        expect(page.locator("#jobTrainAnalysisGallery img")).to_have_count(1)
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-analysis-component="plate_type"]').click()
        expect(page.locator("#jobTrainAnalysisMetricTitle")).to_have_text(
            "车牌类型 · 训练指标"
        )
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )
        assert page.locator('[data-job-panel="analysis"]').evaluate(
            "element => getComputedStyle(element).overflowY === 'auto'"
        )
        browser.close()


def test_training_create_modal_stays_local_and_does_not_request_ready_remote_nodes():
    seen_ready_only_calls: list[str] = []
    submitted_bodies: list[dict[str, Any]] = []

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        api_path = parsed.path[len(API_PREFIX) :] if parsed.path.startswith(API_PREFIX) else ""
        if (
            parsed.path == f"{API_PREFIX}/yolo-train/remote-nodes"
            and request.method == "GET"
            and parse_qs(parsed.query).get("env_ready_only") == ["true"]
        ):
            seen_ready_only_calls.append(request.url)
        if api_path.startswith("/annotation-taxonomy/label-presets") and request.method == "GET":
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
                                    "label_key": "garbage",
                                    "label_value": "垃圾暴露",
                                    "sort_order": 1,
                                }
                            ],
                        }
                    ]
                },
            )
            return
        if api_path == "/yolo-train/eligible-tasks" and request.method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        {
                            "task_id": 18,
                            "task_name": "垃圾暴露训练任务",
                            "training_ready": True,
                            "done_count": 12,
                            "created_at": "2026-07-06T10:00:00",
                            "resolved_rect_preset_slug": "garbageStreet",
                            "match_source": "explicit_preset",
                        }
                    ],
                    "meta_data": {"total": 1},
                },
            )
            return
        if api_path == "/tasks/18/yolo-train-jobs" and request.method == "POST":
            submitted_bodies.append(json.loads(request.post_data or "{}"))
            _json_response(route, {"data": {"id": 88, "merge_task_label": None}})
            return
        if api_path == "/tasks/18/yolo-train-jobs/88" and request.method == "GET":
            row = _train_job_row()
            row["id"] = 88
            _json_response(route, {"data": row})
            return
        _handle_training_success(route)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        page.locator("#btnOpenCreate").click()
        expect(page.locator("#modalCreate")).to_be_visible(timeout=15_000)
        page.locator("#btnSwitchToPresetMode").click()
        expect(page.locator('#modalRectPresetSelect option[value="garbageStreet"]')).to_be_attached(timeout=15_000)
        expect(page.locator("#modalCreateBody")).not_to_contain_text("训练位置")
        page.select_option("#modalRectPresetSelect", "garbageStreet")
        expect(page.locator("#modalTaskSelect")).to_have_value("18", timeout=15_000)
        page.locator("#btnModalSubmit").click()
        expect(page.locator("#toast")).to_contain_text("训练作业已创建", timeout=15_000)
        assert not seen_ready_only_calls
        assert submitted_bodies
        assert submitted_bodies[0]["execution_mode"] == "local"
        assert "train_node_id" not in submitted_bodies[0]
        browser.close()
