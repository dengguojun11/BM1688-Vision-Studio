"""
Playwright regression coverage for the YOLO quality policies page.
"""
from __future__ import annotations

import json
import os
from typing import Any

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:9888")
pytestmark = pytest.mark.skipif(
    not os.environ.get("LABELU_E2E_BASE"),
    reason="set LABELU_E2E_BASE to run legacy extension E2E tests",
)
API_PREFIX = "/api/v1"
PAGE_URL = os.environ.get(
    "LABELU_E2E_QUALITY_POLICIES_PAGE",
    f"{BASE_URL}/yolo-quality-policies",
)


def _json_response(route: Route, data: Any, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(data, ensure_ascii=False),
    )


def _build_quality_policy_handler(
    activate_payloads: list[dict[str, Any]],
    save_payloads: list[dict[str, Any]],
):
    def handle_api(route: Route) -> None:
        request = route.request
        url = request.url
        if API_PREFIX not in url:
            route.continue_()
            return
        path = url.split(API_PREFIX, 1)[1]
        method = request.method

        if path.startswith("/local-dev/session") and method == "GET":
            _json_response(route, {"data": {"session_id": "playwright"}})
            return

        if path == "/users/me" and method == "GET":
            _json_response(
                route,
                {"data": {"id": 1, "email": "mock@example.com", "username": "mock-user"}},
            )
            return

        if path.startswith("/annotation-taxonomy/labels") and method == "GET":
            _json_response(
                route,
                {
                    "data": [
                        {"id": 101, "label_key": "Garbage", "label_value": "garbage", "status": "active"},
                        {"id": 102, "label_key": "Bag", "label_value": "bag", "status": "active"},
                        {"id": 103, "label_key": "Vendor", "label_value": "vendor", "status": "active"},
                    ]
                },
            )
            return

        if path == "/yolo-quality-policies" and method == "GET":
            _json_response(
                route,
                {
                    "data": {
                        "active_policy_name": "street-vendor-v1",
                        "active_policy_names": {
                            "semantic:yolov11:bbox:default-signature": "default-policy-v1",
                            "semantic:yolov11:bbox:garbage-signature": "garbage-v2",
                            "semantic:yolov11:bbox:vendor-signature": "street-vendor-v1",
                        },
                        "runtime_file": "D:/labelu_data/yolo_quality_policy.json",
                        "policies": [
                            {
                                "policy_name": "default-policy-v1",
                                "scene_name": "Default",
                                "rect_preset_slug": "__default__",
                                "label_ids": [101],
                                "label_set_signature": "default-signature",
                                "model_family": "yolov11",
                                "annotation_kind": "bbox",
                                "status": "active",
                                "source": "system",
                                "version": 1,
                                "is_active": True,
                                "training_quality": {"map50_95_pass": 0.5},
                                "generalization_quality": {"max_gap_map50_95": 0.12},
                                "edge_usability": {},
                                "release_readiness": {},
                                "key_classes": [],
                                "reasons": [],
                                "risks": [],
                            },
                            {
                                "policy_name": "garbage-v2",
                                "scene_name": "Garbage Street",
                                "rect_preset_slug": "garbageStreet",
                                "label_ids": [101, 102],
                                "label_set_signature": "garbage-signature",
                                "model_family": "yolov11",
                                "annotation_kind": "bbox",
                                "status": "active",
                                "source": "ai_recommended",
                                "version": 2,
                                "is_active": True,
                                "training_quality": {"map50_95_pass": 0.7},
                                "generalization_quality": {"max_gap_map50_95": 0.08},
                                "edge_usability": {},
                                "release_readiness": {},
                                "key_classes": [],
                                "reasons": ["higher bar for training metrics"],
                                "risks": [],
                            },
                            {
                                "policy_name": "garbage-v1",
                                "scene_name": "Garbage Street",
                                "rect_preset_slug": "garbageStreet",
                                "label_ids": [102, 101],
                                "label_set_signature": "garbage-signature",
                                "model_family": "yolov11",
                                "annotation_kind": "bbox",
                                "status": "draft",
                                "source": "manual",
                                "version": 1,
                                "is_active": False,
                                "training_quality": {"map50_95_pass": 0.6},
                                "generalization_quality": {"max_gap_map50_95": 0.1},
                                "edge_usability": {},
                                "release_readiness": {},
                                "key_classes": [],
                                "reasons": [],
                                "risks": [],
                            },
                            {
                                "policy_name": "street-vendor-v1",
                                "scene_name": "Street Vendor",
                                "rect_preset_slug": "streetVendorOccupation",
                                "label_ids": [103],
                                "label_set_signature": "vendor-signature",
                                "model_family": "yolov11",
                                "annotation_kind": "bbox",
                                "status": "active",
                                "source": "manual",
                                "version": 1,
                                "is_active": True,
                                "training_quality": {"map50_95_pass": 0.65},
                                "generalization_quality": {"max_gap_map50_95": 0.09},
                                "edge_usability": {},
                                "release_readiness": {},
                                "key_classes": [],
                                "reasons": [],
                                "risks": [],
                            },
                        ],
                    }
                },
            )
            return

        if path == "/yolo-quality-policies" and method == "POST":
            save_payloads.append(request.post_data_json or {})
            _json_response(route, {"data": {"policy_name": save_payloads[-1].get("policy_name")}})
            return

        if path == "/yolo-quality-policies/activate" and method == "POST":
            activate_payloads.append(request.post_data_json or {})
            _json_response(
                route,
                {
                    "data": {
                        "active_policy_name": "garbage-v1",
                        "active_policy_names": {
                            "semantic:yolov11:bbox:default-signature": "default-policy-v1",
                            "semantic:yolov11:bbox:garbage-signature": "garbage-v1",
                            "semantic:yolov11:bbox:vendor-signature": "street-vendor-v1",
                        },
                        "runtime_file": "D:/labelu_data/yolo_quality_policy.json",
                        "policies": [],
                    }
                },
            )
            return

        raise AssertionError(f"Unexpected API call: {method} {path}")

    return handle_api


