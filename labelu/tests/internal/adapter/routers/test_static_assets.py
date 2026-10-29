from fastapi.testclient import TestClient

import labelu.main as labelu_main
from labelu.internal.common.labelu_frontend_integration import compress_static_assets


def test_hashed_static_asset_uses_gzip_and_long_cache(client: TestClient, tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    js = assets / "index-0f9fab4b.js"
    js.write_text("const value = 'payload';\n" * 200, encoding="utf-8")
    compress_static_assets(tmp_path)

    monkeypatch.setattr(
        labelu_main,
        "statics_built_asset_path",
        lambda name: js if name == "index-0f9fab4b.js" else None,
    )

    resp = client.get("/assets/index-0f9fab4b.js", headers={"Accept-Encoding": "gzip"})

    assert resp.status_code == 200
    assert resp.headers["content-encoding"] == "gzip"
    assert resp.headers["vary"] == "Accept-Encoding"
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_extension_asset_keeps_no_cache(client: TestClient, tmp_path, monkeypatch):
    asset = tmp_path / "task_list_yolo_train_link.js"
    asset.write_text("console.log('extension');", encoding="utf-8")
    monkeypatch.setattr(
        labelu_main,
        "extension_asset_path",
        lambda name: asset if name == "task_list_yolo_train_link.js" else None,
    )

    resp = client.get("/assets/task_list_yolo_train_link.js")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_training_observability_assets_are_registered(client: TestClient, tmp_path, monkeypatch):
    asset = tmp_path / "yolo_training_observability.js"
    asset.write_text("window.LabeluTrainingObservability = {};", encoding="utf-8")
    assert "yolo_training_observability.js" in labelu_main.EXTENSION_ASSET_FILES
    assert "yolo_training_observability.css" in labelu_main.EXTENSION_ASSET_FILES
    monkeypatch.setattr(
        labelu_main,
        "extension_asset_path",
        lambda name: asset if name == "yolo_training_observability.js" else None,
    )

    resp = client.get("/assets/yolo_training_observability.js")

    assert resp.status_code == 200
    assert "LabeluTrainingObservability" in resp.text


def test_retired_model_workflow_redirects_and_api_is_removed(client: TestClient):
    page_resp = client.get("/yolo-model-workflow?model_key=train_job%3A19", follow_redirects=False)
    assert page_resp.status_code == 307
    assert page_resp.headers["location"] == "/training"

    api_resp = client.get("/api/v1/yolo-model-workflow")
    assert api_resp.status_code == 404
