"""BM1688 导出质量报告：仅在编译服务器可完成的评测，结果供前端展示。

算能盒 §4 例程 / §6.2 实测 FPS 不在此模块（见 board_tests_note）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.yolo_training_metrics import (
    final_metrics_from_metrics_summary,
    read_test_val_metrics_from_summary,
)

BOARD_TESTS_NOTE = (
    "§4 图片例程、§6.2 程序实测 FPS 需在算能盒上执行 sophon-demo YOLOv8_plus_det；"
    "本报告仅包含编译服务器可完成的指标。"
)

_INTERESTING_LOG = re.compile(
    r"similar|compare|tolerance|cosine|npz|正确|精度|PASSED|FAILED|exit_code",
    re.IGNORECASE,
)
_QUANT_TAG = re.compile(r"\b(F32|FP32|F16|FP16|INT8|BF16)\b", re.IGNORECASE)
_SIM_NUMBER = re.compile(
    r"(?:cosine[_\s-]?similarity|similarity|tolerance|compare)[^\d]{0,24}([\d.]+)",
    re.IGNORECASE,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_compile_deploy_hints(compile_log: Optional[str]) -> Dict[str, Any]:
    """从 TPU-MLIR model_deploy 日志摘录相似度/比对类信息。"""
    if not compile_log:
        return {"lines": [], "scores": [], "exit_code": None}
    lines = compile_log.splitlines()
    interesting = [ln.strip() for ln in lines if _INTERESTING_LOG.search(ln)][-40:]
    scores: List[Dict[str, Any]] = []
    exit_code = None
    for ln in lines:
        if ln.strip().startswith("exit_code="):
            try:
                exit_code = int(ln.split("=", 1)[1].strip())
            except ValueError:
                pass
        for m in _SIM_NUMBER.finditer(ln):
            q = None
            qm = _QUANT_TAG.search(ln)
            if qm:
                q = qm.group(1).upper()
            scores.append(
                {"quantize": q, "value": float(m.group(1)), "line": ln.strip()[:200]}
            )
    return {"lines": interesting, "scores": scores[-15:], "exit_code": exit_code}


def summarize_artifacts(export_root: Path, cfg: dict) -> Dict[str, Any]:
    basename = str(cfg.get("bmodel_basename") or cfg_io.DEFAULT_BMODEL_BASENAME)
    onnx_fn = str(cfg.get("onnx_filename") or cfg_io.DEFAULT_ONNX_FILENAME)
    models_root = export_root / "models"
    out: Dict[str, Any] = {"onnx": None, "bmodels": [], "platforms": []}
    onnx_path = models_root / "onnx" / onnx_fn
    if onnx_path.is_file():
        out["onnx"] = {
            "path": str(onnx_path.relative_to(export_root)),
            "bytes": onnx_path.stat().st_size,
        }
    for chip in cfg_io.parse_compile_platforms(cfg.get("compile_platforms")):
        plat = cfg_io.chip_to_platform_dir(chip)
        pdir = models_root / plat
        if not pdir.is_dir():
            continue
        plat_entry = {"chip": chip, "dir": plat, "files": []}
        for name in cfg_io.canonical_bmodel_names(basename):
            bp = pdir / name
            if bp.is_file():
                plat_entry["files"].append({"name": name, "bytes": bp.stat().st_size})
        out["platforms"].append(plat_entry)
        for f in sorted(pdir.glob("*.bmodel")):
            if f.name not in [x["name"] for x in plat_entry["files"]]:
                plat_entry["files"].append({"name": f.name, "bytes": f.stat().st_size})
    return out


def _results_dir_from_metrics_summary(metrics_summary: Optional[str]) -> Optional[str]:
    if not metrics_summary:
        return None
    try:
        obj = json.loads(metrics_summary)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    rd = obj.get("results_dir")
    return str(rd).strip() if rd else None


def train_val_from_job(
    metrics_summary: Optional[str],
    *,
    train_results_dir: Optional[str] = None,
) -> Dict[str, Any]:
    fm = final_metrics_from_metrics_summary(metrics_summary)
    if not fm:
        return {
            "status": "unavailable",
            "detail": "训练作业无 metrics_summary.final_metrics",
        }
    rd = train_results_dir or _results_dir_from_metrics_summary(metrics_summary)
    out: Dict[str, Any] = {
        "status": "ok",
        "source": "yolo_train_job.metrics_summary",
        "source_path": rd,
        "map50": fm.get("map50"),
        "map50_95": fm.get("map50_95") or fm.get("map"),
        "precision": fm.get("precision"),
        "recall": fm.get("recall"),
    }
    return out


def test_val_from_job(metrics_summary: Optional[str]) -> Dict[str, Any]:
    tvm = read_test_val_metrics_from_summary(metrics_summary)
    if not tvm:
        return {
            "status": "unavailable",
            "detail": "未运行测试集验证（test_val_metrics）",
        }
    st = str(tvm.get("status") or "unknown")
    if st != "ok":
        return {
            "status": st,
            "detail": tvm.get("detail") or st,
        }
    return {
        "status": "ok",
        "source": "yolo_train_job.metrics_summary.test_val_metrics",
        "map50": tvm.get("map50"),
        "map50_95": tvm.get("map50_95"),
        "precision": tvm.get("precision"),
        "recall": tvm.get("recall"),
        "images": tvm.get("images"),
    }


def _onnx_val_python_hint() -> str:
    py = (os.environ.get("LABELU_PYTHON") or "").strip() or sys.executable
    return (
        f"未安装 ultralytics（当前解释器: {py}）。"
        "请在 LabelU 同一虚拟环境中执行 pip install ultralytics，"
        "或设置 YOLO_AUTO_INSTALL_ULTRALYTICS=true 后重试。"
    )


def _run_onnx_coco128_val_inprocess(onnx_path: Path, *, imgsz: int) -> Dict[str, Any]:
    from ultralytics import YOLO  # noqa: WPS433

    m = YOLO(str(onnx_path))
    r = m.val(data="coco128.yaml", imgsz=int(imgsz), batch=8, verbose=False)
    box = getattr(r, "box", None)
    out: Dict[str, Any] = {
        "status": "ok",
        "source": "ultralytics.val(coco128.yaml)",
        "source_path": str(onnx_path.resolve()),
    }
    if box is not None:
        out["map50"] = float(getattr(box, "map50", 0) or 0)
        out["map50_95"] = float(getattr(box, "map", 0) or 0)
        out["precision"] = float(getattr(box, "mp", 0) or 0)
        out["recall"] = float(getattr(box, "mr", 0) or 0)
    return out


def run_onnx_coco128_val(
    onnx_path: Path,
    *,
    imgsz: int,
    timeout_sec: int = 600,
) -> Dict[str, Any]:
    """在编译服务器上对 ONNX 跑 Ultralytics coco128 验证（需网络和 ultralytics）。"""
    if not onnx_path.is_file():
        return {"status": "skip", "detail": f"ONNX 不存在: {onnx_path}"}
    if os.environ.get("LABELU_SKIP_ONNX_VAL", "").strip() in ("1", "true", "yes"):
        return {"status": "skip", "detail": "已设置 LABELU_SKIP_ONNX_VAL"}

    from labelu.internal.common.optional_dependencies import (
        ensure_ultralytics_installed,
    )

    if not ensure_ultralytics_installed(reason="ONNX coco128 验证"):
        return {"status": "fail", "detail": _onnx_val_python_hint()}

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run_onnx_coco128_val_inprocess, onnx_path, imgsz=imgsz)
            return fut.result(timeout=timeout_sec)
    except FuturesTimeout:
        return {"status": "fail", "detail": f"ONNX val 超时 ({timeout_sec}s)"}
    except Exception as exc:
        logger.exception("onnx coco128 val failed path={}", onnx_path)
        return {"status": "fail", "detail": str(exc)[:500]}


def sophon_verify_summary(
    export_root: Path, *, check_env: bool = False
) -> Dict[str, Any]:
    from labelu.internal.common.sophon_faq_verify import verify_export_tree

    report = verify_export_tree(export_root, check_env=check_env)
    d = report.to_dict()
    checks = d.get("checks") or []
    brief = [
        {
            "section": c.get("section"),
            "name": c.get("name"),
            "status": c.get("status"),
            "message": (c.get("message") or "")[:160],
        }
        for c in checks[:25]
    ]
    return {
        "passed": bool(d.get("passed")),
        "counts": d.get("counts"),
        "checks": brief,
        "export_root": d.get("export_root"),
    }


def build_quality_report(
    *,
    export_root: Path,
    compile_log: Optional[str],
    metrics_summary: Optional[str],
    cfg: Optional[dict] = None,
    run_onnx_val: bool = True,
    train_results_dir: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = cfg or cfg_io.load_bm1688_compiler_config()
    onnx_fn = str(cfg.get("onnx_filename") or cfg_io.DEFAULT_ONNX_FILENAME)
    onnx_path = export_root / "models" / "onnx" / onnx_fn

    export_root = export_root.resolve()
    report: Dict[str, Any] = {
        "generated_at": _iso_now(),
        "scope": "compile_server",
        "export_root_abs": str(export_root),
        "board_tests_note": BOARD_TESTS_NOTE,
        "train_val": train_val_from_job(
            metrics_summary, train_results_dir=train_results_dir
        ),
        "test_val": test_val_from_job(metrics_summary),
        "compile_deploy": parse_compile_deploy_hints(compile_log),
        "artifacts": summarize_artifacts(export_root, cfg),
        "sophon_layout": sophon_verify_summary(export_root, check_env=False),
    }
    if run_onnx_val:
        report["onnx_val"] = run_onnx_coco128_val(
            onnx_path, imgsz=int(cfg.get("imgsz") or 640)
        )
        if isinstance(report.get("onnx_val"), dict) and onnx_path.is_file():
            report["onnx_val"].setdefault("source_path", str(onnx_path.resolve()))
    else:
        report["onnx_val"] = {"status": "skip", "detail": "未请求 ONNX val"}
    cd = report.get("compile_deploy") or {}
    if isinstance(cd, dict):
        cd["source_path"] = str(export_root / "compile_log.txt")
    sl = report.get("sophon_layout") or {}
    if isinstance(sl, dict):
        sl.setdefault("export_root", str(export_root))
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        load_server_sop_eval_summary,
    )

    report["sop_eval"] = load_server_sop_eval_summary(export_root) or {
        "status": "not_run",
        "detail": "导出后未执行或 YOLO_SOP_SERVER_EVAL_ENABLED=false",
    }
    return report


def quality_report_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False)[:48000]


def parse_quality_report(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def normalize_plate_recognition_eval(
    payload: Any,
    *,
    source_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a bounded plate-recognition summary suitable for API responses."""
    if not isinstance(payload, dict):
        return None
    metrics = payload.get("metrics")
    source = metrics if isinstance(metrics, dict) else payload
    if str(source.get("model_type") or "").strip() != "plate_recognition":
        return None

    plate_numbers = [
        str(value).strip()
        for value in (source.get("plate_numbers") or [])
        if str(value).strip()
    ]
    unique_numbers = list(dict.fromkeys(plate_numbers))
    summary: Dict[str, Any] = {
        "status": str(payload.get("status") or source.get("status") or "unknown"),
        "model_type": "plate_recognition",
        "model_tag": payload.get("model_tag") or "fp16",
        "model_filename": (
            payload.get("model_filename") or source.get("model_filename")
        ),
        "backend": payload.get("backend") or "plate_recognition_bm1688",
        "runtime_ok": source.get("runtime_ok") is True,
        "objects_detected": source.get("objects_detected") is True,
        "input_image_count": source.get("input_image_count"),
        "result_image_count": source.get("result_image_count"),
        "images_with_detections": source.get("images_with_detections"),
        "total_detections": source.get("total_detections"),
        "recognized_plate_count": len(plate_numbers),
        "unique_plate_count": len(unique_numbers),
        "plate_number_samples": unique_numbers[:20],
        "plate_type_counts": source.get("plate_type_counts") or {},
        "color_counts": source.get("color_counts") or {},
        "inference": source.get("inference") or {},
        "bmrt_test": source.get("bmrt_test") or {},
        "rendered_images": source.get("rendered_images") or {},
        "device_id": source.get("device_id"),
        "conf_threshold": source.get("conf_threshold"),
        "nms_threshold": source.get("nms_threshold"),
        "plate_iou_threshold": source.get("plate_iou_threshold"),
    }
    if source_path:
        summary["source_path"] = source_path
    return summary


