from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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
EDGE_DEVICE_REGISTRY_PAGE_FILE = REPO_ROOT / "frontend" / "extensions" / "pages" / "edge_device_registry.html"

TRAINING_PAGE_URL = f"{BASE_URL}/yolo-training?embedded=1&panel=list"
BM1688_PAGE_URL = f"{BASE_URL}/yolo-bm1688-exports"
EDGE_DEVICE_REGISTRY_PAGE_URL = f"{BASE_URL}/edge-device-registry"


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


def _route_training(route: Route) -> None:
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
    if api_path == "/yolo-bm1688-exports/edge-ssh-settings" and request.method == "GET":
        _json_response(
            route,
            {"data": {"edge_root": "/data/7G_AI/edge"}},
        )
        return
    if api_path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": [{"slug": "garbageStreet", "name": "垃圾暴露"}]})
        return
    if api_path.startswith("/annotation-taxonomy/"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
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


def _route_bm(route: Route) -> None:
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
    if api_path.startswith("/annotation-taxonomy/label-presets"):
        _json_response(route, {"data": [{"slug": "garbageStreet", "name": "垃圾暴露"}]})
        return
    if api_path.startswith("/annotation-taxonomy/"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return
    if api_path == "/yolo-bm1688-exports/compiler-settings":
        _json_response(route, {"data": {"auto_compile": True, "compile_platforms": "bm1688"}})
        return
    if api_path == "/yolo-bm1688-exports/catalog-summary":
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
                    "password_source_type": "mysql:encrypted",
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
        _json_response(route, {"data": [_bm_export_row()], "meta_data": {"total": 1}})
        return
    if api_path.startswith("/tasks/"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return
    if api_path.startswith("/yolo-edge-validation/jobs"):
        _json_response(route, {"data": [], "meta_data": {"total": 0}})
        return

    raise AssertionError(f"Unexpected bm1688 API call: {request.method} {api_path}")


def _route_edge_device_registry(route: Route) -> None:
    request = route.request
    parsed = urlparse(request.url)
    path = parsed.path

    if path == "/edge-device-registry":
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=EDGE_DEVICE_REGISTRY_PAGE_FILE.read_text(encoding="utf-8"),
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
    if api_path == "/yolo-bm1688-exports/edge-ssh-settings" and request.method == "GET":
        _json_response(
            route,
            {"data": {"edge_root": "/data/7G_AI/edge"}},
        )
        return
    if api_path == "/edge-devices/environment-check-all" and request.method == "POST":
        _json_response(
            route,
            {"data": {"active_device_id": "edge_box_1", "devices": []}},
            status=202,
        )
        return
    if api_path == "/edge-devices" and request.method == "POST":
        payload = json.loads(request.post_data or "{}")
        assert payload.get("edge_root") == "/data/7G_AI/edge"
        assert "/data/labelu_edge" not in json.dumps(payload, ensure_ascii=False)
        _json_response(
            route,
            {
                "data": {
                    "id": "edge_new",
                    "name": payload.get("name") or "新增设备",
                    "validation_channel": payload.get("validation_channel") or "ssh",
                    "agent_bootstrap_control_plane_enabled": False,
                    "agent_bootstrap_status": "NOT_STARTED",
                }
            },
            status=201,
        )
        return
    if api_path == "/edge-devices":
        _json_response(
            route,
            {
                "data": {
                    "active_device_id": "edge_box_1",
                    "devices": [
                        {
                            "id": "edge_box_1",
                            "name": "产线边端 1 号机",
                            "enabled": True,
                            "host": "10.10.11.26",
                            "port": 22,
                            "user": "linaro",
                            "password_configured": True,
                            "password_runtime_available": True,
                            "password_source_type": "mysql:encrypted",
                            "password_masked_hint": "****1818",
                            "remote_base": "/data/labelu_edge/jobs",
                            "sophon_demo_root": "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
                            "auto_after_export": True,
                            "run_coco_eval": False,
                            "connect_timeout_sec": 30,
                            "validation_timeout_sec": 7200,
                            "docker_container": "bm1688",
                            "docker_use_sudo": True,
                            "probe_status": "ok",
                            "last_probe_status": "ok",
                            "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
                            "last_validation_status": "ok",
                            "last_validation_at": "2026-07-08T09:35:00+08:00",
                            "environment_status": "ready",
                            "environment_message": "BM1688 边端验证环境已就绪",
                            "environment_checked_at": "2026-07-08T09:34:00+08:00",
                            "environment_report": {
                                "environment_ready": True,
                                "architecture": "aarch64",
                                "architecture_ok": True,
                                "bm1688_device_ok": True,
                                "libsophon_ok": True,
                                "docker_ok": True,
                                "docker_image_ok": True,
                                "container_ok": True,
                                "python_runtime_ok": True,
                                "sophon_demo_ok": True,
                                "validator_ok": True,
                                "free_mb": 8192,
                                "tmp_free_mb": 2048,
                                "docker_free_mb": 4096,
                                "minimum_free_mb": 2048,
                                "minimum_tmp_free_mb": 256,
                                "minimum_docker_free_mb": 512,
                            },
                            "scheduling_status": "idle",
                            "available_for_dispatch": True,
                            "last_used_at": "2026-07-08T09:35:00+08:00",
                            "created_at": "2026-07-07T10:00:00+08:00",
                            "updated_at": "2026-07-08T09:35:00+08:00",
                        },
                        {
                            "id": "edge_box_2",
                            "name": "产线边端 2 号机",
                            "enabled": True,
                            "host": "10.10.11.27",
                            "port": 22,
                            "user": "linaro",
                            "password_configured": True,
                            "password_runtime_available": True,
                            "password_source_type": "mysql:encrypted",
                            "password_masked_hint": "****2828",
                            "remote_base": "/data/labelu_edge/jobs",
                            "sophon_demo_root": "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
                            "auto_after_export": False,
                            "run_coco_eval": False,
                            "connect_timeout_sec": 30,
                            "validation_timeout_sec": 7200,
                            "docker_container": "bm1688",
                            "docker_use_sudo": True,
                            "probe_status": "unknown",
                            "last_probe_status": "unknown",
                            "last_probe_checked_at": None,
                            "last_validation_status": "",
                            "last_validation_at": None,
                            "environment_status": "blocked",
                            "environment_code": "manual_cleanup_required",
                            "environment_message": "自动日志清理后空间仍不足，需要人工检查",
                            "environment_checked_at": "2026-08-06T17:07:39+08:00",
                            "environment_report": {
                                "environment_ready": False,
                                "tmp_free_mb": 0,
                                "minimum_tmp_free_mb": 256,
                                "fatal_reasons": ["tmp_disk_space_insufficient"],
                                "manual_intervention_required": True,
                                "storage_cleanup": {
                                    "status": "completed",
                                    "freed_mb": 312,
                                    "actions": ["journal_vacuum_256mb"],
                                    "manual_intervention_required": True,
                                },
                            },
                            "scheduling_status": "environment_not_ready",
                            "available_for_dispatch": False,
                            "last_used_at": None,
                            "created_at": "2026-07-07T11:00:00+08:00",
                            "updated_at": "2026-07-07T11:00:00+08:00",
                        },
                    ],
                    "config_backend_label": "MySQL（system_config_value）",
                }
            },
        )
        return

    raise AssertionError(f"Unexpected edge device registry API call: {request.method} {api_path}")


def _route_training_and_edge_registry(route: Route) -> None:
    path = urlparse(route.request.url).path
    if (
        path == "/edge-device-registry"
        or path.startswith(f"{API_PREFIX}/edge-devices")
        or path == f"{API_PREFIX}/yolo-bm1688-exports/edge-ssh-settings"
    ):
        _route_edge_device_registry(route)
        return
    _route_training(route)


def test_training_aux_menu_contains_edge_device_registry_entry():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _route_training)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#globalJobTbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#luTrainingHubMoreBtn").click()
        expect(
            page.locator(
                '#luTrainingHubMoreMenu a[href="http://127.0.0.1:9888/edge-device-registry"]'
            )
        ).to_have_text("边端设备配置")

        browser.close()


def test_training_aux_menu_edge_registry_link_is_absolute_for_top_navigation():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _route_training_and_edge_registry)
        page.set_content(
            f'<html><body style="margin:0"><iframe src="{TRAINING_PAGE_URL}" '
            'style="width:1600px;height:1200px;border:0"></iframe></body></html>'
        )

        frame = page.frame_locator(f'iframe[src="{TRAINING_PAGE_URL}"]')
        expect(frame.locator("#globalJobTbody tr")).to_have_count(1, timeout=15_000)
        frame.locator("#luTrainingHubMoreBtn").click()

        edge_link = frame.locator('#luTrainingHubMoreMenu a[href="http://127.0.0.1:9888/edge-device-registry"]')
        expect(edge_link).to_have_text("边端设备配置")

        edge_link.click()
        expect(page).to_have_url(EDGE_DEVICE_REGISTRY_PAGE_URL, timeout=15_000)
        expect(page.locator("h1")).to_have_text("边端设备配置", timeout=15_000)

        browser.close()


def test_bm1688_config_edge_tab_is_summary_only_and_links_to_registry():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _route_bm)
        page.goto(BM1688_PAGE_URL)

        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.evaluate("document.getElementById('btnToggleConfig').click()")
        page.locator('.config-tabs .tab-btn[data-config-tab="edge"]').click()

        expect(page.locator("#edgeDeviceSummaryName")).to_have_text("产线边端 1 号机", timeout=15_000)
        expect(page.locator("#edgeDeviceSummaryHost")).to_contain_text("10.10.11.26")
        expect(page.locator("#edgeDeviceSummaryValidation")).to_contain_text("ok")
        expect(page.locator("#btnOpenEdgeDeviceRegistry")).to_have_attribute("href", "/edge-device-registry")
        expect(page.locator("#edgeSshHost")).to_have_count(0)
        expect(page.locator("#btnSaveEdgeSsh")).to_have_count(0)

        browser.close()


