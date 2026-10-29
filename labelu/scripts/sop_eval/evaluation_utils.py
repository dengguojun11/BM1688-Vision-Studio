# ===================== 仅添加这4行，解决中文字体报错 =====================
# ======================================================================

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        """Keep edge evaluation usable when the optional progress package is absent."""
        return iterable

# 类别相关常量
CLASS_NAMES = [
    '其他垃圾', '可回收物', '厨余垃圾', '有害垃圾'
]

CLASS_COLORS = [
    '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4'
]

# 尺寸分档
SIZE_BINS = [
    ('tiny', 0, 0.0004),
    ('small', 0.0004, 0.0025),
    ('medium', 0.0025, 0.04),
    ('large', 0.04, float('inf')),
]

SIZE_BIN_DESC = {
    'tiny': 'r < 0.0004',
    'small': '0.0004 <= r < 0.0025',
    'medium': '0.0025 <= r < 0.04',
    'large': 'r >= 0.04',
}

MAP50_95_IOU_THRESHOLDS = tuple(round(0.50 + index * 0.05, 2) for index in range(10))


class Detection:
    __slots__ = ['x1', 'y1', 'x2', 'y2', 'class_id', 'score', 'area']

    def __init__(self, x1, y1, x2, y2, class_id, score=1.0):
        self.x1 = float(x1)
        self.y1 = float(y1)
        self.x2 = float(x2)
        self.y2 = float(y2)
        self.class_id = int(class_id)
        self.score = float(score)
        self.area = max(0, (self.x2 - self.x1) * (self.y2 - self.y1))


def iou(a: Detection, b: Detection) -> float:
    a_x1, a_y1, a_x2, a_y2 = a.x1, a.y1, a.x2, a.y2
    b_x1, b_y1, b_x2, b_y2 = b.x1, b.y1, b.x2, b.y2

    inter_x1 = max(a_x1, b_x1)
    inter_y1 = max(a_y1, b_y1)
    inter_x2 = min(a_x2, b_x2)
    inter_y2 = min(a_y2, b_y2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter = inter_w * inter_h

    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def _empty_stats():
    """补充空统计结构（主脚本依赖）"""
    return {'tp': 0, 'fp': 0, 'fn': 0, 'scores': [], 'matches': []}


def match_detections(predictions: List[Detection], targets: List[Detection], class_count: int, iou_threshold=0.5):
    pred_by_class = [[] for _ in range(class_count)]
    gt_by_class = [[] for _ in range(class_count)]

    for p in predictions:
        if 0 <= p.class_id < class_count:
            pred_by_class[p.class_id].append(p)

    for t in targets:
        if 0 <= t.class_id < class_count:
            gt_by_class[t.class_id].append(t)

    stats = {cid: _empty_stats() for cid in range(class_count)}

    for cid in range(class_count):
        preds = pred_by_class[cid]
        gts = gt_by_class[cid]
        stats[cid]['fn'] = len(gts)

        preds_sorted = sorted(preds, key=lambda x: -x.score)
        gt_used = [False] * len(gts)

        for p in preds_sorted:
            best_iou = 0.0
            best_idx = -1

            for i, t in enumerate(gts):
                if not gt_used[i]:
                    current_iou = iou(p, t)
                    if current_iou > best_iou:
                        best_iou = current_iou
                        best_idx = i

            if best_idx >= 0 and best_iou >= iou_threshold:
                stats[cid]['tp'] += 1
                stats[cid]['fn'] -= 1
                stats[cid]['matches'].append(1)
                gt_used[best_idx] = True
            else:
                stats[cid]['fp'] += 1
                stats[cid]['matches'].append(0)
            stats[cid]['scores'].append(p.score)

    return stats


def match_detections_grouped(
        predictions,
        targets,
        class_count,
        iou_threshold=0.5,
        scene_attrs=None,
        img_w=0,
        img_h=0,
):
    base_stats = match_detections(predictions, targets, class_count, iou_threshold)
    img_area = float(img_w * img_h) if img_w > 0 and img_h > 0 else 1.0

    # 尺寸分组: 每个 bin 单独跑 match_detections
    size_result = {}
    for bin_name, lo, hi in SIZE_BINS:
        bin_preds = [p for p in predictions if lo <= p.area / img_area < hi]
        bin_gts   = [t for t in targets   if lo <= t.area / img_area < hi]
        size_result[bin_name] = match_detections(bin_preds, bin_gts, class_count, iou_threshold)

    # 场景分组: 整张图共用同一组属性, 直接复用 base_stats
    scene_result = {}
    if scene_attrs:
        for field_key, field_val in scene_attrs.items():
            if field_key not in scene_result:
                scene_result[field_key] = {}
            scene_result[field_key][field_val] = base_stats

    return {
        'per_class': base_stats,
        'size':      size_result,
        'scene':     scene_result,
    }

def parse_yolo_txt(txt_path: Path, img_w=0, img_h=0) -> List[Detection]:
    dets = []
    if not txt_path.exists():
        return dets
    with open(txt_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            cid, xc, yc, w, h = parts[:5]
            score = float(parts[5]) if len(parts) >= 6 else 1.0

            cid = int(cid)
            xc = float(xc)
            yc = float(yc)
            w = float(w)
            h = float(h)

            # 转换为绝对坐标（YOLO格式是相对坐标）
            if img_w > 0 and img_h > 0:
                x1 = (xc - w / 2) * img_w
                y1 = (yc - h / 2) * img_h
                x2 = (xc + w / 2) * img_w
                y2 = (yc + h / 2) * img_h
            else:
                x1 = (xc - w / 2)
                y1 = (yc - h / 2)
                x2 = (xc + w / 2)
                y2 = (yc + h / 2)
            dets.append(Detection(x1, y1, x2, y2, cid, score))
    return dets


def compute_ap(scores: List[float], matches: List[int], n_gt: int) -> float:
    if n_gt <= 0 or not scores:
        return float('nan')
    order = np.argsort(-np.array(scores))
    matches_arr = np.array(matches)[order]
    tp = np.cumsum(matches_arr)
    fp = np.cumsum(1 - matches_arr)

    recall = tp / (n_gt + 1e-9)
    precision = tp / (tp + fp + 1e-9)

    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([1.0], precision, [0.0]))

    for i in range(len(mpre) - 1, 0, -1):
        mpre[i - 1] = max(mpre[i - 1], mpre[i])

    idx = np.where(mrec[1:] != mrec[:-1])[0]
    ap = np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1])
    return ap


