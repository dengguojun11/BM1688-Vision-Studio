from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import traceback
from collections import OrderedDict
from pathlib import Path

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont

from inference import load_wedetect_proposals
from vlx_seek_worker import VLXSeekWorker


ROOT = Path(__file__).resolve().parent
MODEL_PATH = os.environ.get(
    "VLX_MODEL_PATH", "/home/ubuntu/vlx-seek-deploy/models/VLX-Seek-1.5-10B"
)
DETECTOR_PATH = os.environ.get(
    "VLX_DETECTOR_PATH", str(ROOT / "resources" / "wedetect_base_uni.pth")
)
MODEL_DEVICE = os.environ.get("VLX_DEVICE_MAP", "balanced")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app = FastAPI(title="VLX-Seek 1.5 Workbench", version="1.0.0")
state = {
    "phase": "starting",
    "message": "正在加载 VLX-Seek 1.5-10B",
    "started_at": time.time(),
    "loaded_at": None,
    "error": None,
}
worker: VLXSeekWorker | None = None
inference_lock = threading.Lock()
proposal_cache: OrderedDict[str, list[list[float]]] = OrderedDict()


def load_model() -> None:
    global worker
    try:
        state.update(phase="loading", message="正在将模型分配到两张 GPU", error=None)
        worker = VLXSeekWorker(MODEL_PATH, device=MODEL_DEVICE)
        state.update(
            phase="ready",
            message="模型已就绪",
            loaded_at=time.time(),
            error=None,
        )
    except Exception as exc:
        state.update(
            phase="error",
            message="模型加载失败",
            error=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()


@app.on_event("startup")
def startup() -> None:
    threading.Thread(target=load_model, name="vlx-model-loader", daemon=True).start()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    gpu = []
    if torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(index)
            gpu.append(
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "used_gb": round((total - free) / 1024**3, 1),
                    "total_gb": round(total / 1024**3, 1),
                }
            )
    return {**state, "model": "omlab/VLX-Seek-1.5-10B", "gpu": gpu}


