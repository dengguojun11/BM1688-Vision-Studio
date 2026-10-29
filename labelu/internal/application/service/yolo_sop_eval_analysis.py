"""SOP 模型评估：按类别精度对比与根因分析（供模型评估页展示）。"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_CLASS_TABLE_HEADER = re.compile(r"^\|\s*类别\s*\|")
_CLASS_ROW = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
    r"\s*([0-9.]+|nan)\s*\|\s*([0-9.]+|nan)\s*\|\s*([0-9.]+|nan)\s*\|\s*([0-9.]+|nan)\s*\|",
    re.IGNORECASE,
)
_SIZE_TIER = re.compile(r"^###\s+(tiny|small|medium|large)\s+", re.IGNORECASE)
_SIZE_MAP50 = re.compile(r"^-\s*mAP50:\s*([0-9.]+|nan)", re.IGNORECASE)


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip().lower()
    if not s or s in ("nan", "none", "—", "-"):
        return None
    try:
        f = float(s)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _round4(val: Optional[float]) -> Optional[float]:
    if val is None:
        return None
    return round(float(val), 4)


def parse_class_metrics_from_report_md(report_path: Path) -> List[Dict[str, Any]]:
    """从 evaluation_report.md 解析「类别指标」表。"""
    if not report_path.is_file():
        return []
    text = report_path.read_text(encoding="utf-8", errors="replace")
    in_section = False
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## 类别指标"):
            in_section = True
            continue
        if in_section and s.startswith("## "):
            break
        if not in_section:
            continue
        if not _CLASS_TABLE_HEADER.match(s):
            m = _CLASS_ROW.match(s)
            if m:
                name = m.group(1).strip()
                if name in ("类别", "------") or name.startswith("-"):
                    continue
                rows.append(
                    {
                        "name": name,
                        "labels": int(m.group(2)),
                        "tp": int(m.group(3)),
                        "fp": int(m.group(4)),
                        "fn": int(m.group(5)),
                        "precision": _safe_float(m.group(6)),
                        "recall": _safe_float(m.group(7)),
                        "f1": _safe_float(m.group(8)),
                        "ap50": _safe_float(m.group(9)),
                    }
                )
    return rows


def parse_size_tier_map50_from_report_md(
    report_path: Path,
) -> Dict[str, Optional[float]]:
    """解析尺寸分档 mAP50（tiny/small/medium/large）。"""
    if not report_path.is_file():
        return {}
    text = report_path.read_text(encoding="utf-8", errors="replace")
    in_section = False
    current: Optional[str] = None
    out: Dict[str, Optional[float]] = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## 尺寸分档指标"):
            in_section = True
            continue
        if in_section and s.startswith("## ") and not s.startswith("###"):
            break
        if not in_section:
            continue
        m_t = _SIZE_TIER.match(s)
        if m_t:
            current = m_t.group(1).lower()
            continue
        if current:
            m_m = _SIZE_MAP50.match(s)
            if m_m:
                out[current] = _safe_float(m_m.group(1))
                current = None
    return out


def _class_severity(ap50_delta: Optional[float], fn_delta: int) -> str:
    if ap50_delta is not None and ap50_delta <= -0.30:
        return "critical"
    if ap50_delta is not None and ap50_delta <= -0.05:
        return "high"
    if fn_delta >= 3 or (ap50_delta is not None and ap50_delta <= -0.01):
        return "medium"
    if ap50_delta is not None and ap50_delta < -0.001:
        return "low"
    return "stable"


def _merge_class_drops(
    pt_rows: List[Dict[str, Any]],
    bm_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    bm_by_name = {r["name"]: r for r in bm_rows}
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for pt in pt_rows:
        name = pt["name"]
        seen.add(name)
        bm = bm_by_name.get(name) or {}
        pt_ap = pt.get("ap50")
        bm_ap = bm.get("ap50")
        ap_delta = None
        if pt_ap is not None and bm_ap is not None:
            ap_delta = round(bm_ap - pt_ap, 4)
        pt_rec = pt.get("recall")
        bm_rec = bm.get("recall")
        recall_delta = None
        if pt_rec is not None and bm_rec is not None:
            recall_delta = round(bm_rec - pt_rec, 4)
        fn_delta = int(bm.get("fn") or 0) - int(pt.get("fn") or 0)
        merged.append(
            {
                "name": name,
                "labels": pt.get("labels"),
                "pt_ap50": _round4(pt_ap),
                "bmodel_ap50": _round4(bm_ap),
                "ap50_delta": ap_delta,
                "pt_recall": _round4(pt_rec),
                "bmodel_recall": _round4(bm_rec),
                "recall_delta": recall_delta,
                "pt_fn": pt.get("fn"),
                "bmodel_fn": bm.get("fn"),
                "fn_delta": fn_delta,
                "severity": _class_severity(ap_delta, fn_delta),
            }
        )
    for name, bm in bm_by_name.items():
        if name in seen:
            continue
        merged.append(
            {
                "name": name,
                "labels": bm.get("labels"),
                "pt_ap50": None,
                "bmodel_ap50": _round4(bm.get("ap50")),
                "ap50_delta": None,
                "pt_recall": None,
                "bmodel_recall": _round4(bm.get("recall")),
                "recall_delta": None,
                "pt_fn": None,
                "bmodel_fn": bm.get("fn"),
                "fn_delta": 0,
                "severity": "medium",
            }
        )
    merged.sort(
        key=lambda r: (
            (
                0
                if r.get("severity") == "critical"
                else 1 if r.get("severity") == "high" else 2
            ),
            r.get("ap50_delta") if r.get("ap50_delta") is not None else 0,
        )
    )
    return merged


def _build_root_causes(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    causes: List[Dict[str, Any]] = []

    def add(
        priority: int,
        severity: str,
        title: str,
        evidence: str,
        action: str,
    ) -> None:
        causes.append(
            {
                "priority": priority,
                "severity": severity,
                "title": title,
                "evidence": evidence,
                "action": action,
            }
        )

    onnx_verdict = ctx.get("onnx_verdict")
    bmodel_verdict = ctx.get("bmodel_verdict")
    bmodel_drop = ctx.get("bmodel_drop_map50")
    bmodel_max_drop = ctx.get("bmodel_max_drop")
    bmodel_precision = str(ctx.get("bmodel_precision") or "int8").upper()
    is_quantized = bmodel_precision == "INT8"
    loss_label = "量化损失" if is_quantized else "部署转换精度损失"

    if bmodel_verdict == "fail" and bmodel_drop is not None:
        add(
            1,
            "high",
            f"{bmodel_precision} {loss_label}（主因）",
            f"{bmodel_precision} BModel 相对 PT 的 mAP50 掉点 {bmodel_drop:.4f}，"
            f"超过最大允许掉点 {float(bmodel_max_drop or 0.0):.4f}。",
            (
                "使用同口径校准集重跑 INT8 校准与编译，并与 FP16 BModel 对比。"
                if is_quantized
                else "核验 PT/ONNX/BModel 的预处理、后处理和阈值配置，修正后重新编译 FP16 BModel。"
            ),
        )
    elif bmodel_verdict == "review":
        add(
            1,
            "medium",
            f"{bmodel_precision} 转换保真度需复核",
            str(
                ctx.get("bmodel_detail") or f"{loss_label}位于复核区间或指标提升异常。"
            ),
            "核验 PT/BModel 的数据集、预处理与评估参数一致性，再决定是否发布。",
        )

    worst = ctx.get("worst_class")
    if worst and worst.get("severity") in ("critical", "high"):
        add(
            2,
            worst["severity"],
            f"类别「{worst['name']}」拖累最大",
            f"AP50 {worst.get('pt_ap50')} → {worst.get('bmodel_ap50')}（Δ {worst.get('ap50_delta')}），"
            f"FN {worst.get('pt_fn')} → {worst.get('bmodel_fn')}（+{worst.get('fn_delta', 0)}）。",
            "补该类别标注样本并重训；量化时确保校准集含足够该类别实例。",
        )

    fn_delta = ctx.get("total_fn_delta")
    recall_delta = ctx.get("recall_delta")
    if fn_delta is not None and fn_delta > 0:
        add(
            3,
            "medium",
            "漏检（FN）是主要精度损失形式",
            f"全局召回 {ctx.get('pt_recall')} → {ctx.get('bmodel_recall')}（Δ {recall_delta}），"
            f"可统计类合计 FN +{fn_delta}。",
            (
                "INT8 后置信度分布下移常见；优先查弱类与小目标，而非仅调 NMS。"
                if is_quantized
                else "优先核对部署前后预处理、后处理和阈值，再检查弱类与小目标。"
            ),
        )

    test_images = ctx.get("test_images")
    if test_images is not None and test_images < 50:
        add(
            4,
            "medium",
            "测试集样本量偏小，指标波动被放大",
            f"当前 SOP test 集仅 {test_images} 张图。",
            "合并更多任务/作业扩大 test 集；门控结论仍有效但类级 AP 易抖动。",
        )

    weak_pt = ctx.get("weak_pt_classes") or []
    if weak_pt:
        names = "、".join(c["name"] for c in weak_pt[:3])
        add(
            5,
            "medium",
            "PT 阶段已有弱类（量化前）",
            f"PT 时 AP50<0.75：{names}。",
            "先提升 PT 上限（补数据/重训），再重新导出与边端评估。",
        )

    size_drops = ctx.get("size_tier_drops") or []
    for sd in size_drops:
        if sd.get("delta") is not None and sd["delta"] <= -0.05:
            add(
                6,
                "medium",
                f"尺寸档「{sd['tier']}」{bmodel_precision} 损失明显",
                f"PT mAP50 {sd.get('pt_map50')} → {bmodel_precision} {sd.get('bmodel_map50')}（Δ {sd.get('delta')}）。",
                (
                    "小目标对 INT8 更敏感；增加小目标样本与校准图。"
                    if is_quantized
                    else "核对小目标在 PT 与部署链路中的缩放、填充和后处理差异。"
                ),
            )
            break

    if onnx_verdict == "pass" and bmodel_verdict == "pass":
        add(
            1,
            "ok",
            "三门转化均通过",
            f"ONNX 与 {bmodel_precision} BModel 相对 PT 的单向 mAP50 掉点均在通过标准内。",
            f"可继续后续发布流程（{bmodel_precision}）或按需查看类级明细。",
        )

    causes.sort(key=lambda c: c["priority"])
    return causes


def build_sop_eval_analysis(
    export_root: Path,
    sop_summary: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """基于 PT / bmodel evaluation_report.md 生成分析结构。"""
    if not sop_summary or not export_root:
        return None

    export_root = Path(export_root)
    pt_block = sop_summary.get("pt") or {}
    bm_block = sop_summary.get("bmodel") or {}
    pt_dir = Path(
        pt_block.get("results_dir") or export_root / "server_eval" / "results_cloud"
    )
    bm_dir = Path(
        bm_block.get("results_dir") or export_root / "server_eval" / "results_bmodel"
    )
    pt_report = pt_dir / "evaluation_report.md"
    bm_report = bm_dir / "evaluation_report.md"

    pt_rows = parse_class_metrics_from_report_md(pt_report)
    bm_rows = parse_class_metrics_from_report_md(bm_report)
    if not pt_rows and not bm_rows:
        return None

    pt_m = (pt_block.get("metrics") or {}) if isinstance(pt_block, dict) else {}
    bm_m = (bm_block.get("metrics") or {}) if isinstance(bm_block, dict) else {}
    cmp_onnx = sop_summary.get("comparison_pt_onnx") or {}
    cmp_bm = sop_summary.get("comparison_pt_bmodel") or {}

    class_drops = _merge_class_drops(pt_rows, bm_rows)
    worst = next(
        (c for c in class_drops if c.get("severity") in ("critical", "high")), None
    )
    if not worst and class_drops:
        worst = min(
            class_drops,
            key=lambda c: c.get("ap50_delta") if c.get("ap50_delta") is not None else 0,
        )

    total_fn_pt = sum(int(r.get("pt_fn") or 0) for r in class_drops if r.get("labels"))
    total_fn_bm = sum(
        int(r.get("bmodel_fn") or 0) for r in class_drops if r.get("labels")
    )
    fn_delta = total_fn_bm - total_fn_pt if class_drops else None

    pt_rec = _safe_float(pt_m.get("recall"))
    bm_rec = _safe_float(bm_m.get("recall"))
    recall_delta = (
        round(bm_rec - pt_rec, 4) if pt_rec is not None and bm_rec is not None else None
    )

    pt_size = parse_size_tier_map50_from_report_md(pt_report)
    bm_size = parse_size_tier_map50_from_report_md(bm_report)
    size_tier_drops: List[Dict[str, Any]] = []
    for tier in ("tiny", "small", "medium", "large"):
        p_v = pt_size.get(tier)
        b_v = bm_size.get(tier)
        if p_v is None and b_v is None:
            continue
        delta = round(b_v - p_v, 4) if p_v is not None and b_v is not None else None
        size_tier_drops.append(
            {
                "tier": tier,
                "pt_map50": _round4(p_v),
                "bmodel_map50": _round4(b_v),
                "delta": delta,
            }
        )

    weak_pt = [
        {"name": r["name"], "ap50": r.get("ap50")}
        for r in pt_rows
        if r.get("labels") and r.get("ap50") is not None and r["ap50"] < 0.75
    ]

    onnx_delta_pct = (
        float(cmp_onnx.get("delta_ratio") or 0) * 100
        if cmp_onnx.get("delta_ratio") is not None
        else None
    )
    bmodel_delta_pct = (
        float(cmp_bm.get("delta_ratio") or 0) * 100
        if cmp_bm.get("delta_ratio") is not None
        else None
    )
    onnx_drop = _safe_float(cmp_onnx.get("drop_map50"))
    bmodel_drop = _safe_float(cmp_bm.get("drop_map50"))
    if onnx_drop is None:
        pt_map50 = _safe_float(pt_m.get("map50"))
        onnx_map50 = _safe_float(cmp_onnx.get("onnx_map50"))
        if pt_map50 is not None and onnx_map50 is not None:
            onnx_drop = max(pt_map50 - onnx_map50, 0.0)
    if bmodel_drop is None:
        pt_map50 = _safe_float(pt_m.get("map50"))
        bmodel_map50 = _safe_float(bm_m.get("map50"))
        if pt_map50 is not None and bmodel_map50 is not None:
            bmodel_drop = max(pt_map50 - bmodel_map50, 0.0)

    def comparison_verdict(comparison: Dict[str, Any]) -> str:
        explicit = str(comparison.get("verdict") or "").strip().lower()
        if explicit:
            return explicit
        return {
            "ok": "pass",
            "warn": "review",
            "fail": "fail",
            "skip": "not_evaluable",
        }.get(str(comparison.get("status") or "").strip().lower(), "not_evaluable")

    onnx_verdict = comparison_verdict(cmp_onnx)
    bmodel_verdict = comparison_verdict(cmp_bm)

    ctx = {
        "onnx_verdict": onnx_verdict,
        "bmodel_verdict": bmodel_verdict,
        "onnx_delta_pct": onnx_delta_pct,
        "bmodel_delta_pct": bmodel_delta_pct,
        "onnx_drop_map50": onnx_drop,
        "bmodel_drop_map50": bmodel_drop,
        "bmodel_max_drop": _safe_float(cmp_bm.get("max_drop")) or 0.02,
        "bmodel_detail": cmp_bm.get("detail"),
        "bmodel_precision": cmp_bm.get("precision") or "int8",
        "worst_class": worst,
        "total_fn_delta": fn_delta,
        "recall_delta": recall_delta,
        "pt_recall": _round4(pt_rec),
        "bmodel_recall": _round4(bm_rec),
        "test_images": pt_m.get("images") or bm_m.get("images"),
        "weak_pt_classes": weak_pt,
        "size_tier_drops": size_tier_drops,
    }

    verdict = {
        "pass": "pass",
        "review": "review",
        "fail": "fail",
        "not_evaluable": "skip",
    }.get(bmodel_verdict, "pending")

    return {
        "status": verdict,
        "summary": cmp_bm.get("detail") or cmp_onnx.get("detail") or "",
        "global_delta": {
            "pt_map50": _round4(_safe_float(pt_m.get("map50"))),
            "bmodel_map50": _round4(_safe_float(bm_m.get("map50"))),
            "pt_precision": _round4(_safe_float(pt_m.get("precision"))),
            "bmodel_precision": _round4(_safe_float(bm_m.get("precision"))),
            "pt_recall": _round4(pt_rec),
            "bmodel_recall": _round4(bm_rec),
            "recall_delta": recall_delta,
            "total_fn_pt": total_fn_pt,
            "total_fn_bmodel": total_fn_bm,
            "total_fn_delta": fn_delta,
            "test_images": ctx["test_images"],
            "bmodel_delta_ratio": cmp_bm.get("delta_ratio"),
            "bmodel_delta_pct": (
                round(bmodel_delta_pct, 2) if bmodel_delta_pct is not None else None
            ),
            "bmodel_signed_delta_map50": cmp_bm.get("signed_delta_map50"),
            "bmodel_drop_map50": _round4(bmodel_drop),
            "bmodel_drop_ratio": cmp_bm.get("drop_ratio"),
            "bmodel_max_drop": cmp_bm.get("max_drop"),
        },
        "class_drops": class_drops,
        "size_tier_drops": size_tier_drops,
        "root_causes": _build_root_causes(ctx),
        "sources": {
            "pt_report": str(pt_report) if pt_report.is_file() else None,
            "bmodel_report": str(bm_report) if bm_report.is_file() else None,
        },
    }


def attach_sop_eval_analysis(
    data: Dict[str, Any], export_root: Optional[Path]
) -> Dict[str, Any]:
    """写入 sop_eval_summary.analysis 字段。"""
    if not data or not export_root:
        return data
    analysis = build_sop_eval_analysis(Path(export_root), data)
    if analysis:
        data["analysis"] = analysis
    return data
