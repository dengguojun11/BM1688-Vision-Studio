"""YOLO Ultralytics train() 可选超参：白名单校验与归一化（不允许覆盖 data/project/name 等）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 允许的键需与 ultralytics YOLO.train 参数名一致（节选常用可调项）
ALLOWED_TRAIN_HYPER_KEYS = frozenset(
    {
        "batch",
        "workers",
        "patience",
        "lr0",
        "lrf",
        "momentum",
        "weight_decay",
        "warmup_epochs",
        "warmup_momentum",
        "warmup_bias_lr",
        "optimizer",
        "cos_lr",
        "close_mosaic",
        "label_smoothing",
        "dropout",
        "freeze",
        "seed",
        "deterministic",
        "single_cls",
        "rect",
        "cache",
        "amp",
        "fraction",
        "hsv_h",
        "hsv_s",
        "hsv_v",
        "degrees",
        "translate",
        "scale",
        "shear",
        "perspective",
        "flipud",
        "fliplr",
        "mosaic",
        "mixup",
        "copy_paste",
        "erasing",
        "crop_fraction",
        "max_det",
        "iou",
        "conf",
        "save_period",
        "cutmix",
        "multi_scale",
        "box",
        "cls",
        "dfl",
    }
)

RUNTIME_EDITABLE_TRAIN_HYPER_KEYS = (
    "batch",
    "imgsz",
    "multi_scale",
    "amp",
    "workers",
    "patience",
    "epochs",
    "save_period",
    "close_mosaic",
    "hsv_h",
    "hsv_s",
    "hsv_v",
    "degrees",
    "translate",
    "scale",
    "flipud",
    "fliplr",
    "mosaic",
    "mixup",
    "copy_paste",
)

_RUNTIME_EDITABLE_TRAIN_HYPER_KEYS_SET = frozenset(RUNTIME_EDITABLE_TRAIN_HYPER_KEYS)

# 54.99 垃圾暴露 SOP（train.yaml）超参基线：供 AI 迭代提示词与「载入 SOP 基线」使用。
# 不含 save_period（平台保持 -1）、device/data/epochs/imgsz（由表单或 AI 单独建议）。
GARBAGE_EXPOSURE_RECT_PRESET_SLUG = "garbageExposure"

GARBAGE_EXPOSURE_SOP_HYPERPARAMS: Dict[str, Any] = {
    "batch": 4,
    "patience": 30,
    "workers": 8,
    "amp": True,
    "optimizer": "AdamW",
    "cos_lr": True,
    "rect": False,
    "multi_scale": True,
    "dropout": 0.1,
    "lr0": 0.0008,
    "lrf": 0.05,
    "momentum": 0.937,
    "weight_decay": 0.0003,
    "box": 5.5,
    "cls": 1.0,
    "dfl": 2.0,
    "mosaic": 0.7,
    "mixup": 0.05,
    "cutmix": 0.05,
    "degrees": 3.0,
    "flipud": 0.0,
    "fliplr": 0.5,
    "hsv_h": 0.02,
    "hsv_s": 0.5,
    "hsv_v": 0.3,
    "erasing": 0.2,
    "iou": 0.45,
    "conf": 0.25,
    "max_det": 800,
}

RECT_PRESET_ITERATION_BASELINES: Dict[str, Dict[str, Any]] = {
    GARBAGE_EXPOSURE_RECT_PRESET_SLUG: GARBAGE_EXPOSURE_SOP_HYPERPARAMS,
}


def default_train_patience(epochs: Any) -> int:
    """Return the platform early-stop patience when no explicit value is supplied."""
    try:
        epoch_count = max(1, int(float(epochs)))
    except (TypeError, ValueError):
        epoch_count = 100
    return min(100, max(30, (epoch_count * 3 + 9) // 10))


def protect_automatic_iteration_patience(
    hyperparams: Optional[Dict[str, Any]], *, epochs: Any
) -> Dict[str, Any]:
    """Raise only automatically applied iteration patience to the platform floor."""
    protected = normalize_hyperparams(hyperparams)
    floor = default_train_patience(epochs)
    raw = protected.get("patience")
    try:
        patience = int(float(raw)) if raw is not None else floor
    except (TypeError, ValueError):
        patience = floor
    if patience < floor:
        protected["patience"] = floor
    elif raw is None:
        protected["patience"] = floor
    return protected


def get_iteration_baseline_hyperparams(rect_preset_slug: Optional[str]) -> Dict[str, Any]:
    """按拉框预设返回迭代建议/SOP 超参基线（已归一化）。"""
    slug = (rect_preset_slug or "").strip()
    if not slug:
        return {}
    raw = RECT_PRESET_ITERATION_BASELINES.get(slug)
    if not raw:
        return {}
    return normalize_hyperparams(dict(raw))


def iteration_baseline_recommended_train_params(
    rect_preset_slug: Optional[str],
) -> Dict[str, Any]:
    """供提示词 JSON 示例：基础项 + 超参基线（含 epochs/imgsz 示意）。"""
    hp = get_iteration_baseline_hyperparams(rect_preset_slug)
    if not hp and not (rect_preset_slug or "").strip():
        return {}
    out: Dict[str, Any] = {
        "epochs": 200,
        "imgsz": 1280,
        **hp,
        "notes": "与 54.99 train.yaml 对齐的街道垃圾检测基线；指标已达标时可仅微调 lr/增强。",
    }
    return out


def normalize_hyperparams(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """过滤非法键并做简单类型归一化。"""
    if not raw:
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k not in ALLOWED_TRAIN_HYPER_KEYS or v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[k] = _coerce(k, v)
    return out


def normalize_runtime_editable_hyperparams(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """仅保留允许在 checkpoint 续训前调整的运行期超参。"""
    raw = raw or {}
    normalized = normalize_hyperparams(raw)
    out: Dict[str, Any] = {}
    for key in RUNTIME_EDITABLE_TRAIN_HYPER_KEYS:
        if key in ("epochs", "imgsz") and key in raw and raw[key] is not None:
            out[key] = _coerce(key, raw[key])
        elif key in normalized:
            out[key] = normalized[key]
    return out


def _coerce(key: str, v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        s = v.strip()
        low = s.lower()
        if low in ("true", "false"):
            return low == "true"
        try:
            if "." in s or "e" in low:
                return float(s)
            return int(s)
        except ValueError:
            return s
    if isinstance(v, list):
        return v
    return v


def unwrap_suggested_train_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    """从 AI 返回的 JSON 外壳中取出真正的 train 参数字典。

    提示词常见结构：{"recommended_train_params": {...}}、{"train": {...}}、扁平 ultralytics 键。
    """
    if not isinstance(raw, dict):
        return {}
    for key in ("train", "recommended_train_params", "suggested_params"):
        inner = raw.get(key)
        if isinstance(inner, dict):
            return inner
    return raw


def _load_suggested_train_dict(suggested: Optional[Any]) -> Dict[str, Any]:
    """解析 suggested_params JSON（或 dict）并 unwrap 为扁平 train 参数字典。"""
    if suggested is None:
        return {}
    import json

    if isinstance(suggested, dict):
        raw = suggested
    else:
        text = str(suggested).strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    inner = unwrap_suggested_train_dict(raw)
    return inner if isinstance(inner, dict) else {}


def merge_ai_suggested_into_hyperparams(suggested: Optional[str]) -> Dict[str, Any]:
    """解析评估报告中的 suggested_params（JSON 字符串或 dict），提取可调键。"""
    inner = _load_suggested_train_dict(suggested)
    if not inner:
        return {}
    return normalize_hyperparams(inner)


def merge_ai_suggested_into_iteration_params(
    suggested: Optional[Any],
) -> Dict[str, Any]:
    """解析 AI 迭代建议：含 ultralytics 超参白名单 + epochs/imgsz（自动续训专用）。"""
    inner = _load_suggested_train_dict(suggested)
    if not inner:
        return {}
    return merge_iteration_param_dicts(inner)


def merge_iteration_param_dicts(*layers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并多层参数字典：后者覆盖前者（典型顺序：全维度报告 → 下一次迭代建议）。"""
    merged: Dict[str, Any] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        for key, val in layer.items():
            if key in ("notes", "model_weights", "device"):
                continue
            if val is None or (isinstance(val, str) and not str(val).strip()):
                continue
            merged[key] = val
    if not merged:
        return {}
    out = normalize_hyperparams(merged)
    for key in ("epochs", "imgsz"):
        if key not in merged:
            continue
        out[key] = _coerce(key, merged[key])
    return out


