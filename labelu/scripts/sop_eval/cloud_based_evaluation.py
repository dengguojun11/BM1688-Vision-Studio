"""
YOLOv11 GPU 评估 - 支持场景维度 + 目标尺寸分档评估

场景属性从同名 VOC XML 的 <extra_annotations> 读取 (由 convert_annotations.py 生成).
XML_DIR 留空时跳过场景分组, 仅做全局 + 尺寸分档评估.
"""

import os
import time
from collections import defaultdict

import numpy as np
import matplotlib

matplotlib.use("Agg")

try:
    import torch
except Exception:
    torch = None

from ultralytics import YOLO

from evaluation_utils import (
    Detection,
    SIZE_BINS,
    _empty_stats,
    attach_scene_attrs,
    compute_ap50,
    compute_precision_recall_f1,
    get_cpu_memory_mb,
    load_dataset_from_yaml,
    load_yolo_labels,
    match_detections,
    match_detections_grouped,
    plot_f1_summary_curve,
    plot_scene_metrics,
    plot_size_metrics,
    summarize_metrics,
    summarize_scene_metrics,
    summarize_size_metrics,
    write_markdown_report,
)

# ── 路径配置 ──────────────────────────────────────────────────
MODEL_PATH = os.getenv("EVAL_MODEL_PATH", "D:/ptz/datanew/garbage.pt")
DATA_PATH = os.getenv("EVAL_DATA_PATH", "D:/ptz/datanew/images/test")
XML_DIR = os.getenv(
    "EVAL_XML_DIR", "D:/ptz/datanew/labelsxml/test"
)  # VOC XML 目录, 留空则跳过场景分组
SAVE_DIR = os.getenv("EVAL_SAVE_DIR", "./results_cloud")
IMGSZ = int(os.getenv("EVAL_IMGSZ", "640"))
CONF_THRES = float(os.getenv("EVAL_CONF_THRES", "0.35"))
IOU_THRES = float(os.getenv("EVAL_IOU_THRES", "0.5"))
NMS_THRES = float(os.getenv("EVAL_NMS_THRES", str(IOU_THRES)))
MATCH_IOU_THRES = float(os.getenv("EVAL_MATCH_IOU_THRES", str(IOU_THRES)))
MODEL_TASK = os.getenv("EVAL_MODEL_TASK", "detect").strip().lower()
DEVICE = 0 if torch is not None and torch.cuda.is_available() else "cpu"


os.makedirs(SAVE_DIR, exist_ok=True)


def get_gpu_memory_mb():
    if torch is None or not torch.cuda.is_available():
        return None
    try:
        return torch.cuda.max_memory_allocated() / 1024 / 1024
    except Exception:
        return None


