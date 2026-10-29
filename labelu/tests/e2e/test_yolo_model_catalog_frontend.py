from __future__ import annotations

import json
import io
import os
import zipfile

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Route, expect, sync_playwright

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:9888")
pytestmark = pytest.mark.skipif(
    not os.environ.get("LABELU_E2E_BASE"),
    reason="set LABELU_E2E_BASE to run legacy extension E2E tests",
)
PAGE_URL = f"{BASE_URL}/yolo-model-catalog"
API_PREFIX = "/api/v1"


def _model(model_id: int, *, legacy: bool = False) -> dict:
    return {
        "catalog_id": model_id - 500,
        "model_id": str(model_id),
        "download_path": f"/api/v1/public/models/{model_id}/download",
        "aicm_model_set_id": model_id,
        "aicm_environment_id": "prod-aicm",
        "export_id": None if legacy else 88,
        "yolo_train_job_id": None if legacy else 99,
        "task_id": None if legacy else 100,
        "name": "占道经营_bm1688_v8" if legacy else "电动车加装的雨伞_bm1688_v2",
        "display_name": "占道经营" if legacy else "电动车加装的雨伞",
        "source_type": "remote_legacy" if legacy else "labelu_publish",
        "source_system": "aicm",
        "status": "active",
        "is_public": True,
        "download_enabled": True,
        "availability_status": "available",
        "local_package_available": not legacy,
        "chip_type": "SOPHGO,BM1688,NPU",
        "algorithm_type": "Detection",
        "precision": "int8" if not legacy else None,
        "resolution": "640x640",
        "map50": 0.904,
        "class_names": (
            ["vehicle_motorbike", "nonvehicle_other"]
            if not legacy
            else ["streetVendor"]
        ),
        "class_labels": (
            [
                {
                    "class_index": 0,
                    "label_key": "摩托车",
                    "label_value": "vehicle_motorbike",
                    "color": "#1677ff",
                    "status": "active",
                    "annotation_kind": "bbox",
                    "matched": True,
                },
                {
                    "class_index": 1,
                    "label_key": "大车，马车；小型机动车；购物车，手推车",
                    "label_value": "nonvehicle_other",
                    "color": "#52c41a",
                    "status": "active",
                    "annotation_kind": "bbox",
                    "matched": True,
                },
            ]
            if not legacy
            else []
        ),
        "package_size_bytes": 4875524 if not legacy else None,
        "package_sha256": (
            "73cb403b6803293dcda287a8c87a430fa8476ec5149f0e313eb054610d593b97"
            if not legacy
            else None
        ),
        "package_md5": "6f5902ac237024bdd0c176cb93063dc4" if not legacy else None,
        "publish_job_id": "publish-569" if not legacy else None,
        "remote_download_url": (
            "http://aicm.example/model-569.zip" if not legacy else None
        ),
        "publisher_username": "admin@123.com",
        "published_at": "2026-08-20T14:10:30",
        "last_synced_at": "2026-08-21T09:30:00",
        "aliases": [
            {
                "id": model_id,
                "source_system": "aicm",
                "external_model_id": str(model_id),
                "alias_type": "aicm",
                "created_at": "2026-08-21T09:30:00",
            },
            {
                "id": model_id + 1000,
                "source_system": "vendor-a",
                "external_model_id": f"vendor-{model_id}",
                "alias_type": "third_party",
                "created_at": "2026-08-21T09:31:00",
            },
        ],
    }


def _json(route: Route, payload: dict, status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(payload, ensure_ascii=False),
    )


def _zip_bytes() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("model.bin", b"model-569")
    return stream.getvalue()


def _handler(calls: list[tuple[str, str]]):
    models = [_model(569), _model(568, legacy=True)]
    models[1].pop("download_enabled")

    def handle(route: Route) -> None:
        request = route.request
        path = request.url.split(API_PREFIX, 1)[1]
        calls.append((request.method, path))
        if path.startswith("/local-dev/session"):
            _json(route, {"data": {"session_id": "playwright"}})
        elif path == "/users/about":
            _json(route, {"data": {"version": "test"}})
        elif path == "/users/me":
            _json(route, {"data": {"id": 1, "username": "admin@123.com"}})
        elif path.startswith("/published-models?") and request.method == "GET":
            _json(
                route,
                {
                    "data": {
                        "summary": {
                            "total": 326,
                            "active": 324,
                            "public": 320,
                            "unavailable": 2,
                            "legacy": 310,
                        },
                        "models": models,
                    },
                    "meta_data": {"total": 2, "page": 0, "size": 2},
                },
            )
        elif path.endswith("/568/download"):
            models[1]["availability_status"] = "unavailable"
            _json(
                route,
                {
                    "detail": (
                        "模型 568 在 LabelU 中没有本地模型包，"
                        "且 AICM 无法完成一次性补缓存"
                    )
                },
                status=409,
            )
        elif path.endswith("/download"):
            route.fulfill(
                status=200,
                content_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="model-569.zip"'},
                body=_zip_bytes(),
            )
        elif path.endswith("/aliases") and request.method == "POST":
            updated = dict(models[0])
            updated["aliases"] = models[0]["aliases"] + [
                {
                    "id": 2001,
                    "source_system": "vendor-b",
                    "external_model_id": "third-party-569",
                    "alias_type": "third_party",
                    "created_at": "2026-08-21T10:00:00",
                }
            ]
            _json(route, {"data": updated}, status=201)
        elif path.startswith("/published-models/") and request.method == "PATCH":
            catalog_id = int(path.split("/", 3)[2])
            index = next(
                i for i, model in enumerate(models) if model["catalog_id"] == catalog_id
            )
            updated = dict(models[index], **request.post_data_json)
            models[index] = updated
            _json(route, {"data": updated})
        elif path == "/published-models/sync" and request.method == "POST":
            _json(route, {"data": {"created": 1, "updated": 325}})
        else:
            raise AssertionError(
                f"Unexpected catalog API call: {request.method} {path}"
            )

    return handle


