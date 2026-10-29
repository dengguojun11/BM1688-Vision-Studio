from labelu.internal.common.labelu_frontend_integration import (
    compress_static_assets,
    hashed_static_asset_cache_control,
    is_hashed_static_asset,
    is_backend_only_path,
    should_proxy_to_vite,
    statics_built_asset_path,
)


def test_statics_built_asset_path_rejects_traversal():
    assert statics_built_asset_path("../index.html") is None
    assert statics_built_asset_path("foo/bar.js") is None


def test_hashed_static_asset_detection():
    assert is_hashed_static_asset("index-0f9fab4b.js")
    assert is_hashed_static_asset("index-171dc47e.css")
    assert is_hashed_static_asset("video.es-5686d574-211f879a.js")
    assert not is_hashed_static_asset("task_list_yolo_train_link.js")
    assert not is_hashed_static_asset("labelu_toolkit_compact.css")


def test_hashed_static_asset_cache_control():
    assert hashed_static_asset_cache_control("index-0f9fab4b.js") == "public, max-age=31536000, immutable"
    assert hashed_static_asset_cache_control("task_list_yolo_train_link.js") == "no-cache"


def test_internal_routes_are_backend_only():
    assert is_backend_only_path("/internal/v1/experiment-datasets") is True
    assert is_backend_only_path("/internal/v1/strategy-evolution/tasks") is True


def test_retired_model_workflow_stays_on_backend(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.labelu_frontend_integration.integrated_frontend_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "labelu.internal.common.labelu_frontend_integration.kit_is_cloned",
        lambda: True,
    )

    assert is_backend_only_path("/yolo-model-workflow") is True
    assert should_proxy_to_vite("/yolo-model-workflow") is False


def test_compress_static_assets_creates_gzip_sidecar(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    js = assets / "index-0f9fab4b.js"
    js.write_text("const value = 'payload';\n" * 200, encoding="utf-8")
    plain_extension = assets / "task_list_yolo_train_link.js"
    plain_extension.write_text("console.log('extension');", encoding="utf-8")

    created = compress_static_assets(tmp_path)

    assert js.with_suffix(".js.gz") in created
    assert js.with_suffix(".js.gz").stat().st_size < js.stat().st_size
    assert not plain_extension.with_suffix(".js.gz").exists()
