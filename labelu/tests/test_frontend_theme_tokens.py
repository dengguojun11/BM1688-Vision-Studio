from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
THEME_PATH = REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src" / "styles" / "theme.json"
GLOBAL_VARIABLES_PATH = (
    REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src" / "styles" / "global-variables.css"
)
TASK_CARD_PATH = (
    REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src" / "pages" / "tasks" / "components" / "taskCard" / "index.tsx"
)
EXTENSION_PAGER_SCRIPT_PATH = REPO_ROOT / "frontend" / "extensions" / "assets" / "labelu_pager.js"
PUBLIC_PAGER_SCRIPT_PATH = REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "public" / "assets" / "labelu_pager.js"
EXTENSION_PAGER_STYLE_PATH = REPO_ROOT / "frontend" / "extensions" / "assets" / "labelu_toolkit_compact.css"
PUBLIC_PAGER_STYLE_PATH = (
    REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "public" / "assets" / "labelu_toolkit_compact.css"
)
YOLO_TRAINING_PAGE_PATH = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"
PLATFORM_ROOT = REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src" / "pages" / "platform"
PLATFORM_MODULES_PATH = PLATFORM_ROOT / "modules.ts"
PLATFORM_LAYOUT_PATH = PLATFORM_ROOT / "layout.tsx"
# Route entries contain JSX lazy-load fallbacks, so the canonical source is TSX.
PLATFORM_ROUTE_ENTRIES_PATH = PLATFORM_ROOT / "routeEntries.tsx"
LOGIN_PAGE_PATH = REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src" / "pages" / "login" / "index.tsx"
REGISTER_PAGE_PATH = REPO_ROOT / "frontend" / "labelU-Kit" / "apps" / "frontend" / "src" / "pages" / "register" / "index.tsx"


def _load_theme_token() -> dict[str, str]:
    return json.loads(THEME_PATH.read_text(encoding="utf-8"))["token"]


def _load_css_variables() -> dict[str, str]:
    content = GLOBAL_VARIABLES_PATH.read_text(encoding="utf-8")
    return dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", content))


def test_theme_does_not_pin_low_contrast_text_tokens():
    token = _load_theme_token()

    assert "colorText" not in token
    assert "colorTextSecondary" not in token
    assert "colorTextTertiary" not in token
    assert "colorTextQuaternary" not in token


def test_generated_css_variables_follow_merged_theme_seed():
    variables = _load_css_variables()

    assert variables["color-primary-text"] == "#0d53de"
    assert variables["color-success-text"] == "#00b365"
    assert variables["color-warning-text"] == "#ff8800"
    assert variables["color-text-tertiary"] == "rgba(18, 19, 22, 0.45)"


def test_task_card_badges_use_active_contrast_colors():
    content = TASK_CARD_PATH.read_text(encoding="utf-8")

    assert "var(--color-warning-active)" in content
    assert "var(--color-success-active)" in content
    assert "var(--color-error-active)" in content
    assert "var(--color-warning-text)" not in content
    assert "var(--color-success-text)" not in content
    assert "var(--color-error-text)" not in content


def test_extension_pager_uses_dedicated_button_classes():
    extension_script = EXTENSION_PAGER_SCRIPT_PATH.read_text(encoding="utf-8")
    public_script = PUBLIC_PAGER_SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'class="lu-pager-btn lu-pager-prev"' in extension_script
    assert 'class="lu-pager-btn lu-pager-next"' in extension_script
    assert 'class="lu-pager-btn lu-pager-jump-btn"' in extension_script
    assert 'class="btn lu-pager-prev"' not in extension_script
    assert 'class="btn lu-pager-next"' not in extension_script
    assert 'class="btn lu-pager-jump-btn"' not in extension_script

    assert 'class="lu-pager-btn lu-pager-prev"' in public_script
    assert 'class="lu-pager-btn lu-pager-next"' in public_script
    assert 'class="lu-pager-btn lu-pager-jump-btn"' in public_script
    assert 'class="btn lu-pager-prev"' not in public_script
    assert 'class="btn lu-pager-next"' not in public_script
    assert 'class="btn lu-pager-jump-btn"' not in public_script