def _load_plate_recognition_eval_from_disk(
    edge_validation: Any,
    report_block: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    export_id = getattr(edge_validation, "export_id", None) or report_block.get(
        "export_id"
    )
    job_id = (
        report_block.get("job_id")
        or getattr(edge_validation, "id", None)
        or getattr(edge_validation, "validation_id", None)
    )
    if not export_id or not job_id:
        return None

    from labelu.internal.common.config import settings

    root = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_BM1688_EXPORT_SUBDIR
        / str(int(export_id))
        / "manual_edge_validation"
        / f"job-{int(job_id)}"
    )
    candidates = (
        root / "server_eval" / "results_bmodel" / "summary.json",
        root / "edge_ssh_pull" / "output" / "results_bmodel" / "summary.json",
        root / "edge_ssh_pull" / "output" / "bmodel_sop_eval.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        normalized = normalize_plate_recognition_eval(
            payload,
            source_path=str(path),
        )
        if normalized:
            return normalized
    return None


def merge_manual_edge_validation_into_report(
    report: Optional[Dict[str, Any]],
    edge_validation: Any = None,
) -> Optional[Dict[str, Any]]:
    out: Dict[str, Any] = dict(report) if isinstance(report, dict) else {}
    report_block = getattr(edge_validation, "report", None)
    if not isinstance(report_block, dict):
        return out if out else report

    result = report_block.get("result")
    if not isinstance(result, dict):
        return out if out else report

    eval_summary = result.get("eval_summary")
    analysis = result.get("analysis")
    pt_eval = result.get("pt_eval")
    direct_comparison = result.get("comparison_pt_bmodel")
    plate_recognition_eval = normalize_plate_recognition_eval(
        result.get("plate_recognition_eval")
    ) or _load_plate_recognition_eval_from_disk(edge_validation, report_block)
    if (
        not isinstance(eval_summary, dict)
        and not isinstance(analysis, dict)
        and not isinstance(pt_eval, dict)
        and not isinstance(direct_comparison, dict)
        and not plate_recognition_eval
    ):
        return out if out else report

    sop_eval = dict(out.get("sop_eval") or {})
    if isinstance(pt_eval, dict) and pt_eval:
        pt_block = dict(sop_eval.get("pt") or {})
        for key, value in pt_eval.items():
            if value is not None:
                pt_block[key] = value
        sop_eval["pt"] = pt_block
    bmodel = dict(sop_eval.get("bmodel") or {})
    metrics = dict(bmodel.get("metrics") or {})
    if isinstance(eval_summary, dict):
        for key in (
            "map50",
            "map50_95",
            "map50_95_status",
            "map50_95_approximation",
            "map50_95_approximation_basis",
            "precision",
            "recall",
            "images",
        ):
            value = eval_summary.get(key)
            if value is not None:
                metrics[key] = value
        if metrics:
            bmodel["metrics"] = metrics
        status_value = str(eval_summary.get("status") or "").strip().lower()
        if status_value:
            bmodel["status"] = status_value
        elif metrics.get("map50") is not None:
            bmodel["status"] = "ok"

    sources = analysis.get("sources") if isinstance(analysis, dict) else {}
    if isinstance(sources, dict):
        bmodel_report = str(sources.get("bmodel_report") or "").strip()
        if bmodel_report:
            bmodel["report_md_path"] = bmodel_report
            bmodel["report_md_exists"] = True
            bmodel["results_dir"] = str(Path(bmodel_report).parent)

    if not bmodel.get("results_dir"):
        export_id = getattr(edge_validation, "export_id", None) or report_block.get(
            "export_id"
        )
        job_id = report_block.get("job_id")
        if export_id and job_id:
            from labelu.internal.common.config import settings

            manual_dir = (
                Path(settings.MEDIA_ROOT)
                / settings.YOLO_BM1688_EXPORT_SUBDIR
                / str(int(export_id))
                / "manual_edge_validation"
                / f"job-{int(job_id)}"
                / "server_eval"
                / "results_bmodel"
            )
            if manual_dir.is_dir():
                bmodel["results_dir"] = str(manual_dir)
                from labelu.internal.application.service.yolo_sop_model_evaluation import (
                    _sync_sop_eval_block_paths,
                )

                _sync_sop_eval_block_paths(bmodel, manual_dir)

    if bmodel.get("results_dir"):
        from labelu.internal.application.service.yolo_sop_model_evaluation import (
            _sync_sop_eval_block_paths,
        )

        _sync_sop_eval_block_paths(bmodel, Path(str(bmodel["results_dir"])))

    # New edge runner payload: keep all precision variants while preserving
    # sop_eval.bmodel as the legacy INT8 gate block.
    eval_models = result.get("eval_models")
    if not isinstance(eval_models, dict) and isinstance(eval_summary, dict):
        eval_models = eval_summary.get("models")
    if isinstance(eval_models, dict):
        bmodels = dict(sop_eval.get("bmodels") or {})
        for tag in ("fp32", "fp16", "int8"):
            item = eval_models.get(tag)
            if not isinstance(item, dict):
                continue
            block = dict(bmodels.get(tag) or {})
            for key, value in item.items():
                if value is not None:
                    block[key] = value
            metric_values = {
                key: item.get(key)
                for key in (
                    "map50",
                    "map50_95",
                    "map50_95_status",
                    "precision",
                    "recall",
                    "f1",
                    "images",
                )
                if item.get(key) is not None
            }
            if metric_values:
                block["metrics"] = {
                    **(block.get("metrics") or {}),
                    **metric_values,
                }
            model_dir = block.get("results_dir")
            if model_dir:
                _sync_sop_eval_block_paths(block, Path(str(model_dir)))
            bmodels[tag] = block
        if bmodels:
            sop_eval["bmodels"] = bmodels

            comparison_precision = (
                str((direct_comparison or {}).get("precision") or "").strip().lower()
            )
            if comparison_precision in bmodels:
                selected_bmodel = dict(bmodel)
                selected_bmodel.update(bmodels[comparison_precision])
                for path_key in (
                    "results_dir",
                    "report_md_path",
                    "eval_summary_path",
                    "report_md_exists",
                    "eval_summary_exists",
                ):
                    if bmodel.get(path_key):
                        selected_bmodel[path_key] = bmodel[path_key]
                bmodel = selected_bmodel

    if bmodel:
        sop_eval["bmodel"] = bmodel

    if plate_recognition_eval:
        sop_eval["plate_recognition_eval"] = plate_recognition_eval

    pt_metrics = (sop_eval.get("pt") or {}).get("metrics") or {}
    pt_map50 = pt_metrics.get("map50")
    if pt_map50 is None:
        train_val = out.get("train_val") or {}
        if (
            isinstance(train_val, dict)
            and str(train_val.get("status") or "").lower() == "ok"
            and train_val.get("map50") is not None
        ):
            pt_map50 = train_val.get("map50")
    bmodel_map50 = metrics.get("map50")
    comparison = (
        dict(direct_comparison)
        if isinstance(direct_comparison, dict) and direct_comparison
        else dict(sop_eval.get("comparison_pt_bmodel") or {})
    )
    if not direct_comparison and pt_map50 is not None and bmodel_map50 is not None:
        from labelu.internal.application.service.yolo_sop_model_evaluation import (
            _compare_pt_bmodel,
        )

        comparison = _compare_pt_bmodel(
            {"map50": pt_map50},
            {"map50": bmodel_map50},
        )
    if comparison:
        sop_eval["comparison_pt_bmodel"] = comparison

    if isinstance(analysis, dict) and analysis:
        sop_eval["analysis"] = analysis

    if sop_eval:
        out["sop_eval"] = sop_eval
    return out if out else report