def test_edge_device_registry_page_renders_active_device_summary():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        requested_urls = []
        page.on("request", lambda request: requested_urls.append(request.url))
        page.route("**/*", _route_edge_device_registry)
        page.goto(EDGE_DEVICE_REGISTRY_PAGE_URL)

        expect(page.locator("h1")).to_have_text("边端设备配置", timeout=15_000)
        expect(page.locator("#summaryActiveName")).to_have_text("产线边端 1 号机")
        expect(page.locator("#summaryCount")).to_have_text("2")
        expect(page.locator("#summaryProbe")).to_have_text("1")
        expect(page.locator("#summaryEnvironmentReady")).to_have_text("1")
        expect(page.locator("#backendLabel")).to_contain_text("MySQL")
        expect(page.locator("#deviceTableBody tr")).to_have_count(2)
        expect(page.locator('a[href="/training?panel=compile"]')).to_have_text("返回 BM1688 编译")
        first = page.locator("#deviceTableBody tr").nth(0)
        expect(first).to_contain_text("当前")
        expect(first).to_contain_text("已加密保存")
        expect(first).to_contain_text("BM1688 就绪")

        first.locator('[data-action="view"]').click()
        expect(page.locator("#detailModalBackdrop")).to_have_class(
            "detail-modal-backdrop show"
        )
        expect(page.locator("#metaPassword")).to_contain_text("已加密保存")
        expect(page.locator("#environmentChecks")).to_contain_text("aarch64")
        expect(page.locator("#environmentChecks")).to_contain_text("8192 MB / 最低 2048 MB")
        assert not any("/environment-check-all" in url for url in requested_urls)

        page.locator("#btnCloseDetail").click()
        second = page.locator("#deviceTableBody tr").nth(1)
        expect(second).to_contain_text("环境未就绪")
        second.locator('[data-action="view"]').click()
        expect(page.locator("#metaEnvironment")).to_contain_text("需要人工检查")
        expect(page.locator("#environmentChecks")).to_contain_text("0 MB / 最低 256 MB")
        expect(page.locator("#environmentCleanupNote")).to_contain_text("释放约 312 MB")

        page.locator("#btnCloseDetail").click()
        page.locator("#btnCreate").click()
        expect(page.locator("#editorTitle")).to_have_text("新增设备")
        expect(page.locator("#editorModalBackdrop")).to_have_class("modal-backdrop show")
        expect(page.locator("#fieldPassword")).to_have_value("")
        expect(page.locator("#fieldEdgeRoot")).to_have_value("/data/7G_AI/edge")

        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        browser.close()