# Ultralytics DEFAULT_CFG 中部分键为 None，表示 train() 不传该参数、由库内部处理
_OFFICIAL_NONE_FORM_EQUIVALENTS: Dict[str, Any] = {
    "label_smoothing": 0.0,
    "freeze": 0,
    "crop_fraction": 1.0,
}

# 无 ultralytics 时的静态兜底（与 DEFAULT_CFG_DICT 快照一致，含 null）
YOLO_OFFICIAL_FIELD_DEFAULTS_FALLBACK: Dict[str, Any] = {
    "batch": 16,
    "workers": 8,
    "patience": 100,
    "lr0": 0.01,
    "lrf": 0.01,
    "momentum": 0.937,
    "weight_decay": 0.0005,
    "warmup_epochs": 3.0,
    "warmup_momentum": 0.8,
    "warmup_bias_lr": 0.1,
    "optimizer": "auto",
    "cos_lr": False,
    "close_mosaic": 10,
    "label_smoothing": None,
    "dropout": 0.0,
    "freeze": None,
    "seed": 0,
    "deterministic": True,
    "single_cls": False,
    "rect": False,
    "cache": False,
    "amp": True,
    "fraction": 1.0,
    "hsv_h": 0.015,
    "hsv_s": 0.7,
    "hsv_v": 0.4,
    "degrees": 0.0,
    "translate": 0.1,
    "scale": 0.5,
    "shear": 0.0,
    "perspective": 0.0,
    "flipud": 0.0,
    "fliplr": 0.5,
    "mosaic": 1.0,
    "mixup": 0.0,
    "copy_paste": 0.0,
    "erasing": 0.4,
    "crop_fraction": None,
    "save_period": -1,
    "max_det": 300,
    "iou": 0.7,
    "conf": None,
    "cutmix": 0.0,
    "multi_scale": 0.0,
    "box": 7.5,
    "cls": 0.5,
    "dfl": 1.5,
}


