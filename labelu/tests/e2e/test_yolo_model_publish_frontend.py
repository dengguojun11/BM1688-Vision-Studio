from pathlib import Path


PAGE_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "extensions"
    / "pages"
    / "yolo_model_publish.html"
)


def _source() -> str:
    return PAGE_PATH.read_text(encoding="utf-8")


def test_publish_views_are_above_their_statistics_and_records() -> None:
    source = _source()

    switcher_at = source.index('class="publish-view-switcher"')
    stats_at = source.index('id="publishStatsGrid"')
    workspace_at = source.index('class="card lu-train-list-card publish-workspace"')

    assert switcher_at < stats_at < workspace_at
    assert '>候选发布</button>' in source
    assert '>发布记录</button>' in source
    assert 'document.querySelectorAll(".publish-view-tabs button")' in source
    assert 'b.setAttribute("aria-selected", selected ? "true" : "false")' in source


def test_publish_page_drops_console_banner_and_uses_centered_detail_modals() -> None:
    source = _source()

    assert "模型发布控制台" not in source
    assert "publish-console-banner" not in source
    assert "publish-drawer" not in source
    assert source.count('class="modal publish-detail-modal" role="dialog" aria-modal="true"') == 2
    assert 'class="modal-bg publish-detail-modal-bg" id="candidateDetailModal"' in source
    assert 'class="modal-bg publish-detail-modal-bg" id="historyDetailModal"' in source
    assert ".progress-steps li { max-width: 100%" in source
    assert "overflow-wrap: anywhere" in source


def test_publish_blocker_navigation_preserves_workspace_context() -> None:
    source = _source()

    assert 'params.set("embedded", "1")' in source
    assert 'params.set("manual_edge", "1")' in source
    assert 'params.set("from", "publish")' in source
    assert 'params.set("blocker_status", edgeStatus)' in source
    assert "LabeluPortal.navigateTrainingHubTo(url)" in source


def test_publish_review_state_exposes_manual_routes_and_training_selection() -> None:
    source = _source()

    assert "function publishGateRoute(row)" in source
    assert '"pt_bmodel_comparison_missing"' in source
    assert '"generalization_missing_review"' in source
    assert "人工复核路线" in source
    assert 'params.set("taskId", String(row.task_id || ""))' in source
    assert 'params.set("jobId", String(row.yolo_train_job_id))' in source
    assert 'params.set("manual_edge", "1")' in source
    assert "bindPublishGateRouteButtons(body, row)" in source


def test_manual_publish_keeps_quality_risk_models_selectable() -> None:
    source = _source()

    assert 'return !!option && !!String(option.rel_path || option.filename || "").trim()' in source
    assert 'view.group !== "legacy"' in source
    assert '"确认人工发布 "' in source
    assert "本次操作将按人工发布记录" in source
    assert "质量评估只展示风险，不阻断人工操作" in source
    assert 'set("publishStatLabel3", "不可人工发布")' in source
    assert 'set("publishStatLabel4", "风险发布")' in source
    assert 'view.tone === "blocked" ? " publish-danger" : " publish-risk"' in source
    assert 'confirm.classList.toggle("publish-danger", isHighRisk)' in source
    assert 'confirm.classList.toggle("publish-risk", isRisk)' in source


def test_publish_page_marks_and_selects_backend_recommended_precision() -> None:
    source = _source()

    assert '"推荐发布 " + String(recommended.precision || "").toUpperCase()' in source
    assert 'o.recommended && bmodelOptionPublishable(o) ? " 推荐发布"' in source
    assert 'var recommendation = option.recommended ? "（推荐发布）" : ""' in source
    assert "var defaultOption = preferredBmodelOption(row)" in source
    assert "function selectedOptionInheritsRowRisk(row, selected)" in source
    assert "pt_bmodel_comparison_review: true" in source


def test_manifest_integrity_block_cannot_be_overridden_by_manual_publish() -> None:
    source = _source()

    assert 'group: "manifest"' in source
    assert 'manifest_signature_invalid' in source
    assert 'artifact_0_sha256_mismatch' in source
    assert '"制品清单未通过"' in source
    assert 'confirm.disabled = !selected || manifestBlocked' in source
    assert 'kind: "manifest"' in source