def main():
    print("开始 GPU 评估 (逐图推理, 支持场景/尺寸分组)")
    model = (
        YOLO(MODEL_PATH) if MODEL_TASK == "auto" else YOLO(MODEL_PATH, task=MODEL_TASK)
    )
    class_names = (
        list(model.names.values())
        if isinstance(model.names, dict)
        else list(model.names)
    )
    class_count = len(class_names)

    ordered_names, samples = load_dataset_from_yaml(DATA_PATH)

    # 附加场景属性
    if XML_DIR and os.path.isdir(XML_DIR):
        attach_scene_attrs(samples, XML_DIR)
        print(f"已加载场景属性: {XML_DIR}")
    else:
        print("XML_DIR 未配置, 跳过场景分组")

    # if torch is not None and torch.cuda.is_available():
    #     torch.cuda.reset_peak_memory_stats()

    # 累加器
    global_per_class = {cid: _empty_stats() for cid in range(class_count)}
    scene_accum: dict = {}  # { field: { value: per_class } }
    size_accum = {
        b[0]: {cid: _empty_stats() for cid in range(class_count)} for b in SIZE_BINS
    }
    label_counts = np.zeros(class_count, dtype=int)
    evaluation_samples = []

    start_time = time.perf_counter()

    for sample in samples:
        img = __import__("cv2").imread(sample.image_path)
        if img is None:
            continue
        img_h, img_w = img.shape[:2]

        # GT
        targets = load_yolo_labels(
            sample.label_path,
            img_w,
            img_h,
            class_names=class_names,
            xml_label_path=getattr(sample, "xml_label_path", None),
        )
        for t in targets:
            if 0 <= t.class_id < class_count:
                label_counts[t.class_id] += 1

        # 推理
        res = model.predict(
            sample.image_path,
            imgsz=IMGSZ,
            conf=CONF_THRES,
            iou=NMS_THRES,
            device=DEVICE,
            verbose=False,
        )[0]
        predictions = []
        for box in res.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            predictions.append(
                Detection(
                    class_id=int(box.cls[0]),
                    score=float(box.conf[0]),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                )
            )

        grouped = match_detections_grouped(
            predictions,
            targets,
            class_count,
            iou_threshold=MATCH_IOU_THRES,
            scene_attrs=sample.scene_attrs if sample.scene_attrs else None,
            img_w=img_w,
            img_h=img_h,
        )
        evaluation_samples.append((list(predictions), list(targets)))

        # 合并到全局
        for cid in range(class_count):
            for k in ("tp", "fp", "fn"):
                global_per_class[cid][k] += grouped["per_class"][cid][k]
            global_per_class[cid]["scores"].extend(grouped["per_class"][cid]["scores"])
            global_per_class[cid]["matches"].extend(
                grouped["per_class"][cid]["matches"]
            )

        # 合并场景
        for field_key, val_dict in grouped["scene"].items():
            if field_key not in scene_accum:
                scene_accum[field_key] = {}
            for val, pc in val_dict.items():
                if val not in scene_accum[field_key]:
                    scene_accum[field_key][val] = {
                        cid: _empty_stats() for cid in range(class_count)
                    }
                for cid in range(class_count):
                    for k in ("tp", "fp", "fn"):
                        scene_accum[field_key][val][cid][k] += pc[cid][k]
                    scene_accum[field_key][val][cid]["scores"].extend(pc[cid]["scores"])
                    scene_accum[field_key][val][cid]["matches"].extend(
                        pc[cid]["matches"]
                    )

        # 合并尺寸
        for bin_name, pc in grouped["size"].items():
            for cid in range(class_count):
                for k in ("tp", "fp", "fn"):
                    size_accum[bin_name][cid][k] += pc[cid][k]
                size_accum[bin_name][cid]["scores"].extend(pc[cid]["scores"])
                size_accum[bin_name][cid]["matches"].extend(pc[cid]["matches"])

    total_time = time.perf_counter() - start_time
    cpu_mb = get_cpu_memory_mb()
    gpu_mb = get_gpu_memory_mb()

    # 全局汇总
    summary = summarize_metrics(
        class_names,
        global_per_class,
        label_counts,
        len(samples),
        total_time,
        cpu_memory_mb=cpu_mb,
        gpu_memory_mb=gpu_mb,
        evaluation_samples=evaluation_samples,
    )

    # 场景 / 尺寸汇总
    scene_summary = (
        summarize_scene_metrics(scene_accum, class_names) if scene_accum else {}
    )
    size_summary = summarize_size_metrics(size_accum, class_names)

    # 图表
    curve_path = plot_f1_summary_curve(SAVE_DIR, class_names, summary["per_class_rows"])
    scene_img_paths = (
        plot_scene_metrics(SAVE_DIR, scene_summary) if scene_summary else {}
    )
    size_img_path = plot_size_metrics(SAVE_DIR, size_summary)

    # 报告
    report_path = write_markdown_report(
        SAVE_DIR,
        summary,
        curve_image_name=os.path.basename(curve_path),
        scene_summary=scene_summary,
        scene_image_paths=scene_img_paths,
        size_summary=size_summary,
        size_image_path=size_img_path,
        thresholds={
            "模型路径": MODEL_PATH,
            "数据集": DATA_PATH,
            "输入尺寸": IMGSZ,
            "置信度阈值": CONF_THRES,
            "NMS IoU 阈值": NMS_THRES,
            "匹配 IoU 阈值": MATCH_IOU_THRES,
            "推理设备": f"GPU:{DEVICE}",
        },
    )

    print(
        f"图片: {len(samples)} | mAP50: {summary['map50']:.4f} | mAP50-95: {summary['map5095']:.4f}"
    )
    print(f"报告: {report_path}")

    import json

    with open(os.path.join(SAVE_DIR, "eval_summary.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "kind": "pt",
                "images": len(samples),
                "map50": summary.get("map50"),
                "map50_95": summary.get("map5095"),
                "map50_95_status": summary.get("map5095_status"),
                "metric_validity": summary.get("metric_validity"),
                "precision": summary.get("overall_precision"),
                "recall": summary.get("overall_recall"),
                "eval_params": {
                    "imgsz": IMGSZ,
                    "conf_thres": CONF_THRES,
                    "nms_thres": NMS_THRES,
                    "match_iou_thres": MATCH_IOU_THRES,
                    "model_task": MODEL_TASK,
                },
                "report_md": os.path.basename(report_path),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()
