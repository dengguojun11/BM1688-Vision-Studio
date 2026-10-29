import ast
import json
import os
import time

import cv2
import numpy as np
import onnxruntime

from evaluation_utils import (
    CLASS_NAMES,
    Detection,
    SIZE_BINS,
    _empty_stats,
    attach_scene_attrs,
    get_cpu_memory_mb,
    load_dataset_from_yaml,
    load_yolo_labels,
    match_detections_grouped,
    plot_f1_summary_curve,
    plot_scene_metrics,
    plot_size_metrics,
    summarize_metrics,
    summarize_scene_metrics,
    summarize_size_metrics,
    write_markdown_report,
)

INPUT_IMG_H  = int(os.getenv("EVAL_INPUT_H", "640"))
INPUT_IMG_W  = int(os.getenv("EVAL_INPUT_W", "640"))
NMS_THRESH   = float(os.getenv("EVAL_IOU_THRES", "0.5"))
OBJECT_THRESH = float(os.getenv("EVAL_CONF_THRES", "0.35"))
MODEL_PATH   = os.getenv("EVAL_MODEL_PATH", "D:/ptz/datanew/garbage.onnx")
DATA_PATH    = os.getenv("EVAL_DATA_PATH", "D:/ptz/datanew/images/test")
XML_DIR      = os.getenv("EVAL_XML_DIR", "D:/ptz/datanew/labelsxml/test")          # VOC XML 目录, 留空则跳过场景分组
SAVE_DIR     = os.getenv("EVAL_SAVE_DIR", "./results_onnx")
DUMP_PREDICTIONS = os.getenv("EVAL_DUMP_PREDICTIONS", "1") != "0"
os.makedirs(SAVE_DIR, exist_ok=True)


class DetectBox:
    def __init__(self, class_id, score, xmin, ymin, xmax, ymax):
        self.class_id = class_id
        self.score = score
        self.xmin = xmin
        self.ymin = ymin
        self.xmax = xmax
        self.ymax = ymax


def iou_xyxy(box1, box2):
    xmin = max(box1[0], box2[0])
    ymin = max(box1[1], box2[1])
    xmax = min(box1[2], box2[2])
    ymax = min(box1[3], box2[3])
    inner_w = max(0.0, xmax - xmin)
    inner_h = max(0.0, ymax - ymin)
    inner_area = inner_w * inner_h
    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])
    return inner_area / (area1 + area2 - inner_area + 1e-9)


def nms(detect_result):
    keep = []
    sorted_boxes = sorted(detect_result, key=lambda x: x.score, reverse=True)
    suppressed = [False] * len(sorted_boxes)
    for i, current in enumerate(sorted_boxes):
        if suppressed[i]:
            continue
        keep.append(current)
        for j in range(i + 1, len(sorted_boxes)):
            other = sorted_boxes[j]
            if suppressed[j] or current.class_id != other.class_id:
                continue
            if iou_xyxy(
                (current.xmin, current.ymin, current.xmax, current.ymax),
                (other.xmin, other.ymin, other.xmax, other.ymax),
            ) > NMS_THRESH:
                suppressed[j] = True
    return keep


