import re

from fastapi.testclient import TestClient

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _has_chinese(value) -> bool:
    return bool(CHINESE_PATTERN.search(str(value or "")))


def _operations(schema: dict):
    return [
        (path, method, operation)
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    ]


def test_chinese_openapi_covers_all_registered_http_interfaces(client: TestClient):
    response = client.get("/api-docs/openapi.json")

    assert response.status_code == 200, response.text
    schema = response.json()
    operations = _operations(schema)

    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "7GAI 中文 API 接口文档"
    assert schema["info"]["x-legacy-product"] == "LabelU"
    assert schema["info"]["x-document-language"] == "zh-CN"
    assert "Swagger 会自动添加 `Bearer` 前缀" in schema["info"]["description"]
    assert "LabelU API" not in schema["info"]["description"]
    assert "7GAI Evolution Platform" in schema["info"]["description"]
    assert schema["x-labelu-chinese-docs"] is True
    assert schema["x-seven-gai-chinese-docs"] is True
    assert len(schema["paths"]) >= 280
    assert len(operations) >= 350
    assert len(schema["tags"]) >= 22
    assert all(_has_chinese(item["name"]) for item in schema["tags"])
    assert all(_has_chinese(item["description"]) for item in schema["tags"])

    for path, method, operation in operations:
        assert operation["tags"], (method, path)
        assert all(_has_chinese(tag) for tag in operation["tags"]), (method, path)
        assert _has_chinese(operation["summary"]), (method, path)
        assert _has_chinese(operation["description"]), (method, path)
        assert operation["x-labelu-module-codes"], (method, path)
        for parameter in operation.get("parameters", []):
            assert _has_chinese(parameter.get("description")), (
                method,
                path,
                parameter.get("name"),
            )
        for status_code, payload in operation.get("responses", {}).items():
            assert _has_chinese(payload.get("description")), (
                method,
                path,
                status_code,
            )

    schemas = schema["components"]["schemas"]
    assert len(schemas) >= 440
    schema_names_by_casefold = {}
    for schema_name in schemas:
        folded_name = schema_name.casefold()
        assert folded_name not in schema_names_by_casefold, (
            schema_names_by_casefold[folded_name],
            schema_name,
        )
        schema_names_by_casefold[folded_name] = schema_name
    for schema_name, component in schemas.items():
        assert _has_chinese(component.get("description")), schema_name
        for field_name, field_schema in component.get("properties", {}).items():
            assert _has_chinese(field_schema.get("description")), (
                schema_name,
                field_name,
            )


def test_online_chinese_api_docs_pages_are_available(client: TestClient):
    swagger = client.get("/api-docs")
    redoc = client.get("/api-docs/redoc")
    legacy_docs = client.get("/docs")
    legacy_redoc = client.get("/redoc")
    legacy_openapi = client.get("/openapi.json")

    assert swagger.status_code == 200
    assert "7GAI 中文 API 接口文档" in swagger.text
    assert "/api-docs/openapi.json" in swagger.text
    assert '"tryItOutEnabled": true' in swagger.text
    assert '"persistAuthorization": true' in swagger.text
    assert "/api-docs/assets/swagger-ui-bundle.js" in swagger.text
    assert "/api-docs/assets/swagger-ui.css" in swagger.text
    assert "cdn.jsdelivr.net" not in swagger.text
    assert redoc.status_code == 200
    assert "/api-docs/openapi.json" in redoc.text
    assert "/api-docs/assets/redoc.standalone.js" in redoc.text
    assert "cdn.jsdelivr.net" not in redoc.text
    assert legacy_docs.status_code == 200
    assert "7GAI 中文 API 接口文档" in legacy_docs.text
    assert "/api-docs/openapi.json" in legacy_docs.text
    assert legacy_redoc.status_code == 200
    assert "7GAI 中文 API 接口文档" in legacy_redoc.text
    assert "/api-docs/openapi.json" in legacy_redoc.text
    assert legacy_openapi.status_code == 200
    assert legacy_openapi.json()["info"]["title"] == "7GAI 中文 API 接口文档"

    for asset_name in (
        "swagger-ui-bundle.js",
        "swagger-ui.css",
        "redoc.standalone.js",
    ):
        asset = client.get(f"/api-docs/assets/{asset_name}")
        assert asset.status_code == 200
        assert len(asset.content) > 100_000


def test_docs_routes_precede_root_spa_mount():
    from labelu.main import app

    paths = [getattr(route, "path", None) for route in app.routes]
    docs_index = paths.index("/docs")
    api_docs_index = paths.index("/api-docs")
    openapi_index = paths.index("/api-docs/openapi.json")
    root_mount_index = paths.index("")

    assert docs_index < root_mount_index
    assert api_docs_index < root_mount_index
    assert openapi_index < root_mount_index


