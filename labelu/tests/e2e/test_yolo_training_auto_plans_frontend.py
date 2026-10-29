from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PAGE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"


def test_training_page_exposes_auto_plan_workspace_and_api_contract():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "历史自动训练计划" in html
    assert "autoTrainingPlan" in html
    assert "/auto-training-plans" in html
    assert "整体召回率" in html
    assert "一期仅供查询，不创建、不修改、不扫描、不触发新轮次。" in html


def test_training_page_separates_diagnostic_and_publish_ready_states():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "诊断编译：不进入正式发布" in html
    assert "PUBLISH_READY" in html
    assert "发布就绪：等待管理员确认" in html


def test_training_page_keeps_historical_configuration_fields_for_audit():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    for field in (
        "min_eligible_samples_per_task",
        "min_new_samples",
        "min_growth_ratio",
        "max_wait_hours",
        "daily_gpu_budget_minutes",
        "device_pool",
    ):
        assert field in html


def test_training_page_requires_real_contract_policy_and_dataset_bindings():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "dataset_category_id: 1" not in html
    assert 'label_set_signature: "0".repeat(64)' not in html
    assert 'class_order_signature: "0".repeat(64)' not in html
    for field_id in (
        "autoTrainingDatasetCategoryId",
        "autoTrainingLabelSetSignature",
        "autoTrainingClassOrderSignature",
        "autoTrainingQualityPolicyApprovalId",
        "autoTrainingValidationDatasetId",
        "autoTrainingBenchmarkDatasetId",
        "autoTrainingSealedDatasetId",
        "autoTrainingCalibrationDatasetId",
        "autoTrainingEdgeDatasetId",
    ):
        assert f'id="{field_id}"' in html
    assert "/annotation-taxonomy/dataset-categories" in html
    assert "/quality-policy-approvals" in html
    assert "/training-evaluation-datasets" in html


def test_training_page_keeps_quality_history_without_rendering_write_actions():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "/auto-training-rounds/" in html
    assert "/quality" in html
    assert 'id="autoTrainingPlanDetail"' in html
    assert 'id="autoTrainingPerLabelQuality"' in html
    assert "technical_stage_flags" in html
    assert "var diagnostic = '';" in html
    assert 'id="btnCreateAutoTrainingPlan" hidden' in html
    assert 'id="btnCreateEvaluationDataset" hidden' in html
    assert 'id="btnSaveAutoTrainingSettings" hidden' in html


def test_auto_training_workspace_keeps_hidden_panels_hidden_and_text_readable():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert ".auto-training-toolbar[hidden]" in html
    assert ".auto-training-detail[hidden]" in html
    assert "background: #ffffff;" in html
    assert 'body[data-lu-page="yolo-training"] .auto-training-policy-grid input' in html
    assert "#autoTrainingPlanTable { min-width: 1180px; font-size: 14px; }" in html
    assert "#autoTrainingPlanTable th:last-child" in html
    assert "position: sticky;" in html


def test_auto_training_workspace_keeps_historical_management_surfaces_read_only():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    for workspace in ("plans", "datasets", "approvals", "settings"):
        assert f'data-auto-workspace="{workspace}"' in html
        assert f'data-auto-workspace-panel="{workspace}"' in html
    for field_id in (
        "autoTrainingRepresentativeTaskId",
        "evaluationDatasetEditor",
        "evaluationSamplePicker",
        "qualityPolicyApprovalTbody",
        "autoTrainingRuntimeSwitches",
        "autoTrainingReasonDialog",
    ):
        assert f'id="{field_id}"' in html
    assert 'id="autoTrainingLabelSetSignature"' in html and "readonly" in html
    assert 'id="autoTrainingDevicePool"' not in html
    assert "/edge-devices" not in html
    assert "自动分配环境就绪的空闲设备" in html
    assert 'body[data-lu-page="yolo-training"] .auto-training-workspace-panel > .train-list-section-head' in html
    assert "grid-template-columns: minmax(0, 1fr) auto" in html
    assert "/quality-policy-approvals/submit-current" in html
    assert "/auto-training-settings" in html
    assert "开启时自动补齐前置项" in html
    assert "applyAutoTrainingRuntimeDependencyChange" in html
    assert "一期只读保留历史配置，不能修改运行开关。" in html
    assert "选择全部已完成" in html
    assert "尚未扫描" in html


def test_evaluation_picker_loads_selected_task_with_clear_states():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'await loadEvaluationSamplePage(0, taskId)' in html
    assert 'renderEvaluationSampleState("正在加载图片…")' in html
    assert 'renderEvaluationSampleState("图片加载失败，请重试")' in html
    assert 'renderEvaluationSampleState("暂无匹配的来源任务")' in html
    assert "评估每轮训练质量" in html
    assert "固定样本，跨轮对比" in html
    assert "INT8 量化校准" in html
    assert "批准后冻结，不纳入训练池" in html
    assert "fmtTime(" not in html