def compute_ap50(scores: List[float], matches: List[int], n_gt: int) -> float:
    """补充mAP50计算（主脚本可能依赖）"""
    return compute_ap(scores, matches, n_gt)  # AP50即IOU=0.5的AP


def compute_map50_95(evaluation_samples, class_count: int):
    """Compute COCO-style AP averaged over IoU 0.50:0.95 for each class."""
    thresholds = MAP50_95_IOU_THRESHOLDS
    per_threshold = {
        threshold: {cid: _empty_stats() for cid in range(class_count)}
        for threshold in thresholds
    }
    label_counts = np.zeros(class_count, dtype=int)

    for predictions, targets in evaluation_samples or []:
        for target in targets:
            if 0 <= target.class_id < class_count:
                label_counts[target.class_id] += 1
        for threshold in thresholds:
            matched = match_detections(
                predictions,
                targets,
                class_count,
                iou_threshold=threshold,
            )
            for cid in range(class_count):
                per_threshold[threshold][cid]['scores'].extend(matched[cid]['scores'])
                per_threshold[threshold][cid]['matches'].extend(matched[cid]['matches'])

    per_class = {}
    class_averages = []
    for cid in range(class_count):
        if label_counts[cid] <= 0:
            per_class[cid] = float('nan')
            continue
        threshold_aps = [
            compute_ap(
                per_threshold[threshold][cid]['scores'],
                per_threshold[threshold][cid]['matches'],
                int(label_counts[cid]),
            )
            for threshold in thresholds
        ]
        class_ap = float(np.nanmean(threshold_aps))
        per_class[cid] = class_ap
        class_averages.append(class_ap)

    return {
        'map50_95': float(np.nanmean(class_averages)) if class_averages else 0.0,
        'per_class': per_class,
        'iou_thresholds': list(thresholds),
    }