def test_extension_pager_styles_pin_readable_text_and_disabled_states():
    extension_css = EXTENSION_PAGER_STYLE_PATH.read_text(encoding="utf-8")
    public_css = PUBLIC_PAGER_STYLE_PATH.read_text(encoding="utf-8")

    for content in (extension_css, public_css):
        assert "body.labelu-shell .lu-pager {" in content
        assert "color: rgba(18, 19, 22, 0.76);" in content
        assert "body.labelu-shell .lu-pager-meta," in content
        assert "body.labelu-shell .lu-pager-page {" in content
        assert "color: rgba(18, 19, 22, 0.82);" in content
        assert "body.labelu-shell .lu-pager-size-label," in content
        assert "body.labelu-shell .lu-pager-jump-label {" in content
        assert "color: rgba(18, 19, 22, 0.72);" in content
        assert "body.labelu-shell .lu-pager-btn {" in content
        assert "background: #f8fafc;" in content
        assert "body.labelu-shell .lu-pager-btn:disabled {" in content
        assert "opacity: 1;" in content
        assert "background: #f3f4f6;" in content
        assert "color: rgba(18, 19, 22, 0.4);" in content


def test_yolo_training_dark_banner_pins_light_foreground_text():
    content = YOLO_TRAINING_PAGE_PATH.read_text(encoding="utf-8")

    assert "body[data-lu-page=\"yolo-training\"] .train-hub-banner .train-hub-title" in content
    assert "body[data-lu-page=\"yolo-training\"] .train-hub-banner .train-hub-sub" in content
    assert "rgba(241, 245, 249, 0.96)" in content
    assert "rgba(203, 213, 225, 0.82)" in content


def test_yolo_training_optional_tag_dimensions_do_not_block_list_bootstrap():
    content = YOLO_TRAINING_PAGE_PATH.read_text(encoding="utf-8")

    # Global tag dimensions are only needed by the create-training workflow;
    # loading them during bootstrap made legacy training-list pages fail when
    # an older deployment did not expose the optional endpoint.
    bootstrap_start = content.index("async function runBootstrapImpl()")
    bootstrap_end = content.index("var btnSearch", bootstrap_start)
    bootstrap = content[bootstrap_start:bootstrap_end]
    assert "await loadRectLabelPresets();" in bootstrap
    assert "Promise.all([loadRectLabelPresets(), loadSystemTrainingCatalogs()])" not in bootstrap

    dimensions_start = content.index('api("/annotation-taxonomy/global-tag-dimensions')
    dimensions_call = content[dimensions_start : dimensions_start + 420]
    assert ".catch(function ()" in dimensions_call


def test_7gai_platform_navigation_and_project_free_contract():
    modules = PLATFORM_MODULES_PATH.read_text(encoding="utf-8")
    layout = PLATFORM_LAYOUT_PATH.read_text(encoding="utf-8")
    route_entries = PLATFORM_ROUTE_ENTRIES_PATH.read_text(encoding="utf-8")
    task_card = TASK_CARD_PATH.read_text(encoding="utf-8")

    primary_modules = modules[
        modules.index("export const PLATFORM_MODULES") : modules.index(
            "export const INTERNAL_PLATFORM_MODULES"
        )
    ]
    primary_keys = re.findall(r"key: '([^']+)'", primary_modules)
    assert primary_keys == [
        "workspace",
        "tasks",
        "training",
        "models",
        "evolution",
        "admin",
    ]
    assert "key: 'deployments'" not in primary_modules
    assert "key: 'workflows'" not in primary_modules
    # Training is a first-class platform module and must keep its own active
    # navigation state instead of being folded into the algorithm center.
    assert "if (pathname.startsWith('/training')) return 'training';" in modules
    assert "onBreakpoint={(broken) => setCollapsed(broken)}" in layout
    assert "workspace: () => import('./pages/workspace')" in route_entries
    assert "annotationReview: () => import('./pages/annotationReview')" in route_entries
    assert "evolution: () => import('./pages/evolution')" in route_entries
    assert "deployments:" not in route_entries
    assert "workflows:" not in route_entries
    assert "project_id" not in task_card
    assert "未归属项目" not in task_card


def test_7gai_brand_is_consistent_on_auth_pages():
    for path in (LOGIN_PAGE_PATH, REGISTER_PAGE_PATH):
        content = path.read_text(encoding="utf-8")
        assert "7G EVOAI" not in content
        assert "PRODUCT_TITLE" in content
        assert "PRODUCT_TITLE_ZH" in content
