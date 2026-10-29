"""Service probe contracts must win over the root SPA fallback."""


def test_root_service_probes_return_json_before_spa_mount(client):
    for path in ("/healthz", "/readyz", "/version"):
        response = client.get(path)
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert isinstance(body.get("data"), dict)
        assert body["data"].get("version")


def test_canonical_openapi_is_json_before_spa_mount(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["info"]["title"] == "7GAI 中文 API 接口文档"

