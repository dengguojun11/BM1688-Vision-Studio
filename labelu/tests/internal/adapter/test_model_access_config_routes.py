from labelu.main import app


def test_model_access_config_routes_are_registered_with_admin_mutations():
    routes = {
        (route.path, method)
        for route in app.routes
        if route.path.startswith("/api/v1/ai-model-configs")
        for method in (route.methods or set())
    }
    assert ("/api/v1/ai-model-configs", "GET") in routes
    assert ("/api/v1/ai-model-configs", "POST") in routes
    assert ("/api/v1/ai-model-configs/{config_id}", "PATCH") in routes
    assert ("/api/v1/ai-model-configs/{config_id}", "DELETE") in routes
    assert ("/api/v1/ai-model-configs/{config_id}/test", "POST") in routes
