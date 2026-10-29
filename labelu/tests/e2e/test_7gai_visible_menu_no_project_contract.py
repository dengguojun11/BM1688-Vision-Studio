from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / ".." / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src"


def test_default_navigation_has_six_business_modules_and_no_retired_controls() -> None:
    modules = (FRONTEND / "pages" / "platform" / "modules.ts").read_text(encoding="utf-8")
    routes = (FRONTEND / "routes.tsx").read_text(encoding="utf-8")
    expected = ["workspace", "tasks", "training", "models", "evolution", "admin"]
    assert "export const PLATFORM_MODULES" in modules
    assert modules.count("{ key: '") >= len(expected)
    for key in expected:
        assert f"key: '{key}'" in modules
    assert "PLATFORM_MODULES).toHaveLength" not in modules
    assert "path: 'deployments'" in routes and "RetiredPlatformRoute target=\"/models\"" in routes
    assert "path: 'workflows'" in routes and "RetiredPlatformRoute target=\"/admin\"" in routes


def test_visible_business_pages_do_not_load_project_scope_or_project_query_parameters() -> None:
    page_root = FRONTEND / "pages" / "platform" / "pages"
    visible_pages = {
        "workspace.tsx",
        "training.tsx",
        "models.tsx",
        "evolution.tsx",
        "admin.tsx",
    }
    for filename in visible_pages:
        source = (page_root / filename).read_text(encoding="utf-8")
        assert "usePlatformProject" not in source, filename
        assert "projectQueryKey" not in source, filename
        assert "project_id" not in source, filename
        assert "projectId" not in source, filename
