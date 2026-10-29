"""Permission-scoped read projections for the model evolution cockpit.

The screen deliberately reads the existing training, automatic-plan and model
records instead of creating a second execution engine.  Two lineage adapters
are exposed through one contract: governed PLAN rounds and legacy JOB_CHAIN
``trained:<job id>`` iterations.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session, selectinload

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.common.config import settings
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.common.yolo_training_metrics import metrics_contract_from_summary
from labelu.internal.domain.models.auto_training import (
    AutoTrainingPlan,
    AutoTrainingRound,
)
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

ACTIVE_STATUSES = {"PENDING", "PROCESSING", "RUNNING", "QUEUED"}
FAILED_STATUSES = {"FAILED", "TRAIN_FAILED", "BM1688_FAILED", "EDGE_VALIDATION_FAILED", "PUBLISH_FAILED"}
WAITING_STATUSES = {
    "WAITING_FOR_DATA",
    "WAITING_FOR_REVIEW",
    "WAITING_FOR_COVERAGE",
    "POLICY_REVIEW_REQUIRED",
    "QUALITY_EVIDENCE_INSUFFICIENT",
    "QUALITY_FAILED",
    "BMODEL_QUALITY_FAILED",
    "PAUSED_SAFETY",
}
GATE_TYPES = ("training", "generalization", "conversion", "edge", "release")
STAGE_DEFINITIONS = (
    ("FEEDBACK", "难例回流"),
    ("DATA_GOVERNANCE", "数据治理"),
    ("SNAPSHOT", "冻结快照"),
    ("TRAINING", "模型训练"),
    ("PT_EVALUATION", "质量评估"),
    ("AI_DECISION", "AI 决策"),
    ("CHAMPION_CHALLENGE", "冠军挑战"),
    ("CONVERSION", "边端转换"),
    ("EDGE_VALIDATION", "真机验证"),
    ("PUBLISH", "模型发布"),
)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "UNKNOWN").upper()


def _metric_number(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _job_metrics(job: YoloTrainJob) -> dict[str, Any]:
    contract = metrics_contract_from_summary(getattr(job, "metrics_summary", None))
    metrics = contract.get("authoritative_val_metrics") or contract.get("last_epoch_metrics") or {}
    return {
        key: metrics.get(key)
        for key in ("map50_95", "map50", "precision", "recall", "fitness", "epoch")
        if metrics.get(key) is not None
    }


def _job_params(job: YoloTrainJob) -> dict[str, Any]:
    return _json(getattr(job, "hyperparams_json", None))


def _dataset_version_id(job: YoloTrainJob) -> int | None:
    params = _job_params(job)
    value = params.get("dataset_version_id") or _json(getattr(job, "dataset_manifest_json", None)).get("dataset_version_id")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _parent_job_id(job: YoloTrainJob) -> int | None:
    match = re.fullmatch(r"trained:(\d+)", str(getattr(job, "model_weights", "") or ""))
    return int(match.group(1)) if match else None


def _job_progress(job: YoloTrainJob) -> dict[str, Any]:
    params = _job_params(job)
    metrics = _json(getattr(job, "metrics_summary", None))
    latest = metrics.get("last_epoch_metrics") if isinstance(metrics.get("last_epoch_metrics"), dict) else {}
    current = params.get("current_epoch") or params.get("actual_epochs") or latest.get("epoch")
    target = getattr(job, "epochs", None) or params.get("epochs")
    try:
        target = max(1, int(target)) if target else None
    except (TypeError, ValueError):
        target = None
    try:
        current = max(0, int(current)) if current is not None else None
    except (TypeError, ValueError):
        current = None
    if current is None and _status(job.status) == "COMPLETED":
        current = target
    if current is None and _status(job.status) == "PENDING":
        current = 0
    if current is not None and target:
        current = min(current, target)
    percent = min(100, max(0, round(current / target * 100))) if current is not None and target else None
    return {"current_epoch": current, "target_epochs": target, "percent": percent}


def _job_stage(job: YoloTrainJob) -> tuple[str, str]:
    status = _status(job.status)
    if status in FAILED_STATUSES or status == "CANCELLED":
        return "TRAINING", "FAILED"
    if status in ACTIVE_STATUSES:
        return "TRAINING", "WAITING" if status in {"PENDING", "QUEUED"} else "RUNNING"
    if status == "COMPLETED":
        if "RUNNING" in {_status(getattr(job, "ai_eval_full_status", None)), _status(getattr(job, "ai_eval_next_status", None))}:
            return "AI_DECISION", "RUNNING"
        return "PT_EVALUATION", "PASSED"
    return "TRAINING", "NOT_STARTED"


def _round_stage(raw_status: str) -> tuple[str, str]:
    status = _status(raw_status)
    if status in FAILED_STATUSES or status in WAITING_STATUSES:
        stage = "TRAINING"
        if "BM1688" in status or "BMODEL" in status:
            stage = "CONVERSION"
        elif "EDGE" in status:
            stage = "EDGE_VALIDATION"
        elif "PUBLISH" in status:
            stage = "PUBLISH"
        elif "QUALITY" in status:
            stage = "PT_EVALUATION"
        return stage, "FAILED" if status in FAILED_STATUSES else "WAITING"
    if status in {"PREPARING", "WAITING_FOR_DATA", "WAITING_FOR_REVIEW", "WAITING_FOR_COVERAGE"}:
        return "DATA_GOVERNANCE", "RUNNING" if status == "PREPARING" else "WAITING"
    if status == "SNAPSHOT_READY":
        return "SNAPSHOT", "PASSED"
    if status in {"QUEUED", "TRAINING"}:
        return "TRAINING", "WAITING" if status == "QUEUED" else "RUNNING"
    if status in {"PT_EVALUATING", "PT_CANDIDATE", "AUTO_QUALITY_REACHED"}:
        return "PT_EVALUATION", "RUNNING" if status == "PT_EVALUATING" else "PASSED"
    if status.startswith("BM1688") or status.startswith("BMODEL") or status.startswith("MANUAL_BM"):
        return "CONVERSION", "RUNNING" if "COMPILING" in status or "EVALUATING" in status else "WAITING"
    if status.startswith("EDGE_VALIDATION") or status == "EDGE_VALIDATING":
        return "EDGE_VALIDATION", "RUNNING" if status == "EDGE_VALIDATING" else "WAITING"
    if status in {"PUBLISH_READY", "PUBLISHING", "PUBLISHED"}:
        return "PUBLISH", "PASSED" if status == "PUBLISHED" else "RUNNING"
    return "CHAMPION_CHALLENGE", "RUNNING"


def _decision_payload(report: YoloEvalReport | None, lineage_id: str, job: YoloTrainJob) -> dict[str, Any] | None:
    if report is None:
        return None
    body = str(report.body or "").strip()
    first_line = next((line.strip(" #>*-\t") for line in body.splitlines() if line.strip()), "")
    suggested = _json(report.suggested_params)
    return {
        "id": f"report:{report.id}",
        "report_id": report.id,
        "lineage_id": lineage_id,
        "job_id": job.id,
        "decision_type": "NEXT_ITERATION_SUGGESTION" if report.report_kind == "next_iteration" else "TRAINING_ANALYSIS",
        "actor_type": "AI",
        "provider_key": report.provider_key,
        "status": "FAILED" if report.error_message else "COMPLETED",
        "summary": report.error_message or first_line[:160] or "AI 评估报告已生成",
        "suggested_params": suggested or None,
        "created_at": _iso(report.created_at),
    }


def _quality_payload(job: YoloTrainJob, model: ModelVersion | None) -> dict[str, str]:
    gates = {gate: "PENDING" for gate in GATE_TYPES}
    metrics = _job_metrics(job)
    if any(_metric_number(metrics, key) is not None for key in ("map50_95", "map50", "precision", "recall")):
        gates["training"] = "PASS"
    summary = _json(getattr(job, "metrics_summary", None))
    test_metrics = summary.get("test_val_metrics")
    if isinstance(test_metrics, dict) and any(_metric_number(test_metrics, key) is not None for key in ("map50_95", "map50", "precision", "recall")):
        gates["generalization"] = "PASS"
    if model is not None:
        artifacts = list(getattr(model, "artifacts", []) or [])
        ready = {str(item.artifact_type).lower() for item in artifacts if _status(item.status) == "READY"}
        if "onnx" in ready and "bmodel" in ready:
            gates["conversion"] = "PASS"
        for evidence in sorted(getattr(model, "quality_evidence", []) or [], key=lambda item: (item.gate_type, item.revision)):
            if evidence.gate_type in gates:
                gates[evidence.gate_type] = _status(evidence.status)
    return gates


def _model_payload(model: ModelVersion | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "id": model.id,
        "model_key": model.model_key,
        "display_name": model.display_name,
        "version_no": model.version_no,
        "status": _status(model.status),
        "parent_model_version_id": model.parent_model_version_id,
        "chip_type": model.chip_type,
        "precision": model.precision,
        "published_at": _iso(model.published_at),
    }


def _best_job(jobs: Iterable[YoloTrainJob]) -> YoloTrainJob | None:
    completed = [job for job in jobs if _status(job.status) == "COMPLETED"]
    if not completed:
        return None
    return max(
        completed,
        key=lambda job: (
            _metric_number(_job_metrics(job), "fitness") or -1,
            _metric_number(_job_metrics(job), "map50_95") or -1,
            int(job.id),
        ),
    )


def _metric_delta(jobs: list[YoloTrainJob]) -> dict[str, Any] | None:
    completed = sorted((job for job in jobs if _status(job.status) == "COMPLETED"), key=lambda job: int(job.id))
    if len(completed) < 2:
        return None
    before, after = _job_metrics(completed[-2]), _job_metrics(completed[-1])
    key = "map50_95" if _metric_number(after, "map50_95") is not None else "map50"
    old, new = _metric_number(before, key), _metric_number(after, key)
    if old is None or new is None:
        return None
    return {"metric": key, "before": old, "after": new, "delta": round(new - old, 6)}


def _lineage_status(
    jobs: list[YoloTrainJob], rounds: list[AutoTrainingRound], plan_status: str | None = None
) -> str:
    if any(_status(job.status) in ACTIVE_STATUSES for job in jobs):
        return "RUNNING"
    if rounds and _status(rounds[-1].state) in ACTIVE_STATUSES | {"TRAINING", "PT_EVALUATING", "BM1688_COMPILING", "EDGE_VALIDATING", "PUBLISHING"}:
        return "RUNNING"
    latest = _status(rounds[-1].state) if rounds else (_status(jobs[-1].status) if jobs else _status(plan_status))
    if latest in FAILED_STATUSES or latest in WAITING_STATUSES:
        return "ATTENTION"
    if latest in {"PUBLISHED", "AUTO_QUALITY_REACHED", "COMPLETED"}:
        return "COMPLETED"
    return "IDLE"


def _node_from_job(
    job: YoloTrainJob,
    *,
    lineage_id: str,
    round_no: int,
    model: ModelVersion | None,
    report: YoloEvalReport | None,
    round_row: AutoTrainingRound | None = None,
) -> dict[str, Any]:
    raw_status = _status(round_row.state) if round_row else _status(job.status)
    stage, stage_status = _round_stage(raw_status) if round_row else _job_stage(job)
    node_id = f"round:{round_row.id}" if round_row else f"job:{job.id}"
    return {
        "id": node_id,
        "lineage_id": lineage_id,
        "round_id": round_row.id if round_row else None,
        "round_no": round_row.round_no if round_row else round_no,
        "job_id": job.id,
        "name": f"第 {round_row.round_no if round_row else round_no} 代 · 训练作业 #{job.id}",
        "status": raw_status,
        "stage": stage,
        "stage_status": stage_status,
        "progress": _job_progress(job),
        "metrics": _job_metrics(job),
        "dataset_version_id": _dataset_version_id(job),
        "model": _model_payload(model),
        "quality_gate": _quality_payload(job, model),
        "decision": _decision_payload(report, lineage_id, job),
        "parent_job_id": round_row.baseline_job_id if round_row else _parent_job_id(job),
        "execution_mode": job.execution_mode,
        "train_node_id": job.train_node_id,
        "assigned_gpu": _json(getattr(job, "assigned_gpu_json", None)) or None,
        "failure_reason": job.error_message,
        "created_at": _iso(round_row.created_at if round_row else job.created_at),
        "updated_at": _iso(round_row.updated_at if round_row else job.updated_at),
    }


def _lineage_payload(
    *,
    lineage_id: str,
    source_type: str,
    name: str,
    series_type: str,
    jobs: list[YoloTrainJob],
    rounds: list[AutoTrainingRound],
    models_by_job: dict[int, ModelVersion],
    reports_by_job: dict[int, YoloEvalReport],
    champion_job_id: int | None,
    include_nodes: bool,
    plan_status: str | None = None,
) -> dict[str, Any]:
    jobs = sorted(jobs, key=lambda job: int(job.id))
    rounds = sorted(rounds, key=lambda row: int(row.round_no))
    job_by_id = {int(job.id): job for job in jobs}
    round_by_job = {int(row.yolo_train_job_id): row for row in rounds if row.yolo_train_job_id}
    nodes = [
        _node_from_job(
            job,
            lineage_id=lineage_id,
            round_no=index,
            model=models_by_job.get(int(job.id)),
            report=reports_by_job.get(int(job.id)),
            round_row=round_by_job.get(int(job.id)),
        )
        for index, job in enumerate(jobs, start=1)
    ]
    # Rounds which have not created a training job still need to be visible.
    for row in rounds:
        if row.yolo_train_job_id:
            continue
        stage, stage_status = _round_stage(_status(row.state))
        nodes.append({
            "id": f"round:{row.id}", "lineage_id": lineage_id, "round_id": row.id,
            "round_no": row.round_no, "job_id": None,
            "name": f"第 {row.round_no} 代 · 数据准备", "status": _status(row.state),
            "stage": stage, "stage_status": stage_status, "progress": None,
            "metrics": {}, "dataset_version_id": None, "model": None,
            "quality_gate": {gate: "PENDING" for gate in GATE_TYPES}, "decision": None,
            "parent_job_id": row.baseline_job_id, "execution_mode": None,
            "train_node_id": None, "assigned_gpu": None, "failure_reason": None,
            "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
        })
    nodes.sort(key=lambda node: (int(node["round_no"]), str(node["id"])))
    best = _best_job(jobs)
    champion = job_by_id.get(int(champion_job_id or 0))
    if champion is None:
        published = [job for job in jobs if (models_by_job.get(int(job.id)) and _status(models_by_job[int(job.id)].status) == "PUBLISHED")]
        champion = published[-1] if published else None
    current = next((job for job in reversed(jobs) if _status(job.status) in ACTIVE_STATUSES), jobs[-1] if jobs else None)
    decisions = [node["decision"] for node in nodes if node.get("decision")]
    updated_values = [job.updated_at or job.created_at for job in jobs] + [row.updated_at or row.created_at for row in rounds]
    updated_at = max((value for value in updated_values if value), default=None)
    payload = {
        "id": lineage_id,
        "source_type": source_type,
        "name": name,
        "series_type": series_type,
        "status": _lineage_status(jobs, rounds, plan_status),
        "round_count": len(rounds) if rounds else len(jobs),
        "model_count": sum(1 for job in jobs if int(job.id) in models_by_job),
        "current_job_id": current.id if current else None,
        "current_round_id": rounds[-1].id if rounds else None,
        "best_job_id": best.id if best else None,
        "champion_job_id": champion.id if champion else None,
        "champion": _model_payload(models_by_job.get(int(champion.id))) if champion else None,
        "latest_metrics": _job_metrics(current) if current else {},
        "best_metrics": _job_metrics(best) if best else {},
        "metric_delta": _metric_delta(jobs),
        "latest_decision": decisions[-1] if decisions else None,
        "updated_at": _iso(updated_at),
    }
    if include_nodes:
        payload["nodes"] = nodes
        payload["edges"] = [
            {"source": f"job:{node['parent_job_id']}", "target": node["id"], "type": "ITERATION"}
            for node in nodes
            if node.get("parent_job_id") and any(item.get("job_id") == node["parent_job_id"] for item in nodes)
        ]
        payload["decisions"] = list(reversed(decisions))
    return payload


def _visible_jobs(db: Session, user: User) -> tuple[list[YoloTrainJob], dict[int, Task]]:
    visible_task_ids = crud_task.visible_query(
        db, owner_id=user.id, list_all_tasks=is_task_superuser(user)
    ).with_entities(Task.id)
    jobs = db.query(YoloTrainJob).filter(YoloTrainJob.task_id.in_(visible_task_ids)).order_by(YoloTrainJob.id.asc()).limit(500).all()
    task_ids = {int(job.task_id) for job in jobs if job.task_id}
    tasks = db.query(Task).filter(Task.id.in_(task_ids)).all() if task_ids else []
    return jobs, {int(task.id): task for task in tasks}


def _build_lineages(db: Session, user: User, *, include_nodes: bool) -> list[dict[str, Any]]:
    jobs, tasks = _visible_jobs(db, user)
    job_ids = [int(job.id) for job in jobs]
    models = []
    reports = []
    if job_ids:
        models = db.query(ModelVersion).options(
            selectinload(ModelVersion.artifacts), selectinload(ModelVersion.quality_evidence)
        ).filter(ModelVersion.training_job_id.in_(job_ids)).all()
        reports = db.query(YoloEvalReport).filter(
            YoloEvalReport.yolo_train_job_id.in_(job_ids)
        ).order_by(YoloEvalReport.id.desc()).limit(500).all()
    models_by_job = {int(model.training_job_id): model for model in models if model.training_job_id}
    reports_by_job: dict[int, YoloEvalReport] = {}
    for report in reports:
        # Prefer next-iteration reports, then the newest report of any kind.
        existing = reports_by_job.get(int(report.yolo_train_job_id))
        if existing is None or (report.report_kind == "next_iteration" and existing.report_kind != "next_iteration"):
            reports_by_job[int(report.yolo_train_job_id)] = report

    plan_query = db.query(AutoTrainingPlan).filter(AutoTrainingPlan.deleted_at.is_(None))
    if not is_task_superuser(user):
        plan_query = plan_query.filter(AutoTrainingPlan.owner_id == user.id)
    plans = plan_query.order_by(AutoTrainingPlan.id.asc()).limit(100).all()
    plan_ids = [int(plan.id) for plan in plans]
    rounds = db.query(AutoTrainingRound).filter(AutoTrainingRound.plan_id.in_(plan_ids)).all() if plan_ids else []
    rounds_by_plan: dict[int, list[AutoTrainingRound]] = {plan_id: [] for plan_id in plan_ids}
    for row in rounds:
        rounds_by_plan[int(row.plan_id)].append(row)

    result = []
    plan_job_ids = {int(job.id) for job in jobs if getattr(job, "auto_training_plan_id", None)}
    for plan in plans:
        plan_rounds = rounds_by_plan.get(int(plan.id), [])
        round_job_ids = {int(row.yolo_train_job_id) for row in plan_rounds if row.yolo_train_job_id}
        plan_jobs = [job for job in jobs if int(job.id) in round_job_ids or int(job.auto_training_plan_id or 0) == int(plan.id)]
        plan_job_ids.update(int(job.id) for job in plan_jobs)
        result.append(_lineage_payload(
            lineage_id=f"plan:{plan.id}", source_type="PLAN", name=plan.name,
            series_type=plan.series_type, jobs=plan_jobs, rounds=plan_rounds,
            models_by_job=models_by_job, reports_by_job=reports_by_job,
            champion_job_id=plan.champion_job_id, include_nodes=include_nodes,
            plan_status=plan.status,
        ))

    job_by_id = {int(job.id): job for job in jobs if int(job.id) not in plan_job_ids}
    groups: dict[int, list[YoloTrainJob]] = {}
    for job in job_by_id.values():
        root = job
        seen = set()
        while True:
            parent_id = _parent_job_id(root)
            if not parent_id or parent_id in seen or parent_id not in job_by_id:
                break
            seen.add(int(root.id))
            root = job_by_id[parent_id]
        groups.setdefault(int(root.id), []).append(job)
    for root_id, chain_jobs in groups.items():
        chain_jobs.sort(key=lambda job: int(job.id))
        root = chain_jobs[0]
        task = tasks.get(int(root.task_id))
        result.append(_lineage_payload(
            lineage_id=f"job-chain:{root_id}", source_type="JOB_CHAIN",
            name=f"{task.name if task else f'任务 #{root.task_id}'} · 自我进化",
            series_type=root.category or root.job_type or "yolo_detection",
            jobs=chain_jobs, rounds=[], models_by_job=models_by_job,
            reports_by_job=reports_by_job, champion_job_id=None, include_nodes=include_nodes,
        ))
    return sorted(result, key=lambda item: item.get("updated_at") or "", reverse=True)


def list_lineages(db: Session, user: User, *, include_nodes: bool = False) -> list[dict[str, Any]]:
    return _build_lineages(db, user, include_nodes=include_nodes)


def get_lineage(db: Session, user: User, lineage_id: str) -> dict[str, Any] | None:
    return next((item for item in _build_lineages(db, user, include_nodes=True) if item["id"] == lineage_id), None)


def _event_payload(lineages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for lineage in lineages:
        for node in lineage.get("nodes", []):
            if node.get("job_id"):
                events.append({
                    "id": f"job-event:{node['job_id']}:{node.get('updated_at')}",
                    "event_type": "TRAINING_STAGE",
                    "lineage_id": lineage["id"], "job_id": node["job_id"],
                    "title": f"{lineage['name']} · 第 {node['round_no']} 代",
                    "summary": node.get("failure_reason") or f"{node['stage']} / {node['status']}",
                    "status": node["stage_status"], "actor_type": "SYSTEM",
                    "occurred_at": node.get("updated_at") or node.get("created_at"),
                })
            if node.get("decision"):
                decision = node["decision"]
                events.append({
                    "id": f"decision-event:{decision['report_id']}", "event_type": "AI_DECISION",
                    "lineage_id": lineage["id"], "job_id": node.get("job_id"),
                    "title": "AI 生成进化建议", "summary": decision["summary"],
                    "status": decision["status"], "actor_type": "AI",
                    "occurred_at": decision["created_at"],
                })
    return sorted(events, key=lambda item: item.get("occurred_at") or "", reverse=True)[:30]


def overview(db: Session, user: User) -> dict[str, Any]:
    lineages = _build_lineages(db, user, include_nodes=True)
    nodes = [dict(node, lineage_name=lineage["name"]) for lineage in lineages for node in lineage.get("nodes", [])]
    active_jobs = [node for node in nodes if node.get("job_id") and _status(node["status"]) in ACTIVE_STATUSES]
    active_jobs.sort(key=lambda node: node.get("updated_at") or "", reverse=True)
    model_ids = {node["model"]["id"] for node in nodes if node.get("model")}
    published_ids = {node["model"]["id"] for node in nodes if node.get("model") and node["model"]["status"] == "PUBLISHED"}
    decisions = sorted(
        [node["decision"] for node in nodes if node.get("decision")],
        key=lambda item: item.get("created_at") or "", reverse=True,
    )
    stages = []
    for key, label in STAGE_DEFINITIONS:
        stage_nodes = [node for node in nodes if node["stage"] == key]
        stages.append({
            "key": key, "label": label, "count": len(stage_nodes),
            "active_count": sum(node["stage_status"] in {"RUNNING", "WAITING"} for node in stage_nodes),
            "failed_count": sum(node["stage_status"] == "FAILED" for node in stage_nodes),
            "waiting_count": sum(node["stage_status"] == "WAITING" for node in stage_nodes),
            "status": "FAILED" if any(node["stage_status"] == "FAILED" for node in stage_nodes)
            else "RUNNING" if any(node["stage_status"] == "RUNNING" for node in stage_nodes)
            else "WAITING" if any(node["stage_status"] == "WAITING" for node in stage_nodes)
            else "PASSED" if stage_nodes else "NOT_STARTED",
        })
    gate_summary = []
    for gate in GATE_TYPES:
        values = [node["quality_gate"][gate] for node in nodes if node.get("quality_gate")]
        gate_summary.append({
            "gate": gate, "pass_count": values.count("PASS"),
            "fail_count": values.count("FAIL"), "pending_count": values.count("PENDING"),
            "status": "FAIL" if "FAIL" in values else "RUNNING" if "PENDING" in values else "PASS" if values else "NOT_STARTED",
        })
    enabled = bool(settings.YOLO_TRAIN_ENABLED)
    plan_enabled = bool(settings.AUTO_TRAINING_PLAN_WRITES_ENABLED)
    iteration_enabled = bool(settings.YOLO_AUTO_ITERATION_ENABLED)
    return {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "lineage_count": len(lineages),
            "active_round_count": sum(item["status"] == "RUNNING" for item in lineages),
            "active_training_count": len(active_jobs),
            "model_count": len(model_ids),
            "published_model_count": len(published_ids),
            "pending_intervention_count": sum(item["status"] == "ATTENTION" for item in lineages),
            "autonomous_decision_count": len(decisions),
        },
        "automation": {
            "training_enabled": enabled,
            "plan_writes_enabled": plan_enabled,
            "auto_iteration_enabled": iteration_enabled,
            "status": "ENABLED" if enabled and (plan_enabled or iteration_enabled) else "PARTIAL" if enabled else "DISABLED",
            "mode": "PLAN" if plan_enabled else "JOB_CHAIN" if iteration_enabled else "MANUAL",
        },
        "stages": stages,
        "lineages": [{key: value for key, value in item.items() if key not in {"nodes", "edges", "decisions"}} for item in lineages[:12]],
        "active_jobs": active_jobs[:12],
        "decisions": decisions[:8],
        "quality_gates": gate_summary,
        "events": _event_payload(lineages)[:12],
    }
