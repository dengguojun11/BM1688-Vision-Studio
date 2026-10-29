from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PAGE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"


def test_iteration_modal_exposes_frozen_scope_and_submission_state():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'id="iterationScopePanel"' in source
    assert 'id="iterationScopeCategories"' in source
    assert 'id="iterationScopeLabels"' in source
    assert "function renderIterationScopePanel" in source
    assert "function setIterationSubmitting" in source
    assert 'classList.toggle("is-iteration"' in source
    assert 'btn.textContent = submitting ? "正在创建…"' in source
    assert 'detail.name || draft.trainingTaskName || draft.taskName' in source
    assert 'datasetCategoryCatalog.find' in source
    assert 'systemLabelCatalog.find' in source


def test_iteration_routes_task_dimension_jobs_back_to_task_mode():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "function resolveIterationCreateMode(row, taskDetail)" in source
    assert 'rowTrainingMode === CREATE_MODE_TASK' in source
    assert "Array.isArray(taskDetail.selected_label_ids)" in source
    assert "Array.isArray(taskDetail.source_tasks)" in source
    assert "return CREATE_MODE_TASK;" in source
    assert 'await applyTaskIterationDraftToCreateModal(draft)' in source
    assert "var iterationCreateMode = resolveIterationCreateMode(row, taskDetail);" in source
    assert "createMode: iterationCreateMode" in source
    assert "await openModal(iterationCreateMode);" in source


def test_label_iteration_restores_recipe_without_requiring_selected_label_ids():
    source = TRAINING_PAGE.read_text(encoding="utf-8")
    start = source.index("async function applyLabelIterationDraftToCreateModal(draft)")
    end = source.index("async function applyTaskIterationDraftToCreateModal(draft)", start)
    function_source = source[start:end]

    recipe_restore = function_source.index(
        'if (recipeSelect && detail && detail.training_recipe_key)'
    )
    selected_label_gate = function_source.index(
        "if (detail && Array.isArray(detail.selected_label_ids)"
    )

    assert recipe_restore < selected_label_gate
    assert (
        'recipeSelect.value = detail.training_recipe_key + "::" + '
        "(detail.training_recipe_version || 1);"
    ) in function_source
