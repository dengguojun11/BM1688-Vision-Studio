"""一期平台菜单、中文界面和弹窗约束的只读静态契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT.parent / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src"
PLATFORM = FRONTEND / "pages" / "platform"
VISIBLE_PAGES = PLATFORM / "pages"


def test_primary_navigation_has_only_the_six_first_class_modules():
    source = (PLATFORM / "modules.ts").read_text(encoding="utf-8")
    expected = ["workspace", "tasks", "training", "models", "evolution", "admin"]
    visible = source.split("export const PLATFORM_MODULES", 1)[1].split("/*", 1)[0]
    assert [f"key: '{key}'" for key in expected] == [
        line.strip().lstrip("{").strip().split(",", 1)[0]
        for line in visible.splitlines()
        if "key: '" in line
    ]
    for retired_label in ("部署中心", "设备发布", "柔性工作流", "项目空间", "Provider 注册"):
        assert retired_label not in visible


def test_visible_pages_do_not_reintroduce_project_scope_or_old_drawer_markup():
    files = [
        VISIBLE_PAGES / name
        for name in ("workspace.tsx", "training.tsx", "models.tsx", "evolution.tsx", "admin.tsx")
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "usePlatformProject" not in text
    assert "projectQueryKey" not in text
    assert "project_id" not in text
    assert "<Drawer" not in text
    assert "drawer-panel" not in text
    assert ".ant-drawer" not in text


def test_ingestion_page_uses_account_scope_and_centered_modals():
    source = (VISIBLE_PAGES / "ingestion.tsx").read_text(encoding="utf-8")
    for forbidden in ("usePlatformProject", "projectQueryKey", "project_id", "当前项目"):
        assert forbidden not in source
    assert "needsProject={false}" in source
    assert "<Modal" in source
    assert "centered" in source


def test_legacy_task_page_does_not_expose_project_assignment_controls():
    source = (FRONTEND / "pages" / "tasks" / "index.tsx").read_text(encoding="utf-8")
    assert "assignTasksToProject" not in source
    assert "project-assignment" not in source
    assert "归属项目" not in source
    assert "projectsQuery" not in source

    service = (FRONTEND / "api" / "services" / "task.ts").read_text(encoding="utf-8")
    assert "assignTasksToProject" not in service
    assert "/v1/tasks/project-assignment" not in service


def test_legacy_training_page_does_not_forward_project_scope():
    source = (ROOT.parent / "frontend" / "extensions" / "pages" / "yolo_training.html").read_text(encoding="utf-8")
    assert "get(\"project_id\")" not in source
    assert 'return "&project_id=' not in source


def test_role_and_model_actions_are_explicitly_present_in_chinese_ui():
    admin = (VISIBLE_PAGES / "admin.tsx").read_text(encoding="utf-8")
    models = (VISIBLE_PAGES / "models.tsx").read_text(encoding="utf-8")
    training = (VISIBLE_PAGES / "training.tsx").read_text(encoding="utf-8")
    assert "管理人员" in admin and "标注人员" in admin
    assert "发布模型" in models and "下载模型" in models
    assert "新建训练作业" in training and "开始训练" in training
    assert "只有系统管理员" not in admin
    assert "英文值" not in admin and "分类标识（英文）" not in admin


def test_compatibility_redirect_removes_project_query_parameter():
    routes = (FRONTEND / "routes.tsx").read_text(encoding="utf-8")
    assert "params.delete('project_id')" in routes
    assert 'RetiredPlatformRoute target="/models"' in routes
    assert 'RetiredPlatformRoute target="/admin"' in routes
    assert "path: 'rules'" in routes and 'RetiredPlatformRoute target="/admin"' in routes
    assert "path: 'annotation-review'" in routes and 'RetiredPlatformRoute target="/evolution"' in routes


def test_shared_platform_surfaces_hide_project_compatibility_fields_from_users():
    resource = (PLATFORM / "ResourcePage.tsx").read_text(encoding="utf-8")
    notification = (PLATFORM / "NotificationCenter.tsx").read_text(encoding="utf-8")
    layout = (FRONTEND / "layouts" / "MainLayoutWithNavigation" / "index.tsx").read_text(encoding="utf-8")
    evidence = (PLATFORM / "DeviceSignatureEvidence.tsx").read_text(encoding="utf-8")

    for forbidden in ("当前项目", "项目权限", "项目成员", "暂无可访问项目"):
        assert forbidden not in resource
        assert forbidden not in notification
        assert forbidden not in evidence
    assert "project_id" not in notification
    assert "projectId" not in notification
    assert "project_id" not in layout
    assert "projectId" not in layout
    # The rule evidence endpoint still needs its historical project parameter;
    # it must remain an internal request field and never become visible copy.
    assert "project_id" in evidence
    assert "ruleApiPaths" in evidence
    assert "needsProject\n      ? projectQueryKey" in resource
    assert ": [queryKey, ...(pagination ? [page, size] : [])]" in resource