def test_model_catalog_uses_separate_management_and_public_modules(
    client: TestClient,
):
    schema = client.get("/api-docs/openapi.json").json()

    assert schema["paths"]["/api/v1/published-models"]["get"]["tags"] == [
        "模型目录管理"
    ]
    assert schema["paths"]["/api/v1/public/models"]["get"]["tags"] == ["公开模型服务"]
    assert (
        schema["components"]["securitySchemes"]["HTTPBearer"]["bearerFormat"] == "JWT"
    )
    assert (
        "只填写令牌原文"
        in schema["components"]["securitySchemes"]["HTTPBearer"]["description"]
    )


def test_flexible_workflow_interfaces_share_one_business_module(
    client: TestClient,
):
    schema = client.get("/api-docs/openapi.json").json()

    representative_operations = (
        ("/api/v1/integrations/strategy-tasks", "post"),
        ("/internal/v1/strategy-evolution/threshold-experiments", "post"),
        ("/internal/v1/strategy-evolution/model-trainings", "post"),
        ("/api/v1/strategy-evolution/platform-callbacks", "post"),
        ("/api/v1/third-party-calls/summary", "get"),
    )
    for path, method in representative_operations:
        assert schema["paths"][path][method]["tags"] == ["柔性工作流"]

    flexible_workflow_tags = [
        tag for tag in schema["tags"] if tag["name"] == "柔性工作流"
    ]
    assert len(flexible_workflow_tags) == 1
    assert set(flexible_workflow_tags[0]["x-labelu-module-codes"]) == {
        "strategy_task_integration",
        "strategy_evolution",
        "third_party_calls",
    }
    assert {
        "策略任务接入",
        "策略进化",
        "第三方调用",
    }.isdisjoint({tag["name"] for tag in schema["tags"]})


def test_new_platform_domains_have_explicit_chinese_business_modules(
    client: TestClient,
):
    schema = client.get("/api-docs/openapi.json").json()

    expected_modules = {
        ("/api/v1/algorithm-groups", "get"): "算法注册",
        ("/api/v1/annotation-reviews", "get"): "标注审核",
        ("/api/v1/active-learning/batches", "post"): "主动学习",
        ("/api/v1/data-governance/upload-sessions", "post"): "数据治理",
        ("/api/v1/provider-jobs", "get"): "Provider作业",
        ("/api/v1/ingestion-jobs", "get"): "数据接入",
    }
    for (path, method), expected_module in expected_modules.items():
        assert schema["paths"][path][method]["tags"] == [expected_module]

    assert all(not tag["name"].startswith("其他接口（") for tag in schema["tags"])


def test_generated_chinese_summaries_and_schema_descriptions_are_natural(
    client: TestClient,
):
    schema = client.get("/api-docs/openapi.json").json()

    expected_summaries = {
        ("/api/v1/tasks/{task_id}/samples/export", "post"): "导出样本",
        ("/api/v1/datasources", "get"): "查询数据源列表",
        ("/api/v1/yolo-train/remote-nodes", "put"): "保存YOLO远程训练节点",
        ("/api/v1/yolo-quality-policies/activate", "post"): "启用YOLO质量策略",
        ("/third-party-calls", "get"): "获取第三方调用页面",
    }
    for (path, method), expected in expected_summaries.items():
        assert schema["paths"][path][method]["summary"] == expected

    assert (
        schema["components"]["schemas"]["UpdatePublishedModelCommand"]["description"]
        == "接口请求数据结构。"
    )


def test_retired_control_plane_contracts_are_hidden_from_phase_one_docs(
    client: TestClient,
):
    schema = client.get("/api-docs/openapi.json").json()
    paths = schema["paths"]
    agent_path = (
        "/api/v1/edge-agents/{agent_id}/rule-bindings/{binding_id}/signature-report"
    )
    project_audit_path = "/api/v1/project-ownership/audit"
    retired_rule_path = "/api/v1/rule-templates/device-signature-reports"
    retired_prefixes = (
        "/api/v1/projects",
        "/api/v1/organizations",
        "/api/v1/deployment-releases",
        "/api/v1/edge-agents",
        "/api/v1/edge-device-jobs",
        "/api/v1/board-agent",
        "/api/v1/rule-templates",
    )

    assert not [path for path in paths if path.startswith(retired_prefixes)]
    assert agent_path not in paths
    board_validation_path = "/api/v1/yolo-bm1688-exports/{export_id}/board-validation"
    assert {"get", "post"} <= set(paths[board_validation_path])
    assert project_audit_path not in paths
    assert retired_rule_path not in paths
    assert {"get", "post"} <= set(paths["/api/v1/edge-devices"])
    assert {"get", "post"} <= set(paths["/api/v1/yolo-edge-validation/jobs"])
    assert "ReportRuleSignatureCommand" not in schema["components"]["schemas"]
    assert "RuleDeviceSignatureReportResponse" not in schema["components"]["schemas"]
