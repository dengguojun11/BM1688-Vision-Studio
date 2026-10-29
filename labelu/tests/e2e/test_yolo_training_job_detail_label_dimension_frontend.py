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
TRAINING_PAGE_FILE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"
TRAINING_PAGE_URL = f"{BASE_URL}/yolo-training?embedded=1"


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


def test_train_job_detail_renders_label_dimension_training_task_snapshot():
    page_source = TRAINING_PAGE_FILE.read_text(encoding="utf-8")
    assert "优先查看错误样本和类别指标，再结合 AI 建议发起迭代训练。" in page_source
    assert "流程已经全部执行完成" not in page_source

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
            _json_response(route, {"data": {"id": 1, "username": "admin@123.com"}})
            return
        if api_path.startswith("/annotation-taxonomy/label-presets"):
            _json_response(route, {"data": []})
            return
        if api_path.startswith(
            (
                "/annotation-taxonomy/labels",
                "/annotation-taxonomy/dataset-categories",
                "/annotation-taxonomy/training-recipes",
                "/annotation-taxonomy/runtime-templates",
            )
        ):
            _json_response(route, {"data": []})
            return
        if api_path.startswith("/yolo-train-jobs/901/gt-pred-compare"):
            _json_response(
                route,
                {
                    "data": {
                        "status": {
                            "status": "completed",
                            "job_id": 901,
                            "processed": 10,
                            "total": 10,
                            "message": "已生成 10 张对比图",
                        },
                        "summary": {"generated_at": "2026-07-08T10:11:00"},
                        "items": [],
                        "total": 10,
                        "page": 0,
                        "size": 1,
                    }
                },
            )
            return
        if api_path.startswith("/yolo-train-jobs"):
            _json_response(
                route,
                {
                    "data": [
                        {
                            "id": 901,
                            "task_id": 11,
                            "task_name": "主任务A",
                            "training_task_id": 71,
                            "training_task_name": "零散垃圾训练任务",
                            "training_task_mode": "label_dimension",
                            "training_task_display_subtitle": "主任务A 等 1 个任务",
                            "status": "COMPLETED",
                            "model_weights": "yolo11n.pt",
                            "epochs": 50,
                            "imgsz": 640,
                            "train_sample_count": 9,
                            "val_sample_count": 1,
                            "test_sample_count": 0,
                            "total_sample_count": 10,
                            "creator_email": "admin@123.com",
                            "created_at": "2026-07-08T10:00:00",
                            "updated_at": "2026-07-08T10:10:00",
                        }
                    ],
                    "meta_data": {"total": 1, "page": 0, "size": 1},
                },
            )
            return
        if api_path == "/tasks/11/yolo-train-jobs/901":
            _json_response(
                route,
                {
                    "data": {
                        "id": 901,
                        "task_id": 11,
                        "training_task_id": 71,
                        "training_task_name": "零散垃圾训练任务",
                        "training_task_mode": "label_dimension",
                        "training_task_display_subtitle": "主任务A 等 1 个任务",
                        "training_task_detail": {
                            "training_task_id": 71,
                            "name": "零散垃圾训练任务",
                            "mode": "label_dimension",
                            "status": "PENDING",
                            "primary_task_id": 11,
                            "primary_task_name": "主任务A",
                            "selected_presets": [
                                {"slug": "preset_a", "name": "预设A"},
                                {"slug": "preset_b", "name": "预设B"},
                            ],
                            "selected_label_items": [
                                {"display_name": "预设A/零散垃圾", "selected_order": 0},
                                {"display_name": "预设B/零散垃圾", "selected_order": 1},
                            ],
                            "merged_classes": [
                                {
                                    "class_name": "零散垃圾",
                                    "class_order": 0,
                                    "selected_from": ["预设A/零散垃圾", "预设B/零散垃圾"],
                                    "image_count": 9,
                                }
                            ],
                            "source_tasks": [
                                {
                                    "task_id": 11,
                                    "task_name": "主任务A",
                                    "included_image_count": 9,
                                    "matched_done_sample_count": 10,
                                    "match_sources": ["预设A/零散垃圾"],
                                }
                            ],
                            "ignored_tasks": [
                                {
                                    "task_id": 22,
                                    "task_name": "主任务B",
                                    "reason_code": "NO_VALID_SAMPLES_AFTER_CLEAN",
                                    "reason_text": "按标签清洗后无有效图片",
                                }
                            ],
                            "latest_job_id": 901,
                            "latest_job_status": "COMPLETED",
                            "created_at": "2026-07-08T10:00:00",
                            "updated_at": "2026-07-08T10:10:00",
                        },
                        "status": "COMPLETED",
                        "category": "零散垃圾训练任务",
                        "hyperparams": {},
                        "model_weights": "yolo11n.pt",
                        "epochs": 50,
                        "imgsz": 640,
                        "train_sample_count": 9,
                        "val_sample_count": 1,
                        "test_sample_count": 0,
                        "total_sample_count": 10,
                        "dataset_manifest": {
                            "sample_composition": {
                                "positive_images": 6,
                                "negative_images": 3,
                                "negative_ratio": 0.3333,
                            }
                        },
                        "weights_path": "runs/train/exp901/weights/best.pt",
                        "metrics_summary": json.dumps({"ok": True}, ensure_ascii=False),
                        "created_at": "2026-07-08T10:00:00",
                        "updated_at": "2026-07-08T10:10:00",
                        "training_val_metrics": {
                            "map50_95": 0.61,
                            "map50": 0.81,
                            "summary_line": "mAP50-95=0.61",
                            "source": "metrics_summary",
                            "split": "val",
                        },
                        "validation_pass": {
                            "status": "达标",
                            "detail": "mAP50-95=0.6100，达标线≥0.5000",
                        },
                        "quality_assessment": {
                            "training_quality": {"key": "training", "label": "训练指标", "status": "达标", "summary": "训练指标正常"},
                            "generalization_quality": {"key": "generalization", "label": "泛化表现", "status": "达标", "summary": "泛化正常"},
                            "edge_usability": {"key": "edge", "label": "边端可用性", "status": "待验证", "summary": "未验证"},
                            "release_readiness": {"key": "release", "label": "发布建议", "status": "待复核", "summary": "需人工复核"},
                        },
                        "runtime_status": "COMPLETED",
                        "ai_eval_full_status": "COMPLETED",
                        "ai_eval_next_status": "COMPLETED",
                        "is_restartable": False,
                        "train_steps": [],
                    }
                },
            )
            return
        if api_path.startswith("/yolo-trained-models"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-imported-models"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path.startswith("/yolo-eval-reports"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
            return
        if api_path == "/yolo-bm1688-exports/compiler-settings":
            _json_response(route, {"data": {"auto_compile": True}})
            return
        if api_path.startswith("/yolo-bm1688-exports"):
            _json_response(route, {"data": [], "meta_data": {"total": 0}})
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
        page.add_init_script("localStorage.setItem('token', 'Bearer mock-token');")
        page.route("**/*", handler)
        page.goto(TRAINING_PAGE_URL)

        page.locator(".btn-job-detail").click()
        expect(page.locator("#modalJobDetail")).to_be_visible(timeout=15_000)
        expect(page.locator("#modalJobDetail")).to_contain_text("预设A/零散垃圾", timeout=15_000)
        expect(page.locator("#modalJobDetail")).to_contain_text("主任务A：纳入 9 张", timeout=15_000)
        expect(page.locator("#modalJobDetail")).to_contain_text("主任务B：按标签清洗后无有效图片", timeout=15_000)
        expect(page.locator("#modalJobDetail")).to_contain_text("正样本 6 / 负样本 3（33.3%）", timeout=15_000)
        overview = page.locator("#jobOverviewGrid .job-overview-console")
        expect(overview).to_be_visible(timeout=15_000)
        expect(overview).to_contain_text("Val mAP50-95")
        expect(overview).to_contain_text("训练事实")
        expect(overview).to_contain_text("训练流程")
        expect(overview).to_contain_text("PT 权重")
        expect(overview).to_contain_text("六个关键节点")
        expect(overview).to_contain_text("标注对比")
        expect(overview).to_contain_text("10 张图片已完成 GT / 预测对比")
        expect(overview).to_contain_text("AI 评估")
        expect(overview).to_contain_text("全维度报告 + 迭代建议")
        expect(overview).to_contain_text("训练与分析闭环已完成")
        expect(overview).not_to_contain_text("流程已经全部执行完成")
        expect(overview).not_to_contain_text("指标口径")
        expect(overview.locator(".job-overview-console-kpi").first).to_contain_text(
            "best.pt 独立验证"
        )
        assert overview.locator(".job-overview-console-kpi").first.get_attribute(
            "title"
        )
        expect(overview).not_to_contain_text("训练运行观察")
        expect(overview).not_to_contain_text("BM1688")
        expect(overview).not_to_contain_text("BModel")
        expect(page.locator("#jobObservabilityPanel")).to_be_hidden()
        expect(page.locator("#jobDetailFooter")).to_be_hidden()
        expect(page.locator("#btnDetailBm1688")).to_have_count(0)

        page.set_viewport_size({"width": 390, "height": 844})
        expect(overview).to_be_visible()
        assert page.locator("#jobOverviewGrid .job-overview-console-stage").count() == 6
        assert page.locator("#jobOverviewGrid .job-overview-console").evaluate(
            "el => el.scrollWidth <= el.clientWidth + 1"
        )

        browser.close()