def get_yolo_official_field_defaults() -> Dict[str, Any]:
    """Ultralytics YOLO.train() 官方 DEFAULT_CFG（仅白名单键）。"""
    try:
        from ultralytics.cfg import DEFAULT_CFG_DICT  # type: ignore
    except ImportError:
        return dict(YOLO_OFFICIAL_FIELD_DEFAULTS_FALLBACK)
    out: Dict[str, Any] = {}
    for key in ALLOWED_TRAIN_HYPER_KEYS:
        if key in DEFAULT_CFG_DICT:
            out[key] = DEFAULT_CFG_DICT[key]
    for key, val in YOLO_OFFICIAL_FIELD_DEFAULTS_FALLBACK.items():
        out.setdefault(key, val)
    return out


def official_default_for_form(key: str, raw: Any) -> Any:
    """将官方 None 转为表单可填写的等价展示值（提交时仍可按空字段省略）。"""
    if raw is not None:
        return raw
    if key in _OFFICIAL_NONE_FORM_EQUIVALENTS:
        return _OFFICIAL_NONE_FORM_EQUIVALENTS[key]
    return None


# LabelU 平台新建训练表单初始值（可与官方不同；workers/amp 由 hyperparams/迭代建议传入，勿依赖 .env）
YOLO_TRAIN_FIELD_DEFAULTS: Dict[str, Any] = {
    "batch": 16,
    "workers": 8,
    "patience": 100,
    "lr0": 0.01,
    "lrf": 0.01,
    "momentum": 0.937,
    "weight_decay": 0.0005,
    "warmup_epochs": 3.0,
    "warmup_momentum": 0.8,
    "warmup_bias_lr": 0.1,
    "optimizer": "auto",
    "cos_lr": False,
    "close_mosaic": 10,
    "label_smoothing": 0.0,
    "dropout": 0.0,
    "freeze": 0,
    "seed": 0,
    "deterministic": True,
    "single_cls": False,
    "rect": False,
    "cache": False,
    "amp": True,
    "fraction": 1.0,
    "hsv_h": 0.015,
    "hsv_s": 0.7,
    "hsv_v": 0.4,
    "degrees": 0.0,
    "translate": 0.1,
    "scale": 0.5,
    "shear": 0.0,
    "perspective": 0.0,
    "flipud": 0.0,
    "fliplr": 0.5,
    "mosaic": 1.0,
    "mixup": 0.0,
    "copy_paste": 0.0,
    "erasing": 0.4,
    "crop_fraction": 1.0,
    "save_period": -1,
    "max_det": 300,
    "iou": 0.7,
    "conf": 0.25,
}


