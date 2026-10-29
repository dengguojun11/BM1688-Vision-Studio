"""
LabelU Model Server - NVIDIA LocateAnything-3B

Adapter for the LabelU auto-label model API protocol:
  POST /  -> { request_id, image_url, labels, constraints, prompt }
  Returns -> { model, results: [{ toolName, label, result, score }], ... }

LocateAnything returns text containing normalized boxes like:
  <box><x1><y1><x2><y2></box>
This adapter converts those boxes to LabelU rectTool annotations.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gc
import inspect
import io
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field


MODEL_NAME = "nvidia/LocateAnything-3B"
DEFAULT_INSTALL_ROOT = "/data/ai/locateanything-3b"
DEFAULT_EMBODIED_DIR = "Eagle/Embodied"
DEFAULT_MAX_IMAGE_SIDE = 1536
DEFAULT_QUANTIZATION = "q4"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_BOX_AREA_RATIO = 0.65
MAX_EDGE_SPAN_RATIO = 0.82
CONTAINED_BOX_IOA_THRESHOLD = 0.9
NORMALIZED_BOX_PATTERN = re.compile(
    r"<box>\s*<(\d+)>\s*<(\d+)>\s*<(\d+)>\s*<(\d+)>\s*</box>",
    re.IGNORECASE,
)
CSV_BOX_PATTERN = re.compile(r"<box>\s*([0-9.,\s-]+?)\s*</box>", re.IGNORECASE)

app = FastAPI(title="LabelU LocateAnything-3B Model Server")

worker = None
model_name = MODEL_NAME
install_root = DEFAULT_INSTALL_ROOT
embodied_dir = DEFAULT_EMBODIED_DIR
max_image_side = int(os.getenv("LA_MAX_IMAGE_SIDE", str(DEFAULT_MAX_IMAGE_SIDE)))
quantization = os.getenv("LA_QUANTIZATION", DEFAULT_QUANTIZATION)
# 空闲超过 N 秒后卸载模型并释放 GPU 显存；0 表示不自动卸载（与 --preload 常驻行为一致）
gpu_idle_unload_seconds = int(os.getenv("LA_GPU_IDLE_UNLOAD_SECONDS", "300"))
_last_activity_ts = time.monotonic()
_idle_watchdog_started = False
_idle_watchdog_lock = threading.Lock()


class LabelItem(BaseModel):
    name: str
    display_name: str | None = None
    color: str | None = None
    tool: str


class Constraints(BaseModel):
    allowed_tools: list[str] = []
    max_results_per_label: int = 100
    filter_by_labels: bool = False


class PredictRequest(BaseModel):
    request_id: str = ""
    image_url: str
    task: dict[str, Any] = {}
    labels: list[LabelItem] = []
    constraints: Constraints = Field(default_factory=Constraints)
    prompt: str | None = None


class ResultItem(BaseModel):
    toolName: str
    label: str
    result: dict[str, float]
    score: float | None = None


class PredictResponse(BaseModel):
    model: str = MODEL_NAME
    latency_ms: int = 0
    results: list[ResultItem] = []
    warning_message: str | None = None


def _normalize_quantization(value: str | None) -> str:
    raw = (value or DEFAULT_QUANTIZATION).strip().lower()
    aliases = {
        "": DEFAULT_QUANTIZATION,
        "default": DEFAULT_QUANTIZATION,
        "q4": "q4",
        "4bit": "q4",
        "bnb4": "q4",
        "nf4": "q4",
        "q8": "q8",
        "8bit": "q8",
        "bnb8": "q8",
        "bf16": "none",
        "fp16": "none",
        "none": "none",
        "off": "none",
        "false": "none",
        "0": "none",
    }
    if raw not in aliases:
        raise ValueError("LA_QUANTIZATION must be one of: q4, q8, none")
    return aliases[raw]


quantization = _normalize_quantization(quantization)


async def _download_image(url: str) -> Image.Image:
    if url.lower().startswith("data:image/"):
        header, separator, encoded = url.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ValueError("image data URL must use base64 encoding")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("image data URL contains invalid base64") from exc
    else:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        payload = resp.content

    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the 20MB limit")
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
        return image.convert("RGB")
    except Exception as exc:
        raise ValueError("image payload is invalid") from exc


def _resize_for_inference(image: Image.Image, max_side: int) -> tuple[Image.Image, str | None]:
    width, height = image.size
    if max_side <= 0 or max(width, height) <= max_side:
        return image, None

    scale = max_side / float(max(width, height))
    target_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = image.resize(target_size, Image.Resampling.LANCZOS)
    return resized, f"resized_for_inference {width}x{height} -> {target_size[0]}x{target_size[1]}"


def _parse_boxes(answer: str, image_size: tuple[int, int] | None = None) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    width, height = image_size or (1000, 1000)
    for match in NORMALIZED_BOX_PATTERN.finditer(answer or ""):
        x1, y1, x2, y2 = (float(g) for g in match.groups())
        boxes.append(
            (
                x1 / 1000.0 * width,
                y1 / 1000.0 * height,
                x2 / 1000.0 * width,
                y2 / 1000.0 * height,
            )
        )
    for match in CSV_BOX_PATTERN.finditer(answer or ""):
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(p) for p in parts)
        except ValueError:
            continue
        boxes.append((x1, y1, x2, y2))
    return boxes


def _clamp_box(
    box: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> dict[str, float] | None:
    width, height = image_size
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return None
    left = max(0.0, min(float(width), x1))
    top = max(0.0, min(float(height), y1))
    right = max(0.0, min(float(width), x2))
    bottom = max(0.0, min(float(height), y2))
    rect_width = right - left
    rect_height = bottom - top
    if rect_width <= 0 or rect_height <= 0:
        return None
    return {
        "x": round(left, 3),
        "y": round(top, 3),
        "width": round(rect_width, 3),
        "height": round(rect_height, 3),
    }


def _build_results_from_answer(
    detections: dict[str, str],
    labels: list[LabelItem],
    image_size: tuple[int, int],
    max_per_label: int,
    inference_size: tuple[int, int] | None = None,
) -> list[ResultItem]:
    label_by_query = {_label_query_text(label).strip().lower(): label for label in labels}
    source_size = inference_size or image_size
    results: list[ResultItem] = []
    for raw_label, answer in detections.items():
        label_key = raw_label.strip().lower()
        label = label_by_query.get(label_key)
        if label is None:
            continue
        count = 0
        for box in _parse_boxes(answer, source_size):
            rect = _map_box_to_original(_clamp_box(box, source_size), source_size, image_size)
            if rect is None:
                continue
            if _is_low_quality_rect(rect, image_size):
                continue
            count += 1
            if count > max_per_label:
                break
            results.append(
                ResultItem(
                    toolName=label.tool or "rectTool",
                    label=label.name,
                    result=rect,
                )
            )
    return _suppress_duplicate_and_container_results(results, image_size)


def _map_box_to_original(
    rect: dict[str, float] | None,
    source_size: tuple[int, int],
    image_size: tuple[int, int],
) -> dict[str, float] | None:
    if rect is None:
        return None
    if source_size == image_size:
        return rect
    src_w, src_h = source_size
    dst_w, dst_h = image_size
    if src_w <= 0 or src_h <= 0:
        return None
    scale_x = float(dst_w) / float(src_w)
    scale_y = float(dst_h) / float(src_h)
    return _clamp_box(
        (
            rect["x"] * scale_x,
            rect["y"] * scale_y,
            (rect["x"] + rect["width"]) * scale_x,
            (rect["y"] + rect["height"]) * scale_y,
        ),
        image_size,
    )


def _rect_area(rect: dict[str, float]) -> float:
    return max(0.0, float(rect.get("width") or 0.0)) * max(0.0, float(rect.get("height") or 0.0))


def _is_low_quality_rect(rect: dict[str, float], image_size: tuple[int, int]) -> bool:
    image_w, image_h = image_size
    image_area = float(image_w * image_h)
    if image_area <= 0:
        return True
    area = _rect_area(rect)
    if area <= 0:
        return True
    if area / image_area > MAX_BOX_AREA_RATIO:
        return True
    if rect["width"] / float(image_w) > MAX_EDGE_SPAN_RATIO and (
        rect["x"] <= 1.0 or rect["x"] + rect["width"] >= image_w - 1.0
    ):
        return True
    if rect["height"] / float(image_h) > MAX_EDGE_SPAN_RATIO and (
        rect["y"] <= 1.0 or rect["y"] + rect["height"] >= image_h - 1.0
    ):
        return True
    return False


def _label_query_text(label: LabelItem) -> str:
    display_name = (label.display_name or "").strip()
    if display_name:
        return display_name
    return label.name.strip()


def _intersection_area(a: dict[str, float], b: dict[str, float]) -> float:
    left = max(float(a["x"]), float(b["x"]))
    top = max(float(a["y"]), float(b["y"]))
    right = min(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
    bottom = min(float(a["y"]) + float(a["height"]), float(b["y"]) + float(b["height"]))
    return max(0.0, right - left) * max(0.0, bottom - top)


def _suppress_duplicate_and_container_results(
    results: list[ResultItem],
    image_size: tuple[int, int],
) -> list[ResultItem]:
    kept: list[ResultItem] = []
    for item in sorted(results, key=lambda r: _rect_area(r.result)):
        area = _rect_area(item.result)
        if area <= 0:
            continue
        duplicate_or_container = False
        for other in kept:
            if item.label != other.label:
                continue
            inter = _intersection_area(item.result, other.result)
            other_area = _rect_area(other.result)
            if inter / min(area, other_area) >= CONTAINED_BOX_IOA_THRESHOLD:
                duplicate_or_container = True
                break
            if area > other_area and inter / other_area >= CONTAINED_BOX_IOA_THRESHOLD:
                duplicate_or_container = True
                break
        if duplicate_or_container:
            continue
        if not _is_low_quality_rect(item.result, image_size):
            kept.append(item)
    return kept


def _touch_activity() -> None:
    global _last_activity_ts
    _last_activity_ts = time.monotonic()


def _unload_worker() -> bool:
    """卸载模型并释放 GPU 显存；返回是否曾加载过 worker。"""
    global worker
    if worker is None:
        return False
    try:
        if hasattr(worker, "close"):
            worker.close()
        elif hasattr(worker, "unload"):
            worker.unload()
    except Exception:
        pass
    worker = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:
        pass
    return True


def _seconds_since_activity() -> float:
    return max(0.0, time.monotonic() - _last_activity_ts)


def _maybe_unload_idle_worker() -> None:
    if gpu_idle_unload_seconds <= 0 or worker is None:
        return
    if _seconds_since_activity() < float(gpu_idle_unload_seconds):
        return
    if _unload_worker():
        print(
            f"[locateanything] idle unload after {gpu_idle_unload_seconds}s without requests",
            flush=True,
        )


def _schedule_idle_watchdog() -> None:
    global _idle_watchdog_started
    if gpu_idle_unload_seconds <= 0:
        return
    with _idle_watchdog_lock:
        if _idle_watchdog_started:
            return
        _idle_watchdog_started = True

    def _tick() -> None:
        try:
            _maybe_unload_idle_worker()
        except Exception as exc:
            print(f"[locateanything] idle watchdog error: {exc}", flush=True)
        interval = max(15.0, min(60.0, float(gpu_idle_unload_seconds) / 4.0))
        timer = threading.Timer(interval, _tick)
        timer.daemon = True
        timer.start()

    threading.Thread(target=_tick, daemon=True, name="la-idle-watchdog").start()


def _quantization_from_pretrained_kwargs(mode: str) -> dict[str, Any]:
    mode = _normalize_quantization(mode)
    if mode == "none":
        return {}

    try:
        import torch
        import bitsandbytes  # noqa: F401
        from transformers import BitsAndBytesConfig
    except Exception as exc:
        raise RuntimeError(
            f"LocateAnything quantization={mode} requires transformers, torch and bitsandbytes support"
        ) from exc

    if mode == "q4":
        return {
            "quantization_config": BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            ),
            "device_map": "auto",
        }
    if mode == "q8":
        return {
            "quantization_config": BitsAndBytesConfig(load_in_8bit=True),
            "device_map": "auto",
        }
    raise ValueError(f"Unsupported LocateAnything quantization mode: {mode}")


def _worker_supports_quantization(worker_cls: type) -> bool:
    try:
        return "quantization" in inspect.signature(worker_cls).parameters
    except (TypeError, ValueError):
        return False


def _build_worker(worker_cls: type, model_path: str, mode: str):
    mode = _normalize_quantization(mode)
    if _worker_supports_quantization(worker_cls):
        return worker_cls(model_path, quantization=mode)
    if mode == "none":
        return worker_cls(model_path)

    worker_module = sys.modules.get(worker_cls.__module__)
    auto_model = getattr(worker_module, "AutoModel", None) if worker_module is not None else None
    original_from_pretrained = getattr(auto_model, "from_pretrained", None)
    if auto_model is None or original_from_pretrained is None:
        raise RuntimeError("LocateAnything worker does not expose AutoModel.from_pretrained for quantized loading")

    quantized_kwargs = _quantization_from_pretrained_kwargs(mode)

    def _from_pretrained_with_quantization(*args, **kwargs):
        kwargs.update(quantized_kwargs)
        if "quantization_config" in quantized_kwargs:
            kwargs.pop("torch_dtype", None)
        return original_from_pretrained(*args, **kwargs)

    auto_model.from_pretrained = _from_pretrained_with_quantization
    try:
        return worker_cls(model_path)
    finally:
        auto_model.from_pretrained = original_from_pretrained


def _ensure_worker():
    global worker
    _touch_activity()
    if worker is not None:
        return worker

    root = Path(install_root)
    embodied_path = root / embodied_dir
    if embodied_path.exists():
        sys.path.insert(0, str(embodied_path))

    try:
        from locateanything_worker import LocateAnythingWorker
    except Exception as exc:
        raise RuntimeError(
            "Cannot import LocateAnythingWorker. "
            f"Check install_root={install_root!r} and embodied_dir={embodied_dir!r}."
        ) from exc

    worker = _build_worker(LocateAnythingWorker, model_name, quantization)
    return worker


def _answer_to_text(result: Any) -> str:
    answer = result.get("answer") if isinstance(result, dict) else result
    return answer if isinstance(answer, str) else ""


def _detect_for_labels(image: Image.Image, labels: list[LabelItem]) -> dict[str, str]:
    active_labels = [_label_query_text(label) for label in labels if label.tool == "rectTool"]
    if not active_labels:
        return {}

    model_worker = _ensure_worker()
    detections: dict[str, str] = {}
    for label_name in active_labels:
        if hasattr(model_worker, "ground_multi"):
            result = model_worker.ground_multi(image, label_name, temperature=0.0, verbose=False)
        else:
            result = model_worker.detect(image, [label_name], temperature=0.0, verbose=False)
        answer = _answer_to_text(result)
        if answer:
            detections[label_name] = answer
    return detections


@app.post("/", response_model=PredictResponse)
async def predict(req: PredictRequest) -> PredictResponse:
    _touch_activity()
    start = time.perf_counter()
    allowed = set(req.constraints.allowed_tools) if req.constraints.allowed_tools else None
    if allowed and "rectTool" not in allowed:
        return PredictResponse(model=model_name, latency_ms=0, results=[])

    labels = [label for label in req.labels if label.tool == "rectTool"]
    if not labels:
        return PredictResponse(model=model_name, latency_ms=0, results=[])

    try:
        image = await _download_image(req.image_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to download image: {exc}")

    original_size = image.size
    inference_image, resize_message = _resize_for_inference(image, max_image_side)

    try:
        detections = _detect_for_labels(inference_image, labels)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LocateAnything inference failed: {exc}")

    results = _build_results_from_answer(
        detections,
        labels,
        image_size=original_size,
        inference_size=inference_image.size,
        max_per_label=req.constraints.max_results_per_label,
    )
    latency = int((time.perf_counter() - start) * 1000)
    return PredictResponse(model=model_name, latency_ms=latency, results=results, warning_message=resize_message)


@app.post("/unload")
async def unload_model():
    """手动卸载模型并释放 GPU（运维/训练前腾显存）。"""
    unloaded = _unload_worker()
    return {
        "status": "ok",
        "unloaded": unloaded,
        "worker_loaded": worker is not None,
    }


@app.get("/health")
async def health():
    idle_sec = round(_seconds_since_activity(), 1)
    unload_in = None
    if gpu_idle_unload_seconds > 0 and worker is not None:
        unload_in = max(0.0, float(gpu_idle_unload_seconds) - idle_sec)
    return {
        "status": "ok",
        "model": model_name,
        "worker_loaded": worker is not None,
        "install_root": install_root,
        "quantization": quantization,
        "max_image_side": max_image_side,
        "gpu_idle_unload_seconds": gpu_idle_unload_seconds,
        "seconds_since_last_request": idle_sec,
        "unload_in_seconds": round(unload_in, 1) if unload_in is not None else None,
    }


def main():
    global model_name, install_root, embodied_dir, max_image_side, gpu_idle_unload_seconds, quantization

    parser = argparse.ArgumentParser(description="LabelU LocateAnything-3B Model Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--install-root", default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--embodied-dir", default=DEFAULT_EMBODIED_DIR)
    parser.add_argument("--max-image-side", type=int, default=max_image_side)
    parser.add_argument(
        "--quantization",
        default=quantization,
        help="LocateAnything quantization mode: q4 (default), q8, none. Env: LA_QUANTIZATION",
    )
    parser.add_argument(
        "--gpu-idle-unload-seconds",
        type=int,
        default=gpu_idle_unload_seconds,
        help="Unload model after N seconds idle (0=never). Env: LA_GPU_IDLE_UNLOAD_SECONDS",
    )
    parser.add_argument("--preload", action="store_true", help="Load model before serving requests")
    args = parser.parse_args()

    model_name = args.model
    install_root = args.install_root
    embodied_dir = args.embodied_dir
    max_image_side = args.max_image_side
    quantization = _normalize_quantization(args.quantization)
    gpu_idle_unload_seconds = int(args.gpu_idle_unload_seconds)
    _schedule_idle_watchdog()
    if args.preload:
        _ensure_worker()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
