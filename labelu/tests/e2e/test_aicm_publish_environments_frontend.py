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
AICM_ENV_PAGE_FILE = (
    REPO_ROOT
    / "frontend"
    / "extensions"
    / "pages"
    / "aicm_publish_environments.html"
)
PORTAL_FILE = REPO_ROOT / "frontend" / "extensions" / "assets" / "labelu_portal.js"

AICM_ENV_PAGE_URL = f"{BASE_URL}/aicm-publish-environments"


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
    if path in ("/favicon.ico", "/favicon.svg"):
        route.fulfill(status=204, body="")
        return True
    return False


def _environment_bundle() -> dict:
    blocked_reason = "尚未保存 AICM 密码，请编辑环境后重新录入"
    return {
        "data": {
            "active_environment_id": "env_f48512efd595",
            "config_backend_label": "MySQL 治理表（system_config_*）",
            "environments": [
                {
                    "id": "env_f48512efd595",
                    "name": "生产 AICM",
                    "base_url": "http://192.168.54.169:8102",
                    "username": "admin",
                    "password_configured": False,
                    "password_runtime_available": False,
                    "password_masked_hint": "",
                    "chip_type": "SOPHGO,BM1688,NPU",
                    "algorithm_type": "Detection",
                    "verify_ssl": False,
                    "ready_to_publish": False,
                    "missing_fields": ["password"],
                    "readiness_reason": blocked_reason,
                    "is_active": True,
                    "created_at": "2026-07-01T00:00:00+00:00",
                    "updated_at": "2026-08-07T01:00:00+00:00",
                },
                {
                    "id": "env_ready001",
                    "name": "备用 AICM",
                    "base_url": "http://192.168.54.170:8102",
                    "username": "admin",
                    "password_configured": True,
                    "password_runtime_available": True,
                    "password_masked_hint": "********0001",
                    "chip_type": "SOPHGO,BM1688,NPU",
                    "algorithm_type": "Detection",
                    "verify_ssl": False,
                    "ready_to_publish": True,
                    "missing_fields": [],
                    "readiness_reason": "连接地址、账号和 MySQL 加密密码均已就绪",
                    "is_active": False,
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "updated_at": "2026-08-07T02:00:00+00:00",
                },
            ],
        }
    }


def _route_aicm_env_page(route: Route, calls: list[dict] | None = None) -> None:
    parsed = urlparse(route.request.url)
    path = parsed.path
    if path == "/aicm-publish-environments":
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=AICM_ENV_PAGE_FILE.read_text(encoding="utf-8"),
        )
        return
    if _fulfill_asset(route, path):
        return
    if not path.startswith(API_PREFIX):
        route.fulfill(status=404, body="")
        return

    api_path = path[len(API_PREFIX) :]
    method = route.request.method
    if calls is not None and method != "GET":
        calls.append(
            {
                "method": method,
                "path": api_path,
                "body": route.request.post_data_json
                if route.request.post_data
                else None,
            }
        )
    if api_path == "/users/me":
        _json_response(route, {"data": {"id": 1, "email": "admin@123.com"}})
        return
    if api_path == "/yolo-aicm-publish/environments" and method == "GET":
        _json_response(route, _environment_bundle())
        return
    if api_path == "/yolo-aicm-publish/environments" and method == "POST":
        _json_response(
            route,
            {"data": {**_environment_bundle()["data"]["environments"][1], "id": "env_new001"}},
        )
        return
    if api_path == "/yolo-aicm-publish/environments/active" and method == "PUT":
        _json_response(route, _environment_bundle())
        return
    if api_path.startswith("/yolo-aicm-publish/environments/") and method == "PATCH":
        environment_id = api_path.rsplit("/", 1)[-1]
        item = next(
            row
            for row in _environment_bundle()["data"]["environments"]
            if row["id"] == environment_id
        )
        _json_response(route, {"data": item})
        return
    if api_path.startswith("/yolo-aicm-publish/environments/") and method == "DELETE":
        _json_response(route, _environment_bundle())
        return
    route.fulfill(status=404, body="")


def _open_page(page, calls: list[dict] | None = None) -> None:
    page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
    page.route("**/*", lambda route: _route_aicm_env_page(route, calls))
    page.goto(AICM_ENV_PAGE_URL)
    expect(page.locator("#envTableBody tr")).to_have_count(2, timeout=15_000)


