"""按算能 sophon-demo YOLOv8_plus_det Export Guide 导出 ONNX（opset=17、static batch=1）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger


def _try_patch_ultralytics_predict_once() -> bool:
    """在 _predict_once 返回值上增加 permute(0,2,1)，与官方 Export Guide 一致。"""
    try:
        import torch
        from ultralytics.nn import tasks as ul_tasks
    except ImportError:
        return False

    for cls_name in ("DetectionModel", "BaseModel"):
        cls = getattr(ul_tasks, cls_name, None)
        if cls is None or not hasattr(cls, "_predict_once"):
            continue
        if getattr(cls._predict_once, "_labelu_sophon_patched", False):
            return True
        orig = cls._predict_once

        def _patched(self, x, profile=False, visualize=False, embed=None):
            out = orig(self, x, profile=profile, visualize=visualize, embed=embed)
            if isinstance(out, torch.Tensor) and out.dim() == 3:
                return out.permute(0, 2, 1)
            return out

        _patched._labelu_sophon_patched = True  # type: ignore[attr-defined]
        cls._predict_once = _patched
        logger.info("Applied Sophon export patch on ultralytics.nn.tasks.{}", cls_name)
        return True
    return False


def export_yolo_to_sophon_onnx(
    weights_path: Path,
    *,
    imgsz: int = 640,
    dest_dir: Path,
    dest_basename: str = "model.onnx",
    dynamic: bool = False,
) -> Path:
    """
    导出 ONNX，参数对齐：
    https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/docs/YOLOv8_Export_Guide.md
    """
    from ultralytics import YOLO

    _try_patch_ultralytics_predict_once()
    dest_dir.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(weights_path))
    export_kwargs: dict[str, Any] = {
        "format": "onnx",
        "imgsz": int(imgsz),
        "simplify": True,
        "opset": 17,
        "dynamic": bool(dynamic),
    }
    if not dynamic:
        export_kwargs["batch"] = 1
    exported: Any = model.export(**export_kwargs)
    src = Path(str(exported))
    dest = dest_dir / dest_basename
    if src.resolve() != dest.resolve():
        dest.write_bytes(src.read_bytes())
    return dest
