"""Read-only browser acceptance for a locally running 7GAI backend and frontend.

The test is opt-in so the normal backend suite never depends on a developer
server or credentials. It only authenticates, navigates, and inspects pages;
it does not create or mutate platform resources.
"""

import json
import os

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright


LIVE_URL = os.getenv("LABELU_PLATFORM_LIVE_URL")
LIVE_USERNAME = os.getenv("LABELU_PLATFORM_LIVE_USERNAME")
LIVE_PASSWORD = os.getenv("LABELU_PLATFORM_LIVE_PASSWORD")

pytestmark = pytest.mark.skipif(
    not (LIVE_URL and LIVE_USERNAME and LIVE_PASSWORD),
    reason=(
        "set LABELU_PLATFORM_LIVE_URL, LABELU_PLATFORM_LIVE_USERNAME and "
        "LABELU_PLATFORM_LIVE_PASSWORD to run local live acceptance"
    ),
)


ROUTES = (
    "/workspace",
    "/datasets",
    "/ingestion",
    "/annotation-review",
    "/algorithms",
    "/training",
    "/models",
    "/deployments",
    "/evolution",
    "/workflows",
    "/admin",
    "/tasks",
)


def _login_token(request_context):
    response = request_context.post(
        "/api/v1/users/login",
        data={"username": LIVE_USERNAME, "password": LIVE_PASSWORD},
    )
    assert response.status == 200, response.text()
    payload = response.json()
    token = ((payload.get("data") or {}).get("token") or "").strip()
    assert token, "local login response did not contain a token"
    return token


def test_local_live_platform_routes_share_the_7gai_shell():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(base_url=LIVE_URL.rstrip("/"), viewport={"width": 1440, "height": 900})
        request_context = playwright.request.new_context(base_url=LIVE_URL.rstrip("/"))
        token = _login_token(request_context)
        request_context.dispose()
        context.add_init_script(f"localStorage.setItem('token', {json.dumps(token)});")
        page = context.new_page()

        page.goto("/", wait_until="networkidle")
        assert page.url.rstrip("/").endswith("/workspace")

        for route in ROUTES:
            page.goto(route, wait_until="networkidle")
            assert page.locator('nav[aria-label="主导航"]').is_visible(), route
            assert page.locator("body").inner_text().find("7GAI") >= 0, route
            assert "Label U" not in page.locator("body").inner_text(), route
            assert "LabelU" not in page.locator("body").inner_text(), route

        page.goto("/training?embedded=1", wait_until="networkidle")
        assert page.locator('nav[aria-label="主导航"]').is_visible()
        assert page.locator('iframe[title="模型训练"]').is_visible()
        browser.close()


def test_local_live_docs_and_openapi_are_backend_responses():
    with sync_playwright() as playwright:
        request_context = playwright.request.new_context(base_url=LIVE_URL.rstrip("/"))
        docs = request_context.get("/docs")
        openapi = request_context.get("/openapi.json")
        assert docs.status == 200
        assert openapi.status == 200
        payload = openapi.json()
        assert payload["info"]["title"] == "7GAI 中文 API 接口文档"
        paths_json = json.dumps(payload.get("paths", {}), ensure_ascii=False)
        # 项目/业务空间已从最终菜单与数据主链路中移除；验收应锁定
        # 任务、数据集、模型版本等仍然有效的主链路契约。
        assert "/api/v1/datasets" in paths_json
        assert "/api/v1/model-versions" in paths_json
        assert "/api/v1/projects" not in paths_json
        request_context.dispose()