def hyperparam_field_meta() -> List[Dict[str, Any]]:
    """供前端展示的中文说明（与 ALLOWED 键对应）。"""
    fields: List[Dict[str, Any]] = [
        {"key": "batch", "label": "批次大小 batch", "type": "int", "hint": "-1 表示自动"},
        {
            "key": "workers",
            "label": "数据加载进程数 workers",
            "type": "int",
            "hint": "单 GPU 按填写值生效（0-8）；大图训练最多 4；多 GPU DDP 为避免卡死固定为 0",
        },
        {"key": "patience", "label": "早停耐心值 patience", "type": "int", "hint": "验证无提升时等待的 epoch 数"},
        {"key": "lr0", "label": "初始学习率 lr0", "type": "float", "hint": ""},
        {"key": "lrf", "label": "最终学习率因子 lrf", "type": "float", "hint": "lr_final = lr0 * lrf"},
        {"key": "momentum", "label": "SGD 动量 momentum", "type": "float", "hint": ""},
        {"key": "weight_decay", "label": "权重衰减 weight_decay", "type": "float", "hint": ""},
        {"key": "warmup_epochs", "label": "预热轮数 warmup_epochs", "type": "float", "hint": ""},
        {"key": "warmup_momentum", "label": "预热动量 warmup_momentum", "type": "float", "hint": ""},
        {"key": "warmup_bias_lr", "label": "预热 bias 学习率 warmup_bias_lr", "type": "float", "hint": ""},
        {"key": "optimizer", "label": "优化器 optimizer", "type": "str", "hint": "如 auto / SGD / Adam / AdamW / RMSProp"},
        {"key": "cos_lr", "label": "余弦学习率 cos_lr", "type": "bool", "hint": ""},
        {"key": "close_mosaic", "label": "关闭 mosaic 的 epoch close_mosaic", "type": "int", "hint": "最后若干 epoch 关闭 mosaic"},
        {"key": "label_smoothing", "label": "标签平滑 label_smoothing", "type": "float", "hint": ""},
        {"key": "dropout", "label": "Dropout", "type": "float", "hint": ""},
        {"key": "freeze", "label": "冻结层数 freeze", "type": "int", "hint": "冻结 backbone 前几层"},
        {"key": "seed", "label": "随机种子 seed", "type": "int", "hint": ""},
        {"key": "deterministic", "label": "确定性训练 deterministic", "type": "bool", "hint": "可复现，略慢"},
        {"key": "single_cls", "label": "单类模式 single_cls", "type": "bool", "hint": ""},
        {"key": "rect", "label": "矩形训练 rect", "type": "bool", "hint": ""},
        {"key": "cache", "label": "缓存图像 cache", "type": "bool", "hint": "内存允许时可加速"},
        {"key": "amp", "label": "混合精度 AMP", "type": "bool", "hint": ""},
        {"key": "fraction", "label": "数据集比例 fraction", "type": "float", "hint": "0–1，子集实验"},
        {"key": "hsv_h", "label": "HSV-H 增强", "type": "float", "hint": ""},
        {"key": "hsv_s", "label": "HSV-S 增强", "type": "float", "hint": ""},
        {"key": "hsv_v", "label": "HSV-V 增强", "type": "float", "hint": ""},
        {"key": "degrees", "label": "旋转角度 degrees", "type": "float", "hint": ""},
        {"key": "translate", "label": "平移 translate", "type": "float", "hint": ""},
        {"key": "scale", "label": "缩放 scale", "type": "float", "hint": ""},
        {"key": "shear", "label": "剪切 shear", "type": "float", "hint": ""},
        {"key": "perspective", "label": "透视 perspective", "type": "float", "hint": ""},
        {"key": "flipud", "label": "上下翻转概率 flipud", "type": "float", "hint": ""},
        {"key": "fliplr", "label": "左右翻转概率 fliplr", "type": "float", "hint": ""},
        {"key": "mosaic", "label": "Mosaic 概率 mosaic", "type": "float", "hint": ""},
        {"key": "mixup", "label": "MixUp 概率 mixup", "type": "float", "hint": ""},
        {"key": "copy_paste", "label": "Copy-Paste 概率 copy_paste", "type": "float", "hint": ""},
        {"key": "erasing", "label": "随机擦除 erasing", "type": "float", "hint": ""},
        {"key": "crop_fraction", "label": "裁剪比例 crop_fraction", "type": "float", "hint": ""},
        {"key": "save_period", "label": "保存周期 save_period", "type": "int", "hint": "每 N epoch 存 checkpoint，-1 关闭"},
        {"key": "max_det", "label": "最大检测数 max_det", "type": "int", "hint": "验证用"},
        {"key": "iou", "label": "NMS IoU 阈值 iou", "type": "float", "hint": ""},
        {"key": "conf", "label": "置信度阈值 conf", "type": "float", "hint": ""},
        {"key": "cutmix", "label": "CutMix 概率 cutmix", "type": "float", "hint": ""},
        {
            "key": "multi_scale",
            "label": "多尺度训练 multi_scale",
            "type": "bool",
            "hint": "true 时启用多尺度；与 54.99 SOP 一致",
        },
        {"key": "box", "label": "定位损失权重 box", "type": "float", "hint": "ultralytics 默认 7.5"},
        {"key": "cls", "label": "分类损失权重 cls", "type": "float", "hint": ""},
        {"key": "dfl", "label": "DFL 损失权重 dfl", "type": "float", "hint": ""},
    ]
    official = get_yolo_official_field_defaults()
    for item in fields:
        key = item.get("key")
        if key in YOLO_TRAIN_FIELD_DEFAULTS:
            item["default"] = YOLO_TRAIN_FIELD_DEFAULTS[key]
        if key in official:
            item["official_default"] = official[key]
    return fields
