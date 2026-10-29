"""文档 3.6.2 服务端评估：在 LabelU 部署机对 pt / onnx 跑 SOP 脚本（bmodel 仍在边端）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from labelu.internal.application.service.yolo_train import _job_work_root
from labelu.internal.common.bm1688_quality_rules import (
    BM1688_QUALITY_RULE_VERSION,
    BM1688_QUALITY_RULE,
    get_bm1688_quality_rule,
)
from labelu.internal.common.config import settings

CONVERSION_GATE_CRITERIA_VERSION = "map50-absolute-drop-v2"
ONNX_PASS_MAX_DROP = BM1688_QUALITY_RULE["conversion"]["onnx_vs_pt"]["pass_max_drop"]
ONNX_REVIEW_MAX_DROP = BM1688_QUALITY_RULE["conversion"]["onnx_vs_pt"]["review_max_drop"]
BMODEL_PASS_MAX_DROP = BM1688_QUALITY_RULE["conversion"]["fp32_vs_pt"]["pass_max_drop"]
BMODEL_REVIEW_MAX_DROP = BM1688_QUALITY_RULE["conversion"]["fp32_vs_pt"]["review_max_drop"]


def build_conversion_gate_criteria() -> Dict[str, Any]:
    """Return a serializable snapshot of the conversion-fidelity criteria."""
    return {
        "version": CONVERSION_GATE_CRITERIA_VERSION,
        "quality_rule_version": BM1688_QUALITY_RULE_VERSION,
        "primary_metric": "mAP50",
        "threshold_unit": "map50_absolute_drop",
        "comparison_direction": "one_sided_pt_drop",
        "onnx_vs_pt": {
            "pass_max_drop": ONNX_PASS_MAX_DROP,
            "review_max_drop": ONNX_REVIEW_MAX_DROP,
            "max_drop": ONNX_REVIEW_MAX_DROP,
        },
        "bmodel_vs_pt": {
            "pass_max_drop": BMODEL_PASS_MAX_DROP,
            "review_max_drop": BMODEL_REVIEW_MAX_DROP,
            "max_drop": BMODEL_REVIEW_MAX_DROP,
        },
        "significant_improvement_review": True,
        "legacy_reference": {
            "active": False,
            "threshold_unit": "relative_absolute_difference",
            "onnx_vs_pt_max_ratio": 0.03,
            "bmodel_vs_pt_max_ratio": 0.05,
        },
        "purpose": (
            "检验模型转换保真度；主门禁使用候选模型相对 PT 的 mAP50 单向绝对掉点，"
            "相对差值仅作为兼容参考"
        ),
        "quality_rule": get_bm1688_quality_rule(),
    }


def _sop_eval_scripts_dir() -> Path:
    import labelu

    return Path(labelu.__file__).resolve().parent / "scripts" / "sop_eval"


def _export_root(export_id: int) -> Path:
    return (
        Path(settings.MEDIA_ROOT) / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    )


def discover_manual_bmodel_results_dir(export_root: Path) -> Optional[Path]:
    """边端 SSH 回传的 bmodel 评估目录（manual_edge_validation/job-*/server_eval/results_bmodel）。"""
    export_root = Path(export_root)
    base = export_root / "manual_edge_validation"
    if not base.is_dir():
        return None
    candidates = sorted(base.glob("job-*/server_eval/results_bmodel"), reverse=True)
    for candidate in candidates:
        if (candidate / "eval_summary.json").is_file() or (
            candidate / "evaluation_report.md"
        ).is_file():
            return candidate
    return None


def discover_manual_bmcv_preview_dir(export_root: Path) -> Optional[Path]:
    """边端 bmcv 可视化预览目录（manual_edge_validation/job-*/server_eval/bmcv_preview 等）。"""
    export_root = Path(export_root)
    base = export_root / "manual_edge_validation"
    if not base.is_dir():
        return None
    candidates: List[Path] = []
    candidates.extend(sorted(base.glob("job-*/server_eval/bmcv_preview"), reverse=True))
    candidates.extend(sorted(base.glob("job-*/bmcv_preview"), reverse=True))
    for candidate in candidates:
        if candidate.is_dir() and any(
            p.is_file()
            and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
            for p in candidate.iterdir()
        ):
            return candidate
    return None


def _sync_sop_eval_block_paths(block: Dict[str, Any], rpath: Path) -> None:
    """同步 results_dir / report / eval_summary 路径字段。"""
    rpath = Path(rpath)
    report = rpath / "evaluation_report.md"
    summary = rpath / "eval_summary.json"
    block["results_dir"] = str(rpath)
    block["report_md_path"] = str(report)
    block["report_md_exists"] = report.is_file()
    block["eval_summary_path"] = str(summary)
    block["eval_summary_exists"] = summary.is_file()


def _resolve_bmodel_results_dir(export_id: int) -> Path:
    export_root = _export_root(export_id)
    server_dir = export_root / "server_eval" / "results_bmodel"
    if (server_dir / "evaluation_report.md").is_file() or (
        server_dir / "eval_summary.json"
    ).is_file():
        return server_dir
    manual = discover_manual_bmodel_results_dir(export_root)
    if manual:
        return manual
    return server_dir


def _read_class_names_from_data_yaml(dataset_root: Path) -> List[str]:
    for name in ("data.yaml", "dataset.yaml"):
        p = dataset_root / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        names: List[str] = []
        in_names = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("names:"):
                in_names = True
                continue
            if in_names:
                m = re.match(r"^\s*(\d+)\s*:\s*(.+)$", s)
                if m:
                    names.append(m.group(2).strip().strip("'\""))
                elif s and not s.startswith("#"):
                    in_names = False
        if names:
            return names
    cn = dataset_root / "class_names.txt"
    if cn.is_file():
        return [
            ln.strip()
            for ln in cn.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    return []


def _resolve_test_eval_paths(
    *,
    yolo_train_job_id: int,
    export_id: int,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path], str]:
    """
    返回 (dataset_root, images_dir, xml_dir, split_name)。
    优先 test，无图则用 val。
    """
    dataset_root = _job_work_root(yolo_train_job_id) / "dataset"
    if not dataset_root.is_dir():
        return None, None, None, ""

    for split in ("test", "val"):
        img_dir = dataset_root / "images" / split
        if img_dir.is_dir() and any(img_dir.iterdir()):
            xml_dir = dataset_root / "labelsxml" / split
            if not xml_dir.is_dir():
                xml_dir = dataset_root / "labelsxml" / "test"
            return dataset_root, img_dir, xml_dir if xml_dir.is_dir() else None, split
    return dataset_root, None, None, ""


def _resolve_onnx_path(export_id: int) -> Optional[Path]:
    from labelu.internal.common import bm1688_compiler_config as cfg_io

    root = _export_root(export_id)
    cfg = cfg_io.load_bm1688_compiler_config()
    onnx_fn = str(cfg.get("onnx_filename") or cfg_io.DEFAULT_ONNX_FILENAME)
    p = root / "models" / "onnx" / onnx_fn
    if p.is_file():
        return p
    onnx_dir = root / "models" / "onnx"
    if onnx_dir.is_dir():
        for f in sorted(onnx_dir.glob("*.onnx")):
            return f
    return None


def _pick_python() -> str:
    venv = os.environ.get("VENV_DIR", "").strip()
    if venv:
        cand = Path(venv) / "bin" / "python3"
        if cand.is_file():
            return str(cand)
        cand = Path(venv) / "Scripts" / "python.exe"
        if cand.is_file():
            return str(cand)
    return sys.executable


def _run_eval_script(
    script_name: str,
    *,
    env_extra: Dict[str, str],
    timeout_sec: int,
) -> Tuple[bool, str]:
    script = _sop_eval_scripts_dir() / script_name
    if not script.is_file():
        return False, f"缺少脚本: {script}"

    env = os.environ.copy()
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    save_dir = Path(str(env_extra.get("EVAL_SAVE_DIR") or "").strip())
    try:
        proc = subprocess.run(
            [_pick_python(), str(script)],
            cwd=str(script.parent),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时 (>{timeout_sec}s)"
    except OSError as exc:
        return False, str(exc)

    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if save_dir.is_dir() and (save_dir / "eval_summary.json").is_file():
        if proc.returncode != 0:
            return (
                True,
                (log[-2000:] or f"exit={proc.returncode}")
                + "\n(recovered: eval_summary.json)",
            )
        return True, log[-2000:]
    if proc.returncode != 0:
        return False, log[-4000:] or f"exit={proc.returncode}"
    return True, log[-2000:]


_REPORT_METRIC_PATTERNS = {
    "map50": re.compile(r"-\s*mAP50:\s*([0-9.]+)", re.IGNORECASE),
    "map50_95": re.compile(r"-\s*mAP50-95:\s*([0-9.]+)", re.IGNORECASE),
    "precision": re.compile(r"-\s*整体精确率:\s*([0-9.]+)"),
    "recall": re.compile(r"-\s*整体召回率:\s*([0-9.]+)"),
    "images": re.compile(r"-\s*评估图片数:\s*(\d+)"),
}


def _normalize_eval_metrics(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """统一 eval_summary / 报告字段，兼容旧版 precision 写错为 null 的导出。"""
    if not raw or not isinstance(raw, dict):
        return None
    out = dict(raw)
    if out.get("precision") is None and out.get("overall_precision") is not None:
        out["precision"] = out["overall_precision"]
    if out.get("recall") is None and out.get("overall_recall") is not None:
        out["recall"] = out["overall_recall"]
    validity = (
        out.get("metric_validity")
        if isinstance(out.get("metric_validity"), dict)
        else {}
    )
    map5095_validity = (
        validity.get("map50_95") if isinstance(validity.get("map50_95"), dict) else {}
    )
    map5095_status = (
        str(out.get("map50_95_status") or map5095_validity.get("status") or "")
        .strip()
        .lower()
    )
    if map5095_status == "not_computed":
        out["map50_95"] = None
        out["map50_95_status"] = "not_computed"
    elif out.get("map50_95") is None and out.get("map5095") is not None:
        out["map50_95"] = out["map5095"]
    return out


def _parse_metrics_from_report_md(report_path: Path) -> Dict[str, Any]:
    if not report_path.is_file():
        return {}
    text = report_path.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, Any] = {}
    for key, pattern in _REPORT_METRIC_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            continue
        val: Any = int(m.group(1)) if key == "images" else float(m.group(1))
        out[key] = val
    return out


def _load_eval_summary(results_dir: Path) -> Optional[Dict[str, Any]]:
    p = results_dir / "eval_summary.json"
    loaded: Optional[Dict[str, Any]] = None
    if p.is_file():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
    metrics = _normalize_eval_metrics(loaded)
    report_metrics = _parse_metrics_from_report_md(results_dir / "evaluation_report.md")
    if not metrics:
        metrics = report_metrics or None
    elif report_metrics:
        for key, val in report_metrics.items():
            if val is None:
                continue
            cur = metrics.get(key)
            if cur is None:
                metrics[key] = val
            elif key in ("precision", "recall") and cur == 0 and val != 0:
                metrics[key] = val
    return metrics


def _metrics_need_reload(metrics: Optional[Dict[str, Any]]) -> bool:
    if not metrics or not isinstance(metrics, dict):
        return True
    normalized = _normalize_eval_metrics(metrics) or {}
    if normalized.get("map50") is None and normalized.get("map50_95") is None:
        return True
    if normalized.get("precision") is None or normalized.get("recall") is None:
        return True
    # 旧版 summary 曾写入 precision/recall=0 而 report.md 有真实值
    if normalized.get("map50") is not None:
        p = normalized.get("precision")
        r = normalized.get("recall")
        try:
            if float(p or 0) == 0.0 and float(r or 0) == 0.0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _merge_block_metrics_from_disk(block: Dict[str, Any], results_dir: Path) -> None:
    """eval_summary.json / evaluation_report.md 存在但 summary 未写入 metrics 时，从磁盘补齐。"""
    if not block.get("results_dir"):
        block["results_dir"] = str(results_dir)
    loaded = _load_eval_summary(results_dir)
    if not loaded:
        return
    if _metrics_need_reload(block.get("metrics")):
        block["metrics"] = loaded
    elif block.get("metrics"):
        merged = dict(_normalize_eval_metrics(block.get("metrics")) or {})
        for key, val in loaded.items():
            if merged.get(key) is None and val is not None:
                merged[key] = val
        block["metrics"] = merged
    if block.get("status") in (None, "", "pending") and loaded.get("map50") is not None:
        block["status"] = "ok"


def _compare_pt_candidate(
    pt: Optional[Dict],
    candidate: Optional[Dict],
    *,
    candidate_kind: str,
    candidate_label: Optional[str] = None,
    pass_max_drop: float,
    max_drop: float,
) -> Dict[str, Any]:
    """Compare conversion fidelity using one-sided absolute mAP50 drop."""
    candidate_key = f"{candidate_kind}_map50"
    candidate_label = candidate_label or (
        "INT8 BModel" if candidate_kind == "bmodel" else candidate_kind.upper()
    )
    pass_limit = max(float(pass_max_drop), 0.0)
    review_limit = max(float(max_drop), pass_limit)
    base: Dict[str, Any] = {
        "criteria_version": CONVERSION_GATE_CRITERIA_VERSION,
        "metric": "mAP50",
        "threshold_unit": "map50_absolute_drop",
        "pass_max_drop": round(pass_limit, 4),
        "review_max_drop": round(review_limit, 4),
        "max_drop": round(review_limit, 4),
        "pt_map50": (pt or {}).get("map50"),
        candidate_key: (candidate or {}).get("map50"),
        "signed_delta_map50": None,
        "drop_map50": None,
        "drop_ratio": None,
        "delta_map50": None,
        "delta_ratio": None,
    }
    if not pt or not candidate:
        return {
            **base,
            "status": "skip",
            "verdict": "not_evaluable",
            "reason_code": "comparison_metrics_missing",
            "detail": f"缺少 PT 或 {candidate_kind.upper()} 评估结果，无法判定转换保真度",
        }
    try:
        pt_m = float(pt.get("map50"))
        candidate_m = float(candidate.get("map50"))
    except (TypeError, ValueError):
        return {
            **base,
            "status": "skip",
            "verdict": "not_evaluable",
            "reason_code": "map50_not_numeric",
            "detail": "mAP50 非数值，无法判定转换保真度",
        }

    signed_delta = candidate_m - pt_m
    delta = abs(signed_delta)
    drop = max(pt_m - candidate_m, 0.0)
    delta_ratio = delta / max(abs(pt_m), 1e-9)
    drop_ratio = drop / max(abs(pt_m), 1e-9)

    epsilon = 1e-12
    if signed_delta > review_limit + epsilon:
        status_value = "warn"
        verdict = "review"
        reason_code = "candidate_metric_improved_significantly"
        detail = (
            f"{candidate_label} mAP50 较 PT 提升 {signed_delta:.4f}，"
            "未判为精度损失失败，但需核验数据集、预处理和评估参数一致性"
        )
    elif drop <= pass_limit + epsilon:
        status_value = "ok"
        verdict = "pass"
        reason_code = "drop_within_pass_limit"
        detail = (
            f"{candidate_label} 相对 PT 的 mAP50 掉点 {drop:.4f}，"
            f"通过标准 ≤{pass_limit:.4f}"
        )
    elif drop <= review_limit + epsilon:
        status_value = "warn"
        verdict = "review"
        reason_code = "drop_requires_review"
        detail = (
            f"{candidate_label} 相对 PT 的 mAP50 掉点 {drop:.4f}，"
            f"超过通过标准 {pass_limit:.4f}，但未超过复核上限 {review_limit:.4f}"
        )
    else:
        status_value = "fail"
        verdict = "fail"
        reason_code = "drop_exceeds_max"
        detail = (
            f"{candidate_label} 相对 PT 的 mAP50 掉点 {drop:.4f}，"
            f"超过最大允许掉点 {review_limit:.4f}"
        )

    return {
        **base,
        "status": status_value,
        "verdict": verdict,
        "reason_code": reason_code,
        "detail": detail,
        "pt_map50": pt_m,
        candidate_key: candidate_m,
        "signed_delta_map50": round(signed_delta, 4),
        "drop_map50": round(drop, 4),
        "drop_ratio": round(drop_ratio, 4),
        # Historical consumers expect absolute gap fields.
        "delta_map50": round(delta, 4),
        "delta_ratio": round(delta_ratio, 4),
    }


def _compare_pt_bmodel(
    pt: Optional[Dict],
    bmodel: Optional[Dict],
    *,
    precision: Optional[str] = None,
    pass_max_drop: float = BMODEL_PASS_MAX_DROP,
    max_drop: float = BMODEL_REVIEW_MAX_DROP,
) -> Dict[str, Any]:
    normalized_precision = str(precision or "").strip().lower()
    candidate_label = (
        f"{normalized_precision.upper()} BModel"
        if normalized_precision
        else "INT8 BModel"
    )
    comparison = _compare_pt_candidate(
        pt,
        bmodel,
        candidate_kind="bmodel",
        candidate_label=candidate_label,
        pass_max_drop=pass_max_drop,
        max_drop=max_drop,
    )
    if normalized_precision:
        comparison["precision"] = normalized_precision
        comparison["loss_type"] = (
            "quantization"
            if normalized_precision == "int8"
            else "deployment_conversion"
        )
    return comparison


def _compare_pt_onnx(
    pt: Optional[Dict],
    onnx: Optional[Dict],
    *,
    pass_max_drop: float = ONNX_PASS_MAX_DROP,
    max_drop: float = ONNX_REVIEW_MAX_DROP,
) -> Dict[str, Any]:
    return _compare_pt_candidate(
        pt,
        onnx,
        candidate_kind="onnx",
        pass_max_drop=pass_max_drop,
        max_drop=max_drop,
    )


def run_server_sop_evaluations_for_export(
    export_id: int,
    *,
    yolo_train_job_id: int,
    imgsz: int = 640,
    execution_monitor=None,
) -> Dict[str, Any]:
    """
    在部署服务器执行 cloud_based（pt）与 onnx_based（onnx）评估。
    结果写入 yolo_bm1688_exports/<id>/server_eval/。
    """
    if not settings.YOLO_SOP_SERVER_EVAL_ENABLED:
        return {"status": "skipped", "reason": "YOLO_SOP_SERVER_EVAL_ENABLED=false"}

    if execution_monitor is not None:
        execution_monitor.ensure_current()

    dataset_root, img_dir, xml_dir, split = _resolve_test_eval_paths(
        yolo_train_job_id=yolo_train_job_id,
        export_id=export_id,
    )
    out_base = _export_root(export_id) / "server_eval"
    if execution_monitor is not None:
        execution_monitor.ensure_current()
    out_base.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "status": "pending",
        "export_id": export_id,
        "yolo_train_job_id": yolo_train_job_id,
        "split": split,
        "dataset_root": str(dataset_root) if dataset_root else None,
        "note": "bmodel 评估 (pythonTest_evaluation.py) 需在算能盒执行，不在此运行",
    }

    if not img_dir:
        summary["status"] = "fail"
        summary["error"] = "dataset 中无 test/val 图片，无法跑 3.6.2 评估"
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        _write_summary(out_base, summary)
        return summary

    pt_path = _job_work_root(yolo_train_job_id) / "train" / "weights" / "best.pt"
    onnx_path = _resolve_onnx_path(export_id)
    class_names = _read_class_names_from_data_yaml(dataset_root) if dataset_root else []
    timeout = int(settings.YOLO_SOP_SERVER_EVAL_TIMEOUT_SEC or 7200)

    common_env = {
        "EVAL_DATA_PATH": str(img_dir.resolve()),
        "EVAL_XML_DIR": str(xml_dir.resolve()) if xml_dir else "",
        "EVAL_IMGSZ": str(int(imgsz or 640)),
        "EVAL_CONF_THRES": str(settings.YOLO_SOP_EVAL_CONF_THRES),
        "EVAL_IOU_THRES": str(settings.YOLO_SOP_EVAL_IOU_THRES),
    }
    if class_names:
        common_env["EVAL_CLASS_NAMES"] = json.dumps(class_names, ensure_ascii=False)

    # PT
    pt_dir = out_base / "results_cloud"
    if execution_monitor is not None:
        execution_monitor.ensure_current()
    pt_dir.mkdir(parents=True, exist_ok=True)
    pt_ok, pt_log = _run_eval_script(
        "cloud_based_evaluation.py",
        env_extra={
            **common_env,
            "EVAL_MODEL_PATH": str(pt_path.resolve()),
            "EVAL_SAVE_DIR": str(pt_dir.resolve()),
        },
        timeout_sec=timeout,
    )
    if execution_monitor is not None:
        execution_monitor.ensure_current()
    summary["pt"] = {
        "status": "ok" if pt_ok else "fail",
        "model": str(pt_path),
        "results_dir": str(pt_dir),
        "metrics": _load_eval_summary(pt_dir),
        "log_tail": pt_log[-800:] if not pt_ok else None,
    }

    # ONNX
    onnx_dir = out_base / "results_onnx"
    if execution_monitor is not None:
        execution_monitor.ensure_current()
    onnx_dir.mkdir(parents=True, exist_ok=True)
    onnx_summary: Dict[str, Any] = {"status": "skip", "detail": "无 ONNX 文件"}
    if onnx_path and onnx_path.is_file():
        onnx_ok, onnx_log = _run_eval_script(
            "onnx_based_evaluation.py",
            env_extra={
                **common_env,
                "EVAL_MODEL_PATH": str(onnx_path.resolve()),
                "EVAL_INPUT_H": str(int(imgsz or 640)),
                "EVAL_INPUT_W": str(int(imgsz or 640)),
                "EVAL_SAVE_DIR": str(onnx_dir.resolve()),
            },
            timeout_sec=timeout,
        )
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        onnx_summary = {
            "status": "ok" if onnx_ok else "fail",
            "model": str(onnx_path),
            "results_dir": str(onnx_dir),
            "metrics": _load_eval_summary(onnx_dir),
            "log_tail": onnx_log[-800:] if not onnx_ok else None,
        }
    summary["onnx"] = onnx_summary

    summary["comparison_pt_onnx"] = _compare_pt_onnx(
        (summary.get("pt") or {}).get("metrics"),
        (summary.get("onnx") or {}).get("metrics"),
    )
    summary["eval_params"] = {
        "imgsz": int(imgsz or 640),
        "conf_thres": float(settings.YOLO_SOP_EVAL_CONF_THRES),
        "iou_thres": float(settings.YOLO_SOP_EVAL_IOU_THRES),
        "xml_dir": str(xml_dir.resolve()) if xml_dir else None,
        "images_dir": str(img_dir.resolve()),
    }
    summary["criteria"] = build_conversion_gate_criteria()
    cmp = summary.get("comparison_pt_onnx") or {}
    summary["conversion_pt_onnx"] = {
        "success": cmp.get("status") == "ok",
        "status": cmp.get("status"),
        "verdict": cmp.get("verdict"),
        "reason_code": cmp.get("reason_code"),
        "detail": cmp.get("detail"),
    }
    summary["status"] = (
        "ok"
        if summary.get("pt", {}).get("status") == "ok"
        and summary.get("onnx", {}).get("status") in ("ok", "skip")
        else "partial"
    )
    if summary.get("comparison_pt_onnx", {}).get("status") in {"warn", "fail"}:
        summary["status"] = "warn"

    if execution_monitor is not None:
        execution_monitor.ensure_current()
    _write_summary(out_base, summary)
    logger.info(
        "SOP server eval export_id={} split={} pt={} onnx={} compare={}",
        export_id,
        split,
        summary.get("pt", {}).get("status"),
        summary.get("onnx", {}).get("status"),
        summary.get("comparison_pt_onnx", {}).get("status"),
    )
    return summary


def _write_summary(out_base: Path, data: Dict[str, Any]) -> None:
    p = out_base / "sop_eval_summary.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_server_sop_eval_summary(export_root: Path) -> Optional[Dict[str, Any]]:
    p = export_root / "server_eval" / "sop_eval_summary.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return enrich_sop_eval_summary(data, export_root)


def enrich_sop_eval_summary(
    data: Dict[str, Any], export_root: Optional[Path] = None
) -> Dict[str, Any]:
    """补齐文档 3.6 展示字段（兼容旧 summary）。"""
    if not data:
        return data
    existing_criteria = data.get("criteria")
    criteria = build_conversion_gate_criteria()
    if (
        isinstance(existing_criteria, dict)
        and existing_criteria.get("version") != criteria["version"]
    ):
        criteria["legacy_snapshot"] = existing_criteria
    data["criteria"] = criteria
    if not data.get("eval_params"):
        data["eval_params"] = {
            "conf_thres": float(settings.YOLO_SOP_EVAL_CONF_THRES),
            "iou_thres": float(settings.YOLO_SOP_EVAL_IOU_THRES),
        }
    cmp = data.get("comparison_pt_onnx") or {}
    if "conversion_pt_onnx" not in data:
        data["conversion_pt_onnx"] = {
            "success": cmp.get("status") == "ok",
            "status": cmp.get("status"),
            "verdict": cmp.get("verdict"),
            "reason_code": cmp.get("reason_code"),
            "detail": cmp.get("detail"),
        }
    if export_root:
        export_root = Path(export_root)
        data["sop_eval_summary_path"] = str(
            export_root / "server_eval" / "sop_eval_summary.json"
        )
        manual_bm_dir = discover_manual_bmodel_results_dir(export_root)
        for key, sub in (
            ("pt", "results_cloud"),
            ("onnx", "results_onnx"),
            ("bmodel", "results_bmodel"),
        ):
            block = data.get(key) or {}
            rdir = block.get("results_dir")
            if not rdir:
                if key == "bmodel" and manual_bm_dir:
                    rdir = str(manual_bm_dir)
                else:
                    rdir = str(export_root / "server_eval" / sub)
            rpath = Path(rdir)
            if key == "bmodel":
                server_path = export_root / "server_eval" / sub
                if (
                    rpath == server_path
                    and manual_bm_dir
                    and not (
                        (rpath / "evaluation_report.md").is_file()
                        or (rpath / "eval_summary.json").is_file()
                    )
                ):
                    rpath = manual_bm_dir
            _sync_sop_eval_block_paths(block, rpath)
            if (rpath / "eval_summary.json").is_file():
                _merge_block_metrics_from_disk(block, rpath)
            data[key] = block
        # Keep precision variants in the same normalized shape as the legacy
        # bmodel block. INT8 remains the compatibility gate block.
        bmodels = data.get("bmodels")
        if isinstance(bmodels, dict):
            normalized = {}
            for tag in ("fp32", "fp16", "int8"):
                block = bmodels.get(tag)
                if not isinstance(block, dict):
                    continue
                rdir = block.get("results_dir")
                if not rdir:
                    rdir = export_root / "server_eval" / "results_bmodel"
                    if tag != "int8":
                        rdir = rdir / "variants" / tag
                block["model_tag"] = tag
                _sync_sop_eval_block_paths(block, Path(rdir))
                if Path(rdir, "eval_summary.json").is_file():
                    _merge_block_metrics_from_disk(block, Path(rdir))
                normalized[tag] = block
            if normalized:
                data["bmodels"] = normalized
                if isinstance(data.get("bmodel"), dict) and normalized.get("int8"):
                    legacy = dict(data["bmodel"])
                    legacy.update(normalized["int8"])
                    data["bmodel"] = legacy
        pt_m = (data.get("pt") or {}).get("metrics")
        onnx_m = (data.get("onnx") or {}).get("metrics")
        bm_m = (data.get("bmodel") or {}).get("metrics")
        cmp_onnx = data.get("comparison_pt_onnx") or {}
        if pt_m and onnx_m:
            data["comparison_pt_onnx"] = _compare_pt_onnx(pt_m, onnx_m)
            cmp_onnx = data["comparison_pt_onnx"]
        data["conversion_pt_onnx"] = {
            "success": cmp_onnx.get("status") == "ok",
            "status": cmp_onnx.get("status"),
            "verdict": cmp_onnx.get("verdict"),
            "reason_code": cmp_onnx.get("reason_code"),
            "detail": cmp_onnx.get("detail"),
        }
        if pt_m and bm_m:
            data["comparison_pt_bmodel"] = _compare_pt_bmodel(pt_m, bm_m)
        from labelu.internal.application.service.yolo_sop_eval_analysis import (
            attach_sop_eval_analysis,
        )

        attach_sop_eval_analysis(data, export_root)
    return data


def resolve_sop_eval_report_path(export_id: int, kind: str) -> Path:
    """kind: pt | onnx | bmodel → evaluation_report.md（bmcv 无 Markdown 报告）"""
    k = (kind or "").strip().lower()
    sub_map = {
        "pt": "results_cloud",
        "onnx": "results_onnx",
        "bmodel": "results_bmodel",
    }
    if k == "bmcv":
        raise ValueError("bmcv has no evaluation_report.md; use board_report metrics")
    if k not in sub_map:
        raise ValueError("kind must be pt, onnx, bmodel or bmcv")
    sub = sub_map[k]
    if k == "bmodel":
        p = _resolve_bmodel_results_dir(export_id) / "evaluation_report.md"
    else:
        p = _export_root(export_id) / "server_eval" / sub / "evaluation_report.md"
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p


def resolve_sop_eval_results_dir(export_id: int, kind: str) -> Path:
    k = (kind or "").strip().lower()
    sub_map = {
        "pt": "results_cloud",
        "onnx": "results_onnx",
        "bmodel": "results_bmodel",
        "bmcv": "bmcv_preview",
    }
    if k not in sub_map:
        raise ValueError("kind must be pt, onnx, bmodel or bmcv")
    export_root = _export_root(export_id)
    if k == "bmodel":
        return _resolve_bmodel_results_dir(export_id)
    if k == "bmcv":
        server_dir = export_root / "server_eval" / "bmcv_preview"
        if server_dir.is_dir() and any(server_dir.iterdir()):
            return server_dir
        manual = discover_manual_bmcv_preview_dir(export_root)
        if manual:
            return manual
        return server_dir
    return export_root / "server_eval" / sub_map[k]


def resolve_sop_eval_asset_path(export_id: int, kind: str, filename: str) -> Path:
    """返回评估产物（曲线图等），禁止路径穿越。"""
    name = Path((filename or "").strip()).name
    if not name or name != filename or ".." in filename:
        raise ValueError("invalid asset filename")
    base = resolve_sop_eval_results_dir(export_id, kind).resolve()
    path = (base / name).resolve()
    if not str(path).startswith(str(base)):
        raise ValueError("invalid asset path")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path