def _maybe_align_pt_onnx_comparison_with_test_val(
    sop_eval: Dict[str, Any],
    report: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """SOP PT 脚本与训练 test_val 偏差较大时，用 test_val 作为 ONNX 转化门控基线。"""
    if not isinstance(sop_eval, dict):
        return sop_eval
    cmp = sop_eval.get("comparison_pt_onnx") or {}
    if cmp.get("status") not in {"warn", "fail"}:
        return sop_eval
    test_val = (report or {}).get("test_val") or {}
    if (
        not isinstance(test_val, dict)
        or str(test_val.get("status") or "").lower() != "ok"
        or test_val.get("map50") is None
    ):
        return sop_eval
    onnx_m = (sop_eval.get("onnx") or {}).get("metrics") or {}
    if onnx_m.get("map50") is None:
        return sop_eval
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        _compare_pt_onnx,
    )

    alt_cmp = _compare_pt_onnx({"map50": test_val["map50"]}, onnx_m)
    if alt_cmp.get("status") != "ok":
        return sop_eval
    sop_pt = ((sop_eval.get("pt") or {}).get("metrics") or {}).get("map50")
    alt_cmp = dict(alt_cmp)
    alt_cmp["baseline"] = "test_val"
    alt_cmp["detail"] = f"{alt_cmp.get('detail')}；基线已改用同数据集训练 test_val" + (
        f"；SOP PT 脚本 mAP50={sop_pt}" if sop_pt is not None else ""
    )
    alt_cmp["pt_map50"] = float(test_val["map50"])
    out = dict(sop_eval)
    out["comparison_pt_onnx"] = alt_cmp
    out["conversion_pt_onnx"] = {
        "success": True,
        "status": "ok",
        "verdict": alt_cmp.get("verdict"),
        "reason_code": alt_cmp.get("reason_code"),
        "detail": alt_cmp["detail"],
    }
    if (
        out.get("pt", {}).get("status") == "ok"
        and out.get("onnx", {}).get("status") == "ok"
    ):
        bmodel_status = out.get("comparison_pt_bmodel", {}).get("status")
        out["status"] = "warn" if bmodel_status in {"warn", "fail"} else "ok"
    return out


