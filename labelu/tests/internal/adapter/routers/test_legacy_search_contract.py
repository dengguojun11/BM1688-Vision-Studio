"""Search contracts for the legacy taxonomy and publication extensions."""

from labelu.internal.common.config import settings
from labelu.internal.application.service import yolo_aicm_publish as publish_service


def _create_label(client, headers, *, key, value, color):
    response = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=headers,
        json={
            "label_key": key,
            "label_value": value,
            "annotation_kind": "bbox",
            "color": color,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_taxonomy_extension_lists_search_and_page(
    client, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        key="车辆",
        value="vehicle_search_contract",
        color="#112233",
    )
    preset = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "vehicleSearchPreset",
            "name": "车辆搜索预设",
            "items": [
                {
                    "label_key": "车辆",
                    "label_value": "vehicle_search_contract",
                    "color": "#112233",
                }
            ],
        },
    )
    assert preset.status_code == 201, preset.text

    alias = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-aliases",
        headers=testadmin_token_headers,
        json={
            "source_system": "external_detector",
            "external_label_value": "car_external",
            "label_id": label["id"],
        },
    )
    assert alias.status_code == 201, alias.text

    recipe = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/training-recipes",
        headers=testadmin_token_headers,
        json={
            "recipe_key": "vehicleSearchRecipe",
            "name": "车辆检测搜索配方",
            "job_type": "yolo_detection",
            "model_family": "yolov11_search",
            "annotation_kind": "bbox",
        },
    )
    assert recipe.status_code == 201, recipe.text

    runtime = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/runtime-templates",
        headers=testadmin_token_headers,
        json={
            "template_key": "vehicleSearchRuntime",
            "name": "车辆发布运行模板",
            "environment_id": "search-env",
            "runtime_profile": "vehicle_search_runtime",
            "aicm_model_set_id": "12345",
            "expected_model_alg_type": "vehicle_detection",
        },
    )
    assert runtime.status_code == 201, runtime.text

    cases = (
        (
            "/annotation-taxonomy/label-presets",
            "车辆",
            "name",
        ),
        (
            "/annotation-taxonomy/label-aliases",
            "EXTERNAL_DETECTOR",
            "source_system",
        ),
        (
            "/annotation-taxonomy/training-recipes",
            "YOLOV11_SEARCH",
            "model_family",
        ),
        (
            "/annotation-taxonomy/runtime-templates",
            "12345",
            "aicm_model_set_id",
        ),
    )
    for path, keyword, field in cases:
        response = client.get(
            f"{settings.API_V1_STR}{path}",
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 1},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"] == {"total": 1, "page": 0, "size": 1}
        assert keyword.casefold() in str(body["data"][0][field]).casefold()

    # A second matching recipe proves that total remains the filtered count,
    # while page/size controls the returned slice.
    second = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/training-recipes",
        headers=testadmin_token_headers,
        json={
            "recipe_key": "vehicleSearchRecipeSecond",
            "name": "车辆检测搜索配方二",
            "job_type": "yolo_detection",
            "model_family": "yolov11_search",
            "annotation_kind": "bbox",
        },
    )
    assert second.status_code == 201, second.text
    response = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/training-recipes",
        headers=testadmin_token_headers,
        params={"q": "yolov11_search", "page": 1, "size": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["meta_data"] == {"total": 2, "page": 1, "size": 1}


def test_publish_list_routes_forward_unified_search_query(
    client, testuser_token_headers, monkeypatch
):
    captured = {}

    def fake_publishable(db, current_user, **kwargs):
        captured["publishable"] = kwargs.get("search")
        return [], 0

    def fake_history(db, current_user, **kwargs):
        captured["history"] = kwargs.get("search")
        return [], 0

    monkeypatch.setattr(publish_service, "list_publishable", fake_publishable)
    monkeypatch.setattr(publish_service, "list_publish_history", fake_history)

    for path, key in (
        ("/yolo-aicm-publish/publishable", "publishable"),
        ("/yolo-aicm-publish/publish-jobs", "history"),
    ):
        response = client.get(
            f"{settings.API_V1_STR}{path}",
            headers=testuser_token_headers,
            params={"q": "车辆模型", "page": 0, "size": 10},
        )
        assert response.status_code == 200, response.text
        assert captured[key] == "车辆模型"

