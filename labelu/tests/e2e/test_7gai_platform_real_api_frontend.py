"""Opt-in, read-only staging API smoke checks for the 7GAI frontend contract."""

import json
import os

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright


REAL_API_URL = os.getenv("LABELU_PLATFORM_REAL_API_URL")
PROJECT_ID = os.getenv("LABELU_PLATFORM_REAL_PROJECT_ID")
TOKEN = os.getenv("LABELU_PLATFORM_REAL_TOKEN")
pytestmark = pytest.mark.skipif(
    not (REAL_API_URL and PROJECT_ID and TOKEN),
    reason="set LABELU_PLATFORM_REAL_API_URL, LABELU_PLATFORM_REAL_PROJECT_ID and LABELU_PLATFORM_REAL_TOKEN",
)


def test_staging_projects_api_returns_json_for_selected_project():
    with sync_playwright() as playwright:
        request = playwright.request.new_context(
            base_url=REAL_API_URL.rstrip("/"),
            extra_http_headers={"Authorization": TOKEN},
        )
        response = request.get("/api/v1/projects?page=0&size=100")
        assert response.status == 200, response.text()
        assert "application/json" in (response.headers.get("content-type") or "")
        payload = response.json()
        assert isinstance(payload, dict)
        assert isinstance(payload.get("data"), list)
        assert any(str(item.get("id")) == str(PROJECT_ID) for item in payload["data"])
        request.dispose()


def test_staging_openapi_contains_7gai_platform_routes():
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=REAL_API_URL.rstrip("/"))
        response = request.get("/api-docs/openapi.json")
        assert response.status == 200, response.text()
        payload = response.json()
        paths = payload.get("paths", {}) if isinstance(payload, dict) else {}
        assert "/api/v1/rule-templates/device-signature-reports" in json.dumps(paths)
        request.dispose()