def _new_page(browser):
    page = browser.new_page()
    page.add_init_script(
        """
        localStorage.setItem('token', 'Bearer mock-token');
        """
    )
    return page


def test_quality_policies_page_uses_global_label_set_contract():
    activate_payloads: list[dict[str, Any]] = []
    save_payloads: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = _new_page(browser)
        page.route(
            f"**{API_PREFIX}/**",
            _build_quality_policy_handler(activate_payloads, save_payloads),
        )
        page.goto(PAGE_URL)

        expect(page.locator("#policyRows tr")).to_have_count(1, timeout=15_000)
        expect(page.locator("#policyTrainingQualityJson")).to_be_visible()
        expect(page.locator("#policyEdgeUsabilityJson")).to_be_visible()
        expect(page.locator("#policyReleaseReadinessJson")).to_be_visible()
        expect(page.locator("#activeSceneSummary")).to_contain_text("garbage-v2")
        expect(page.locator("#activeSceneSummary")).to_contain_text("street-vendor-v1")

        page.locator("#presetSelect").select_option(["101", "102"])
        expect(page.locator("#policyRows tr")).to_have_count(2, timeout=15_000)

        page.locator('button[data-act="view"][data-name="garbage-v1"]').click()
        page.locator("#btnSavePolicy").click()
        expect(page.locator("#toast")).to_contain_text("已保存", timeout=10_000)

        page.locator("#btnClearPreset").click()
        expect(page.locator("#policyRows tr")).to_have_count(4, timeout=15_000)

        page.locator('button[data-act="view"][data-name="garbage-v1"]').click()
        expect(page.locator("#currentSummary")).to_contain_text("garbage-v2")
        expect(page.locator("#currentSummary")).to_contain_text("Garbage")
        expect(page.locator("#currentSummary")).to_contain_text("Bag")

        page.locator('button[data-act="activate"][data-name="garbage-v1"]').click()
        expect(page.locator("#toast")).not_to_be_empty(timeout=10_000)
        browser.close()

    assert activate_payloads == [{"policy_name": "garbage-v1"}]
    assert save_payloads
    assert save_payloads[0]["label_ids"] == [102, 101]
    assert save_payloads[0]["model_family"] == "yolov11"
    assert save_payloads[0]["annotation_kind"] == "bbox"
    assert "rect_preset_slug" not in save_payloads[0]


@pytest.mark.parametrize(
    ("section_hash", "visible_selector", "hidden_selectors"),
    [
        (
            "#trainingStandards",
            "#trainingStandards",
            ("#edgeStandards", "#releaseStandards"),
        ),
        (
            "#edgeStandards",
            "#edgeStandards",
            ("#trainingStandards", "#releaseStandards"),
        ),
        (
            "#releaseStandards",
            "#releaseStandards",
            ("#trainingStandards", "#edgeStandards"),
        ),
    ],
)
def test_quality_policies_hash_entry_shows_only_target_standard(
    section_hash: str,
    visible_selector: str,
    hidden_selectors: tuple[str, str],
):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = _new_page(browser)
        page.route(f"**{API_PREFIX}/**", _build_quality_policy_handler([], []))
        page.goto(f"{PAGE_URL}{section_hash}")

        expect(page.locator(visible_selector)).to_be_visible(timeout=15_000)
        for hidden_selector in hidden_selectors:
            expect(page.locator(hidden_selector)).to_be_hidden(timeout=15_000)

        browser.close()