def hydrate_quality_report_from_disk(
    export_id: int,
    report: Optional[Dict[str, Any]],
    edge_validation: Any = None,
) -> Optional[Dict[str, Any]]:
    """详情 API 返回前从导出目录刷新 sop_eval（避免 DB 中 quality_report 滞后）。"""
    from labelu.internal.common.config import settings
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        load_server_sop_eval_summary,
    )
    from labelu.internal.application.service.yolo_sop_eval_analysis import (
        attach_sop_eval_analysis,
    )

    out: Dict[str, Any] = dict(report) if report else {}
    export_root = (
        Path(settings.MEDIA_ROOT) / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    )
    sop = load_server_sop_eval_summary(export_root)
    if sop:
        out["sop_eval"] = sop
    merged = merge_manual_edge_validation_into_report(
        out if out else report, edge_validation
    )
    if not isinstance(merged, dict):
        return merged
    sop_eval = dict(merged.get("sop_eval") or {})
    sop_eval = _maybe_align_pt_onnx_comparison_with_test_val(sop_eval, merged)
    if sop_eval and export_root.is_dir():
        attach_sop_eval_analysis(sop_eval, export_root)
        merged["sop_eval"] = sop_eval
    return merged


def quality_summary_one_liner(report: Optional[Dict[str, Any]]) -> str:
    """列表页短摘要。"""
    if not report:
        return ""
    parts: List[str] = []
    tv = report.get("train_val") or {}
    if tv.get("status") == "ok" and tv.get("map50_95") is not None:
        parts.append(f"训练mAP50-95={float(tv['map50_95']):.3f}")
    tt = report.get("test_val") or {}
    if tt.get("status") == "ok" and tt.get("map50") is not None:
        parts.append(f"测试mAP50={float(tt['map50']):.3f}")
    ov = report.get("onnx_val") or {}
    if ov.get("status") == "ok" and ov.get("map50_95") is not None:
        parts.append(f"ONNX mAP50-95={float(ov['map50_95']):.3f}")
    sv = report.get("sophon_layout") or {}
    if sv.get("passed") is True:
        parts.append("产物校验通过")
    elif sv.get("passed") is False:
        parts.append("产物校验未通过")
    se = report.get("sop_eval") or {}
    conv = se.get("conversion_pt_onnx") or {}
    cmp_ = se.get("comparison_pt_onnx") or {}
    if conv.get("success") is True:
        parts.append("SOP转化通过")
    elif conv.get("status") == "fail":
        parts.append("SOP转化未通过")
    elif cmp_.get("status") == "ok":
        parts.append(
            f"SOP pt/onnx mAP50 差 {float(cmp_.get('delta_ratio', 0)) * 100:.1f}%"
        )
    elif cmp_.get("status") == "fail":
        parts.append("SOP pt/onnx 超 3%")
    pt_m = (se.get("pt") or {}).get("metrics") or {}
    if pt_m.get("map50") is not None:
        parts.append(
            f"固定{se.get('split', 'test')} pt mAP50={float(pt_m['map50']):.3f}"
        )
    return " · ".join(parts)


