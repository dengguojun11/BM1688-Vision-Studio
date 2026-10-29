"""YOLO 标注与预测框：加载、匹配、绘制（逐图对比用）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class BBox:
    class_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


def parse_sample_id_from_stem(stem: str) -> Optional[int]:
    text = (stem or "").strip()
    if not text:
        return None
    head = text.split("_", 1)[0]
    if not head.isdigit():
        return None
    return int(head)


def load_yolo_label_file(
    label_path: Path,
    *,
    img_w: int,
    img_h: int,
) -> List[BBox]:
    if not label_path.is_file() or img_w <= 0 or img_h <= 0:
        return []
    boxes: List[BBox] = []
    try:
        lines = label_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            cid = int(float(parts[0]))
            xc, yc, nw, nh = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError:
            continue
        x1 = (xc - nw / 2) * img_w
        y1 = (yc - nh / 2) * img_h
        x2 = (xc + nw / 2) * img_w
        y2 = (yc + nh / 2) * img_h
        boxes.append(
            BBox(
                class_id=cid,
                x1=max(0.0, min(float(img_w), x1)),
                y1=max(0.0, min(float(img_h), y1)),
                x2=max(0.0, min(float(img_w), x2)),
                y2=max(0.0, min(float(img_h), y2)),
            )
        )
    return boxes


def bbox_iou(a: BBox, b: BBox) -> float:
    inter_x1 = max(a.x1, b.x1)
    inter_y1 = max(a.y1, b.y1)
    inter_x2 = min(a.x2, b.x2)
    inter_y2 = min(a.y2, b.y2)
    inter = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def match_boxes_per_class(
    predictions: Sequence[BBox],
    ground_truth: Sequence[BBox],
    *,
    class_count: int,
    iou_threshold: float = 0.5,
) -> Dict[str, Any]:
    """按类贪心匹配，返回 TP/FP/FN 及框级状态。"""
    pred_by: List[List[BBox]] = [[] for _ in range(class_count)]
    gt_by: List[List[BBox]] = [[] for _ in range(class_count)]
    for p in predictions:
        if 0 <= p.class_id < class_count:
            pred_by[p.class_id].append(p)
    for g in ground_truth:
        if 0 <= g.class_id < class_count:
            gt_by[g.class_id].append(g)

    matched_pairs: List[Dict[str, Any]] = []
    false_positives: List[Dict[str, Any]] = []
    false_negatives: List[Dict[str, Any]] = []

    for cid in range(class_count):
        preds = sorted(pred_by[cid], key=lambda b: -b.score)
        gts = gt_by[cid]
        gt_used = [False] * len(gts)
        for pred in preds:
            best_iou = 0.0
            best_idx = -1
            for idx, gt in enumerate(gts):
                if gt_used[idx]:
                    continue
                cur = bbox_iou(pred, gt)
                if cur > best_iou:
                    best_iou = cur
                    best_idx = idx
            if best_idx >= 0 and best_iou >= iou_threshold:
                gt_used[best_idx] = True
                matched_pairs.append(
                    {
                        "class_id": cid,
                        "iou": round(best_iou, 4),
                        "pred": _box_dict(pred),
                        "gt": _box_dict(gts[best_idx]),
                    }
                )
            else:
                false_positives.append(
                    {"class_id": cid, "best_iou": round(best_iou, 4), "pred": _box_dict(pred)}
                )
        for idx, gt in enumerate(gts):
            if gt_used[idx]:
                continue
            best_pred_iou = 0.0
            for pred in preds:
                best_pred_iou = max(best_pred_iou, bbox_iou(pred, gt))
            false_negatives.append(
                {"class_id": cid, "best_pred_iou": round(best_pred_iou, 4), "gt": _box_dict(gt)}
            )

    return {
        "tp": len(matched_pairs),
        "fp": len(false_positives),
        "fn": len(false_negatives),
        "gt": len(ground_truth),
        "pred": len(predictions),
        "matched": matched_pairs,
        "fps": false_positives,
        "fns": false_negatives,
    }


def _box_dict(box: BBox) -> Dict[str, Any]:
    return {
        "class_id": box.class_id,
        "x1": round(box.x1, 2),
        "y1": round(box.y1, 2),
        "x2": round(box.x2, 2),
        "y2": round(box.y2, 2),
        "score": round(box.score, 4),
    }


def classify_image_issues(match: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if match.get("fn"):
        issues.append("fn")
    if match.get("fp"):
        issues.append("fp")
    if not issues:
        issues.append("ok")
    return issues


def _class_display_name(class_names: Sequence[str], class_id: int) -> str:
    if 0 <= class_id < len(class_names):
        return str(class_names[class_id])
    return f"类别{class_id}"


def weak_class_note_cn(class_name: Optional[str]) -> str:
    if not class_name:
        return ""
    return (
        f"模型在此图上最薄弱的类别是「{class_name}」。"
        f"表示漏检/误检多属于该类，不是指标注有误；"
        f"若您在该类上标得很多，恰恰说明模型对该类召回不足。"
    )


def summarize_image_issues_cn(
    match: Dict[str, Any],
    class_names: Sequence[str],
    *,
    max_detail_items: int = 5,
) -> Dict[str, Any]:
    """生成单张图的中文问题说明（供前端展示）。"""
    from collections import Counter

    fn_count = int(match.get("fn") or 0)
    fp_count = int(match.get("fp") or 0)
    tp_count = int(match.get("tp") or 0)
    gt_count = int(match.get("gt") or 0)
    pred_count = int(match.get("pred") or 0)
    details: List[Dict[str, str]] = []
    count_line = (
        f"人工标注 {gt_count} 个框，模型输出 {pred_count} 个框。"
        f"（标注框数多，只说明图里目标多，不代表标注错误。）"
    )

    if fn_count == 0 and fp_count == 0:
        return {
            "summary": count_line + " 本图模型与标注完全一致。",
            "details": [],
            "level": "ok",
            "suggestions": [],
        }

    summary_parts: List[str] = []
    if tp_count:
        summary_parts.append(f"检对 {tp_count} 处（预测框与标注框成功配对）")
    if fn_count:
        summary_parts.append(f"漏检 {fn_count} 处（标注有、模型未检出或未对齐）")
    if fp_count:
        summary_parts.append(f"误检 {fp_count} 处（模型报了、标注里没有对应框）")
    summary = count_line + " " + "；".join(summary_parts) + "。"

    def _count_by_class(entries: Sequence[Dict], box_key: str) -> Counter:
        counts: Counter = Counter()
        for entry in entries:
            box = entry.get(box_key) or {}
            cid = int(box.get("class_id", 0))
            counts[_class_display_name(class_names, cid)] += 1
        return counts

    fns = list(match.get("fns") or [])
    fps = list(match.get("fps") or [])
    fn_by_class = _count_by_class(fns, "gt")
    fp_by_class = _count_by_class(fps, "pred")

    show_n = min(3, max_detail_items)
    fn_shown = 0
    for name, cnt in fn_by_class.most_common(show_n):
        details.append(
            {
                "type": "fn",
                "class": name,
                "text": f"「{name}」漏检 {cnt} 处：标注有、模型未检出或未对齐（左图黄框）。",
            }
        )
        fn_shown += cnt
    fn_rest = fn_count - fn_shown
    if fn_rest > 0:
        details.append(
            {
                "type": "fn",
                "class": "",
                "text": f"另有 {fn_rest} 处漏检分布在其它类别，请在左图查看黄框。",
            }
        )

    fp_shown = 0
    for name, cnt in fp_by_class.most_common(show_n):
        details.append(
            {
                "type": "fp",
                "class": name,
                "text": f"「{name}」误检 {cnt} 处：模型报了、附近无匹配标注（右图红框）。",
            }
        )
        fp_shown += cnt
    fp_rest = fp_count - fp_shown
    if fp_rest > 0:
        details.append(
            {
                "type": "fp",
                "class": "",
                "text": f"另有 {fp_rest} 处误检分布在其它类别，请在右图查看红框。",
            }
        )

    if fn_count or fp_count:
        details.append(
            {
                "type": "hint",
                "class": "",
                "text": "常见原因：目标过小/过密、夜间反光、训练样本不足，或标注较碎而模型只报大块。",
            }
        )

    suggestions: List[str] = []
    for name, cnt in fn_by_class.most_common(2):
        suggestions.append(f"「{name}」漏检 {cnt} 处：补相似场景样本，或检查是否需合并细碎标注。")
    for name, cnt in fp_by_class.most_common(2):
        suggestions.append(f"「{name}」误检 {cnt} 处：回看红框区域是否漏标，或适当提高推理 conf。")

    level = "error" if fn_count and fp_count else ("warn" if fn_count or fp_count else "ok")
    return {
        "summary": summary,
        "details": details,
        "level": level,
        "suggestions": suggestions,
    }


def summarize_image_issues_cn_from_row(
    row: Dict[str, Any],
    class_names: Sequence[str],
) -> Dict[str, Any]:
    """从 manifest 行（含 fn_by_class）生成聚合中文说明。"""
    fn = int(row.get("fn") or 0)
    fp = int(row.get("fp") or 0)
    tp = int(row.get("tp") or 0)
    gt = int(row.get("gt") or 0)
    pred = int(row.get("pred") or 0)
    fn_bc = row.get("fn_by_class") if isinstance(row.get("fn_by_class"), dict) else {}
    fp_bc = row.get("fp_by_class") if isinstance(row.get("fp_by_class"), dict) else {}
    if not fn_bc and not fp_bc:
        return summarize_image_issues_cn_brief(
            tp=tp,
            fp=fp,
            fn=fn,
            gt=gt,
            pred=pred,
            worst_class=row.get("worst_class"),
        )
    name_to_id = {str(n): i for i, n in enumerate(class_names)}
    fns: List[Dict[str, Any]] = []
    for name, cnt in fn_bc.items():
        cid = name_to_id.get(str(name), 0)
        for _ in range(int(cnt)):
            fns.append({"gt": {"class_id": cid}})
    fps: List[Dict[str, Any]] = []
    for name, cnt in fp_bc.items():
        cid = name_to_id.get(str(name), 0)
        for _ in range(int(cnt)):
            fps.append({"pred": {"class_id": cid}, "best_iou": 0.0})
    return summarize_image_issues_cn(
        {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "gt": gt,
            "pred": pred,
            "fns": fns,
            "fps": fps,
            "matched": [],
        },
        class_names,
    )


def summarize_image_issues_cn_brief(
    *,
    tp: int,
    fp: int,
    fn: int,
    gt: int = 0,
    pred: int = 0,
    worst_class: Optional[str] = None,
) -> Dict[str, Any]:
    """旧 manifest 无明细时，按计数生成简要中文说明。"""
    count_line = f"人工标注 {int(gt)} 个框，模型输出 {int(pred)} 个框。"
    if fp == 0 and fn == 0:
        return {
            "summary": count_line + " 本图模型与标注完全一致。",
            "details": [],
            "level": "ok",
            "suggestions": [],
        }
    parts: List[str] = []
    if tp:
        parts.append(f"检对 {tp} 处")
    if fn:
        parts.append(f"漏检 {fn} 处")
    if fp:
        parts.append(f"误检 {fp} 处")
    summary = count_line + " " + "；".join(parts) + "。"
    if worst_class:
        summary += " " + weak_class_note_cn(worst_class)
    details: List[Dict[str, str]] = []
    if fn:
        details.append(
            {
                "type": "fn",
                "class": worst_class or "",
                "text": (
                    f"共 {fn} 处漏检：标注里有、模型没检出或未对齐。"
                    f"请在标注图（左）看黄色框；标得多不等于标错，多是模型召回不足。"
                ),
            }
        )
    if fp:
        details.append(
            {
                "type": "fp",
                "class": worst_class or "",
                "text": (
                    f"共 {fp} 处误检：模型报了、标注无对应框。"
                    f"请在预测图（右）看红色框；可能是背景误识别或标注遗漏。"
                ),
            }
        )
    return {
        "summary": summary,
        "details": details,
        "level": "error" if fn and fp else "warn",
        "suggestions": [],
    }


def draw_labeled_image(
    image_path: Path,
    *,
    ground_truth: Sequence[BBox],
    predictions: Sequence[BBox],
    class_names: Sequence[str],
    match: Dict[str, Any],
    mode: str,
    max_side: int = 1280,
) -> bytes:
    """mode: gt | pred"""
    import cv2  # noqa: WPS433
    import numpy as np  # noqa: WPS433

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"无法读取图片: {image_path}")

    h, w = img.shape[:2]
    scale = 1.0
    if max(h, w) > max_side:
        scale = max_side / float(max(h, w))
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    def _scale_box(b: BBox) -> Tuple[int, int, int, int]:
        return (
            int(b.x1 * scale),
            int(b.y1 * scale),
            int(b.x2 * scale),
            int(b.y2 * scale),
        )

    def _name(cid: int) -> str:
        if 0 <= cid < len(class_names):
            return str(class_names[cid])
        return str(cid)

    if mode == "gt":
        for fn in match.get("fns") or []:
            gt = fn.get("gt") or {}
            cid = int(gt.get("class_id", 0))
            b = BBox(cid, gt["x1"], gt["y1"], gt["x2"], gt["y2"])
            x1, y1, x2, y2 = _scale_box(b)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 220, 255), 2)
            cv2.putText(img, _name(cid), (x1, max(14, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1)
        for m in match.get("matched") or []:
            gt = m.get("gt") or {}
            cid = int(gt.get("class_id", 0))
            b = BBox(cid, gt["x1"], gt["y1"], gt["x2"], gt["y2"])
            x1, y1, x2, y2 = _scale_box(b)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 0), 2)
            cv2.putText(img, _name(cid), (x1, max(14, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 0), 1)
    else:
        for fp in match.get("fps") or []:
            pred = fp.get("pred") or {}
            cid = int(pred.get("class_id", 0))
            b = BBox(cid, pred["x1"], pred["y1"], pred["x2"], pred["y2"], score=pred.get("score", 1))
            x1, y1, x2, y2 = _scale_box(b)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(
                img,
                f"{_name(cid)} {b.score:.2f}",
                (x1, max(14, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 0, 255),
                1,
            )
        for m in match.get("matched") or []:
            pred = m.get("pred") or {}
            cid = int(pred.get("class_id", 0))
            b = BBox(cid, pred["x1"], pred["y1"], pred["x2"], pred["y2"], score=pred.get("score", 1))
            x1, y1, x2, y2 = _scale_box(b)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 0), 2)
            cv2.putText(
                img,
                f"{_name(cid)} {b.score:.2f}",
                (x1, max(14, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 200, 0),
                1,
            )
        for fn in match.get("fns") or []:
            gt = fn.get("gt") or {}
            cid = int(gt.get("class_id", 0))
            b = BBox(cid, gt["x1"], gt["y1"], gt["x2"], gt["y2"])
            x1, y1, x2, y2 = _scale_box(b)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 220, 255), 1, cv2.LINE_AA)

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise ValueError("编码对比图失败")
    return buf.tobytes()
