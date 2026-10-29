import asyncio
import base64
import io
import sys
import types

from model_server.locateanything.server import (
    DEFAULT_QUANTIZATION,
    LabelItem,
    app,
    _build_results_from_answer,
    _detect_for_labels,
    _download_image,
    _normalize_quantization,
    _parse_boxes,
    _resize_for_inference,
)
import time
from fastapi.testclient import TestClient
from PIL import Image


def test_download_image_accepts_base64_data_url():
    output = io.BytesIO()
    Image.new("RGB", (12, 8), color=(10, 20, 30)).save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")

    image = asyncio.run(_download_image(f"data:image/png;base64,{encoded}"))

    assert image.mode == "RGB"
    assert image.size == (12, 8)


def test_download_image_rejects_invalid_data_url():
    try:
        asyncio.run(_download_image("data:image/png;base64,not-valid-base64"))
    except ValueError as exc:
        assert "invalid base64" in str(exc)
    else:
        raise AssertionError("invalid base64 should be rejected")


def test_parse_boxes_from_locateanything_answer():
    answer = "Found target <box><100><200><500><700></box> and <box>0,1,2,3</box>."

    assert _parse_boxes(answer, image_size=(200, 100)) == [
        (20.0, 20.0, 100.0, 70.0),
        (0.0, 1.0, 2.0, 3.0),
    ]


def test_idle_unload_after_timeout(monkeypatch):
    class FakeWorker:
        pass

    fake = FakeWorker()
    monkeypatch.setattr("model_server.locateanything.server.worker", fake)
    monkeypatch.setattr("model_server.locateanything.server.gpu_idle_unload_seconds", 10)
    monkeypatch.setattr("model_server.locateanything.server._last_activity_ts", time.monotonic() - 20)

    from model_server.locateanything import server

    server._maybe_unload_idle_worker()
    assert server.worker is None


def test_health_shows_idle_unload_config(monkeypatch):
    monkeypatch.setattr("model_server.locateanything.server.gpu_idle_unload_seconds", 300)
    monkeypatch.setattr("model_server.locateanything.server.worker", None)
    monkeypatch.setattr("model_server.locateanything.server.quantization", "q4")

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["gpu_idle_unload_seconds"] == 300
    assert body["quantization"] == "q4"
    assert body["worker_loaded"] is False


def test_default_quantization_is_q4():
    assert DEFAULT_QUANTIZATION == "q4"
    assert _normalize_quantization("") == "q4"
    assert _normalize_quantization("4bit") == "q4"
    assert _normalize_quantization("none") == "none"


def test_ensure_worker_passes_quantization_when_supported(monkeypatch, tmp_path):
    from model_server.locateanything import server

    calls = []

    class FakeWorker:
        def __init__(self, model_name, quantization=None):
            calls.append((model_name, quantization))

    monkeypatch.setattr("model_server.locateanything.server.worker", None)
    monkeypatch.setattr("model_server.locateanything.server.install_root", str(tmp_path))
    monkeypatch.setattr("model_server.locateanything.server.embodied_dir", "missing")
    monkeypatch.setattr("model_server.locateanything.server.model_name", "model-path")
    monkeypatch.setattr("model_server.locateanything.server.quantization", "q4")
    monkeypatch.setitem(sys.modules, "locateanything_worker", type("M", (), {"LocateAnythingWorker": FakeWorker}))

    assert server._ensure_worker().__class__ is FakeWorker
    assert calls == [("model-path", "q4")]


def test_build_worker_injects_quantization_for_legacy_worker(monkeypatch):
    from model_server.locateanything import server

    calls = []

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(model_path, **kwargs):
            calls.append((model_path, kwargs))
            return object()

    class LegacyWorker:
        def __init__(self, model_path):
            FakeAutoModel.from_pretrained(model_path, torch_dtype="bf16", trust_remote_code=True)

    LegacyWorker.__module__ = "legacy_locateanything_worker"
    monkeypatch.setitem(
        sys.modules,
        "legacy_locateanything_worker",
        type("M", (), {"AutoModel": FakeAutoModel}),
    )
    quant_config = object()
    monkeypatch.setattr(
        "model_server.locateanything.server._quantization_from_pretrained_kwargs",
        lambda mode: {"quantization_config": quant_config, "device_map": "auto"},
    )

    server._build_worker(LegacyWorker, "model-path", "q4")

    assert calls == [
        (
            "model-path",
            {
                "trust_remote_code": True,
                "quantization_config": quant_config,
                "device_map": "auto",
            },
        )
    ]


def test_quantization_kwargs_requires_bitsandbytes(monkeypatch):
    from model_server.locateanything import server

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "bitsandbytes":
            raise ImportError("missing bitsandbytes")
        if name == "torch":
            return types.SimpleNamespace(bfloat16="bf16")
        if name == "transformers":
            return types.SimpleNamespace(BitsAndBytesConfig=lambda **kw: kw)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    try:
        server._quantization_from_pretrained_kwargs("q4")
    except RuntimeError as exc:
        assert "bitsandbytes" in str(exc)
    else:
        raise AssertionError("q4 should require bitsandbytes")


def test_unload_endpoint_clears_worker(monkeypatch):
    class FakeWorker:
        pass

    monkeypatch.setattr("model_server.locateanything.server.worker", FakeWorker())
    client = TestClient(app)
    response = client.post("/unload")
    assert response.status_code == 200
    assert response.json()["unloaded"] is True
    assert response.json()["worker_loaded"] is False


