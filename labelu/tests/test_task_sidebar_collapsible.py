from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_PAGE = (
    REPO_ROOT
    / "frontend"
    / "labelU-Kit"
    / "apps"
    / "frontend"
    / "src"
    / "pages"
    / "tasks"
    / "index.tsx"
)


def test_task_sidebar_sections_are_independently_collapsible_and_persisted():
    source = TASKS_PAGE.read_text(encoding="utf-8")

    assert "labelu:tasks-sidebar-sections:v2" in source
    assert "datasetCategories: true" in source
    assert "labels: false" in source
    assert "labels: parsed.labels === true" in source
    assert 'aria-controls="dataset-category-filter-items"' in source
    assert 'aria-controls="task-label-filter-items"' in source
    assert "aria-expanded={sidebarSections.datasetCategories}" in source
    assert "aria-expanded={sidebarSections.labels}" in source
    assert "hidden={!sidebarSections.datasetCategories}" in source
    assert "hidden={!sidebarSections.labels}" in source
    assert "persistTaskSidebarSectionState(next)" in source
    assert "<DownOutlined aria-hidden />" in source
    assert "<RightOutlined aria-hidden />" in source


def test_task_label_navigation_has_immediate_loading_feedback():
    source = TASKS_PAGE.read_text(encoding="utf-8")

    assert "const navigation = useNavigation();" in source
    assert "navigation.location?.pathname.match" in source
    assert "正在加载标签图片" in source
    assert "正在加载“{loadingLabelName}”标签图片" in source
    assert "disabled={loadingLabelId !== null}" in source
    assert "aria-busy={loadingLabelId === item.id}" in source
    assert 'role="status" aria-live="polite"' in source
    assert "width: 100%;" in source
    assert "box-sizing: border-box;" in source
