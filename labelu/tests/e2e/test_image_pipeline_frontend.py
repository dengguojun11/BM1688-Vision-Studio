"""
Playwright 前端验证：登录 → 新建图片任务 → 上传测试图 → 标注配置保存 → 检查 API 中 image_pipeline 元数据。

前置：
  pip install playwright pytest-playwright
  playwright install chromium
  .env 或环境变量：IMAGE_PIPELINE_ENABLED=true
  服务：python -m labelu.main --host 127.0.0.1 --port 8002

运行：
  pytest labelu/tests/e2e/test_image_pipeline_frontend.py -v -s
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests

pytest.importorskip("playwright")
# The browser fixture is provided by the optional pytest-playwright plugin;
# keep the integration test as a clean skip in backend-only environments.
pytest.importorskip("pytest_playwright")
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("LABELU_E2E_BASE", "http://127.0.0.1:8002")
API = f"{BASE_URL}/api/v1"
USER = os.environ.get("LABELU_E2E_USER", "admin@123.com")
PASSWORD = os.environ.get("LABELU_E2E_PASSWORD", "Admin@123")
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "pipeline_upload"


@pytest.fixture(scope="module")
def upload_images() -> list[Path]:
    if not UPLOAD_DIR.is_dir():
        pytest.skip(f"缺少测试图目录: {UPLOAD_DIR}")
    files = sorted(UPLOAD_DIR.glob("*.png"))
    if len(files) < 2:
        pytest.skip("pipeline_upload 至少需要 2 张 png")
    return files


def _api_headers(page: Page) -> dict[str, str]:
    token = page.evaluate("() => localStorage.getItem('token')")
    assert token, "localStorage 无 token，请确认登录成功"
    return {"Authorization": token}


def test_frontend_image_import_and_pipeline_metadata(page: Page, upload_images: list[Path]):
    page.goto(f"{BASE_URL}/login")
    page.get_by_role("textbox", name="Email邮箱地址").fill(USER)
    page.get_by_role("textbox", name="密码").fill(PASSWORD)
    page.get_by_role("button", name="登 录").click()
    page.wait_for_url("**/tasks**", timeout=30_000)

    page.get_by_role("button", name="新建任务").click()
    page.get_by_role("textbox", name="* 任务名称 :").fill("图像流水线E2E")
    page.locator("text=请选择数据类型").click()
    page.get_by_title("图片").click()
    page.get_by_role("button", name="下一步").click()
    page.wait_for_url("**#upload**", timeout=15_000)

    with page.expect_file_chooser() as fc_info:
        page.get_by_role("button", name="上传文件").click()
    fc_info.value.set_files([str(p) for p in upload_images])

    for p in upload_images:
        expect(page.get_by_text(p.name)).to_be_visible(timeout=60_000)
        expect(page.get_by_role("row", name=p.name).get_by_text("上传成功")).to_be_visible(
            timeout=60_000
        )

    page.get_by_role("button", name="下一步").click()
    page.wait_for_url("**#config**", timeout=15_000)

    page.locator("text=添加工具").click()
    page.get_by_text("矩形框", exact=True).click()

    page.get_by_role("button", name="保 存").click()
    page.wait_for_url("**/tasks**", timeout=30_000)

    task_id = None
    for part in page.url.split("/"):
        if part.isdigit():
            task_id = int(part)
            break
    assert task_id, f"无法从 URL 解析 task_id: {page.url}"

    headers = _api_headers(page)
    samples_resp = requests.get(
        f"{API}/tasks/{task_id}/samples",
        headers=headers,
        params={"page": 0, "size": 50},
        timeout=30,
    )
    assert samples_resp.status_code == 200, samples_resp.text
    rows = samples_resp.json().get("data") or []
    assert len(rows) >= len(upload_images), "样本数应不少于上传图片数"

    with_pipeline = 0
    for row in rows:
        data = row.get("data") or {}
        if isinstance(data, str):
            data = json.loads(data)
        pipe = (data or {}).get("image_pipeline") or {}
        if pipe.get("scene_id"):
            with_pipeline += 1

    if os.environ.get("IMAGE_PIPELINE_ENABLED", "").lower() in ("1", "true", "yes"):
        assert with_pipeline >= 2, (
            f"已开启 IMAGE_PIPELINE_ENABLED 但仅 {with_pipeline} 条样本含 scene_id；"
            "请确认已安装 opencv/sklearn 并重启服务"
        )
    else:
        pytest.skip("IMAGE_PIPELINE_ENABLED 未开启，跳过元数据断言")