def test_build_results_clamps_boxes_and_filters_low_quality_boxes():
    labels = [LabelItem(name="garbage", tool="rectTool")]
    detections = {
        "garbage": "answer <box>-5, 10, 80, 60</box> <box>0, 0, 200, 100</box> <box>50, 60, 40, 70</box>",
    }

    results = _build_results_from_answer(detections, labels, image_size=(200, 100), max_per_label=10)

    assert len(results) == 1
    assert results[0].toolName == "rectTool"
    assert results[0].label == "garbage"
    assert results[0].result == {"x": 0.0, "y": 10.0, "width": 80.0, "height": 50.0}


def test_build_results_maps_inference_coordinates_back_to_original_image():
    labels = [LabelItem(name="garbage_street_dump", display_name="垃圾堆", tool="rectTool")]

    results = _build_results_from_answer(
        {"垃圾堆": "<box><100><200><500><600></box>"},
        labels,
        image_size=(4000, 3000),
        inference_size=(1536, 1152),
        max_per_label=100,
    )

    assert results[0].label == "garbage_street_dump"
    assert results[0].result == {"x": 400.0, "y": 600.0, "width": 1600.0, "height": 1200.0}


def test_build_results_only_suppresses_duplicate_and_container_boxes_within_same_label():
    labels = [LabelItem(name="garbage", tool="rectTool"), LabelItem(name="box", tool="rectTool")]

    results = _build_results_from_answer(
        {
            "garbage": "<box>10, 10, 50, 50</box> <box>8, 8, 54, 54</box>",
            "box": "<box>0, 0, 90, 70</box>",
        },
        labels,
        image_size=(200, 100),
        max_per_label=100,
    )

    assert [(item.label, item.result) for item in results] == [
        ("garbage", {"x": 10.0, "y": 10.0, "width": 40.0, "height": 40.0}),
        ("box", {"x": 0.0, "y": 0.0, "width": 90.0, "height": 70.0}),
    ]


def test_detect_for_labels_calls_worker_once_per_rect_label(monkeypatch):
    class FakeWorker:
        def __init__(self):
            self.calls = []

        def ground_multi(self, image, label_name, **kwargs):
            self.calls.append((label_name, kwargs))
            return {"answer": f"{label_name} <box><100><100><200><200></box>"}

    fake_worker = FakeWorker()
    monkeypatch.setattr("model_server.locateanything.server._ensure_worker", lambda: fake_worker)

    detections = _detect_for_labels(
        Image.new("RGB", (100, 80)),
        [
            LabelItem(name="trash_bag", display_name="垃圾袋", tool="rectTool"),
            LabelItem(name="person", tool="rectTool"),
            LabelItem(name="point", tool="pointTool"),
        ],
    )

    assert detections == {
        "垃圾袋": "垃圾袋 <box><100><100><200><200></box>",
        "person": "person <box><100><100><200><200></box>",
    }
    assert fake_worker.calls == [
        ("垃圾袋", {"temperature": 0.0, "verbose": False}),
        ("person", {"temperature": 0.0, "verbose": False}),
    ]


def test_predict_endpoint_returns_labelu_rect_results(monkeypatch):
    async def fake_download_image(url):
        return Image.new("RGB", (100, 80))

    def fake_detect_for_labels(image, labels):
        return {"garbage": "<box><100><250><500><875></box>"}

    monkeypatch.setattr("model_server.locateanything.server._download_image", fake_download_image)
    monkeypatch.setattr("model_server.locateanything.server._detect_for_labels", fake_detect_for_labels)

    client = TestClient(app)
    response = client.post(
        "/",
        json={
            "image_url": "http://example.test/image.jpg",
            "labels": [{"name": "garbage", "tool": "rectTool"}],
            "constraints": {"allowed_tools": ["rectTool"], "max_results_per_label": 100},
        },
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "toolName": "rectTool",
            "label": "garbage",
            "result": {"x": 10.0, "y": 20.0, "width": 40.0, "height": 50.0},
            "score": None,
        }
    ]


def test_predict_endpoint_resizes_for_inference_and_returns_original_coordinates(monkeypatch):
    seen_sizes = []

    async def fake_download_image(url):
        return Image.new("RGB", (4000, 3000))

    def fake_detect_for_labels(image, labels):
        seen_sizes.append(image.size)
        return {"garbage": "<box><100><200><500><600></box>"}

    monkeypatch.setattr("model_server.locateanything.server._download_image", fake_download_image)
    monkeypatch.setattr("model_server.locateanything.server._detect_for_labels", fake_detect_for_labels)

    client = TestClient(app)
    response = client.post(
        "/",
        json={
            "image_url": "http://example.test/large.jpg",
            "labels": [{"name": "garbage", "tool": "rectTool"}],
            "constraints": {"allowed_tools": ["rectTool"], "max_results_per_label": 100},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert seen_sizes == [(1536, 1152)]
    assert body["warning_message"] == "resized_for_inference 4000x3000 -> 1536x1152"
    assert body["results"][0]["result"] == {"x": 400.0, "y": 600.0, "width": 1600.0, "height": 1200.0}


def test_resize_for_inference_scales_large_image_and_results_map_to_original_space():
    image = Image.new("RGB", (4000, 3000))

    resized, message = _resize_for_inference(image, max_side=1536)
    labels = [LabelItem(name="garbage", tool="rectTool")]
    results = _build_results_from_answer(
        {"garbage": "<box><100><200><500><600></box>"},
        labels,
        image_size=image.size,
        inference_size=resized.size,
        max_per_label=100,
    )

    assert resized.size == (1536, 1152)
    assert message == "resized_for_inference 4000x3000 -> 1536x1152"
    assert results[0].result == {"x": 400.0, "y": 600.0, "width": 1600.0, "height": 1200.0}
