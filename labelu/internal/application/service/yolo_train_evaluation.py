"""YOLO 训练完成后：对多个视觉大模型分别调用，生成评估 Markdown（不解析为标注 JSON）。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_eval_report,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_eval import YoloEvalStartCommand
from labelu.internal.application.response.yolo_eval import (
    YoloEvalHubCompareGroup,
    YoloEvalHubReportItem,
    YoloEvalHubResponse,
    YoloEvalReportListRowResponse,
    YoloEvalReportResponse,
)
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service.vision_multimodal_chat import (
    chat_multimodal_markdown,
)
from labelu.internal.application.service.vision_llm_chain import _configured_model_for
from labelu.internal.common.ai_labeling_runtime import (
    is_qwen_visual_provider,
    load_ai_labeling_runtime,
)
from labelu.internal.common.config import settings
from labelu.internal.common.vision_volcengine import normalize_volcengine_ark_model_id
from labelu.internal.common.task_access import (
    assert_user_has_task_access,
    is_task_superuser,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.model_access_config import (
    validate_volcengine_coding_endpoint,
)
from labelu.internal.common.yolo_eval_prompts import (
    EVAL_OUTPUT_LANG_SUFFIX,
    load_yolo_eval_prompts,
)
from labelu.internal.common.yolo_training_metrics import (
    metrics_contract_from_summary,
    read_test_val_metrics_from_summary,
    resolve_train_dir_from_weights,
    summarize_ai_report,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus

_KIND_FULL = "full_analysis"
_KIND_NEXT = "next_iteration"
_AUTO_EVAL_SCHEDULE_LOCK = threading.Lock()
_AUTO_EVAL_SCHEDULED: set[int] = set()
_AUTO_EVAL_WORKER_SEMAPHORE = threading.Semaphore(1)

_YOLO_EVAL_SYSTEM_PROMPT = (
    "你是 YOLO 目标检测训练分析与调参专家。"
    "无论输入材料（args.yaml、results.csv、图表）是否为英文，"
    "你的报告正文、章节标题与建议说明必须使用简体中文撰写；"
    "仅 YAML/JSON 键名、指标缩写、路径等可保留英文。"
    "涉及负样本数量或占比时，只能引用系统提供的数据集正负样本统计；"
    "统计缺失时必须明确写无法判断，不能根据曲线或图片猜测。"
)
_YOLO_EVAL_SYSTEM_PROMPT += (
    "\n严格执行证据优先规则：不得补造输入中没有的数值、类别、样本数或验收阈值。"
    "所有建议必须能回指到具体字段、数据划分或图表文件；无法确认时明确写证据不足。"
    "报告正文使用简体中文，最后只输出一个可解析的 recommended_train_params JSON 代码块。"
)

_PROVIDER_TITLE = {
    "volcengine": "火山方舟",
    "qwen_plus": "通义千问 Plus",
    "qwen_235b": "通义千问 235B",
}


def _yolo_eval_disabled_providers() -> set[str]:
    raw = (settings.YOLO_EVAL_DISABLED_PROVIDERS or "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _report_list_title(
    provider_key: str, job_id: int, task_name: Optional[str] = None
) -> str:
    label = _PROVIDER_TITLE.get(provider_key, provider_key)
    tn = (task_name or "").strip()
    if tn:
        return f"{tn} · {label} · 作业 #{job_id}"
    return f"{label} · 作业 #{job_id}"


def _to_eval_report_response(r, job, task) -> YoloEvalReportResponse:
    job_id = r.yolo_train_job_id or job.id
    suggested = r.suggested_params
    if not suggested and r.body and not r.error_message:
        from labelu.internal.common.yolo_eval_report_params import (
            extract_recommended_params_json_for_storage,
        )

        suggested = extract_recommended_params_json_for_storage(r.body)
    return YoloEvalReportResponse(
        id=r.id,
        yolo_train_job_id=job_id,
        task_id=task.id,
        task_name=task.name,
        provider_key=r.provider_key,
        report_kind=r.report_kind,
        operation_id=getattr(r, "operation_id", None),
        title=_report_list_title(r.provider_key, job_id, task.name),
        body=r.body,
        suggested_params=suggested,
        error_message=r.error_message,
        created_at=r.created_at,
    )


def _normalize_kind(kind: str) -> str:
    k = (kind or "").strip()
    if k not in (_KIND_FULL, _KIND_NEXT):
        raise LabelUException(
            code=ErrorCode.CODE_57010_YOLO_EVAL_INVALID_KIND,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return k


def _eval_status_field(kind: str) -> str:
    return "ai_eval_full_status" if kind == _KIND_FULL else "ai_eval_next_status"


def _eval_operation_field(kind: str) -> str:
    return (
        "ai_eval_full_operation_id"
        if kind == _KIND_FULL
        else "ai_eval_next_operation_id"
    )


def _has_successful_report(reports: list[Any]) -> bool:
    return any(not row.error_message and (row.body or "").strip() for row in reports)


def _can_auto_retry_failed_reports(reports: list[Any]) -> bool:
    """Allow one automatic retry after the original failed generation.

    Failed generations are persisted, so the limit survives process restarts.
    Successful historical reports always win and never trigger another call.
    """
    if not reports or _has_successful_report(reports):
        return False
    failed_generations = {
        str(getattr(row, "operation_id", None) or "legacy").strip() or "legacy"
        for row in reports
        if row.error_message
    }
    return 0 < len(failed_generations) < 2


def _set_job_eval_flag(
    job_id: int,
    report_kind: str,
    value: str,
    operation_id: str | None = None,
) -> bool:
    from labelu.internal.common.db import SessionLocal

    status_field = _eval_status_field(report_kind)
    dbf = SessionLocal()
    try:
        if operation_id:
            with begin_transaction(dbf):
                return crud_yolo_train_job.update_eval_status_for_operation(
                    dbf,
                    job_id=int(job_id),
                    report_kind=report_kind,
                    operation_id=operation_id,
                    value=value,
                )
        job = crud_yolo_train_job.get(db=dbf, job_id=job_id)
        if job:
            with begin_transaction(dbf):
                crud_yolo_train_job.update_status(
                    dbf, job, job.status, **{status_field: value}
                )
            return True
        return False
    finally:
        dbf.close()


def _run_yolo_eval_for_operation(
    job_id: int, report_kind: str, operation_id: str
) -> None:
    """Invoke the evaluator while keeping compatibility with old test hooks."""
    parameters = inspect.signature(_run_yolo_eval_sync).parameters
    if "operation_id" in parameters:
        _run_yolo_eval_sync(job_id, report_kind, operation_id=operation_id)
    else:
        # A legacy integration hook may still expose the old two-argument
        # callable.  The real implementation is always generation-aware.
        _run_yolo_eval_sync(job_id, report_kind)


def _claim_eval_job(job_id: int) -> bool:
    with _AUTO_EVAL_SCHEDULE_LOCK:
        if int(job_id) in _AUTO_EVAL_SCHEDULED:
            return False
        _AUTO_EVAL_SCHEDULED.add(int(job_id))
        return True


def _release_eval_job(job_id: int) -> None:
    with _AUTO_EVAL_SCHEDULE_LOCK:
        _AUTO_EVAL_SCHEDULED.discard(int(job_id))


def _eval_job_is_claimed(job_id: int) -> bool:
    with _AUTO_EVAL_SCHEDULE_LOCK:
        return int(job_id) in _AUTO_EVAL_SCHEDULED


def _reset_interrupted_eval_states(job_id: int) -> None:
    """Clear status-only states left behind when the owning process exited."""
    from labelu.internal.common.db import SessionLocal

    db = SessionLocal()
    try:
        job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
        if not job:
            return
        updates: dict[str, None] = {}
        operation_ids: dict[str, str] = {}
        for kind in (_KIND_FULL, _KIND_NEXT):
            current = getattr(job, _eval_status_field(kind), None)
            operation_id = crud_yolo_train_job.get_eval_operation_id(
                db, job_id=int(job_id), report_kind=kind
            )
            reports = (
                crud_yolo_eval_report.list_by_job_and_kind(
                    db,
                    job_id=int(job_id),
                    report_kind=kind,
                    operation_id=operation_id,
                )
                if operation_id
                else []
            )
            # Only a generation with no matching report is recoverable.  A
            # historical NULL generation remains untouched for compatibility.
            if operation_id and not reports and current in {"RUNNING", "COMPLETED"}:
                updates[_eval_status_field(kind)] = None
                updates[_eval_operation_field(kind)] = None
                operation_ids[kind] = operation_id
        if updates:
            with begin_transaction(db):
                for kind, operation_id in operation_ids.items():
                    crud_yolo_train_job.reset_eval_status_for_operation(
                        db,
                        job_id=int(job_id),
                        report_kind=kind,
                        operation_id=operation_id,
                    )
    finally:
        db.close()


def schedule_auto_evaluations_after_train(
    job_id: int, *, recover_interrupted: bool = False
) -> bool:
    """Run automatic reports outside the training worker and track process ownership."""
    if not settings.YOLO_EVAL_ENABLED or not _build_provider_specs():
        return False
    if not _claim_eval_job(job_id):
        return False

    def _run() -> None:
        try:
            with _AUTO_EVAL_WORKER_SEMAPHORE:
                if recover_interrupted:
                    _reset_interrupted_eval_states(job_id)
                run_auto_evaluations_after_train(job_id)
        finally:
            _release_eval_job(job_id)

    try:
        threading.Thread(
            target=_run,
            name=f"yolo-auto-eval-{int(job_id)}",
            daemon=True,
        ).start()
    except Exception:
        _release_eval_job(job_id)
        raise
    return True


def _schedule_missing_auto_evaluations(db: Session, job) -> bool:
    """Backfill missing reports and recover status left by an exited process."""
    if not job or job.status != YoloTrainStatus.COMPLETED.value or not job.weights_path:
        return False
    if not _build_provider_specs():
        return False

    job_id = int(job.id)
    if _eval_job_is_claimed(job_id):
        return False

    needs_run = False
    recover_interrupted = False
    for kind in (_KIND_FULL, _KIND_NEXT):
        operation_id = crud_yolo_train_job.get_eval_operation_id(
            db, job_id=job_id, report_kind=kind
        )
        all_reports = crud_yolo_eval_report.list_by_job_and_kind(
            db, job_id=job_id, report_kind=kind
        )
        reports = (
            crud_yolo_eval_report.list_by_job_and_kind(
                db,
                job_id=job_id,
                report_kind=kind,
                operation_id=operation_id,
            )
            if operation_id
            else all_reports
        )
        if _has_successful_report(reports):
            continue
        current = getattr(job, _eval_status_field(kind), None)
        if reports and _can_auto_retry_failed_reports(all_reports):
            needs_run = True
        elif not reports and current in (None, ""):
            needs_run = True
        elif not reports and current in {"RUNNING", "COMPLETED"}:
            needs_run = True
            recover_interrupted = True

    if not needs_run:
        return False
    return schedule_auto_evaluations_after_train(
        job_id, recover_interrupted=recover_interrupted
    )


def schedule_missing_auto_evaluations_for_job(job_id: int) -> bool:
    from labelu.internal.common.db import SessionLocal

    db = SessionLocal()
    try:
        job = crud_yolo_train_job.get(db=db, job_id=int(job_id))
        return _schedule_missing_auto_evaluations(db, job)
    finally:
        db.close()


def _validated_provider_spec(
    label: str,
    base_url: str,
    api_key: str,
    model: str,
    *,
    provider_key: str = "",
    allow_unauthenticated: bool = False,
) -> tuple[str, str, str, str] | None:
    label, base_url, api_key, model = (
        str(value or "").strip() for value in (label, base_url, api_key, model)
    )
    if not base_url or not model or (not api_key and not allow_unauthenticated):
        return None
    if is_qwen_visual_provider(base_url, model, f"{provider_key} {label}"):
        return None
    try:
        hostname = (urlsplit(base_url).hostname or "").lower()
        if (
            provider_key == "volcengine"
            or label == "volcengine"
            or hostname == "volces.com"
            or hostname.endswith(".volces.com")
        ):
            base_url = validate_volcengine_coding_endpoint(base_url)
    except ValueError:
        return None
    return label, base_url, api_key, model


def _build_provider_specs() -> list[tuple[str, str, str, str]]:
    # Share visual understanding's authoritative page-configuration policy:
    # a saved Ark connection blocks fallback even when disabled or unchecked.
    disabled = _yolo_eval_disabled_providers()
    configured_model = _configured_model_for("vision_llm")
    if configured_model:
        provider_key = str(configured_model.get("provider_key") or "vision_llm")
        if configured_model.get("blocked") or provider_key in disabled:
            return []
        label = (
            "volcengine"
            if provider_key == "volcengine"
            else str(configured_model.get("name") or "通用视觉模型")
        )
        spec = _validated_provider_spec(
            label,
            configured_model.get("endpoint"),
            configured_model.get("api_key"),
            configured_model.get("model"),
            provider_key=provider_key,
            allow_unauthenticated=(
                provider_key != "volcengine"
                and configured_model.get("auth_type") == "none"
            ),
        )
        return [spec] if spec else []

    ov_chain = load_ai_labeling_runtime()
    enabled = set(ov_chain.get("enabled_providers") or ["volcengine"])
    enabled -= disabled
    specs: list[tuple[str, str, str, str]] = []

    # Reuse the model profiles configured in the AI labeling settings page.
    # YOLO evaluation uses the same OpenAI-compatible contract as image
    # auto-labeling, so a configured profile must not be invisible to reports.
    profiles_by_id = {
        str(profile.get("id") or ""): profile
        for profile in (ov_chain.get("llm_profiles") or [])
        if isinstance(profile, dict)
    }
    for profile_id in ov_chain.get("annotation_chain_profile_ids") or []:
        profile = profiles_by_id.get(str(profile_id).strip())
        if not profile:
            continue
        base_url = str(profile.get("base_url") or "").strip()
        api_key = str(profile.get("api_key") or "").strip()
        model = str(profile.get("model") or "").strip()
        label = str(profile.get("name") or profile_id).strip() or str(profile_id)
        if str(profile_id) in disabled or label in disabled:
            continue
        spec = _validated_provider_spec(
            label, base_url, api_key, model, provider_key=str(profile_id)
        )
        if spec:
            specs.append(spec)

    # Keep the environment-backed providers as the compatibility fallback.
    # When a profile chain is configured, its order is authoritative, matching
    # the behavior of the vision labeling chain.
    if specs:
        return specs
    if "volcengine" in enabled and (settings.VISION_VOLCENGINE_API_KEY or "").strip():
        spec = _validated_provider_spec(
            "volcengine",
            settings.VISION_VOLCENGINE_BASE_URL,
            settings.VISION_VOLCENGINE_API_KEY,
            normalize_volcengine_ark_model_id(settings.VISION_VOLCENGINE_MODEL),
            provider_key="volcengine",
        )
        if spec:
            specs.append(spec)
    return specs


def _train_dir_from_weights(weights_path: str) -> Path:
    p = Path(weights_path).resolve()
    if not p.is_file():
        raise RuntimeError("weights file missing")
    # .../train/weights/best.pt
    train_dir = p.parent.parent
    if train_dir.name != "train":
        raise RuntimeError("unexpected weights layout")
    return train_dir


def _resolve_eval_weights(job) -> tuple[Path, Path]:
    """Resolve the checkpoint and run directory used as AI-evaluation evidence.

    Standard YOLO jobs store ``train/weights/best.pt`` directly in
    ``weights_path``. Composite/plate jobs additionally publish a deployable
    artifact at the job root and keep the held-out evaluation checkpoint in
    ``component_artifacts_json``. Prefer the legacy direct path, then use the
    structured component metadata for those jobs.
    """
    direct = str(getattr(job, "weights_path", None) or "").strip()
    direct_error: RuntimeError | None = None
    if direct:
        try:
            return Path(direct).resolve(), _train_dir_from_weights(direct)
        except RuntimeError as exc:
            direct_error = exc

    raw_artifacts = getattr(job, "component_artifacts_json", None)
    try:
        artifacts = json.loads(raw_artifacts or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        artifacts = {}
    if isinstance(artifacts, dict):
        components = []
        if isinstance(artifacts.get("pose"), dict):
            components.append(artifacts["pose"])
        components.extend(
            value
            for key, value in artifacts.items()
            if key != "pose" and isinstance(value, dict)
        )
        for artifact in components:
            for key in ("evaluation_weights", "pt"):
                candidate = str(artifact.get(key) or "").strip()
                if not candidate:
                    continue
                try:
                    return Path(candidate).resolve(), _train_dir_from_weights(candidate)
                except RuntimeError:
                    continue

    if direct_error is not None:
        raise direct_error
    raise RuntimeError("weights file missing")


def _dataset_composition_evidence(train_dir: Path) -> str:
    summary_path = train_dir.parent / "reports" / "export_summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        summary = {}
    composition = (
        summary.get("sample_composition") if isinstance(summary, dict) else None
    )
    if not isinstance(composition, dict):
        return (
            "【数据集正负样本统计】\n"
            "本次作业没有可用的结构化正负样本统计。禁止断言负样本偏少或偏多，"
            "只能说明当前证据不足。"
        )
    return (
        "【数据集正负样本统计（训练导出真实值）】\n"
        + json.dumps(composition, ensure_ascii=False, indent=2)
        + "\n负样本指人工明确确认无检测目标、并导出为空 YOLO 标签文件的图片。"
    )


def _structured_metrics_evidence(
    metrics_summary: str | None, *, train_dir: Path | None = None
) -> str:
    """Reduce the persisted metrics JSON to an explicit AI-readable evidence block."""
    if not metrics_summary:
        return json.dumps(
            {
                "parse_status": "missing",
                "note": "metrics_summary 未提供，不能根据指标下结论",
            },
            ensure_ascii=False,
            indent=2,
        )
    try:
        raw = json.loads(metrics_summary)
    except (TypeError, ValueError, json.JSONDecodeError):
        return json.dumps(
            {"parse_status": "invalid", "note": "metrics_summary 不是有效 JSON"},
            ensure_ascii=False,
            indent=2,
        )
    if not isinstance(raw, dict):
        return json.dumps(
            {"parse_status": "invalid", "note": "metrics_summary 顶层不是对象"},
            ensure_ascii=False,
            indent=2,
        )

    allowed = (
        "dataset",
        "classes",
        "class_instance_counts",
        "final_metrics",
        "last_epoch_metrics",
        "best_epoch_metrics",
        "best_pt_val_metrics",
        "metrics_contract_version",
        "per_class_val_metrics",
        "test_val_metrics",
        "speed",
        "merge_tasks",
    )
    evidence = {key: raw[key] for key in allowed if key in raw}
    evidence["available_fields"] = sorted(raw.keys())
    evidence["parse_status"] = "ok"

    # Make the val/test comparison explicit without inventing values.
    contract = metrics_contract_from_summary(metrics_summary, train_dir=train_dir)
    authoritative_source = str(contract.get("authoritative_source") or "none")
    semantic_notes = {
        "best_pt_val_metrics": "模型质量结论采用 best.pt 独立复验指标；末轮仅用于判断训练回落。",
        "results_csv_best_epoch": "历史作业采用 best.pt 对应的 results.csv 最佳轮次，尚未独立复验；末轮仅用于判断训练回落。",
        "final_metrics": "历史作业仅有末轮兼容指标，结论证据不足，建议重新执行验证。",
        "auto_training_evaluation": "训练质量结论采用受信自动训练评估的整体召回率；该指标不等同于 mAP。",
        "none": "没有可用于判断模型质量的验证指标。",
    }
    evidence["metric_semantics"] = {
        "authoritative_source": authoritative_source,
        "legacy_fallback": contract.get("legacy_fallback"),
        "note": semantic_notes[authoritative_source],
    }
    evidence["last_epoch_metrics"] = contract.get("last_epoch_metrics")
    evidence["best_epoch_metrics"] = contract.get("best_epoch_metrics")
    evidence["authoritative_val_metrics"] = contract.get("authoritative_val_metrics")
    val = contract.get("authoritative_val_metrics") or {}
    test = (
        raw.get("test_val_metrics")
        if isinstance(raw.get("test_val_metrics"), dict)
        else {}
    )
    comparison: dict[str, Any] = {}
    for key in ("map50", "map50_95", "precision", "recall"):
        vv = val.get(key)
        tv = test.get(key)
        if vv is not None and tv is not None:
            try:
                comparison[key] = {
                    "val": float(vv),
                    "test": float(tv),
                    "test_minus_val": round(float(tv) - float(vv), 6),
                }
            except (TypeError, ValueError):
                pass
    evidence["val_test_comparison"] = comparison
    return json.dumps(evidence, ensure_ascii=False, indent=2)


def _metrics_summary_for_prompt(
    metrics_summary: str | None,
    *,
    structured_evidence: str,
    max_chars: int = 30_000,
) -> str:
    """Keep the raw prompt block valid JSON instead of slicing it mid-document."""
    if not metrics_summary:
        return "{}"
    try:
        parsed = json.loads(metrics_summary)
    except (TypeError, ValueError, json.JSONDecodeError):
        return structured_evidence
    compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if len(compact) <= max_chars:
        return compact
    return json.dumps(
        {
            "parse_status": "omitted_oversize",
            "note": "metrics_summary 原文过长，未截断写入；请以 STRUCTURED_METRICS 为准。",
            "original_chars": len(compact),
        },
        ensure_ascii=False,
    )


def _assert_under_media(path: Path) -> None:
    root = Path(settings.MEDIA_ROOT).resolve()
    resolved = path.resolve()
    if not str(resolved).startswith(str(root)):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )


def _read_text_file(path: Path, limit: int) -> str:
    if not path.is_file():
        return "(文件不存在)"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(读取失败)"
    if len(raw) > limit:
        return raw[-limit:]
    return raw


def _read_results_csv_tail(train_dir: Path, lines: int = 80) -> str:
    p = train_dir / "results.csv"
    if not p.is_file():
        return "(无 results.csv)"
    try:
        rows = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return "(读取 results.csv 失败)"
    if len(rows) <= lines:
        return "\n".join(rows)
    head = rows[0] if rows else ""
    tail = rows[-(lines - 1) :] if len(rows) > 1 else []
    return "\n".join([head, *tail])


def _collect_chart_jpeg(train_dir: Path, max_images: int = 8) -> list[bytes]:
    from io import BytesIO

    try:
        from PIL import Image
    except ImportError:
        return []

    preferred_names = [
        "results.png",
        "confusion_matrix.png",
        "confusion_matrix_normalized.png",
        "PR_curve.png",
        "F1_curve.png",
        "P_curve.png",
        "R_curve.png",
        "labels.jpg",
        "labels_correlate.jpg",
    ]
    paths: list[Path] = []
    for name in preferred_names:
        cand = train_dir / name
        if cand.is_file():
            paths.append(cand)
    for cand in sorted(train_dir.glob("val_batch*_pred.jpg"))[:2]:
        if cand.is_file():
            paths.append(cand)
    for cand in sorted(train_dir.glob("val_batch*.jpg"))[:2]:
        if cand.is_file() and cand not in paths:
            paths.append(cand)

    out: list[bytes] = []
    max_side = 1024
    quality = 82
    for img_path in paths[:max_images]:
        try:
            with Image.open(img_path) as im:
                im = im.convert("RGB")
                w, h = im.size
                if max(w, h) > max_side:
                    scale = max_side / float(max(w, h))
                    im = im.resize(
                        (int(w * scale), int(h * scale)), Image.Resampling.LANCZOS
                    )
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=quality)
                jb = buf.getvalue()
                if jb:
                    out.append(jb)
        except Exception as exc:
            logger.debug("skip chart {}: {}", img_path, exc)
    return out


def _extract_recommended_params_json(text: str) -> str | None:
    """从模型 Markdown 正文中抽出可入库的 JSON（JSON 块 / YAML 块 / 文中键值）。"""
    from labelu.internal.common.yolo_eval_report_params import (
        extract_recommended_params_json_for_storage,
    )

    return extract_recommended_params_json_for_storage(text)


def _fill_prompt(
    template: str,
    *,
    args_yaml: str,
    results_tail: str,
    prior_reports: str,
    weights_line: str,
    metrics_line: str,
    structured_evidence: str,
) -> str:
    return (
        template.replace("{{ARGS_YAML}}", args_yaml)
        .replace("{{RESULTS_TAIL}}", results_tail)
        .replace("{{PRIOR_REPORTS}}", prior_reports)
        .replace("{{WEIGHTS_PATH}}", weights_line)
        .replace("{{METRICS_SUMMARY}}", metrics_line)
        .replace("{{STRUCTURED_EVIDENCE}}", structured_evidence)
    )


def _prior_reports_block(db: Session, job_id: int) -> str:
    bodies = crud_yolo_eval_report.latest_full_bodies_for_prior(
        db, job_id=job_id, limit=6
    )
    if not bodies:
        return "（尚无 full_analysis 报告，请先运行「全维度分析」类评估。）"
    parts: list[str] = []
    max_each = 6000
    for i, b in enumerate(bodies, start=1):
        chunk = b if len(b) <= max_each else b[:max_each] + "\n…(截断)"
        parts.append(f"### 报告片段 {i}\n\n{chunk}")
    return "\n\n".join(parts)


def list_eval_reports(
    db: Session,
    *,
    task_id: int,
    job_id: int,
    report_kind: str | None,
    current_user: User,
) -> list[YoloEvalReportResponse]:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    _schedule_missing_auto_evaluations(db, job)

    kind_filter = None
    if report_kind is not None and str(report_kind).strip():
        kind_filter = _normalize_kind(report_kind)
    rows = crud_yolo_eval_report.list_by_job_and_kind(
        db, job_id=job_id, report_kind=kind_filter
    )
    return [_to_eval_report_response(r, job, task) for r in rows]


async def start_evaluation_job(
    db: Session,
    *,
    task_id: int,
    job_id: int,
    cmd: YoloEvalStartCommand,
    current_user: User,
) -> dict[str, Any]:
    if not settings.YOLO_EVAL_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57006_YOLO_EVAL_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job.status != YoloTrainStatus.COMPLETED.value or not job.weights_path:
        raise LabelUException(
            code=ErrorCode.CODE_57008_YOLO_EVAL_JOB_NOT_COMPLETED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    kind = _normalize_kind(cmd.report_kind)
    status_field = _eval_status_field(kind)
    current_eval = getattr(job, status_field, None)
    current_operation_id = crud_yolo_train_job.get_eval_operation_id(
        db, job_id=job_id, report_kind=kind
    )
    if current_eval == "RUNNING" and current_operation_id:
        raise LabelUException(
            code=ErrorCode.CODE_57009_YOLO_EVAL_ALREADY_RUNNING,
            status_code=status.HTTP_409_CONFLICT,
        )

    specs = _build_provider_specs()
    if not specs:
        raise LabelUException(
            code=ErrorCode.CODE_57007_YOLO_EVAL_VISION_NOT_CONFIGURED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not _claim_eval_job(job_id):
        raise LabelUException(
            code=ErrorCode.CODE_57009_YOLO_EVAL_ALREADY_RUNNING,
            status_code=status.HTTP_409_CONFLICT,
        )

    try:
        with begin_transaction(db):
            operation_id = crud_yolo_train_job.claim_eval_status_if_current(
                db,
                job_id=job_id,
                report_kind=kind,
                expected=current_eval,
            )
        if not operation_id:
            raise LabelUException(
                code=ErrorCode.CODE_57009_YOLO_EVAL_ALREADY_RUNNING,
                status_code=status.HTTP_409_CONFLICT,
            )
    except Exception:
        _release_eval_job(job_id)
        raise

    def _run_manual_eval() -> None:
        try:
            _run_yolo_eval_for_operation(job_id, kind, operation_id)
        finally:
            _release_eval_job(job_id)

    loop = asyncio.get_event_loop()
    try:
        loop.run_in_executor(None, _run_manual_eval)
    except Exception:
        _release_eval_job(job_id)
        raise

    return {
        "accepted": True,
        "report_kind": kind,
        "providers": [s[0] for s in specs],
        "operation_id": operation_id,
    }


def run_auto_evaluations_after_train(job_id: int) -> None:
    """训练完成后自动生成全维度报告，再基于报告生成下一轮迭代建议。"""
    if not settings.YOLO_EVAL_ENABLED:
        logger.info("skip auto yolo eval job {}: YOLO_EVAL_ENABLED is false", job_id)
        return

    specs = _build_provider_specs()
    if not specs:
        logger.info("skip auto yolo eval job {}: no vision provider configured", job_id)
        return

    from labelu.internal.common.db import SessionLocal

    for kind in (_KIND_FULL, _KIND_NEXT):
        status_field = _eval_status_field(kind)
        operation_id: str | None = None
        db = SessionLocal()
        try:
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            if (
                not job
                or job.status != YoloTrainStatus.COMPLETED.value
                or not job.weights_path
            ):
                logger.info(
                    "skip auto yolo eval job {} kind {}: train job not completed",
                    job_id,
                    kind,
                )
                return
            current_operation_id = crud_yolo_train_job.get_eval_operation_id(
                db, job_id=job_id, report_kind=kind
            )
            all_reports = crud_yolo_eval_report.list_by_job_and_kind(
                db, job_id=job_id, report_kind=kind
            )
            existing_reports = (
                crud_yolo_eval_report.list_by_job_and_kind(
                    db,
                    job_id=job_id,
                    report_kind=kind,
                    operation_id=current_operation_id,
                )
                if current_operation_id
                else all_reports
            )
            current_eval = getattr(job, status_field, None)
            if _has_successful_report(existing_reports):
                if current_eval != "COMPLETED":
                    with begin_transaction(db):
                        if current_operation_id:
                            crud_yolo_train_job.update_eval_status_for_operation(
                                db,
                                job_id=int(job_id),
                                report_kind=kind,
                                operation_id=current_operation_id,
                                value="COMPLETED",
                            )
                        else:
                            crud_yolo_train_job.update_status(
                                db, job, job.status, **{status_field: "COMPLETED"}
                            )
                logger.info(
                    "skip auto yolo eval job {} kind {}: successful report exists",
                    job_id,
                    kind,
                )
                continue
            if current_eval == "RUNNING":
                logger.info(
                    "skip auto yolo eval job {} kind {}: already running", job_id, kind
                )
                continue
            retry_failed = bool(existing_reports) and _can_auto_retry_failed_reports(
                all_reports
            )
            if existing_reports and not retry_failed:
                if current_eval != "FAILED":
                    with begin_transaction(db):
                        crud_yolo_train_job.update_status(
                            db, job, job.status, **{status_field: "FAILED"}
                        )
                logger.info(
                    "skip auto yolo eval job {} kind {}: automatic retry exhausted",
                    job_id,
                    kind,
                )
                continue
            if current_eval == "FAILED" and not retry_failed:
                logger.info(
                    "skip auto yolo eval job {} kind {}: preparation previously failed",
                    job_id,
                    kind,
                )
                continue
            if retry_failed:
                logger.info(
                    "retry auto yolo eval job {} kind {} after transient failure",
                    job_id,
                    kind,
                )
            with begin_transaction(db):
                operation_id = crud_yolo_train_job.claim_eval_status_if_current(
                    db,
                    job_id=int(job_id),
                    report_kind=kind,
                    expected=current_eval,
                )
            if not operation_id:
                logger.info(
                    "skip auto yolo eval job {} kind {}: another process claimed it",
                    job_id,
                    kind,
                )
                continue
        except Exception:
            logger.exception(
                "prepare auto yolo eval job {} kind {} failed", job_id, kind
            )
            _set_job_eval_flag(job_id, kind, "FAILED", operation_id)
            continue
        finally:
            db.close()

        try:
            _run_yolo_eval_for_operation(job_id, kind, operation_id)
        except Exception:
            logger.exception("auto yolo eval job {} kind {} failed", job_id, kind)
            _set_job_eval_flag(job_id, kind, "FAILED", operation_id)


def _run_yolo_eval_sync(
    job_id: int, report_kind: str, *, operation_id: str | None = None
) -> None:
    from labelu.internal.common.db import SessionLocal

    status_field = _eval_status_field(report_kind)
    user_text = ""

    try:
        db0 = SessionLocal()
        try:
            job = crud_yolo_train_job.get(db=db0, job_id=job_id)
            if not job or not job.weights_path:
                _set_job_eval_flag(job_id, report_kind, "FAILED", operation_id)
                return
            if not operation_id:
                operation_id = crud_yolo_train_job.get_eval_operation_id(
                    db0, job_id=job_id, report_kind=report_kind
                )
            if not operation_id:
                # Direct legacy callers (and old queued jobs) are upgraded at
                # the first execution boundary, while historical NULL rows
                # remain readable.
                with begin_transaction(db0):
                    operation_id = crud_yolo_train_job.claim_eval_status_if_current(
                        db0,
                        job_id=job_id,
                        report_kind=report_kind,
                        expected=getattr(job, status_field, None),
                    )
                if not operation_id:
                    logger.info(
                        "discard yolo eval job {} kind {}: generation claim lost",
                        job_id,
                        report_kind,
                    )
                    return
            eval_weights, train_dir = _resolve_eval_weights(job)
            _assert_under_media(train_dir)
            prior = (
                _prior_reports_block(db0, job_id) if report_kind == _KIND_NEXT else ""
            )
            weights_line = str(eval_weights)
            structured_evidence = _structured_metrics_evidence(
                job.metrics_summary, train_dir=train_dir
            )
            metrics_line = _metrics_summary_for_prompt(
                job.metrics_summary, structured_evidence=structured_evidence
            )
            prompts = load_yolo_eval_prompts()
            template = (
                prompts["full_analysis"]
                if report_kind == _KIND_FULL
                else prompts["next_iteration"]
            )
            args_yaml = _read_text_file(train_dir / "args.yaml", 120_000)
            results_tail = _read_results_csv_tail(train_dir, lines=80)
            user_text = _fill_prompt(
                template,
                args_yaml=args_yaml,
                results_tail=results_tail,
                prior_reports=prior,
                weights_line=weights_line,
                metrics_line=metrics_line,
                structured_evidence=structured_evidence,
            )
            user_text += "\n\n---\n" + _dataset_composition_evidence(train_dir)
            user_text += "\n\n" + EVAL_OUTPUT_LANG_SUFFIX
        except Exception:
            logger.exception("yolo eval job {} prepare failed", job_id)
            _set_job_eval_flag(job_id, report_kind, "FAILED", operation_id)
            return
        finally:
            db0.close()

        images = _collect_chart_jpeg(train_dir)
        specs = _build_provider_specs()
        if not specs:
            logger.warning(
                "yolo eval job {}: no usable vision provider configured", job_id
            )
            _set_job_eval_flag(job_id, report_kind, "FAILED", operation_id)
            return
        successful_reports = 0

        for label, base_url, api_key, model in specs:
            try:
                db_guard = SessionLocal()
                try:
                    current_operation = crud_yolo_train_job.get_eval_operation_id(
                        db_guard, job_id=job_id, report_kind=report_kind
                    )
                finally:
                    db_guard.close()
                if current_operation != operation_id:
                    logger.info(
                        "discard stale yolo eval report job={} kind={} operation_id={}",
                        job_id,
                        report_kind,
                        operation_id,
                    )
                    return
                body = chat_multimodal_markdown(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    user_text=user_text,
                    images_jpeg=images,
                    max_tokens=8192,
                    temperature=0.25,
                    timeout=settings.YOLO_EVAL_TIMEOUT_SECONDS,
                    max_retries=settings.YOLO_EVAL_MAX_RETRIES,
                    system_prompt=_YOLO_EVAL_SYSTEM_PROMPT,
                )
                if not isinstance(body, str) or not body.strip():
                    raise RuntimeError("视觉模型返回了空评估报告")
                suggested = _extract_recommended_params_json(body)
                dbw = SessionLocal()
                try:
                    with begin_transaction(dbw):
                        crud_yolo_eval_report.create(
                            dbw,
                            job_id=job_id,
                            provider_key=label,
                            report_kind=report_kind,
                            body=body,
                            suggested_params=suggested,
                            error_message=None,
                            operation_id=operation_id,
                        )
                finally:
                    dbw.close()
                successful_reports += 1
            except Exception as exc:
                logger.warning(
                    "yolo eval {} {} failed: {}", job_id, label, str(exc)[:400]
                )
                dbw = SessionLocal()
                try:
                    with begin_transaction(dbw):
                        crud_yolo_eval_report.create(
                            dbw,
                            job_id=job_id,
                            provider_key=label,
                            report_kind=report_kind,
                            body=None,
                            suggested_params=None,
                            error_message=str(exc)[:8000],
                            operation_id=operation_id,
                        )
                finally:
                    dbw.close()

        dbf = SessionLocal()
        try:
            with begin_transaction(dbf):
                completed = crud_yolo_train_job.update_eval_status_for_operation(
                    dbf,
                    job_id=job_id,
                    report_kind=report_kind,
                    operation_id=operation_id,
                    value="COMPLETED" if successful_reports else "FAILED",
                )
        finally:
            dbf.close()

        if report_kind == _KIND_NEXT and completed and successful_reports:
            try:
                from labelu.internal.application.service.yolo_train_auto_iteration import (
                    maybe_start_auto_iteration_train,
                )

                maybe_start_auto_iteration_train(job_id)
            except Exception:
                logger.exception(
                    "auto iteration train after eval failed for job {}", job_id
                )
    except Exception:
        logger.exception("yolo eval job {} failed", job_id)
        if operation_id:
            _set_job_eval_flag(job_id, report_kind, "FAILED", operation_id)


def list_eval_reports_global(
    db: Session,
    current_user: User,
    *,
    report_kind: str,
    task_name: Optional[str] = None,
    search: Optional[str] = None,
    rect_preset_slug: Optional[str] = None,
    page: int = 0,
    size: int = 20,
    project_id: Optional[int] = None,
) -> Tuple[List[YoloEvalReportListRowResponse], int]:
    kind = _normalize_kind(report_kind)
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    task_ids_filter = yolo_train_service._task_ids_for_rect_preset_filter(
        db, current_user, rect_preset_slug=rect_preset_slug
    )
    rows, total = crud_yolo_eval_report.list_for_user(
        db,
        current_user.id,
        report_kind=kind,
        task_name=task_name,
        search=search,
        task_ids=task_ids_filter,
        page=page,
        size=size,
        task_access_unrestricted=is_task_superuser(current_user),
        project_id=project_id,
    )
    data: List[YoloEvalReportListRowResponse] = []
    for r, job, task in rows:
        summary = summarize_ai_report(
            (
                getattr(r, "body", None)
                if isinstance(getattr(r, "body", None), str)
                else None
            ),
            error_message=getattr(r, "error_message", None),
        )
        data.append(
            YoloEvalReportListRowResponse(
                id=r.id,
                yolo_train_job_id=r.yolo_train_job_id,
                task_id=task.id,
                task_name=task.name,
                provider_key=r.provider_key,
                report_kind=r.report_kind,
                operation_id=getattr(r, "operation_id", None),
                title=_report_list_title(r.provider_key, job.id, task.name),
                summary_level=summary["summary_level"],
                summary_brief=summary["summary_brief"],
                error_message=r.error_message,
                created_at=r.created_at,
            )
        )
    return data, total


def _sop_kind_label(kind: str) -> str:
    return {"pt": "云端 PT", "onnx": "云端 ONNX", "bmodel": "边端 bmodel"}.get(
        kind, kind
    )


def build_evaluation_hub(
    db: Session,
    *,
    task_id: int,
    job_id: int,
    current_user: User,
    focus_export_id: Optional[int] = None,
) -> YoloEvalHubResponse:
    """聚合训练作业相关的全部评估报告（AI + SOP + 边端 + 质量）。"""
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_yolo_train_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    reports: list[YoloEvalHubReportItem] = []
    compare_groups: list[YoloEvalHubCompareGroup] = []

    contract = metrics_contract_from_summary(
        job.metrics_summary,
        train_dir=resolve_train_dir_from_weights(job.weights_path),
    )
    train_m = contract.get("authoritative_val_metrics")
    test_m = read_test_val_metrics_from_summary(job.metrics_summary)
    if train_m or test_m:
        merged = {}
        if train_m:
            merged["val"] = train_m
        if test_m:
            merged["test"] = test_m
        reports.append(
            YoloEvalHubReportItem(
                key="train:metrics",
                group="train",
                subgroup="ultralytics",
                title="训练验证 / 测试指标",
                description="来自 Ultralytics results.csv 与 metrics_summary",
                status="ok",
                format="metrics",
                metrics=merged,
            )
        )

    ai_rows = crud_yolo_eval_report.list_by_job_and_kind(
        db, job_id=job_id, report_kind=None
    )
    ai_full_keys: list[str] = []
    ai_next_keys: list[str] = []
    for r in ai_rows:
        kind_zh = "全维度分析" if r.report_kind == _KIND_FULL else "下一次迭代建议"
        prov = _PROVIDER_TITLE.get(r.provider_key, r.provider_key)
        key = f"ai:{r.id}"
        st = "error" if r.error_message else ("ok" if r.body else "pending")
        item = YoloEvalHubReportItem(
            key=key,
            group="ai",
            subgroup=r.report_kind,
            title=f"{prov} · {kind_zh}",
            description=f"报告 #{r.id}",
            status=st,
            format="markdown",
            created_at=r.created_at,
            ai_report_id=r.id,
            provider_key=r.provider_key,
            report_kind=r.report_kind,
            preview=(r.body or r.error_message or "")[:280] or None,
            fetch_url=f"/api/v1/yolo-eval-reports/{r.id}",
        )
        reports.append(item)
        if r.report_kind == _KIND_FULL and not r.error_message and r.body:
            ai_full_keys.append(key)
        if r.report_kind == _KIND_NEXT and not r.error_message and r.body:
            ai_next_keys.append(key)

    if ai_full_keys:
        compare_groups.append(
            YoloEvalHubCompareGroup(
                id="ai_full_compare",
                title="AI 全维度报告 · 多模型对比",
                description="火山方舟 / 通义 Plus / 通义 235B 并排阅读",
                report_keys=ai_full_keys,
                layout="columns",
            )
        )
    if ai_next_keys:
        compare_groups.append(
            YoloEvalHubCompareGroup(
                id="ai_next_compare",
                title="AI 迭代建议 · 多模型对比",
                description="对比各模型给出的下一轮训练参数建议",
                report_keys=ai_next_keys,
                layout="columns",
            )
        )

    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        load_server_sop_eval_summary,
    )
    from labelu.internal.common.bm1688_quality_report import (
        hydrate_quality_report_from_disk,
        parse_quality_report,
    )

    exports = crud_yolo_bm1688_export.list_for_job(
        db, yolo_train_job_id=job_id, limit=20
    )
    for exp in exports:
        sop_metric_keys: list[str] = []
        export_id = int(exp.id)
        exp_label = f"导出 #{export_id}"
        qr = hydrate_quality_report_from_disk(
            export_id, parse_quality_report(getattr(exp, "quality_report", None))
        )
        export_root = (
            Path(settings.MEDIA_ROOT)
            / settings.YOLO_BM1688_EXPORT_SUBDIR
            / str(export_id)
        )
        sop = (
            load_server_sop_eval_summary(export_root) if export_root.is_dir() else None
        )

        if qr:
            reports.append(
                YoloEvalHubReportItem(
                    key=f"quality:{export_id}",
                    group="quality",
                    subgroup="bm1688",
                    title=f"{exp_label} · 编译质量报告",
                    description="TPU-MLIR 编译、ONNX 验证、Sophon 布局等",
                    status="ok",
                    format="json",
                    export_id=export_id,
                    metrics=qr if isinstance(qr, dict) else None,
                    preview=None,
                )
            )

        if sop:
            reports.append(
                YoloEvalHubReportItem(
                    key=f"sop:summary:{export_id}",
                    group="sop",
                    subgroup="summary",
                    title=f"{exp_label} · SOP 评估摘要",
                    description="PT/ONNX/bmodel 转化对照与 eval_params",
                    status=str(sop.get("status") or "ok"),
                    format="json",
                    export_id=export_id,
                    metrics=sop,
                )
            )

        for sk in ("pt", "onnx", "bmodel"):
            block = (sop or {}).get(sk) or {}
            metrics = block.get("metrics") or {}
            exists = bool(block.get("report_md_exists"))
            st = (
                "ok"
                if exists
                else ("skip" if block.get("status") == "skip" else "missing")
            )
            mkey = f"sop:{sk}:{export_id}"
            reports.append(
                YoloEvalHubReportItem(
                    key=mkey,
                    group="sop",
                    subgroup=sk,
                    title=f"{exp_label} · {_sop_kind_label(sk)}",
                    description=block.get("detail") or block.get("results_dir"),
                    status=st,
                    format="markdown",
                    export_id=export_id,
                    sop_kind=sk,
                    metrics=metrics if metrics else None,
                    fetch_url=(
                        f"/api/v1/yolo-bm1688-exports/{export_id}/sop-eval-report/{sk}"
                        if exists
                        else None
                    ),
                    assets=(
                        _list_sop_curve_assets(export_root, sk)
                        if export_root.is_dir()
                        else None
                    ),
                )
            )
            if metrics and sk in ("pt", "onnx", "bmodel"):
                sop_metric_keys.append(mkey)

        if len(sop_metric_keys) >= 2:
            compare_groups.append(
                YoloEvalHubCompareGroup(
                    id=f"sop_metrics_compare:{export_id}",
                    title=f"{exp_label} · SOP 数值对照",
                    description="PT / ONNX / bmodel 在同一 test 集上的 mAP50、精确率、召回率",
                    report_keys=sop_metric_keys,
                    layout="table",
                )
            )

        bv = None
        try:
            from labelu.internal.application.service import (
                board_agent as board_agent_service,
            )

            bv = board_agent_service.get_status_for_export(
                db, export_id=export_id, quality_report=qr
            )
        except Exception:
            bv = None
        if bv:
            bv_dict = bv.dict() if hasattr(bv, "dict") else dict(bv)
            reports.append(
                YoloEvalHubReportItem(
                    key=f"edge:{export_id}",
                    group="edge",
                    subgroup="ssh",
                    title=f"{exp_label} · 边端 SSH 验证",
                    description=bv_dict.get("stage") or bv_dict.get("status"),
                    status=str(bv_dict.get("status") or "ok"),
                    format="json",
                    export_id=export_id,
                    metrics=bv_dict,
                )
            )

    if focus_export_id is not None:
        fid = int(focus_export_id)

        def _sort_key(item: YoloEvalHubReportItem) -> tuple:
            if item.export_id == fid:
                return (0, item.group, item.key)
            return (1, item.group, item.key)

        reports.sort(key=_sort_key)

    return YoloEvalHubResponse(
        task_id=task_id,
        task_name=task.name or "",
        job_id=job_id,
        job_status=job.status or "",
        ai_eval_full_status=getattr(job, "ai_eval_full_status", None),
        ai_eval_next_status=getattr(job, "ai_eval_next_status", None),
        ai_eval_full_operation_id=getattr(job, "ai_eval_full_operation_id", None),
        ai_eval_next_operation_id=getattr(job, "ai_eval_next_operation_id", None),
        train_metrics=train_m,
        test_metrics=test_m,
        reports=reports,
        compare_groups=compare_groups,
        focus_export_id=int(focus_export_id) if focus_export_id is not None else None,
    )


def _list_sop_curve_assets(export_root: Path, sop_kind: str) -> list[str]:
    sub_map = {
        "pt": "results_cloud",
        "onnx": "results_onnx",
        "bmodel": "results_bmodel",
    }
    sub = sub_map.get(sop_kind)
    if not sub:
        return []
    base = export_root / "server_eval" / sub
    if not base.is_dir():
        return []
    names: list[str] = []
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        for p in sorted(base.glob(pattern)):
            names.append(p.name)
    return names[:12]


def get_eval_report_by_id(
    db: Session,
    *,
    report_id: int,
    current_user: User,
) -> YoloEvalReportResponse:
    row = crud_yolo_eval_report.get_by_id(db, report_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57011_YOLO_EVAL_REPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    job = crud_yolo_train_job.get(db=db, job_id=row.yolo_train_job_id)
    if not job:
        raise LabelUException(
            code=ErrorCode.CODE_57011_YOLO_EVAL_REPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = crud_task.get(db=db, task_id=job.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_57011_YOLO_EVAL_REPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)
    return _to_eval_report_response(row, job, task)
