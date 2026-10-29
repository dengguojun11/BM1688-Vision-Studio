"""BM1688 真机验证证据契约。

该模块只解析验证回传 JSON，不连接设备、不执行编译，也不修改数据库。
它把“进程退出成功”与“可用于发布的真机证据”分开：缺任一关键证据时，
调用方必须保持待复核状态。设备端的职责是加载已有 BModel 并推理，PT/ONNX
到三种 BModel 的编译证据由服务器编译链提供。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from labelu.internal.common.bm1688_quality_rules import validate_bm1688_quality_rule


EDGE_EVIDENCE_CONTRACT_VERSION = "bm1688_edge_validation_evidence.v1"
REQUIRED_BMODEL_PRECISIONS = ("fp32", "fp16", "int8")
# 一期“短稳”不是长时间压测，但不能用一次推理冒充稳定性。
MIN_SHORT_STABILITY_SECONDS = 60


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_positive(value: Any) -> float | None:
    parsed = _as_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _nested_dicts(report: dict[str, Any]) -> list[dict[str, Any]]:
    result = _as_dict(report.get("result"))
    execution = _as_dict(report.get("execution"))
    result_execution = _as_dict(result.get("execution"))
    result_summary = _as_dict(result.get("execution_summary"))
    board_report = _as_dict(report.get("board_report"))
    result_board_report = _as_dict(result.get("board_report"))
    # Current runners keep evaluation details at the report root while older
    # runners put them below ``result``. Expose both shapes to one contract.
    return [
        report,
        result,
        execution,
        result_execution,
        result_summary,
        board_report,
        result_board_report,
        _as_dict(report.get("eval_summary")),
        _as_dict(report.get("quality_gate")),
        _as_dict(report.get("pt_eval")),
        _as_dict(report.get("comparison_pt_bmodel")),
        _as_dict(result.get("eval_summary")),
        _as_dict(result.get("quality_gate")),
    ]


def _collect_infer_runs(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    parents = _nested_dicts(report)
    for parent in parents:
        for key in ("infer_runs", "bmcv_infer_runs", "inference_runs"):
            value = parent.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
        metrics = _as_dict(parent.get("metrics"))
        for key in ("infer_runs", "bmcv_infer_runs", "inference_runs"):
            value = metrics.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
        bmcv = _as_dict(parent.get("bmcv_infer"))
        if bmcv:
            rows.append(bmcv)
        for key in ("models", "eval_models"):
            models = parent.get(key)
            if isinstance(models, dict):
                for precision, value in models.items():
                    if isinstance(value, dict):
                        row = dict(value)
                        # ``precision`` is also a numeric quality metric in
                        # these reports; use the model tag for variant
                        # identity so it is not overwritten by that metric.
                        row.setdefault("model_tag", precision)
                        rows.append(row)
    # Preserve the first observation for a model and avoid duplicate rows when
    # the server normalizes both ``bmcv_infer_runs`` and ``metrics.infer_runs``.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        precision = _precision_of(row)
        name = str(row.get("bmodel") or row.get("model_filename") or "")
        key = (precision, name)
        if key in seen and key != ("", ""):
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _precision_of(row: dict[str, Any]) -> str:
    raw = str(
        row.get("model_tag")
        or row.get("tag")
        or row.get("precision")
        or row.get("bmodel")
        or row.get("model_filename")
        or ""
    ).lower()
    for precision in REQUIRED_BMODEL_PRECISIONS:
        if re.search(rf"(?:^|[^a-z]){precision}(?:[^a-z]|$)", raw):
            return precision
    return ""


def _metric_sources(report: dict[str, Any], runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for parent in _nested_dicts(report):
        for key in ("metrics", "runtime_metrics", "quality", "evaluation", "eval_summary", "stability"):
            value = parent.get(key)
            if isinstance(value, dict):
                sources.append(value)
        bmodel_sop = _as_dict(parent.get("bmodel_sop"))
        if bmodel_sop:
            sources.append(bmodel_sop)
            if isinstance(bmodel_sop.get("metrics"), dict):
                sources.append(bmodel_sop["metrics"])
            models = bmodel_sop.get("models")
            if isinstance(models, dict):
                sources.extend(item for item in models.values() if isinstance(item, dict))
    for row in runs:
        sources.append(row)
        for key in ("metrics", "runtime_metrics", "quality", "evaluation", "stability"):
            value = row.get(key)
            if isinstance(value, dict):
                sources.append(value)
    return sources


def _find_number(sources: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    for source in sources:
        for key in keys:
            value = _as_positive(source.get(key))
            if value is not None:
                return value
    return None


def _find_ratio(sources: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    for source in sources:
        for key in keys:
            value = _as_float(source.get(key))
            if value is not None and 0 <= value <= 1:
                return value
    return None


def _model_identity(report: dict[str, Any]) -> dict[str, Any]:
    for parent in _nested_dicts(report):
        for key in ("model_identity", "model", "edge_evidence_context"):
            value = parent.get(key)
            if isinstance(value, dict):
                identity = dict(value)
                if any(identity.get(name) not in (None, "") for name in ("model_id", "export_id", "aicm_model_id")):
                    return identity
        export_id = parent.get("export_id")
        model_id = parent.get("model_id")
        if export_id not in (None, "") or model_id not in (None, ""):
            return {
                "model_id": _first_non_empty(model_id, export_id),
                "export_id": export_id,
                "aicm_model_id": parent.get("aicm_model_id"),
            }
    return {
        "model_id": _first_non_empty(report.get("model_id"), report.get("export_id")),
        "export_id": report.get("export_id"),
        "aicm_model_id": report.get("aicm_model_id"),
    }


def _device_identity(report: dict[str, Any]) -> dict[str, Any]:
    for parent in _nested_dicts(report):
        candidate = _first_non_empty(
            parent.get("device"),
            parent.get("device_identity"),
            parent.get("execution_device"),
        )
        if isinstance(candidate, dict):
            return dict(candidate)
        host = _first_non_empty(
            parent.get("execution_device_host"),
            parent.get("remote_host"),
            parent.get("edge_host"),
        )
        device_id = _first_non_empty(
            parent.get("execution_device_id"),
            parent.get("device_id"),
            parent.get("edge_device_id"),
        )
        if host or device_id:
            return {"host": host, "device_id": device_id}
    return {}


def _dataset_evidence(report: dict[str, Any]) -> dict[str, Any]:
    for parent in _nested_dicts(report):
        for key in ("fixed_eval_dataset", "eval_dataset", "dataset_evidence", "board_eval_manifest"):
            value = parent.get(key)
            if isinstance(value, dict):
                return dict(value)
        fixed_eval = parent.get("fixed_eval")
        if isinstance(fixed_eval, dict):
            normalized = dict(fixed_eval)
            signature = _first_non_empty(
                normalized.get("dataset_content_signature"),
                normalized.get("pack_signature"),
                normalized.get("fixed_eval_signature"),
                normalized.get("actual_signature"),
                normalized.get("expected_signature"),
            )
            if signature:
                normalized.setdefault("fixed_eval_signature", signature)
            return normalized
    return {}


def _artifact_signature(report: dict[str, Any]) -> str | None:
    for parent in _nested_dicts(report):
        for key in ("artifact_execution_signature", "execution_signature", "model_execution_signature"):
            value = str(parent.get(key) or "").strip()
            if value:
                return value
        manifest = _as_dict(parent.get("artifact_manifest"))
        value = str(manifest.get("execution_signature") or "").strip()
        if value:
            return value
    return None


def _profile_digest(report: dict[str, Any]) -> str | None:
    """读取服务器编译档案摘要，不从板端字段推断或重建。"""
    for parent in _nested_dicts(report):
        for key in ("profile_digest", "bm1688_profile_digest"):
            value = str(parent.get(key) or "").strip()
            if value:
                return value
        profile = _as_dict(parent.get("bm1688_profile"))
        value = str(
            profile.get("profile_digest") or profile.get("snapshot_digest") or ""
        ).strip()
        if value:
            return value
        manifest = _as_dict(parent.get("artifact_manifest"))
        value = str(manifest.get("profile_digest") or "").strip()
        if value:
            return value
    return None


def _fencing_evidence(report: dict[str, Any]) -> dict[str, Any]:
    for parent in _nested_dicts(report):
        value = _first_non_empty(parent.get("execution_fencing"), parent.get("fencing"))
        if isinstance(value, dict):
            return dict(value)
        token = _first_non_empty(parent.get("execution_fencing_token"), parent.get("fencing_token"))
        if token not in (None, ""):
            return {"token": token, "status": "present"}
    return {}


def _execution_generation_state(report: dict[str, Any]) -> dict[str, Any]:
    """Read the device-local persisted execution-generation evidence."""
    for parent in _nested_dicts(report):
        value = parent.get("execution_generation_state")
        if isinstance(value, dict):
            return dict(value)
    return {}


def evaluate_edge_validation_evidence(
    report: dict[str, Any] | None,
    *,
    expected: dict[str, Any] | None = None,
    require_aicm_id: bool = False,
    min_stability_seconds: int = MIN_SHORT_STABILITY_SECONDS,
) -> dict[str, Any]:
    """Evaluate the publish-grade evidence without inferring missing facts.

    ``status=pass`` means the report has enough evidence for the caller's
    declared gate. ``pending`` is used for incomplete reports; ``fail`` is
    reserved for an explicit failed execution or identity mismatch.
    """
    payload = _as_dict(report)
    expected = _as_dict(expected)
    reasons: list[str] = []
    identity = _model_identity(payload)
    device = _device_identity(payload)
    dataset = _dataset_evidence(payload)
    runs = _collect_infer_runs(payload)
    sources = _metric_sources(payload, runs)
    artifact_signature = _artifact_signature(payload)
    profile_digest = _profile_digest(payload)
    fencing = _fencing_evidence(payload)
    generation_state = _execution_generation_state(payload)

    status_raw = str(payload.get("status") or _as_dict(payload.get("result")).get("status") or "").lower()
    if status_raw in {"fail", "failed", "error"} or payload.get("passed") is False:
        reasons.append("edge_execution_failed")

    if not (device.get("device_id") or device.get("host") or device.get("serial")):
        reasons.append("device_identity_missing")
    model_id = _first_non_empty(identity.get("model_id"), identity.get("export_id"))
    if model_id in (None, ""):
        reasons.append("model_identity_missing")
    if require_aicm_id and identity.get("aicm_model_id") in (None, ""):
        reasons.append("aicm_model_id_missing")
    expected_signature = str(expected.get("execution_signature") or "").strip()
    if not artifact_signature:
        reasons.append("artifact_signature_missing")
    elif expected_signature and artifact_signature != expected_signature:
        reasons.append("artifact_signature_mismatch")
    expected_profile_digest = str(expected.get("profile_digest") or "").strip()
    if expected_profile_digest and not profile_digest:
        reasons.append("profile_digest_missing")
    elif expected_profile_digest and profile_digest != expected_profile_digest:
        reasons.append("profile_digest_mismatch")

    expected_generation = str(expected.get("execution_generation") or "").strip()
    if expected.get("require_execution_generation_state"):
        state_status = str(generation_state.get("status") or "").strip().lower()
        if not generation_state:
            reasons.append("execution_generation_state_missing")
        elif state_status not in {"accepted_new", "idempotent"}:
            reasons.append("execution_generation_not_consumed")
        state_generation = str(
            generation_state.get("execution_generation") or ""
        ).strip()
        if expected_generation and state_generation and state_generation != expected_generation:
            reasons.append("execution_generation_mismatch")

    available_precisions = {
        _precision_of(row)
        for row in runs
        if _precision_of(row) and row.get("ok", row.get("returncode", 0) == 0) is not False
    }
    required = tuple(expected.get("required_precisions") or REQUIRED_BMODEL_PRECISIONS)
    missing_precisions = [item for item in required if item not in available_precisions]
    if missing_precisions:
        reasons.append("bmodel_precision_missing:" + ",".join(missing_precisions))

    eval_count = _find_number(
        [dataset, *sources],
        ("valid_inference_count", "successful_inference_count", "input_image_count", "image_count", "images", "inference_count"),
    )
    expected_count = _as_positive(expected.get("eval_image_count")) or _as_positive(dataset.get("image_count"))
    if eval_count is None or eval_count <= 0:
        reasons.append("valid_inference_count_missing")
    elif expected_count is not None and eval_count < expected_count:
        reasons.append("valid_inference_count_incomplete")

    eval_signature = str(
        dataset.get("dataset_content_signature")
        or dataset.get("pack_signature")
        or dataset.get("fixed_eval_signature")
        or ""
    ).strip()
    if not eval_signature:
        reasons.append("fixed_eval_signature_missing")
    expected_eval_signature = str(expected.get("fixed_eval_signature") or "").strip()
    if expected_eval_signature and eval_signature != expected_eval_signature:
        reasons.append("fixed_eval_signature_mismatch")

    # A board report may contain one dataset block plus one result per
    # precision. If a runner reports those bindings, every precision must
    # point to the exact same sealed pack and image count. Missing optional
    # fields remain compatible with historical reports; a declared mismatch
    # always fails closed.
    binding_status = str(dataset.get("binding_status") or "").strip().lower()
    if binding_status in {"fail", "failed", "invalid"}:
        reasons.append("fixed_eval_binding_failed")
    run_signatures = set()
    run_counts = set()
    for row in runs:
        row_dataset = _as_dict(row.get("fixed_eval_dataset") or row.get("dataset_evidence"))
        row_signature = str(
            row_dataset.get("dataset_content_signature")
            or row_dataset.get("pack_signature")
            or row_dataset.get("fixed_eval_signature")
            or row.get("fixed_eval_signature")
            or ""
        ).strip()
        if row_signature:
            run_signatures.add(row_signature)
        row_count = _as_positive(
            row_dataset.get("image_count")
            or row_dataset.get("images")
            or row.get("input_image_count")
        )
        if row_count is not None:
            run_counts.add(int(row_count))
    if len(run_signatures) > 1 or (eval_signature and run_signatures and eval_signature not in run_signatures):
        reasons.append("fixed_eval_precision_signature_mismatch")
    if expected_count is not None and any(count != int(expected_count) for count in run_counts):
        reasons.append("fixed_eval_precision_image_count_mismatch")

    quality_rule = expected.get("quality_rule")
    if not isinstance(quality_rule, dict):
        quality_rule = payload.get("quality_rule")
    if isinstance(quality_rule, dict):
        rule_errors = validate_bm1688_quality_rule(quality_rule)
        reasons.extend(rule_errors)
        limits = (
            quality_rule.get("edge", {}).get("variant_max_drop", {})
            if isinstance(quality_rule.get("edge"), dict)
            else {}
        )
        maps: dict[str, float] = {}
        for row in runs:
            precision = _precision_of(row)
            metrics = _as_dict(row.get("metrics"))
            raw_map = metrics.get("map50", row.get("map50"))
            value = _as_float(raw_map)
            if precision and value is not None:
                maps[precision] = value
        if all(tag in maps for tag in REQUIRED_BMODEL_PRECISIONS):
            baseline = maps["fp32"]
            drops = {
                tag: max(0.0, baseline - maps[tag])
                for tag in ("fp16", "int8")
            }
            for tag, drop in drops.items():
                try:
                    limit = float(limits.get(tag))
                except (TypeError, ValueError):
                    limit = float("nan")
                if math.isfinite(limit) and drop > limit + 1e-9:
                    reasons.append("{}_map50_drop_exceeded".format(tag))
        # Board Agent's generic runtime report can legitimately arrive before
        # the separate BModel evaluation summary. The runner itself owns the
        # numerical drop gate; do not turn an otherwise valid runtime report
        # into a false failure merely because that summary is not embedded.

    accuracy = _find_number(
        sources,
        ("map50_95", "map50", "precision", "recall", "accuracy", "f1"),
    )
    if accuracy is None:
        reasons.append("accuracy_missing")

    p95_ms = _find_number(
        sources,
        ("p95_ms", "latency_p95_ms", "p95_inference_ms", "infer_p95_ms", "total_p95_ms"),
    )
    if p95_ms is None:
        reasons.append("p95_latency_missing")

    error_rate = _find_ratio(
        sources,
        ("error_rate", "inference_error_rate", "failure_rate"),
    )
    if error_rate is None:
        reasons.append("error_rate_missing")
    elif error_rate > 0:
        reasons.append("inference_error_rate_nonzero")

    stability: dict[str, Any] = {}
    for parent in [payload, _as_dict(payload.get("result")), *sources]:
        candidate = parent.get("stability") if isinstance(parent, dict) else None
        if isinstance(candidate, dict):
            stability = dict(candidate)
            break
    stability_seconds = _as_positive(
        _first_non_empty(
            stability.get("duration_seconds"),
            stability.get("stability_seconds"),
            stability.get("duration"),
            stability.get("seconds"),
        )
    )
    stability_status = str(stability.get("status") or "").lower()
    if stability.get("enabled") is False or stability_seconds is None or stability_seconds < max(1, int(min_stability_seconds)):
        reasons.append("short_stability_missing")
    elif stability_status in {"fail", "failed", "error"} or stability.get("passed") is False:
        reasons.append("short_stability_failed")

    if not fencing.get("token") and not fencing.get("fencing_token"):
        reasons.append("execution_fencing_missing")

    if (
        "edge_execution_failed" in reasons
        or "execution_generation_not_consumed" in reasons
        or any(item.endswith("_mismatch") for item in reasons)
    ):
        gate_status = "fail"
    elif reasons:
        gate_status = "pending"
    else:
        gate_status = "pass"
    return {
        "contract_version": EDGE_EVIDENCE_CONTRACT_VERSION,
        "status": gate_status,
        "publishable": gate_status == "pass",
        "reason_codes": reasons,
        "device": device,
        "model_identity": identity,
        "artifact_execution_signature": artifact_signature,
        "profile_digest": profile_digest,
        "expected_profile_digest": expected_profile_digest or None,
        "execution_generation_state": generation_state,
        "expected_execution_generation": expected_generation or None,
        "required_precisions": list(required),
        "available_precisions": sorted(available_precisions),
        "missing_precisions": missing_precisions,
        "fixed_eval": {
            "signature": eval_signature or None,
            "image_count": eval_count,
            "expected_image_count": expected_count,
            "precision_signatures": sorted(run_signatures),
            "precision_image_counts": sorted(run_counts),
        },
        "quality_rule": {
            "version": quality_rule.get("version") if isinstance(quality_rule, dict) else None,
            "validated": bool(isinstance(quality_rule, dict) and not validate_bm1688_quality_rule(quality_rule)),
        },
        "accuracy": accuracy,
        "latency_p95_ms": p95_ms,
        "error_rate": error_rate,
        "stability_seconds": stability_seconds,
        "fencing": fencing,
    }


def edge_validation_evidence_ready(report: dict[str, Any] | None, **kwargs: Any) -> bool:
    return bool(evaluate_edge_validation_evidence(report, **kwargs).get("publishable"))


__all__ = [
    "EDGE_EVIDENCE_CONTRACT_VERSION",
    "REQUIRED_BMODEL_PRECISIONS",
    "MIN_SHORT_STABILITY_SECONDS",
    "evaluate_edge_validation_evidence",
    "edge_validation_evidence_ready",
]