def test_model_catalog_desktop_management_and_download():
    calls: list[tuple[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(
            "localStorage.setItem('token', 'Bearer mock-token');"
            "window.__openedDownload = '';"
            "window.open = function (url) { window.__openedDownload = url; };"
        )
        page.route(f"**{API_PREFIX}/**", _handler(calls))
        page.goto(PAGE_URL)

        expect(page.locator("#catalogTbody tr[data-catalog-id]")).to_have_count(2)
        expect(page.locator("#metricTotal")).to_have_text("326")
        expect(page.locator("#metricPublic")).to_have_text("320")
        expect(page.get_by_text("电动车加装的雨伞", exact=True)).to_be_visible()
        expect(page.locator("#catalogTbody")).to_contain_text("AICM 历史")

        page.locator("#catalogTbody tr").first.get_by_role(
            "button", name="详情"
        ).click()
        expect(page.locator("#detailModal")).to_have_class("modal-bg show")
        expect(page.locator("#labelContractCount")).to_have_text("2 类")
        expect(page.locator("#labelContractList")).to_contain_text("摩托车")
        expect(page.locator("#labelContractList")).to_contain_text("vehicle_motorbike")
        expect(page.locator("#labelContractList")).to_contain_text(
            "大车，马车；小型机动车；购物车，手推车"
        )
        expect(page.locator("#packageDetailGrid")).to_contain_text("4.65 MB")
        expect(page.locator("#packageDetailGrid")).to_contain_text("73cb403b")
        expect(page.locator("#detailGrid")).to_contain_text("标注任务 ID")
        expect(page.locator("#detailGrid")).to_contain_text("#100")
        expect(page.locator("#detailGrid")).to_contain_text("模型精度")
        expect(page.locator("#detailGrid")).to_contain_text("INT8")
        expect(page.locator("#aliasList")).to_contain_text("vendor-a")
        page.locator("#aliasSource").fill("vendor-b")
        page.locator("#aliasId").fill("third-party-569")
        page.locator("#btnAddAlias").click()
        expect(page.locator("#aliasList")).to_contain_text("third-party-569")

        page.locator("#btnCloseModal").click()
        with page.expect_download() as download_info:
            page.locator("#catalogTbody tr").first.get_by_role(
                "button", name="下载"
            ).click()
        assert download_info.value.suggested_filename == "model-569.zip"
        assert any(path.endswith("/569/download") for _, path in calls)

        legacy_row = page.locator("#catalogTbody tr").nth(1)
        legacy_row.get_by_role("button", name="下载").click()
        expect(page.locator("#toast")).to_contain_text(
            "模型 568 在 LabelU 中没有本地模型包"
        )
        expect(legacy_row).to_contain_text("本地包缺失")
        expect(legacy_row.get_by_role("button", name="下载")).to_be_enabled()

        download_switch = legacy_row.locator('[data-act="download-toggle"]')
        expect(download_switch).to_be_checked()
        download_switch.uncheck()
        expect(legacy_row).to_contain_text("下载已关闭")
        expect(legacy_row.get_by_role("button", name="下载")).to_be_disabled()

        page.locator("#filterQuery").fill("雨伞")
        page.locator("#btnSearch").click()
        page.wait_for_timeout(100)
        assert any("q=%E9%9B%A8%E4%BC%9E" in path for _, path in calls)
        browser.close()


def test_model_catalog_mobile_has_no_horizontal_page_overflow():
    calls: list[tuple[str, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route(f"**{API_PREFIX}/**", _handler(calls))
        page.goto(PAGE_URL)

        expect(page.locator("#catalogTbody tr[data-catalog-id]")).to_have_count(2)
        overflow = page.evaluate(
            "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth"
        )
        assert overflow <= 1
        first_row = page.locator("#catalogTbody tr").first.bounding_box()
        assert first_row is not None
        assert first_row["width"] <= 374
        page.locator("#catalogTbody tr").first.get_by_role(
            "button", name="详情"
        ).click()
        expect(page.locator("#labelContractList")).to_contain_text("nonvehicle_other")
        modal_overflow = page.locator(".modal").evaluate(
            "element => element.scrollWidth - element.clientWidth"
        )
        assert modal_overflow <= 1
        browser.close()