def letterbox(img_src, new_shape=(INPUT_IMG_H, INPUT_IMG_W), color=(114, 114, 114)):
    """Resize with unchanged aspect ratio, matching Ultralytics YOLO preprocessing."""
    src_h, src_w = img_src.shape[:2]
    new_h, new_w = new_shape
    ratio = min(new_h / src_h, new_w / src_w)

    resized_w = int(round(src_w * ratio))
    resized_h = int(round(src_h * ratio))
    pad_w = new_w - resized_w
    pad_h = new_h - resized_h
    pad_w_half = pad_w / 2
    pad_h_half = pad_h / 2

    if (src_w, src_h) != (resized_w, resized_h):
        img_src = cv2.resize(img_src, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)

    top = int(round(pad_h_half - 0.1))
    bottom = int(round(pad_h_half + 0.1))
    left = int(round(pad_w_half - 0.1))
    right = int(round(pad_w_half + 0.1))
    image = cv2.copyMakeBorder(img_src, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return image, ratio, (pad_w_half, pad_h_half)


def preprocess_image(img_src, resize_w, resize_h):
    image, ratio, pad = letterbox(img_src, (resize_h, resize_w))
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = image.astype(np.float32) / 255.0
    image = image.transpose((2, 0, 1))
    return np.expand_dims(image, axis=0), ratio, pad


def _prepare_yolo_output(out, class_num):
    out = np.squeeze(out)
    if out.ndim != 2:
        raise ValueError(f"Unsupported ONNX output shape after squeeze: {out.shape}")

    expected_cols = (4 + class_num, 5 + class_num, 6)
    if out.shape[1] not in expected_cols and out.shape[0] in expected_cols:
        out = out.T
    return out


def load_onnx_class_names(session):
    meta = session.get_modelmeta().custom_metadata_map
    names_text = meta.get('names')
    if not names_text:
        return []
    try:
        names = ast.literal_eval(names_text)
    except Exception:
        return []
    if isinstance(names, dict):
        return [names[k] for k in sorted(names, key=lambda x: int(x))]
    return list(names or [])


def postprocess_yolo_onnx(out, img_h, img_w, class_num, ratio, pad):
    out = _prepare_yolo_output(out, class_num)
    pad_w, pad_h = pad
    detect_result = []

    for i in range(out.shape[0]):
        row = out[i]
        if row.shape[0] == 4 + class_num:
            scores = row[4:4 + class_num]
            class_id = int(np.argmax(scores))
            score = float(scores[class_id])
            cx, cy, w, h = row[:4]
            x1 = cx - w / 2.0
            y1 = cy - h / 2.0
            x2 = cx + w / 2.0
            y2 = cy + h / 2.0
        elif row.shape[0] >= 5 + class_num:
            obj_score = float(row[4])
            scores = row[5:5 + class_num] * obj_score
            class_id = int(np.argmax(scores))
            score = float(scores[class_id])
            cx, cy, w, h = row[:4]
            x1 = cx - w / 2.0
            y1 = cy - h / 2.0
            x2 = cx + w / 2.0
            y2 = cy + h / 2.0
        elif row.shape[0] == 6:
            x1, y1, x2, y2, score, class_id = row[:6]
            class_id = int(class_id)
            score = float(score)
        else:
            raise ValueError(f"Unsupported ONNX prediction width: {row.shape[0]}")

        if score < OBJECT_THRESH:
            continue

        x1 = max(0.0, min(float(img_w), (float(x1) - pad_w) / ratio))
        y1 = max(0.0, min(float(img_h), (float(y1) - pad_h) / ratio))
        x2 = max(0.0, min(float(img_w), (float(x2) - pad_w) / ratio))
        y2 = max(0.0, min(float(img_h), (float(y2) - pad_h) / ratio))
        if x2 <= x1 or y2 <= y1 or not (0 <= class_id < class_num):
            continue
        detect_result.append(DetectBox(class_id, score, x1, y1, x2, y2))

    return nms(detect_result)


def prediction_record(sample, predictions, class_names, img_w, img_h):
    boxes = []
    for det in predictions:
        class_name = class_names[det.class_id] if 0 <= det.class_id < len(class_names) else str(det.class_id)
        boxes.append({
            "class_id": int(det.class_id),
            "class_name": class_name,
            "score": float(det.score),
            "x1": float(det.x1),
            "y1": float(det.y1),
            "x2": float(det.x2),
            "y2": float(det.y2),
        })
    return {
        "image": sample.image_path,
        "image_name": os.path.basename(sample.image_path),
        "width": int(img_w),
        "height": int(img_h),
        "boxes": boxes,
    }


def main():
    print("开始 ONNX 评估 (支持场景/尺寸分组)")
    available = onnxruntime.get_available_providers()
    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if 'CUDAExecutionProvider' in available else ['CPUExecutionProvider']
    session = onnxruntime.InferenceSession(MODEL_PATH, providers=providers)
    input_name = session.get_inputs()[0].name

    data_class_names, samples = load_dataset_from_yaml(DATA_PATH)
    model_class_names = load_onnx_class_names(session)
    class_names = model_class_names or data_class_names
    class_count = len(class_names)
    if not class_count:
        raise RuntimeError("No class names found. Use an ONNX model with metadata names or a yaml with names.")
    predictions_path = os.path.join(SAVE_DIR, "predictions.jsonl")
    predictions_file = open(predictions_path, "w", encoding="utf-8") if DUMP_PREDICTIONS else None

    if data_class_names and data_class_names != class_names:
        print(f"警告: 数据集类别顺序和 ONNX metadata 不一致, 使用 ONNX metadata: {class_names}")

    if XML_DIR and os.path.isdir(XML_DIR):
        attach_scene_attrs(samples, XML_DIR)
        print(f"已加载场景属性: {XML_DIR}")

    # 累加器
    global_per_class = {cid: _empty_stats() for cid in range(class_count)}
    scene_accum: dict = {}
    size_accum = {b[0]: {cid: _empty_stats() for cid in range(class_count)} for b in SIZE_BINS}
    label_counts = np.zeros(class_count, dtype=int)
    evaluation_samples = []
    inference_seconds = 0.0

    start_time = time.perf_counter()

    for sample in samples:
        image = cv2.imread(sample.image_path)
        if image is None:
            continue
        img_h, img_w = image.shape[:2]

        gt_boxes = load_yolo_labels(sample.label_path, img_w, img_h,
                                    class_names=class_names,
                                    xml_label_path=getattr(sample, 'xml_label_path', None))
        for gt in gt_boxes:
            if 0 <= gt.class_id < class_count:
                label_counts[gt.class_id] += 1

        input_tensor, ratio, pad = preprocess_image(image, INPUT_IMG_W, INPUT_IMG_H)
        t0 = time.perf_counter()
        outputs = session.run(None, {input_name: input_tensor})
        inference_seconds += time.perf_counter() - t0

        pred_boxes = postprocess_yolo_onnx(outputs[0], img_h, img_w, class_count, ratio, pad)
        predictions = [
            Detection(b.xmin, b.ymin, b.xmax, b.ymax, b.class_id, b.score)
            for b in pred_boxes
        ]
        if predictions_file:
            predictions_file.write(
                json.dumps(
                    prediction_record(sample, predictions, class_names, img_w, img_h),
                    ensure_ascii=False,
                ) + "\n"
            )

        grouped = match_detections_grouped(
            predictions, gt_boxes, class_count,
            iou_threshold=0.5,
            scene_attrs=sample.scene_attrs if sample.scene_attrs else None,
            img_w=img_w, img_h=img_h,
        )
        evaluation_samples.append((list(predictions), list(gt_boxes)))

        # 合并全局
        for cid in range(class_count):
            for k in ('tp', 'fp', 'fn'):
                global_per_class[cid][k] += grouped['per_class'][cid][k]
            global_per_class[cid]['scores'].extend(grouped['per_class'][cid]['scores'])
            global_per_class[cid]['matches'].extend(grouped['per_class'][cid]['matches'])

        # 合并场景
        for field_key, val_dict in grouped['scene'].items():
            if field_key not in scene_accum:
                scene_accum[field_key] = {}
            for val, pc in val_dict.items():
                if val not in scene_accum[field_key]:
                    scene_accum[field_key][val] = {cid: _empty_stats() for cid in range(class_count)}
                for cid in range(class_count):
                    for k in ('tp', 'fp', 'fn'):
                        scene_accum[field_key][val][cid][k] += pc[cid][k]
                    scene_accum[field_key][val][cid]['scores'].extend(pc[cid]['scores'])
                    scene_accum[field_key][val][cid]['matches'].extend(pc[cid]['matches'])

        # 合并尺寸
        for bin_name, pc in grouped['size'].items():
            for cid in range(class_count):
                for k in ('tp', 'fp', 'fn'):
                    size_accum[bin_name][cid][k] += pc[cid][k]
                size_accum[bin_name][cid]['scores'].extend(pc[cid]['scores'])
                size_accum[bin_name][cid]['matches'].extend(pc[cid]['matches'])

    total_time = time.perf_counter() - start_time
    cpu_mb = get_cpu_memory_mb()

    summary = summarize_metrics(
        class_names=class_names,
        per_class=global_per_class,
        label_counts=label_counts,
        total_images=len(samples),
        total_time_seconds=total_time,
        cpu_memory_mb=cpu_mb,
        gpu_memory_mb=None,
        evaluation_samples=evaluation_samples,
    )
    summary['extra_speed_metrics'] = {
        '平均推理耗时(ms/图)': (inference_seconds / max(len(samples), 1)) * 1000,
    }

    scene_summary = summarize_scene_metrics(scene_accum, class_names) if scene_accum else {}
    size_summary  = summarize_size_metrics(size_accum, class_names)

    curve_path      = plot_f1_summary_curve(SAVE_DIR, class_names, summary['per_class_rows'])
    scene_img_paths = plot_scene_metrics(SAVE_DIR, scene_summary) if scene_summary else {}
    size_img_path   = plot_size_metrics(SAVE_DIR, size_summary)

    report_path = write_markdown_report(
        SAVE_DIR, summary,
        curve_image_name=os.path.basename(curve_path),
        scene_summary=scene_summary,
        scene_image_paths=scene_img_paths,
        size_summary=size_summary,
        size_image_path=size_img_path,
        thresholds={
            '模型路径':     MODEL_PATH,
            '数据集':       DATA_PATH,
            '输入尺寸':     f'{INPUT_IMG_W}x{INPUT_IMG_H}',
            '置信度阈值':   OBJECT_THRESH,
            'NMS IoU 阈值': NMS_THRESH,
            '推理后端':     'ONNX Runtime',
        },
    )

    print(f"图片: {len(samples)} | mAP50: {summary['map50']:.4f}")
    print(f"报告: {report_path}")
    if predictions_file:
        predictions_file.close()
        print(f"predictions: {predictions_path}")

    with open(os.path.join(SAVE_DIR, "eval_summary.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "kind": "onnx",
                "images": len(samples),
                "map50": summary.get("map50"),
                "map50_95": summary.get("map5095"),
                "map50_95_status": summary.get("map5095_status"),
                "metric_validity": summary.get("metric_validity"),
                "precision": summary.get("overall_precision"),
                "recall": summary.get("overall_recall"),
                "report_md": os.path.basename(report_path),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == '__main__':
    main()
