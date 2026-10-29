"""Local keyboard and semantic accessibility checks for the 7GAI shell.

These tests intentionally complement, rather than replace, the release-time
axe run. They use mocked API responses so keyboard regressions can be caught
without staging credentials.
"""

import json
import os

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect, sync_playwright


PLATFORM_URL = os.getenv("LABELU_PLATFORM_URL")
pytestmark = pytest.mark.skipif(
    not PLATFORM_URL,
    reason="set LABELU_PLATFORM_URL to run platform accessibility tests",
)


def _mock_platform_api(page) -> None:
    def handle(route):
        path = route.request.url.split("/api/v1", 1)[-1].split("?", 1)[0]
        if path == "/projects":
            payload = {"data": [{"id": 1, "name": "城市治理项目"}]}
        elif path == "/models":
            payload = {
                "data": [
                    {
                        "id": 12,
                        "display_name": "垃圾检测",
                        "version_no": "v2",
                        "status": "PUBLISHED",
                    }
                ]
            }
        elif path == "/models/12":
            payload = {
                "data": {
                    "id": 12,
                    "display_name": "垃圾检测",
                    "version_no": "v2",
                    "status": "PUBLISHED",
                }
            }
        else:
            payload = {"data": []}
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        )

    page.route("**/api/v1/**", handle)


def _open_models_page(browser):
    page = browser.new_page(viewport={"width": 1024, "height": 768})
    page.add_init_script(
        "localStorage.setItem('token', 'Bearer platform-accessibility-token');"
    )
    _mock_platform_api(page)
    page.goto(
        f"{PLATFORM_URL.rstrip('/')}/models?project_id=1",
        wait_until="networkidle",
    )
    expect(page.get_by_role("heading", name="模型仓库", level=1)).to_be_visible()
    return page


def test_platform_landmarks_and_interactive_controls_have_accessible_names():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _open_models_page(browser)

        expect(page.get_by_role("navigation", name="主导航")).to_have_count(1)
        expect(page.get_by_role("heading", level=1)).to_have_count(1)
        assert page.locator("[id]").evaluate_all(
            "nodes => new Set(nodes.map(node => node.id)).size === nodes.length"
        )
        unnamed = page.locator(
            "button:not([aria-label]):not([title]), "
            "a:not([aria-label]):not([title])"
        ).evaluate_all(
            "nodes => nodes.filter(node => !(node.textContent || '').trim()).length"
        )
        assert unnamed == 0

        page.close()
        browser.close()


def test_model_detail_is_reachable_and_dismissible_with_keyboard_only():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = _open_models_page(browser)
        detail = page.get_by_role("button", name="查看详情")

        page.locator("body").focus()
        reached_detail = False
        for _ in range(80):
            page.keyboard.press("Tab")
            if detail.evaluate("node => node === document.activeElement"):
                reached_detail = True
                break
        assert reached_detail, "查看详情按钮无法通过 Tab 顺序到达"

        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("模型仓库详情")).to_be_visible()

        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()

        page.close()
        browser.close()
