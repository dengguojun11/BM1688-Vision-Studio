"""一期 7GAI 平台壳本地浏览器契约。

测试只有在显式设置 ``LABELU_PLATFORM_URL`` 时才会启动浏览器，并且所有
``/api/v1`` 请求都由 Playwright 路由在本地内存中响应，不连接生产环境。
"""

import json
import os

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect
from playwright.sync_api import sync_playwright


PLATFORM_URL = os.getenv("LABELU_PLATFORM_URL")
pytestmark = pytest.mark.skipif(
    not PLATFORM_URL,
    reason="set LABELU_PLATFORM_URL to run local platform-shell browser tests",
)


MODEL = {
    "id": 12,
    "display_name": "垃圾检测",
    "version_no": 2,
    "status": "VALIDATED",
    "quality_gate": {
        "training": "PASS",
        "generalization": "PASS",
        "conversion": "PASS",
        "edge": "PASS",
        "release": "PASS",
    },
    "artifacts": [
        {"id": 1, "artifact_type": "bmodel", "chip_type": "BM1688", "precision": "FP32", "status": "READY"},
        {"id": 2, "artifact_type": "bmodel", "chip_type": "BM1688", "precision": "FP16", "status": "READY"},
        {"id": 3, "artifact_type": "bmodel", "chip_type": "BM1688", "precision": "INT8", "status": "READY"},
    ],
}


def _json(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _mock_platform_api(page, *, manager=True):
    """只模拟当前一期页面所需接口，禁止测试隐式请求项目接口。"""

    def handle(route):
        path = route.request.url.split("/api/v1", 1)[-1].split("?", 1)[0]
        if path == "/users/me":
            _json(route, {"data": {"id": 1, "username": "local-user", "is_task_superuser": manager}})
        elif path == "/users":
            _json(route, {"data": [{"id": 1, "username": "local-user", "is_task_superuser": manager}]})
        elif path == "/models":
            _json(route, {"data": [MODEL], "meta_data": {"total": 1}})
        elif path == "/models/12":
            _json(route, {"data": MODEL})
        elif path == "/edge-feedback/hard-examples":
            _json(route, {"data": [{"id": 14, "device_id": "edge-01", "hard_example_type": "LOW_CONFIDENCE", "review_status": "PENDING"}]})
        elif path == "/edge-feedback/hard-examples/14":
            _json(route, {"data": {"id": 14, "device_id": "edge-01", "hard_example_type": "LOW_CONFIDENCE", "review_status": "PENDING"}})
        else:
            _json(route, {"data": []})

    page.route("**/api/v1/**", handle)


def _new_page(browser, *, manager=True, viewport=(1024, 768)):
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.add_init_script("localStorage.setItem('token', 'Bearer local-platform-token');")
    _mock_platform_api(page, manager=manager)
    return page


def test_manager_sees_the_six_primary_modules_without_project_scope():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _new_page(browser, manager=True)
        requests = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(f"{PLATFORM_URL.rstrip('/')}/workspace", wait_until="networkidle")
        links = page.locator('nav[aria-label="主导航"] a').evaluate_all(
            "links => links.map(link => link.getAttribute('href'))"
        )
        assert links == ["/workspace", "/tasks", "/training", "/models", "/evolution", "/admin"]
        assert not any("project_id" in url for url in requests)
        assert page.locator("body").evaluate("node => node.scrollWidth <= window.innerWidth")
        browser.close()


def test_labeling_user_sees_read_only_evolution_but_not_system_management():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _new_page(browser, manager=False)
        page.goto(f"{PLATFORM_URL.rstrip('/')}/workspace", wait_until="networkidle")
        links = page.locator('nav[aria-label="主导航"] a').evaluate_all(
            "links => links.map(link => link.getAttribute('href'))"
        )
        assert links == ["/workspace", "/tasks", "/training", "/models", "/evolution"]
        assert page.get_by_role("link", name="模型中心").is_visible()
        assert page.get_by_role("link", name="进化中心").is_visible()
        assert page.get_by_role("link", name="系统管理").count() == 0
        browser.close()


@pytest.mark.parametrize("viewport", [(1440, 900), (1024, 768), (375, 812)])
def test_models_detail_is_a_centered_modal_on_desktop_and_narrow_viewports(viewport):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _new_page(browser, viewport=viewport)
        page.goto(f"{PLATFORM_URL.rstrip('/')}/models", wait_until="networkidle")
        page.get_by_role("button", name="查看详情").click()
        dialog = page.locator(".ant-modal-wrap:visible").last.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("垃圾检测", exact=True)).to_be_visible()
        expect(page.locator(".ant-modal-wrap.ant-modal-centered")).to_be_visible()
        assert page.locator(".ant-drawer").count() == 0
        page.keyboard.press("Escape")
        expect(page.locator(".ant-modal-wrap:visible")).to_have_count(0)
        assert page.locator("body").evaluate("node => node.scrollWidth <= window.innerWidth")
        browser.close()


def test_model_publish_button_is_available_to_labeling_user_after_quality_gate():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _new_page(browser, manager=False)
        page.goto(f"{PLATFORM_URL.rstrip('/')}/models", wait_until="networkidle")
        page.get_by_role("button", name="查看详情").click()
        publish = page.get_by_role("button", name="发布模型", exact=True)
        expect(publish).to_be_visible()
        expect(publish).to_be_enabled()
        browser.close()


def test_evolution_detail_is_a_centered_modal_without_project_query():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _new_page(browser, viewport=(375, 812))
        page.goto(f"{PLATFORM_URL.rstrip('/')}/evolution", wait_until="networkidle")
        page.get_by_role("button", name="查看").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("审核难例", exact=True)).to_be_visible()
        expect(page.locator(".ant-modal-wrap.ant-modal-centered")).to_be_visible()
        assert page.locator(".ant-drawer").count() == 0
        assert "project_id" not in page.url
        page.keyboard.press("Escape")
        expect(page.locator(".ant-modal-wrap:visible")).to_have_count(0)
        browser.close()


@pytest.mark.parametrize(
    ("legacy_path", "expected_path"),
    [("deployments", "/models"), ("workflows", "/admin")],
)
def test_retired_routes_redirect_without_project_scope(legacy_path, expected_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _new_page(browser)
        page.goto(f"{PLATFORM_URL.rstrip('/')}/{legacy_path}?project_id=1", wait_until="networkidle")
        expect(page).to_have_url(f"{PLATFORM_URL.rstrip('/')}{expected_path}")
        assert "project_id" not in page.url
        browser.close()