def persist_quality_report_for_export(
    db,
    export_id: int,
    *,
    run_onnx_val: bool = True,
    execution_monitor=None,
) -> Dict[str, Any]:
    from labelu.internal.adapter.persistence import (
        crud_yolo_bm1688_export,
        crud_yolo_train_job,
    )
    from labelu.internal.common.config import settings

    row = crud_yolo_bm1688_export.get(db, export_id)
    if not row:
        return {}
    if execution_monitor is not None:
        execution_monitor.ensure_current()
    out_root = (
        Path(settings.MEDIA_ROOT) / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    )
    job = crud_yolo_train_job.get(db, row.yolo_train_job_id)
    ms = job.metrics_summary if job else None
    train_results_dir = None
    if job:
        train_results_dir = str(
            Path(settings.MEDIA_ROOT)
            / settings.YOLO_TRAIN_WORK_SUBDIR
            / f"job-{job.id}"
            / "train"
        )
    cfg = cfg_io.load_bm1688_compiler_config()
    cfg = {**cfg, "imgsz": row.imgsz}
    try:
        report = build_quality_report(
            export_root=out_root,
            compile_log=row.compile_log,
            metrics_summary=ms,
            cfg=cfg,
            run_onnx_val=run_onnx_val,
            train_results_dir=train_results_dir,
        )
        if execution_monitor is not None:
            execution_monitor.conditional_update(
                {"quality_report": quality_report_json(report)},
                db=db,
                manage_transaction=True,
            )
        else:
            crud_yolo_bm1688_export.update_row(
                db, row, quality_report=quality_report_json(report)
            )
            db.commit()
        return report
    except Exception as exc:
        if execution_monitor is not None:
            # A report-generation error may be converted into an auditable
            # failed report only while this worker still owns the export.
            execution_monitor.ensure_current()
        logger.exception("quality_report export_id={}: {}", export_id, exc)
        fail = {
            "generated_at": _iso_now(),
            "scope": "compile_server",
            "error": str(exc)[:500],
            "board_tests_note": BOARD_TESTS_NOTE,
        }
        if execution_monitor is not None:
            execution_monitor.conditional_update(
                {"quality_report": quality_report_json(fail)},
                db=db,
                manage_transaction=True,
            )
        else:
            crud_yolo_bm1688_export.update_row(
                db, row, quality_report=quality_report_json(fail)
            )
            db.commit()
        return fail
