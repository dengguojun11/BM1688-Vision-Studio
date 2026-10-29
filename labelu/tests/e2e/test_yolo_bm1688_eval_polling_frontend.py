from __future__ import annotations

import base64
import copy
import re
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

from labelu.tests.e2e.test_yolo_bm1688_exports_workspace_frontend import (
    API_PREFIX,
    PAGE_URL,
    _export_detail,
    _export_row,
    _handle_api,
    _json_response,
)


def test_unrelated_busy_export_does_not_reload_completed_evaluation_or_images():
    evaluation_requests: list[str] = []
    list_requests: list[str] = []
    asset_requests: list[str] = []
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    completed = _export_row()
    busy = copy.deepcopy(completed)
    busy.update({"id": 999, "status": "PROCESSING", "task_name": "其他编译中的任务"})
    detail = _export_detail()
    sop_eval = detail["quality_report"]["sop_eval"]
    sop_eval["pt"]["metrics"]["images"] = 320
    sop_eval["onnx"]["metrics"]["images"] = 320
    sop_eval["bmodel"]["metrics"]["images"] = 50
    sop_eval["analysis"]["class_drops"] = []
    detail["artifact_manifest"]["source_dataset"]["class_names"] = [
        "person",
        "car",
        "truck",
        "bus",
        "bike",
        "other",
    ]
    detail["board_validation"]["report"]["source"] = "manual_edge_validation"
    detail["board_validation"]["report"]["metrics"]["bmcv_preview"] = {
        "viz_image_count": 1,
        "preview_files": ["sample-preview.png"],
    }

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if (
            path == f"{API_PREFIX}/yolo-bm1688-exports"
            and request.method == "GET"
            and "page" in parse_qs(parsed.query)
        ):
            list_requests.append(request.url)
            _json_response(route, {"data": [completed, busy], "meta_data": {"total": 2}})
            return
        if path == f"{API_PREFIX}/yolo-bm1688-exports/123/evaluation":
            evaluation_requests.append(request.url)
            _json_response(route, {"data": detail})
            return
        if path.startswith(f"{API_PREFIX}/yolo-bm1688-exports/123/sop-eval-asset/"):
            asset_requests.append(path)
            route.fulfill(status=200, content_type="image/png", body=tiny_png)
            return
        _handle_api(route)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(f"{PAGE_URL}?embedded=1")

        expect(page.locator("#tbody tr")).to_have_count(2, timeout=15_000)
        page.locator('#tbody tr[data-export-id="123"] button[data-act="eval"]').click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        expect(page.locator("#evalModalBody")).to_contain_text("边端人工抽样")
        expect(page.locator("#evalModalBody")).to_contain_text("口径不同")
        expect(page.locator("#evalModalBody")).to_contain_text("数据集类别清单 6 类已回传")
        expect(page.locator("#evalModalBody")).to_contain_text("分类诊断 0 类")

        page.locator('[data-eval-workspace-tab="performance"]').click()
        preview = page.locator('img[data-bmcv-asset="sample-preview.png"]')
        preview.scroll_into_view_if_needed()
        expect(preview).to_have_attribute("src", re.compile(r"^blob:"), timeout=15_000)

        page.wait_for_timeout(8_500)

        assert len(list_requests) >= 3
        assert len(evaluation_requests) == 1
        assert asset_requests.count(asset_requests[0]) == 1
        expect(preview).to_have_attribute("src", re.compile(r"^blob:"))
        browser.close()


def test_edge_visual_evidence_loads_only_current_page_and_defers_comparisons():
    output_requests: list[str] = []
    comparison_requests: list[str] = []
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    completed = _export_row()
    detail = _export_detail()
    items = []
    for index in range(50):
        positive = index < 36
        items.append(
            {
                "filename": f"sample-{index:02d}.jpg",
                "status": "matched" if positive else "true_negative",
                "annotation_state": "positive" if positive else "negative",
                "gt_count": 1 if positive else 0,
                "prediction_count": 1 if positive else 0,
                "tp": 1 if positive else 0,
                "fp": 0,
                "fn": 0,
                "output_asset": f"{index:04d}-output.jpg",
                "comparison_asset": f"{index:04d}-compare.jpg",
            }
        )
    contract = {
        "schema_version": "bm1688-visual-evidence-v2",
        "validation_id": 9,
        "export_id": 123,
        "model_variant": "int8",
        "page_size": 12,
        "available": True,
        "counts": {
            "total_images": 50,
            "positive_images": 36,
            "negative_images": 14,
            "unlabeled_images": 0,
            "matched_images": 36,
            "true_negative_images": 14,
            "false_positive_images": 0,
            "false_negative_images": 0,
            "mismatch_images": 0,
            "tp": 36,
            "fp": 0,
            "fn": 0,
        },
        "items": items,
    }

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if (
            path == f"{API_PREFIX}/yolo-bm1688-exports"
            and request.method == "GET"
            and "page" in parse_qs(parsed.query)
        ):
            _json_response(route, {"data": [completed], "meta_data": {"total": 1}})
            return
        if path == f"{API_PREFIX}/yolo-bm1688-exports/123/evaluation":
            _json_response(route, {"data": detail})
            return
        if path == f"{API_PREFIX}/yolo-edge-validation/jobs/9/visual-evidence":
            _json_response(route, {"data": contract})
            return
        output_prefix = f"{API_PREFIX}/yolo-edge-validation/jobs/9/visual-evidence/output/"
        compare_prefix = f"{API_PREFIX}/yolo-edge-validation/jobs/9/visual-evidence/compare/"
        if path.startswith(output_prefix):
            output_requests.append(path)
            route.fulfill(status=200, content_type="image/png", body=tiny_png)
            return
        if path.startswith(compare_prefix):
            comparison_requests.append(path)
            route.fulfill(status=200, content_type="image/png", body=tiny_png)
            return
        if path.startswith(f"{API_PREFIX}/yolo-bm1688-exports/123/sop-eval-asset/"):
            route.fulfill(status=200, content_type="image/png", body=tiny_png)
            return
        _handle_api(route)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(f"{PAGE_URL}?embedded=1")
        expect(page.locator("#tbody tr")).to_have_count(1, timeout=15_000)
        page.locator('#tbody tr[data-export-id="123"] button[data-act="eval"]').click()
        expect(page.locator("#evalModal.show")).to_be_visible(timeout=15_000)
        page.locator('[data-eval-workspace-tab="performance"]').click()
        visual_host = page.locator("[data-edge-visual-host]")
        expect(visual_host.locator(".edge-visual-card")).to_have_count(12, timeout=15_000)
        expect(visual_host).to_contain_text("正样本")
        expect(visual_host).to_contain_text("36")
        expect(visual_host).to_contain_text("确认负样本")
        expect(visual_host.locator("[data-edge-visual-progress]")).to_have_text(
            "已加载 12 / 12", timeout=15_000
        )
        assert len(output_requests) == 12
        assert comparison_requests == []

        visual_host.locator('[data-edge-visual-mode="compare"]').click()
        expect(visual_host.locator("[data-edge-visual-progress]")).to_have_text(
            "已加载 12 / 12", timeout=15_000
        )
        assert len(output_requests) == 12
        assert len(comparison_requests) == 12

        visual_host.locator('[data-edge-visual-page="next"]').click()
        expect(visual_host).to_contain_text("第 2 / 5 页")
        expect(visual_host.locator("[data-edge-visual-progress]")).to_have_text(
            "已加载 12 / 12", timeout=15_000
        )
        assert len(output_requests) == 12
        assert len(comparison_requests) == 24
        browser.close()