def parse_boxes(raw: str, image: Image.Image) -> list[list[float]] | None:
    if not raw.strip():
        return None
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "候选框不是有效的 JSON") from exc
    if not isinstance(values, list) or len(values) > 100:
        raise HTTPException(400, "候选框必须是最多 100 项的列表")
    width, height = image.size
    boxes = []
    for value in values:
        if not isinstance(value, list) or len(value) != 4:
            raise HTTPException(400, "每个候选框必须为 [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (float(item) for item in value)
        x1, x2 = sorted((max(0.0, min(width, x1)), max(0.0, min(width, x2))))
        y1, y2 = sorted((max(0.0, min(height, y1)), max(0.0, min(height, y2))))
        if x2 <= x1 or y2 <= y1:
            raise HTTPException(400, "候选框裁剪后没有有效面积")
        boxes.append([x1, y1, x2, y2])
    return boxes


def get_font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/noto-cjk/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def annotate(image: Image.Image, boxes: list[dict]) -> str:
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    scale = max(1.0, max(canvas.size) / 1200)
    width = max(3, round(4 * scale))
    font = get_font(max(16, round(20 * scale)))
    colors = (
        (235, 79, 52, 255),
        (14, 132, 104, 255),
        (45, 105, 200, 255),
        (202, 128, 18, 255),
    )
    label_colors: dict[str, tuple[int, int, int, int]] = {}
    for index, box in enumerate(boxes):
        label_key = str(box.get("label", "target"))
        if label_key not in label_colors:
            label_colors[label_key] = colors[len(label_colors) % len(colors)]
        color = label_colors[label_key]
        rect = (box["xmin"], box["ymin"], box["xmax"], box["ymax"])
        draw.rectangle(rect, outline=color, width=width)
        label = f'{index + 1}  {box.get("label", "target")}'
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        label_width = right - left + 16
        label_height = bottom - top + 12
        label_y = max(0, int(box["ymin"]) - label_height)
        draw.rectangle(
            (box["xmin"], label_y, box["xmin"] + label_width, label_y + label_height),
            fill=color,
        )
        draw.text(
            (box["xmin"] + 8, label_y + 4), label, font=font, fill=(255, 255, 255, 255)
        )
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=92, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()


def clean_answer(answer: str) -> str:
    final_answer = re.search(r"<answer>(.*?)</answer>", answer, flags=re.DOTALL)
    if final_answer:
        answer = final_answer.group(1)
    answer = re.sub(r"<objects>.*?</objects>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"</?ground>", "", answer)
    answer = re.sub(r"</?(?:think|answer)>", "", answer)
    answer = re.sub(r"<obj\d+>", "", answer)
    return re.sub(r"\s+", " ", answer).strip(" .")


def get_proposals(image: Image.Image, payload: bytes) -> list[list[float]]:
    cache_key = hashlib.sha256(payload).hexdigest()
    cached = proposal_cache.get(cache_key)
    if cached is not None:
        proposal_cache.move_to_end(cache_key)
        return cached
    boxes = load_wedetect_proposals(image, DETECTOR_PATH)
    proposal_cache[cache_key] = boxes
    while len(proposal_cache) > 4:
        proposal_cache.popitem(last=False)
    return boxes


@app.post("/api/infer")
async def infer(
    image: UploadFile = File(...),
    task: str = Form("detection"),
    text: str = Form("person"),
    lang: str = Form("zh"),
    boxes_json: str = Form(""),
    target_indexes: str = Form("0"),
    max_new_tokens: int = Form(384),
) -> dict:
    if state["phase"] != "ready" or worker is None:
        raise HTTPException(503, state.get("message") or "模型尚未就绪")
    allowed = {
        "vqa",
        "detection",
        "grounding_single",
        "counting",
        "reasoning_detection",
        "brief_region_caption",
        "region_ocr",
    }
    if task not in allowed:
        raise HTTPException(400, "不支持的任务类型")
    if not text.strip() and task not in {"brief_region_caption", "region_ocr"}:
        raise HTTPException(400, "请输入问题或目标描述")
    payload = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "图片不能超过 20MB")
    try:
        source = Image.open(io.BytesIO(payload)).convert("RGB")
        source.load()
    except Exception as exc:
        raise HTTPException(400, "无法读取该图片") from exc
    if source.width * source.height > 40_000_000:
        raise HTTPException(400, "图片像素过大，请使用 4000 万像素以内的图片")

    boxes = parse_boxes(boxes_json, source)
    started = time.perf_counter()
    with inference_lock:
        try:
            if task != "vqa" and boxes is None:
                boxes = get_proposals(source, payload)
            kwargs = {
                "max_new_tokens": max(32, min(max_new_tokens, 1024)),
                "temperature": 0.0 if task != "vqa" else 0.2,
                "top_p": 1.0,
            }
            if task == "vqa":
                result = worker.predict(source, text.strip(), **kwargs)
            elif task == "detection":
                result = worker.detect(source, boxes, text.strip(), lang=lang, **kwargs)
            elif task == "grounding_single":
                result = worker.ground(source, boxes, text.strip(), lang=lang, **kwargs)
            elif task == "counting":
                result = worker.count(source, boxes, text.strip(), lang=lang, **kwargs)
            elif task == "reasoning_detection":
                result = worker.reasoning_detect(
                    source, boxes, text.strip(), lang=lang, **kwargs
                )
            else:
                try:
                    indexes = [int(item.strip()) for item in target_indexes.split(",")]
                except ValueError as exc:
                    raise HTTPException(400, "区域序号必须是逗号分隔的整数") from exc
                if task == "brief_region_caption":
                    result = worker.describe_region(
                        source, boxes, indexes, lang=lang, **kwargs
                    )
                else:
                    result = worker.read_region_text(
                        source, boxes, indexes, lang=lang, **kwargs
                    )
        except HTTPException:
            raise
        except torch.cuda.OutOfMemoryError as exc:
            torch.cuda.empty_cache()
            raise HTTPException(507, "GPU 显存不足，请缩小图片后重试") from exc
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, f"推理失败：{type(exc).__name__}: {exc}") from exc

    result_boxes = result.get("result_bbox_list", [])
    return {
        "task": task,
        "answer": clean_answer(result.get("answer", "")),
        "raw_answer": result.get("answer", ""),
        "boxes": result_boxes,
        "annotated_image": annotate(source, result_boxes),
        "prompt_tokens": result.get("prompt_tokens", 0),
        "completion_tokens": result.get("completion_tokens", 0),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "proposal_count": len(boxes or []),
    }
