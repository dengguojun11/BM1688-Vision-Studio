from __future__ import annotations

import base64
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
TRAINING_PAGE_FILE = (
    REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"
)
MODEL_VALIDATE_PAGE_FILE = (
    REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_model_validate.html"
)
TRAINING_PAGE_URL = f"{BASE_URL}/yolo-training?embedded=1&panel=models"
MODEL_VALIDATE_PAGE_URL = f"{BASE_URL}/yolo-model-validate?task_id=23&job_id=301"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn2R6sAAAAASUVORK5CYII="
)


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
            "id": 301,
            "task_id": 23,
            "task_name": "垃圾街景检测",
            "category": "垃圾街景",
            "model_weights": "yolo11n.pt",
            "epochs": 120,
            "imgsz": 640,
            "creator_email": "admin@123.com",
            "updated_at": "2026-07-06T11:57:33",
        }
    ]


def test_model_library_more_menu_includes_pt_single_image_entry():
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
            _json_response(route, {"data": _trained_rows(), "meta_data": {"total": 1}})
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
        if api_path.startswith("/tasks"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return

        raise AssertionError(f"Unexpected API call: {request.method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("""
            localStorage.setItem('token', 'Bearer mock-token');
            window.__openCalls = [];
            window.open = function (url, target, features) {
              window.__openCalls.push({ url: url, target: target, features: features });
              return { focus: function () {} };
            };
            """)
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        expect(page.locator("#modelsTbody tr")).to_have_count(1, timeout=15_000)
        page.locator("#modelsTbody tr").first.locator(
            '[data-ops-more-toggle="model"]'
        ).click()
        expect(
            page.locator("#modelsTbody tr").first.locator('[data-act="model-validate"]')
        ).to_have_text("PT 单图试跑")
        page.locator("#modelsTbody tr").first.locator(
            '[data-act="model-validate"]'
        ).click()

        open_calls = page.evaluate("window.__openCalls")
        assert open_calls == [
            {
                "url": "/yolo-model-validate?task_id=23&job_id=301",
                "target": "_blank",
                "features": "noopener",
            }
        ]
        browser.close()


@pytest.mark.parametrize("job_type", ["plate_recognition", "plate_detection"])
def test_model_validate_page_renders_side_by_side_preview(
    tmp_path: Path, job_type: str
):
    upload_path = tmp_path / "sample.png"
    upload_path.write_bytes(PNG_BYTES)
    png_base64 = base64.b64encode(PNG_BYTES).decode("ascii")
    requests = []

    def handler(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path

        if path == "/yolo-model-validate":
            if MODEL_VALIDATE_PAGE_FILE.exists():
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=MODEL_VALIDATE_PAGE_FILE.read_text(encoding="utf-8"),
                )
            else:
                route.fulfill(status=404, body="")
            return
        if _fulfill_asset(route, path):
            return
        if not path.startswith(API_PREFIX):
            route.fulfill(status=404, body="")
            return

        api_path = path[len(API_PREFIX) :]
        requests.append((request.method, api_path))
        if api_path in {"/users/me", "/users/about"}:
            _json_response(
                route,
                {"data": {"id": 1, "email": "admin@123.com", "username": "admin"}},
            )
            return
        if api_path == "/tasks/23/yolo-train-jobs/301":
            _json_response(
                route,
                {
                    "data": {
                        "id": 301,
                        "task_id": 23,
                        "task_name": "垃圾街景检测",
                        "status": "COMPLETED",
                        "job_type": job_type,
                        "imgsz": 640,
                        "task_rect_classes": ["塑料袋", "纸盒"],
                        "weights_path": (
                            "/tmp/plate_pose_best.pt"
                            if job_type == "plate_recognition"
                            else "/tmp/plate_detection_best.pt"
                        ),
                    }
                },
            )
            return
        if (
            api_path == "/tasks/23/yolo-train-jobs/301/validate-image"
            and request.method == "POST"
        ):
            _json_response(
                route,
                {
                    "data": {
                        "task_id": 23,
                        "job_id": 301,
                        "job_type": job_type,
                        "model_source": (
                            "4-component PT"
                            if job_type == "plate_recognition"
                            else "plate_detection_best.pt"
                        ),
                        "task_name": "垃圾街景检测",
                        "image_width": 1,
                        "image_height": 1,
                        "box_count": 2,
                        "max_confidence": 0.92,
                        "detections": [
                            {
                                "label": "doubleEP",
                                "confidence": 0.97,
                                "x1": 1,
                                "y1": 2,
                                "x2": 30,
                                "y2": 20,
                                "corners": [
                                    {"x": 1, "y": 2, "confidence": 0.98},
                                    {"x": 30, "y": 2, "confidence": 0.98},
                                    {"x": 30, "y": 20, "confidence": 0.97},
                                    {"x": 1, "y": 20, "confidence": 0.97},
                                ],
                                "plate_number": "123456",
                                "plate_number_confidence": 0.83,
                                "color": "white",
                                "color_confidence": 0.99,
                                "plate_type": "whitePlate",
                                "plate_type_confidence": 0.98,
                            },
                            {
                                "label": "塑料袋",
                                "confidence": 0.92,
                                "x1": 1,
                                "y1": 2,
                                "x2": 3,
                                "y2": 4,
                            },
                            {
                                "label": "纸盒",
                                "confidence": 0.81,
                                "x1": 5,
                                "y1": 6,
                                "x2": 7,
                                "y2": 8,
                            },
                        ],
                        "result_image_data_url": "data:image/png;base64,"
                        + base64.b64encode(PNG_BYTES).decode("ascii"),
                    }
                },
            )
            return

        raise AssertionError(f"Unexpected API call: {request.method} {api_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.add_init_script("""
            localStorage.setItem('token', 'Bearer mock-token');
            window.__inputClickCount = 0;
            const __originalInputClick = HTMLInputElement.prototype.click;
            HTMLInputElement.prototype.click = function () {
              if (this && this.id === 'validateImageInput') {
                window.__inputClickCount += 1;
                return;
              }
              return __originalInputClick.apply(this, arguments);
            };
            """)
        page.route("**/*", handler)
        page.goto(MODEL_VALIDATE_PAGE_URL)

        expected_model_description = (
            "Pose、OCR、颜色和类型"
            if job_type == "plate_recognition"
            else "车牌检测与四角点定位模型"
        )
        expect(page.locator("#modelValidateSubtitle")).to_contain_text(
            expected_model_description
        )
        expect(page.locator("#modelValidateTitle")).to_contain_text("PT 单图试跑")
        expect(page.locator(".run-warning")).to_contain_text("不代表 BM1688 编译成功")
        expect(page.locator(".run-warning")).to_contain_text("发布资格")
        expect(page.locator("#resultCountLabel")).to_have_text("车牌数量")
        expect(page.locator("#modelValidateTitle")).to_contain_text(
            "垃圾街景检测", timeout=10_000
        )
        expect(page.locator("#modelValidateClassSummary")).to_contain_text("塑料袋")
        expect(page.locator("#modelValidateClassChips")).to_contain_text("纸盒")
        page.locator("#sourcePreviewWrap").click()
        assert page.evaluate("window.__inputClickCount") == 1
        dragover_active = page.evaluate(
            """
            ({ base64 }) => {
              const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
              const file = new File([bytes], "sample.png", { type: "image/png" });
              const dataTransfer = new DataTransfer();
              dataTransfer.items.add(file);
              const target = document.getElementById("sourcePreviewWrap");
              ["dragenter", "dragover"].forEach((eventType) => {
                target.dispatchEvent(new DragEvent(eventType, {
                  bubbles: true,
                  cancelable: true,
                  dataTransfer,
                }));
              });
              const dragover = target.classList.contains("is-dragover");
              target.dispatchEvent(new DragEvent("drop", {
                bubbles: true,
                cancelable: true,
                dataTransfer,
              }));
              return dragover;
            }
            """,
            {"base64": png_base64},
        )
        assert dragover_active is True
        assert "is-dragover" not in (
            page.locator("#sourcePreviewWrap").get_attribute("class") or ""
        )
        expect(page.locator("#resultSummaryCount")).to_have_text("2")
        assert (
            requests.count(("POST", "/tasks/23/yolo-train-jobs/301/validate-image"))
            == 1
        )
        assert (
            page.locator("#sourcePreviewImage").get_attribute("src") or ""
        ).startswith("data:image/")
        page.set_input_files("#validateImageInput", str(upload_path))
        expect(page.locator("#resultSummaryCount")).to_have_text("2")
        assert (
            requests.count(("POST", "/tasks/23/yolo-train-jobs/301/validate-image"))
            == 2
        )
        assert (
            page.locator("#sourcePreviewImage").get_attribute("src") or ""
        ).startswith("data:image/")
        page.locator("#btnRunValidation").click()
        expect(page.locator("#resultSummaryCount")).to_have_text("2")
        assert (
            requests.count(("POST", "/tasks/23/yolo-train-jobs/301/validate-image"))
            == 3
        )
        assert (
            page.locator("#resultPreviewImage").get_attribute("src") or ""
        ).startswith("data:image/")
        expected_model_source = (
            "4-component PT"
            if job_type == "plate_recognition"
            else "plate_detection_best.pt / Pose"
        )
        expect(page.locator("#heroJobValue")).to_have_text(expected_model_source)
        expect(page.locator("#resultDetections")).to_contain_text("doubleEP")
        expect(page.locator("#resultDetections")).to_contain_text("四角点 4/4")
        if job_type == "plate_recognition":
            expect(page.locator("#resultDetections")).to_contain_text(
                "号码 123456 (0.83)"
            )
            expect(page.locator("#resultDetections")).to_contain_text(
                "颜色 white (0.99)"
            )
            expect(page.locator("#resultDetections")).to_contain_text(
                "类型 whitePlate (0.98)"
            )
        else:
            for corner_text in (
                "左上 x 1.0 / y 2.0 / 置信度 0.98",
                "右上 x 30.0 / y 2.0 / 置信度 0.98",
                "右下 x 30.0 / y 20.0 / 置信度 0.97",
                "左下 x 1.0 / y 20.0 / 置信度 0.97",
            ):
                expect(page.locator("#resultDetections")).to_contain_text(corner_text)
        expect(page.locator("#resultDetections")).to_contain_text("塑料袋")
        expect(page.locator("#resultDetections")).to_contain_text("纸盒")
        browser.close()