def test_portal_aux_menu_contains_aicm_publish_environments_entry():
    portal = PORTAL_FILE.read_text(encoding="utf-8")
    assert 'href: "/aicm-publish-environments"' in portal
    assert "AICM 发布环境" in portal


def test_page_explains_missing_mysql_password_and_opens_detail_modal():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        _open_page(page)

        expect(page.locator("h1")).to_have_text("AICM 发布环境")
        expect(page.locator("#summaryActiveName")).to_have_text("生产 AICM")
        expect(page.locator("#summaryCount")).to_have_text("2")
        expect(page.locator("#summaryReadyCount")).to_have_text("1")
        expect(page.locator("#activeStatusTitle")).to_have_text("当前发布环境未就绪")
        expect(page.locator("#activeStatusReason")).to_contain_text("尚未保存 AICM 密码")
        expect(page.locator("#envTableBody")).to_contain_text("尚未保存")
        expect(page.locator("#envTableBody")).to_contain_text("凭据不可用")
        expect(page.locator("#envTableBody")).to_contain_text("可发布")

        page.locator('[data-env-row="env_f48512efd595"] [data-action="view"]').click()
        expect(page.locator("#detailModalBackdrop")).to_have_class(
            "detail-modal-backdrop show"
        )
        expect(page.locator("#detailReadinessReason")).to_contain_text("尚未保存 AICM 密码")
        expect(page.locator("#detailRuntime")).to_contain_text("尚未保存")
        browser.close()


def test_page_sends_create_update_activate_and_delete_requests():
    calls: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        _open_page(page, calls)

        page.locator("#btnCreate").click()
        expect(page.locator("#editorTitle")).to_have_text("新增环境")
        page.locator("#fieldHost").fill("127.0.0.1")
        page.locator("#fieldPort").fill("8102")
        page.locator("#fieldUsername").fill("admin")
        page.locator("#fieldPassword").fill("test-password")
        page.locator("#btnSave").click()
        expect(page.locator("#toast")).to_have_text("环境已保存")
        assert len([c for c in calls if c["method"] == "POST"]) == 1

        page.locator('[data-env-row="env_ready001"] [data-action="edit"]').click()
        expect(page.locator("#editorTitle")).to_have_text("编辑环境")
        page.locator("#fieldHost").fill("192.168.54.171")
        page.locator("#btnSave").click()
        expect(page.locator("#toast")).to_have_text("环境已保存")
        assert len([c for c in calls if c["method"] == "PATCH"]) == 1
        expect(page.locator("#detailModalBackdrop")).to_have_class(
            "detail-modal-backdrop show"
        )
        page.locator("#btnCloseDetail").click()

        page.locator('[data-env-row="env_ready001"] [data-action="active"]').click()
        expect(page.locator("#toast")).to_have_text("已设为当前发布环境")
        assert len([c for c in calls if c["method"] == "PUT"]) == 1

        page.locator('[data-env-row="env_ready001"] [data-action="delete"]').click()
        expect(page.locator("#deleteModalBackdrop")).to_have_class("modal-backdrop show")
        expect(page.locator("#deleteMessage")).to_contain_text("备用 AICM")
        page.locator("#btnConfirmDelete").click()
        expect(page.locator("#toast")).to_have_text("环境已删除")
        assert len([c for c in calls if c["method"] == "DELETE"]) == 1

        create_body = next(c for c in calls if c["method"] == "POST")["body"]
        assert "name" not in create_body
        assert create_body["base_url"] == "http://127.0.0.1:8102"
        assert create_body["username"] == "admin"
        assert create_body["password"] == "test-password"
        assert "password_env_name" not in create_body
        update_body = next(c for c in calls if c["method"] == "PATCH")["body"]
        assert update_body["base_url"] == "http://192.168.54.171:8102"
        assert update_body["password_secret_action"] == "keep"
        assert "password" not in update_body
        assert next(c for c in calls if c["method"] == "PUT")["body"] == {
            "environment_id": "env_ready001"
        }
        assert next(c for c in calls if c["method"] == "DELETE")["path"].endswith(
            "/env_ready001"
        )
        browser.close()


def test_page_mobile_layout_has_no_horizontal_overflow():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _open_page(page)
        dimensions = page.evaluate(
            """() => ({
                viewport: document.documentElement.clientWidth,
                content: document.documentElement.scrollWidth
            })"""
        )
        assert dimensions["content"] <= dimensions["viewport"]
        expect(page.locator("#envTableBody tr")).to_have_count(2)
        browser.close()