def compute_precision_recall_f1(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


def _confidence_threshold_grid(min_conf=0.21, max_conf=0.99, step=0.01):
    start = int(round(float(min_conf) * 100))
    end = int(round(float(max_conf) * 100))
    increment = max(1, int(round(float(step) * 100)))
    return [round(value / 100.0, 2) for value in range(start, end + 1, increment)]


def _threshold_metric_point(conf, tp, fp, fn):
    precision, recall, f1 = compute_precision_recall_f1(tp, fp, fn)
    return {
        'confidence': round(float(conf), 2),
        'tp': int(tp),
        'fp': int(fp),
        'fn': int(fn),
        'precision': round(float(precision), 6),
        'recall': round(float(recall), 6),
        'f1': round(float(f1), 6),
    }


def _select_three_thresholds(points):
    usable = [point for point in points if 0.2 < point['confidence'] < 1.0]
    middle_candidates = [
        point for point in usable if point['confidence'] < 0.99
    ]
    if not middle_candidates:
        return None

    t2 = max(middle_candidates, key=lambda point: (point['f1'], point['confidence']))
    lower_points = [
        point for point in usable if point['confidence'] < t2['confidence']
    ]
    upper_points = [
        point for point in usable if point['confidence'] > t2['confidence']
    ]
    if not lower_points or not upper_points:
        return None

    max_recall = max(point['recall'] for point in usable)
    t3 = None
    recall_ratio = None
    for ratio in (0.95, 0.94, 0.93):
        target = max_recall * ratio
        candidates = [point for point in lower_points if point['recall'] >= target]
        if candidates:
            t3 = max(candidates, key=lambda point: (point['f1'], point['confidence']))
            recall_ratio = ratio
            break
    if t3 is None:
        t3 = min(lower_points, key=lambda point: point['confidence'])
        recall_ratio = None

    high_precision_candidates = [
        point for point in upper_points if point['precision'] >= 0.95
    ]
    if high_precision_candidates:
        t1 = max(
            high_precision_candidates,
            key=lambda point: (point['f1'], point['confidence']),
        )
        t1_target_met = True
        t1_source = 'precision_target'
    else:
        t1 = max(upper_points, key=lambda point: point['confidence'])
        t1_target_met = False
        t1_source = 'max_confidence_fallback'

    return {
        'conf_threshold_flink_pass': t1['confidence'],
        'conf_threshold_alert_f1': t2['confidence'],
        'conf_threshold_gray_recall': t3['confidence'],
        't1_metrics': t1,
        't2_metrics': t2,
        't3_metrics': t3,
        't1_precision_target': 0.95,
        't1_precision_target_met': t1_target_met,
        't1_selection_source': t1_source,
        't3_recall_ratio': recall_ratio,
        'max_recall': round(float(max_recall), 6),
    }


def build_business_threshold_analysis(
        evaluation_samples,
        class_names,
        iou_threshold=0.5,
        min_conf=0.21,
        max_conf=0.99,
        step=0.01,
):
    """按规范从真实预测和标注计算总体及逐类 T1/T2/T3。"""
    class_count = len(class_names)
    thresholds = _confidence_threshold_grid(min_conf, max_conf, step)
    curves = {cid: [] for cid in range(class_count)}
    overall_curve = []

    for conf in thresholds:
        totals = {cid: {'tp': 0, 'fp': 0, 'fn': 0} for cid in range(class_count)}
        for predictions, targets in evaluation_samples:
            filtered = [item for item in predictions if item.score >= conf]
            stats = match_detections(
                filtered,
                targets,
                class_count,
                iou_threshold=iou_threshold,
            )
            for cid in range(class_count):
                for key in ('tp', 'fp', 'fn'):
                    totals[cid][key] += int(stats[cid][key])

        overall = {'tp': 0, 'fp': 0, 'fn': 0}
        for cid in range(class_count):
            row = totals[cid]
            curves[cid].append(
                _threshold_metric_point(conf, row['tp'], row['fp'], row['fn'])
            )
            for key in ('tp', 'fp', 'fn'):
                overall[key] += row[key]
        overall_curve.append(
            _threshold_metric_point(
                conf,
                overall['tp'],
                overall['fp'],
                overall['fn'],
            )
        )

    overall_selection = _select_three_thresholds(overall_curve)
    per_class = []
    failed_classes = []
    for cid, class_name in enumerate(class_names):
        points = curves[cid]
        label_count = max(
            (point['tp'] + point['fn'] for point in points),
            default=0,
        )
        selection = _select_three_thresholds(points) if label_count > 0 else None
        item = {
            'class_id': cid,
            'class_name': str(class_name),
            'label_count': int(label_count),
            'status': 'ok' if selection else 'failed',
        }
        if selection:
            item.update(selection)
        else:
            failed_classes.append(str(class_name))
            item['reason'] = '类别缺少有效标注或无法满足 T1 > T2 > T3 > 0.2'
        per_class.append(item)

    status = 'ok' if overall_selection and not failed_classes else 'failed'
    recommended = None
    if overall_selection:
        recommended = {
            'edge_low_threshold': overall_selection['conf_threshold_gray_recall'],
            'edge_high_threshold': overall_selection['conf_threshold_flink_pass'],
            'flink_filter_threshold': overall_selection['conf_threshold_alert_f1'],
        }
    return {
        'status': status,
        'iou_threshold': float(iou_threshold),
        'confidence_step': float(step),
        'confidence_range': [float(min_conf), float(max_conf)],
        'precision_target': 0.95,
        'recall_target_ratio': 0.95,
        'recommended_thresholds': recommended,
        'overall': overall_selection,
        'per_class': per_class,
        'failed_classes': failed_classes,
    }


def summarize_metrics(class_names, per_class, label_counts, total_images, total_time_seconds,
                      cpu_memory_mb=None, gpu_memory_mb=None, evaluation_samples=None):
    """修复汇总逻辑，补充map50/map5095（主脚本输出依赖）"""
    rows = []
    ap50_list = []
    map5095_result = (
        compute_map50_95(evaluation_samples, len(class_names))
        if evaluation_samples is not None
        else None
    )
    for cid, name in enumerate(class_names):
        s = per_class[cid]
        tp, fp, fn = s['tp'], s['fp'], s['fn']
        p, r, f1 = compute_precision_recall_f1(tp, fp, fn)
        ap50 = compute_ap50(s['scores'], s['matches'], label_counts[cid])
        if not np.isnan(ap50):
            ap50_list.append(ap50)
        ap5095 = (
            map5095_result['per_class'].get(cid, float('nan'))
            if map5095_result
            else float('nan')
        )
        rows.append({
            'id': cid,
            'name': name,
            'labels': label_counts[cid],
            'tp': tp,
            'fp': fp,
            'fn': fn,
            'precision': p,
            'recall': r,
            'f1': f1,
            'ap50': ap50,
            'ap5095': ap5095,
        })

    map50 = float(np.nanmean(ap50_list)) if ap50_list else 0.0
    map5095 = map5095_result['map50_95'] if map5095_result else None

    total_tp = sum(r['tp'] for r in rows)
    total_fp = sum(r['fp'] for r in rows)
    total_fn = sum(r['fn'] for r in rows)
    overall_p, overall_r, overall_f1 = compute_precision_recall_f1(total_tp, total_fp, total_fn)

    summary = {
        'images': total_images,
        'time_sec': round(total_time_seconds, 2),
        'speed_avg_ms': round(total_time_seconds * 1000 / total_images, 1) if total_images > 0 else 0,
        'map50': round(map50, 4),
        'map5095': round(map5095, 4) if map5095 is not None else None,
        'map5095_status': 'computed' if map5095_result else 'not_computed',
        'metric_validity': {
            'map50': {'status': 'computed', 'iou_thresholds': [0.5]},
            'map50_95': {
                'status': 'computed' if map5095_result else 'not_computed',
                'iou_thresholds': map5095_result['iou_thresholds'] if map5095_result else [],
                'reason': None if map5095_result else 'evaluation_samples_not_provided',
            },
        },
        'overall_precision': round(overall_p, 4),
        'overall_recall': round(overall_r, 4),
        'overall_f1': round(overall_f1, 4),
        'per_class_rows': rows,  # 主脚本绘图依赖
    }
    if cpu_memory_mb is not None:
        summary['cpu_memory_mb'] = round(cpu_memory_mb, 1)
    if gpu_memory_mb is not None:
        summary['gpu_memory_mb'] = round(gpu_memory_mb, 1)
    return summary


def summarize_scene_metrics(scene_accum, class_names):
    """补充场景指标汇总（主脚本生成报告依赖）"""
    scene_summary = {}
    for field_key, val_dict in scene_accum.items():
        scene_summary[field_key] = {}
        for val, per_class in val_dict.items():
            # 计算该场景维度的汇总指标
            rows = []
            ap50_list = []
            label_counts = {cid: per_class[cid]['tp'] + per_class[cid]['fn'] for cid in range(len(class_names))}
            for cid, name in enumerate(class_names):
                s = per_class[cid]
                tp, fp, fn = s['tp'], s['fp'], s['fn']
                p, r, f1 = compute_precision_recall_f1(tp, fp, fn)
                ap50 = compute_ap50(s['scores'], s['matches'], label_counts[cid])
                if not np.isnan(ap50):
                    ap50_list.append(ap50)
                rows.append({
                    'name': name,
                    'tp': tp,
                    'fp': fp,
                    'fn': fn,
                    'precision': p,
                    'recall': r,
                    'f1': f1,
                    'ap50': ap50,
                })
            map50 = float(np.nanmean(ap50_list)) if ap50_list else 0.0
            scene_summary[field_key][val] = {
                'map50': round(map50, 4),
                'per_class': rows,
            }
    return scene_summary


def summarize_size_metrics(size_accum, class_names):
    """补充尺寸指标汇总（主脚本生成报告依赖）"""
    size_summary = {}
    for bin_name, per_class in size_accum.items():
        rows = []
        ap50_list = []
        label_counts = {cid: per_class[cid]['tp'] + per_class[cid]['fn'] for cid in range(len(class_names))}
        for cid, name in enumerate(class_names):
            s = per_class[cid]
            tp, fp, fn = s['tp'], s['fp'], s['fn']
            p, r, f1 = compute_precision_recall_f1(tp, fp, fn)
            ap50 = compute_ap50(s['scores'], s['matches'], label_counts[cid])
            if not np.isnan(ap50):
                ap50_list.append(ap50)
            rows.append({
                'name': name,
                'tp': tp,
                'fp': fp,
                'fn': fn,
                'precision': p,
                'recall': r,
                'f1': f1,
                'ap50': ap50,
            })
        map50 = float(np.nanmean(ap50_list)) if ap50_list else 0.0
        size_summary[bin_name] = {
            'desc': SIZE_BIN_DESC[bin_name],
            'map50': round(map50, 4),
            'per_class': rows,
        }
    return size_summary


def _plot_grouped_bar_chart_cv2(
    path,
    categories,
    series,
    *,
    title,
    y_label='F1',
):
    """Render a small PNG chart without matplotlib.

    The BM1688 runtime already depends on OpenCV for inference and image
    handling, while matplotlib is often unavailable on the device. Keeping
    this fallback here makes visual evidence deterministic without adding a
    large architecture-specific wheel to the edge bundle.
    """
    import cv2
    import numpy as np

    categories = [str(value) for value in categories]
    series = [
        (str(name), [max(0.0, min(float(value or 0.0), 1.0)) for value in values])
        for name, values in series
    ]
    width, height = 1200, 700
    left, top, right, bottom = 110, 70, 40, 110
    chart_w = width - left - right
    chart_h = height - top - bottom
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    axis = (70, 70, 70)
    cv2.line(image, (left, top), (left, height - bottom), axis, 2)
    cv2.line(image, (left, height - bottom), (width - right, height - bottom), axis, 2)
    for tick in range(0, 6):
        ratio = tick / 5.0
        y = int(height - bottom - ratio * chart_h)
        cv2.line(image, (left, y), (width - right, y), (225, 225, 225), 1)
        cv2.putText(
            image,
            f'{ratio:.1f}',
            (20, y + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            axis,
            1,
            cv2.LINE_AA,
        )
    colors = [
        (107, 107, 255),
        (196, 205, 78),
        (209, 183, 69),
        (180, 206, 150),
        (160, 160, 160),
        (90, 170, 230),
    ]
    category_count = max(len(categories), 1)
    series_count = max(len(series), 1)
    group_w = chart_w / category_count
    bar_w = max(4, int(group_w * 0.72 / series_count))
    for category_index, category in enumerate(categories):
        group_left = int(left + category_index * group_w + group_w * 0.14)
        for series_index, (_, values) in enumerate(series):
            value = values[category_index] if category_index < len(values) else 0.0
            x1 = group_left + series_index * bar_w
            x2 = x1 + bar_w - 2
            y1 = int(height - bottom - value * chart_h)
            cv2.rectangle(
                image,
                (x1, y1),
                (x2, height - bottom - 1),
                colors[series_index % len(colors)],
                -1,
            )
        label = category if len(category) <= 16 else category[:15] + '...'
        cv2.putText(
            image,
            label,
            (group_left, height - bottom + 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            axis,
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        image,
        str(title),
        (left, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (25, 25, 25),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        str(y_label),
        (20, top - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        axis,
        1,
        cv2.LINE_AA,
    )
    legend_x = left
    legend_y = height - 35
    for index, (name, _) in enumerate(series):
        color = colors[index % len(colors)]
        cv2.rectangle(image, (legend_x, legend_y - 12), (legend_x + 16, legend_y + 4), color, -1)
        cv2.putText(
            image,
            name[:18],
            (legend_x + 23, legend_y + 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            axis,
            1,
            cv2.LINE_AA,
        )
        legend_x += 150
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 先由 OpenCV 编码，再用 Python 写文件，兼容 Windows 下包含中文的产物路径。
    encoded_ok, encoded = cv2.imencode('.png', image)
    if not encoded_ok:
        raise RuntimeError(f'无法编码图表：{path}')
    try:
        with open(path, 'wb') as chart_file:
            chart_file.write(encoded.tobytes())
    except OSError as exc:
        raise RuntimeError(f'无法写入图表：{path}') from exc
    return path


def plot_scene_metrics(save_dir, scene_summary):
    """补充场景指标绘图（主脚本依赖）。"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
        matplotlib.rcParams['axes.unicode_minus'] = False
    except (ImportError, ModuleNotFoundError):
        scene_img_paths = {}
        for field_key, val_dict in scene_summary.items():
            vals = list(val_dict.keys())
            classes = val_dict[vals[0]]['per_class'] if vals else []
            series = [
                (str(item['name']), [val_dict[value]['per_class'][index]['f1'] for value in vals])
                for index, item in enumerate(classes)
            ]
            path = os.path.join(save_dir, f'scene_{field_key}_f1.png')
            scene_img_paths[field_key] = _plot_grouped_bar_chart_cv2(
                path, vals, series, title=f'F1 by {field_key}'
            )
        return scene_img_paths
    scene_img_paths = {}
    for field_key, val_dict in scene_summary.items():
        plt.figure(figsize=(10, 6))
        vals = list(val_dict.keys())
        classes = scene_summary[field_key][vals[0]]['per_class'] if vals else []
        class_names = [c['name'] for c in classes]
        n_classes = len(class_names)

        x = np.arange(len(vals))
        width = 0.8 / n_classes
        for i, cls_name in enumerate(class_names):
            f1_scores = [val_dict[v]['per_class'][i]['f1'] for v in vals]
            plt.bar(x + i * width, f1_scores, width, label=cls_name)

        plt.xlabel(f'Scene: {field_key}')
        plt.ylabel('F1 Score')
        plt.title(f'F1 Score by {field_key}')
        plt.xticks(x + width * (n_classes - 1) / 2, vals)
        plt.legend()
        plt.grid(alpha=0.3)
        path = os.path.join(save_dir, f'scene_{field_key}_f1.png')
        plt.tight_layout()
        plt.savefig(path, dpi=120)
        plt.close()
        scene_img_paths[field_key] = path
    return scene_img_paths


def plot_size_metrics(save_dir, size_summary):
    """补充尺寸指标绘图（主脚本依赖）。"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
        matplotlib.rcParams['axes.unicode_minus'] = False
    except (ImportError, ModuleNotFoundError):
        bins = list(size_summary.keys())
        classes = size_summary[bins[0]]['per_class'] if bins else []
        series = [
            (str(item['name']), [size_summary[value]['per_class'][index]['f1'] for value in bins])
            for index, item in enumerate(classes)
        ]
        return _plot_grouped_bar_chart_cv2(
            os.path.join(save_dir, 'size_f1.png'),
            bins,
            series,
            title='F1 by Target Size',
        )
    plt.figure(figsize=(10, 6))
    bins = list(size_summary.keys())
    classes = size_summary[bins[0]]['per_class'] if bins else []
    class_names = [c['name'] for c in classes]
    n_classes = len(class_names)

    x = np.arange(len(bins))
    width = 0.8 / n_classes
    for i, cls_name in enumerate(class_names):
        f1_scores = [size_summary[b]['per_class'][i]['f1'] for b in bins]
        plt.bar(x + i * width, f1_scores, width, label=cls_name)

    plt.xlabel('Size Bin')
    plt.ylabel('F1 Score')
    plt.title('F1 Score by Target Size')
    plt.xticks(x + width * (n_classes - 1) / 2, [f'{b}\n({SIZE_BIN_DESC[b]})' for b in bins])
    plt.legend()
    plt.grid(alpha=0.3)
    path = os.path.join(save_dir, 'size_f1.png')
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close()
    return path


def plot_f1_summary_curve(save_dir, class_names, per_class_rows, curve_name='F1_curve'):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
        matplotlib.rcParams['axes.unicode_minus'] = False
    except (ImportError, ModuleNotFoundError):
        return _plot_grouped_bar_chart_cv2(
            os.path.join(save_dir, f'{curve_name}.png'),
            class_names,
            [('F1', [row['f1'] for row in per_class_rows])],
            title='F1 by Class',
        )
    os.makedirs(save_dir, exist_ok=True)
    plt.figure(figsize=(8, 5))
    x = np.arange(len(class_names))
    f1_scores = [r['f1'] for r in per_class_rows]
    plt.bar(x, f1_scores, color=CLASS_COLORS[:len(class_names)])
    plt.xticks(x, class_names, rotation=45)
    plt.ylim(0, 1.05)
    plt.title('F1 Score by Class')
    plt.ylabel('F1')
    path = os.path.join(save_dir, f'{curve_name}.png')
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close()
    return path  # 补充返回路径（主脚本依赖）


def write_markdown_report(save_dir, summary, curve_image_name=None, scene_summary=None,
                          scene_image_paths=None, size_summary=None, size_image_path=None,
                          thresholds=None):
    report_path = os.path.join(save_dir, 'evaluation_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('# YOLOv11 评估报告' + chr(10) + chr(10))
        if thresholds:
            f.write('## 评估配置' + chr(10) + chr(10))
            f.write('| 参数 | 値 |' + chr(10))
            f.write('| --- | --- |' + chr(10))
            for k, v in thresholds.items():
                f.write(f'| {k} | {v} |' + chr(10))
            f.write(chr(10))
        f.write('## 全局指标' + chr(10) + chr(10))
        f.write(f'- 评估图片数: {summary["images"]}' + chr(10))
        f.write(f'- 总耗时: {summary["time_sec"]}s' + chr(10))
        f.write(f'- 平均推理速度: {summary["speed_avg_ms"]}ms/张' + chr(10))
        f.write(f'- mAP50: {summary["map50"]}' + chr(10))
        if summary.get('map5095_status') == 'not_computed':
            f.write('- mAP50-95: 未计算（当前评估仅计算 IoU=0.50 的 AP）' + chr(10))
            f.write(
                f'- AP50 复用参考值（非 mAP50-95）: {summary.get("map5095_approximation")}'
                + chr(10)
            )
        else:
            f.write(f'- mAP50-95: {summary["map5095"]}' + chr(10))
        f.write(f'- 整体精确率: {summary["overall_precision"]}' + chr(10))
        f.write(f'- 整体召回率: {summary["overall_recall"]}' + chr(10))
        f.write(f'- 整体F1: {summary["overall_f1"]}' + chr(10))
        if 'cpu_memory_mb' in summary:
            f.write(f'- CPU内存占用: {summary["cpu_memory_mb"]}MB' + chr(10))
        if 'gpu_memory_mb' in summary:
            f.write(f'- GPU内存占用: {summary["gpu_memory_mb"]}MB' + chr(10))
        for key, val in summary.get('extra_speed_metrics', {}).items():
            f.write(f'- {key}: {val:.2f}' + chr(10))
        f.write(chr(10) + '## 类别指标' + chr(10) + chr(10))
        f.write('| 类别 | 标签数 | TP | FP | FN | 精确率 | 召回率 | F1 | AP50 |' + chr(10))
        f.write('|------|--------|----|----|----|--------|--------|----|------|' + chr(10))
        for row in summary['per_class_rows']:
            f.write(f'| {row["name"]} | {row["labels"]} | {row["tp"]} | {row["fp"]} | {row["fn"]} | {row["precision"]:.4f} | {row["recall"]:.4f} | {row["f1"]:.4f} | {row["ap50"]:.4f} |' + chr(10))
        if curve_image_name:
            f.write(chr(10) + '## F1曲线' + chr(10) + chr(10))
            f.write(f'![F1 Curve]({curve_image_name})' + chr(10))
        if size_summary:
            f.write(chr(10) + '## 尺寸分档指标' + chr(10) + chr(10))
            f.write('> 尺寸分档基于相对面积占比 r = 目标面积 / 图像面积' + chr(10) + chr(10))
            for bin_name, bin_data in size_summary.items():
                desc = SIZE_BIN_DESC.get(bin_name, '')
                f.write(f'### {bin_name}  ({desc})' + chr(10) + chr(10))
                f.write(f'- mAP50: {bin_data["map50"]}' + chr(10))
                f.write('| 类别 | 精确率 | 召回率 | F1 |' + chr(10))
                f.write('|------|--------|--------|-----|' + chr(10))
                for cls in bin_data['per_class']:
                    f.write(f'| {cls["name"]} | {cls["precision"]:.4f} | {cls["recall"]:.4f} | {cls["f1"]:.4f} |' + chr(10))
            if size_image_path:
                f.write(chr(10) + f'![Size F1]({os.path.basename(size_image_path)})' + chr(10))
        if scene_summary:
            f.write(chr(10) + '## 场景维度指标' + chr(10) + chr(10))
            for field_key, val_dict in scene_summary.items():
                f.write(f'### {field_key}' + chr(10) + chr(10))
                for val, val_data in val_dict.items():
                    f.write(f'#### {val}' + chr(10) + chr(10))
                    f.write(f'- mAP50: {val_data["map50"]}' + chr(10))
                    f.write('| 类别 | 精确率 | 召回率 | F1 |' + chr(10))
                    f.write('|------|--------|--------|-----|' + chr(10))
                    for cls in val_data['per_class']:
                        f.write(f'| {cls["name"]} | {cls["precision"]:.4f} | {cls["recall"]:.4f} | {cls["f1"]:.4f} |' + chr(10))
                if scene_image_paths and field_key in scene_image_paths:
                    f.write(chr(10) + f'![Scene {field_key}]({os.path.basename(scene_image_paths[field_key])})' + chr(10))
    return report_path

def _normalize_class_names(names):
    if isinstance(names, dict):
        return [names[k] for k in sorted(names, key=lambda x: int(x))]
    return list(names or [])


def _read_voc_object_names(xml_path):
    import xml.etree.ElementTree as ET
    names = []
    try:
        root = ET.parse(xml_path).getroot()
        for obj in root.iter("object"):
            name = obj.find("name")
            if name is not None and name.text:
                names.append(name.text.strip())
    except Exception:
        pass
    return names


def load_dataset_from_yaml(yaml_path):
    """Load a YOLO yaml or a dataset directory with images/ and labelsxml/."""
    IMG_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}

    def _resolve(p):
        return p if os.path.isabs(p) else os.path.join(dataset_root, p)

    def _collect_images(src):
        if isinstance(src, list):
            paths = []
            for item in src:
                item = _resolve(item)
                if os.path.isdir(item):
                    paths.extend(_collect_images(item))
                elif os.path.splitext(item)[1].lower() in IMG_EXTS:
                    paths.append(item)
            return paths
        src = _resolve(src)
        if os.path.isdir(src):
            return sorted(
                os.path.join(src, f) for f in os.listdir(src)
                if os.path.splitext(f)[1].lower() in IMG_EXTS
            )
        if os.path.splitext(src)[1].lower() in IMG_EXTS:
            return [src]
        return []

    def _xml_path_for_image(img_path):
        root, _ = os.path.splitext(img_path)
        candidates = [
            root + '.xml',
            root.replace(os.sep + 'images' + os.sep, os.sep + 'labelsxml' + os.sep) + '.xml',
            root.replace(os.sep + 'images' + os.sep, os.sep + 'labelxml' + os.sep) + '.xml',
            root.replace(os.sep + 'images' + os.sep, os.sep + 'labels' + os.sep) + '.xml',
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return candidates[1]

    yaml_path = str(yaml_path)
    if os.path.isdir(yaml_path):
        dataset_root = os.path.abspath(yaml_path)
        image_roots = [
            os.path.join(dataset_root, 'images', 'test'),
            os.path.join(dataset_root, 'images', 'val'),
            os.path.join(dataset_root, 'images'),
            dataset_root,
        ]
        img_paths = []
        for image_root in image_roots:
            if os.path.isdir(image_root):
                img_paths = _collect_images(image_root)
                if img_paths:
                    break

        class_names = []
        seen_names = set()
        for img_path in img_paths:
            xml_path = _xml_path_for_image(img_path)
            if not os.path.exists(xml_path):
                continue
            for name in _read_voc_object_names(xml_path):
                if name not in seen_names:
                    seen_names.add(name)
                    class_names.append(name)
    else:
        import yaml
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        class_names = _normalize_class_names(data.get('names', []))
        base_dir = os.path.dirname(os.path.abspath(yaml_path))
        dataset_root = data.get('path', base_dir)
        if not os.path.isabs(dataset_root):
            dataset_root = os.path.join(base_dir, dataset_root)

        test_src = data.get('test') or data.get('val')
        img_paths = _collect_images(test_src) if test_src else []

    samples = []
    for img_path in img_paths:
        label_path = os.path.splitext(
            img_path.replace(os.sep + 'images' + os.sep, os.sep + 'labels' + os.sep)
        )[0] + '.txt'
        xml_label_path = _xml_path_for_image(img_path)
        samples.append(
            type('Sample', (), {
                'image_path': img_path,
                'label_path': label_path,
                'xml_label_path': xml_label_path,
                'scene_attrs': {}
            })()
        )
    return class_names, samples

def attach_scene_attrs(samples, xml_dir):
    import xml.etree.ElementTree as ET
    import json as _json
    for sample in samples:
        stem = os.path.splitext(os.path.basename(sample.image_path))[0]
        xml_path = getattr(sample, 'xml_label_path', None)
        if not xml_path or not os.path.exists(xml_path):
            xml_path = os.path.join(xml_dir, stem + ".xml")
        if not os.path.exists(xml_path) or os.path.isdir(xml_path):
            continue
        try:
            root = ET.parse(xml_path).getroot()
        except Exception:
            continue
        scene_attrs = {}
        extra_text = root.findtext("extra_annotations")
        if extra_text:
            try:
                for ann in _json.loads(extra_text):
                    if ann.get("toolName") != "tagTool":
                        continue
                    for tag_item in ann.get("result", []):
                        for key, val in tag_item.get("value", {}).items():
                            scene_attrs[key] = val[0] if isinstance(val, list) and val else str(val)
            except Exception:
                pass
        else:
            for field in ("sceneType", "lighting", "weather", "quality", "angle", "source"):
                node = root.find(field)
                if node is not None and node.text:
                    scene_attrs[field] = node.text.strip()
        sample.scene_attrs = scene_attrs

def load_yolo_labels(label_path, img_w, img_h, class_names=None, xml_label_path=None):
    """å è½½GTæ ç­¾. ä¼åè¯»TXT, ä¸å­å¨æ¶å°è¯ååXML(labelsxml/)."""
    txt = Path(label_path)
    if txt.exists():
        return parse_yolo_txt(txt, img_w, img_h)
    xml_path = xml_label_path or str(txt).replace(
        os.sep + "labels" + os.sep, os.sep + "labelsxml" + os.sep
    ).replace(".txt", ".xml")
    if os.path.exists(xml_path):
        return parse_voc_xml(xml_path, img_w, img_h, class_names or [])
    return []


def parse_voc_xml(xml_path, img_w, img_h, class_names):
    """ä» VOC XML è§£æ GT Detection åè¡¨."""
    import xml.etree.ElementTree as ET
    dets = []
    try:
        root = ET.parse(xml_path).getroot()
        size = root.find("size")
        w = int(size.find("width").text) if size is not None else img_w
        h = int(size.find("height").text) if size is not None else img_h
        for obj in root.iter("object"):
            name = obj.find("name")
            if name is None:
                continue
            cls_name = name.text.strip()
            cid = class_names.index(cls_name) if cls_name in class_names else -1
            if cid < 0:
                continue
            bb = obj.find("bndbox")
            x1 = float(bb.find("xmin").text)
            y1 = float(bb.find("ymin").text)
            x2 = float(bb.find("xmax").text)
            y2 = float(bb.find("ymax").text)
            dets.append(Detection(class_id=cid, score=1.0, x1=x1, y1=y1, x2=x2, y2=y2))
    except Exception:
        pass
    return dets


def get_cpu_memory_mb():
    """获取CPU内存（主脚本依赖）"""
    import psutil
    process = psutil.Process()
    return process.memory_info().rss / 1024 / 1024
