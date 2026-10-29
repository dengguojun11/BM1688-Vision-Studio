from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPOSITORY_ROOT / "deploy.sh"


def test_deploy_template_proxies_backend_only_routes_before_spa_fallback() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    expected_routes = (
        'for route in "/docs" "/redoc" "/openapi.json" "/healthz" '
        '"/readyz" "/version" "/api-docs" "/api-docs/redoc" '
        '"/api-docs/openapi.json"'
    )
    assert expected_routes in source
    assert 'deploy_write_nginx_proxy_location "^~ /api-docs/"' in source


def test_docs_proxy_routes_are_emitted_before_root_spa_location() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    route_block = source.index('for route in "/docs"')
    spa_block = source.index("  location / {", route_block)

    assert route_block < spa_block


def test_deploy_probe_rejects_html_fallback_for_backend_json_paths() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "deploy_probe_json_url_expect_200" in source
    assert 'for path in "/healthz" "/readyz" "/version" "/openapi.json"' in source
    assert '"/api-docs/openapi.json"' in source
    assert "application/json" in source
    assert "前端 HTML 当作服务就绪" in source


def test_deploy_finalize_enforces_canonical_openapi_route_contract() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'deploy_checklist_canonical_openapi "${port_note}"' in source
    assert "7gai_openapi_contract.json" in source
    assert "已退休路径仍暴露" in source
    assert '[[ "${DEPLOY_CHK_FAIL}" -eq 0 ]]' in source