def test_edge_device_registry_page_stays_put_when_token_is_missing():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.route("**/*", _route_edge_device_registry)
        page.goto(EDGE_DEVICE_REGISTRY_PAGE_URL)

        expect(page).to_have_url(EDGE_DEVICE_REGISTRY_PAGE_URL, timeout=15_000)
        expect(page.locator("#authBox")).to_be_visible(timeout=15_000)
        expect(page.locator("#authBox")).to_contain_text("当前未登录")
        expect(page.locator("h1")).to_have_text("边端设备配置")

        browser.close()


def test_edge_device_registry_create_submits_server_canonical_root():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _route_edge_device_registry)
        page.goto(EDGE_DEVICE_REGISTRY_PAGE_URL)

        expect(page.locator("#fieldEdgeRoot")).to_have_value(
            "/data/7G_AI/edge", timeout=15_000
        )
        page.locator("#btnCreate").click()
        page.locator("#fieldName").fill("新增边端测试设备")
        page.locator("#fieldHost").fill("10.1.1.147")
        page.locator("#fieldPassword").fill("test-password")
        expect(page.locator("#fieldEdgeRoot")).to_have_value("/data/7G_AI/edge")
        page.locator("#btnSave").click()

        expect(page.locator("#editorModalBackdrop")).not_to_have_class("show", timeout=15_000)
        browser.close()


def test_edge_device_registry_mobile_layout_has_no_horizontal_overflow():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", _route_edge_device_registry)
        page.goto(EDGE_DEVICE_REGISTRY_PAGE_URL)

        expect(page.locator("#deviceTableBody tr")).to_have_count(2, timeout=15_000)
        assert page.locator("thead").evaluate("el => getComputedStyle(el).display") == "none"
        assert page.locator("#deviceTableBody tr").first.evaluate(
            "el => getComputedStyle(el).display"
        ) == "block"

        page.locator("#btnCreate").click()
        expect(page.locator("#editorModalBackdrop")).to_have_class(
            "modal-backdrop show"
        )
        assert page.locator("#editorModalBackdrop .modal").evaluate(
            "el => el.getBoundingClientRect().right <= window.innerWidth"
        )
        page.locator("#btnCloseEditor").click()

        page.locator('#deviceTableBody [data-action="view"]').first.click()
        expect(page.locator("#detailModalBackdrop")).to_have_class(
            "detail-modal-backdrop show"
        )
        assert page.locator("#detailModalBackdrop .detail-modal").evaluate(
            "el => el.getBoundingClientRect().right <= window.innerWidth"
        )
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        browser.close()