def test_evaluation_dataset_editor_keeps_form_controls_readable():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    control_selector = (
        'body[data-lu-page="yolo-training"] #evaluationDatasetEditor '
        ".auto-training-compact-form input:not([type=\"checkbox\"]),"
    )
    checkbox_selector = (
        'body[data-lu-page="yolo-training"] #evaluationDatasetEditor '
        ".auto-training-compact-form .auto-training-check input"
    )
    assert control_selector in html
    assert checkbox_selector in html
    assert "background: #ffffff;" in html
    assert "color: #203247;" in html
    assert "color-scheme: light;" in html
    assert "width: 16px;" in html
    assert "min-height: 16px;" in html


def test_evaluation_dataset_editor_can_auto_generate_all_five_datasets():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'id="btnAutoGenerateEvaluationDatasets"' in html
    assert "autoGenerateEvaluationDatasetsFromEditor" in html
    assert 'api("/training-evaluation-datasets/auto-generate"' in html
    assert "五类评测集已自动选图并完成审批" in html
    assert "已复用完整的已审批评测集组合" in html
    assert "系统自动选图并审批中" in html


def test_quality_approval_uses_one_table_with_on_demand_history():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "质量策略审批" in html
    assert 'id="qualityPolicyApprovalTbody"' in html
    assert 'id="qualityApprovalHistoryTbody"' not in html
    assert "data-quality-history" in html
    assert "qualityHistoryExpanded" in html
    assert 'class="data-table auto-training-approval-table"' in html
    assert ".auto-training-approval-table { min-width: 760px; }" in html


def test_plan_editor_has_visible_return_to_list_action():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'id="autoTrainingPlanListView"' in html
    assert 'id="btnCloseAutoTrainingPlanEditor"' in html
    assert html.count("返回计划列表") >= 2
    assert "function setAutoTrainingPlanEditorVisible" in html
    assert "#autoTrainingPlanListActions[hidden] { display: none !important; }" in html
    assert "document.getElementById(\"autoTrainingPlanListView\").hidden = visible" in html
    assert "document.getElementById(\"btnCloseAutoTrainingPlanEditor\").onclick = closeAutoTrainingPlanEditor" in html


def test_plan_editor_uses_three_step_guided_flow():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    for step, label in ((1, "选择训练对象"), (2, "自动检查"), (3, "确认创建")):
        assert f'data-auto-plan-step-indicator="{step}"' in html
        assert f'data-auto-plan-step="{step}"' in html
        assert label in html
    assert 'id="btnPreviousAutoTrainingStep"' in html
    assert 'id="btnNextAutoTrainingStep"' in html
    assert "function setAutoTrainingEditorStep(step)" in html
    assert "autoTrainingBasicValidation()" in html
    assert "autoTrainingReadinessValidation()" in html


def test_plan_editor_supports_single_and_batch_representative_task_selection():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'id="autoTrainingRepresentativeTaskId" multiple hidden' in html
    assert 'id="autoTrainingTaskSearch"' in html
    assert 'id="autoTrainingTaskList"' in html
    assert 'id="btnSelectAllAutoTrainingTasks"' in html
    assert 'id="btnClearAutoTrainingTasks"' in html
    assert "selectedRepresentativeTaskIds" in html
    assert "autoTrainingTasksShareContract" in html
    assert 'admission.history_task_ids = historyTaskIds' in html
    assert "已选任务将作为计划的初始训练范围" in html
    assert 'id="autoTrainingHistoryTaskIds"' not in html
    assert "历史任务 ID" not in html


def test_plan_editor_auto_matches_unique_dependencies_and_links_missing_configuration():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "approvedPolicies.length === 1" in html
    assert "compatible.length === 1" in html
    assert "没有已批准策略" in html
    assert "没有匹配的已批准数据集" in html
    assert 'data-auto-training-config-target="approvals"' in html
    assert 'data-auto-training-config-target="datasets"' in html
    assert "switchAutoTrainingWorkspace(button.getAttribute(\"data-auto-training-config-target\"))" in html


def test_new_plan_auto_creates_evaluation_datasets_without_configuration_gate():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "var autoCreate = !autoTrainingPlanState.editingPlanId;" in html
    assert 'var autoCreatePlaceholder = "' in html
    assert r"\u4fdd\u5b58\u8ba1\u5212" in html
    assert 'select.disabled = true;' in html
    assert 'if (configButton) configButton.hidden = true;' in html
    assert '["autoTrainingQualityPolicyApprovalId"].concat(evaluationDatasetIds)' in html


def test_plan_editor_requires_explicit_confirmation_of_editable_recommendations():
    html = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "高级设置（推荐值可编辑）" in html
    assert 'id="autoTrainingConfigConfirmed"' in html
    assert "我已检查并确认以上配置" in html
    assert "请确认计划配置后再保存" in html
    assert "markAutoTrainingConfigUnconfirmed" in html
    assert 'data-auto-training-advanced-fields' in html
    assert 'advancedFields.addEventListener("input", markAutoTrainingConfigUnconfirmed)' in html
    assert "保存后还需提交审批" in html
    assert "必须由管理员确认" in html
