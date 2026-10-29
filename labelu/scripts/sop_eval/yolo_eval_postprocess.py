"""YOLO 检测后处理（仅 cv2/numpy，供边端 bmodel 评估，不依赖 onnxruntime）。"""
import os

import cv2
import numpy as np

INPUT_IMG_H = int(os.getenv("EVAL_INPUT_H", "640"))
INPUT_IMG_W = int(os.getenv("EVAL_INPUT_W", "640"))
NMS_THRESH = 0.5
OBJECT_THRESH = float(os.getenv("EVAL_CONF_THRES", "0.35"))


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
        raise ValueError("Unsupported output shape after squeeze: {}".format(out.shape))
    expected_cols = (4 + class_num, 5 + class_num, 6)
    if out.shape[1] not in expected_cols and out.shape[0] in expected_cols:
        out = out.T
    return out


def postprocess_yolo_onnx(out, img_h, img_w, class_num, ratio, pad):
    out = _prepare_yolo_output(out, class_num)
    pad_w, pad_h = pad
    detect_result = []
    conf_thres = float(os.getenv("EVAL_CONF_THRES", str(OBJECT_THRESH)))

    for i in range(out.shape[0]):
        row = out[i]
        if row.shape[0] == 4 + class_num:
            scores = row[4 : 4 + class_num]
            class_id = int(np.argmax(scores))
            score = float(scores[class_id])
            cx, cy, w, h = row[:4]
            x1 = cx - w / 2.0
            y1 = cy - h / 2.0
            x2 = cx + w / 2.0
            y2 = cy + h / 2.0
        elif row.shape[0] >= 5 + class_num:
            obj_score = float(row[4])
            scores = row[5 : 5 + class_num] * obj_score
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
            raise ValueError("Unsupported prediction width: {}".format(row.shape[0]))

        if score < conf_thres:
            continue

        x1 = max(0.0, min(float(img_w), (float(x1) - pad_w) / ratio))
        y1 = max(0.0, min(float(img_h), (float(y1) - pad_h) / ratio))
        x2 = max(0.0, min(float(img_w), (float(x2) - pad_w) / ratio))
        y2 = max(0.0, min(float(img_h), (float(y2) - pad_h) / ratio))
        if x2 <= x1 or y2 <= y1 or not (0 <= class_id < class_num):
            continue
        detect_result.append(DetectBox(class_id, score, x1, y1, x2, y2))

    return nms(detect_result)
