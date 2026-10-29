from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "frontend" / "extensions" / "pages" / "yolo_model_catalog.html"
PORTAL = ROOT / "frontend" / "extensions" / "assets" / "labelu_portal.js"
TRAINING = ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"


def test_model_catalog_route_and_menu_follow_model_publish():
    page = PAGE.read_text(encoding="utf-8")
    portal = PORTAL.read_text(encoding="utf-8")
    training = TRAINING.read_text(encoding="utf-8")

    assert 'data-lu-page="yolo-model-catalog"' in page
    assert portal.index('label: "模型发布"') < portal.index('label: "模型列表"')
    assert training.index("模型发布</button>") < training.index("模型列表</button>")
    assert 'href: "/yolo-model-catalog"' in portal
    assert 'catalog: "/yolo-model-catalog?embedded=1"' in portal


def test_model_catalog_page_exposes_management_and_download_workflows():
    source = PAGE.read_text(encoding="utf-8")

    assert 'id="filterQuery"' in source
    assert 'id="filterAvailability"' in source
    assert 'id="detailModal"' in source
    assert 'class="modal-bg"' in source
    assert 'class="drawer-bg"' not in source
    assert 'class="drawer"' not in source
    assert 'id="labelContractList"' in source
    assert 'id="packageDetailGrid"' in source
    assert "row.class_labels" in source
    assert "formatPrecision(row.precision)" in source
    assert 'detailItem("模型精度"' in source
    assert "待配置中文名" in source
    assert 'id="editDownloadEnabled"' in source
    assert 'data-act=\"download-toggle\"' in source
    assert "row.download_enabled !== false" in source
    assert 'id="aliasSource"' in source
    assert 'id="aliasId"' in source
    assert 'api("/published-models?"' in source
    assert '"/download"' in source
    assert "response.blob()" in source
    assert "headers.Authorization = token()" in source
    assert 'download_enabled: enabled' in source
    assert 'expected_download_enabled: downloadEnabled(selected)' in source
    assert 'id="editStatus"' not in source
    assert 'id="editPublic"' not in source
    assert "不会撤回 AICM 或其他平台已经取得的文件" in source
    assert 'askConfirm(' in source
    assert '"/aliases"' in source
    assert 'api("/users/me")' in source
    assert 'data.is_task_superuser !== false' in source
    assert 'id="catalogManagementSection"' in source
    assert '下载由管理人员控制' in source
    assert "body.labelu-embedded .catalog-page" in source
