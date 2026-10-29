from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from collections import Counter
from typing import Any

from fastapi import status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_active_learning,
    crud_edge_feedback,
    crud_project,
)
from labelu.internal.application.command.active_learning import (
    AcceptActiveLearningBatchCommand,
    CreateActiveLearningBatchCommand,
    RebuildFeedbackClustersCommand,
)
from labelu.internal.application.command.workflow import CreateWorkflowCommand
from labelu.internal.application.response.active_learning import (
    ActiveLearningBatchItemResponse,
    ActiveLearningBatchResponse,
    FeedbackClusterResponse,
)
from labelu.internal.application.service import workflow as workflow_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.active_learning import (
    ActiveLearningBatch,
    ActiveLearningBatchItem,
    FeedbackCluster,
)
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.edge_feedback import EdgeHardExample, InferenceEvent
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.project import Project, ProjectMember

# Keep the policy deterministic and independent of a model embedding service.  A
# later provider can replace any dimension while preserving this snapshot
# contract.  Values are normalized to [0, 100] before the weighted sum.
_ACTIVE_LEARNING_WEIGHTS = {
    "uncertainty": 0.40,
    "representativeness": 0.25,
    "class_scarcity": 0.20,
    "device_scene_coverage": 0.15,
}

_BATCH_TRANSITIONS = {
    "READY": {"ACCEPTED"},
    "ACCEPTED": set(),
}


def _payload_context(event: InferenceEvent | None) -> tuple[str, str]:
    """Return stable scene/class keys from an edge event payload.

    Edge agents are allowed to send different payload shapes.  The fallback to
    event type keeps old events scoreable without requiring a migration.
    """
    payload: dict[str, Any] = {}
    if event is not None and event.payload_json:
        try:
            value = json.loads(event.payload_json)
            if isinstance(value, dict):
                payload = value
        except (TypeError, ValueError):
            payload = {}

    scene = next(
        (
            str(payload[key]).strip()
            for key in ("scene", "scene_type", "scenario", "camera_scene")
            if payload.get(key) not in (None, "")
        ),
        "unknown",
    )
    class_value: Any = None
    for key in ("class_id", "class", "label", "category_id", "category"):
        if payload.get(key) not in (None, ""):
            class_value = payload[key]
            break
    if class_value is None and isinstance(payload.get("predictions"), list):
        for prediction in payload["predictions"]:
            if isinstance(prediction, dict):
                class_value = next(
                    (
                        prediction[key]
                        for key in ("class_id", "class", "label", "category_id")
                        if prediction.get(key) not in (None, "")
                    ),
                    None,
                )
                if class_value is not None:
                    break
    return scene[:128], (
        str(class_value).strip()[:128] if class_value not in (None, "") else "unknown"
    )


def _rarity_score(count: int, total: int) -> float:
    """Map a context frequency to a [0, 100] rarity score."""
    if total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1.0 - (count - 1) / total))), 2)


def _score_candidates(
    candidates: list[EdgeHardExample], events: dict[int, InferenceEvent]
) -> dict[int, tuple[int, dict[str, float]]]:
    """Score candidates with four explainable active-learning dimensions.

    ``representativeness`` and ``class_scarcity`` are inverse frequencies in
    the current project queue; ``device_scene_coverage`` favors under-covered
    edge contexts.  This is deliberately a queue-local heuristic, so the
    recorded policy remains reproducible when a future embedding provider is
    introduced.
    """
    if not candidates:
        return {}
    contexts: dict[int, tuple[str, str]] = {
        row.id: _payload_context(events.get(row.inference_event_id))
        for row in candidates
    }
    total = len(candidates)
    scene_counts = Counter(scene for scene, _ in contexts.values())
    class_counts = Counter()
    coverage_counts = Counter()
    for row in candidates:
        scene, label = contexts[row.id]
        class_counts[(row.hard_example_type or "unknown", label)] += 1
        coverage_counts[(row.device_id or "unknown", scene)] += 1
    result: dict[int, tuple[int, dict[str, float]]] = {}
    for row in candidates:
        scene, label = contexts[row.id]
        confidence = float(row.confidence) if row.confidence is not None else 50.0
        dimensions = {
            "uncertainty": round(max(0.0, min(100.0, 100.0 - confidence)), 2),
            "representativeness": _rarity_score(scene_counts[scene], total),
            "class_scarcity": _rarity_score(
                class_counts[(row.hard_example_type or "unknown", label)], total
            ),
            "device_scene_coverage": _rarity_score(
                coverage_counts[(row.device_id or "unknown", scene)], total
            ),
        }
        score = round(
            sum(
                _ACTIVE_LEARNING_WEIGHTS[key] * value
                for key, value in dimensions.items()
            )
        )
        result[row.id] = (score, dimensions)
    return result


def _project(db: Session, project_id: int, user: User, manage: bool = False):
    project = crud_project.get(db, project_id)
    if project is None:
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR, status_code=404, message="项目不存在"
        )
    if is_task_superuser(user) or project.owner_id == user.id:
        return project
    member = crud_project.get_member(db, project_id, user.id)
    if (
        not member
        or member.status != "ACTIVE"
        or (manage and member.role not in {"admin", "owner"})
    ):
        raise LabelUException(
            ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=403,
            message="无权访问该项目",
        )
    return project


def _scope_key(project_id: int | None) -> str:
    return f"project:{int(project_id)}" if project_id is not None else "global"


def _check_scope(db: Session, project_id: int | None, user: User, manage: bool = False):
    """Authorize a historical project scope or the new global scope."""
    if project_id is None:
        if manage and not is_task_superuser(user):
            raise LabelUException(
                ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=status.HTTP_403_FORBIDDEN,
                message="全局主动学习编排仅允许管理人员操作",
            )
        return None
    return _project(db, int(project_id), user, manage=manage)


def _source_key(event: InferenceEvent | None) -> str | None:
    """Extract a stable third-party/S3 source package key from edge payload."""
    if event is None or not event.payload_json:
        return None
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("source_key", "source_package", "source_package_id", "package_id", "s3_package"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()[:160]
    return None


def _cluster_dimensions(hard: EdgeHardExample, event: InferenceEvent | None) -> tuple[str, int | None, str | None, str]:
    source = _source_key(event)
    model_id = hard.model_version_id
    # Keep the key human-readable while escaping the delimiter used here.
    parts = [
        str(hard.hard_example_type or "unknown"),
        str(hard.device_id or "unknown"),
        f"model:{model_id or 'unknown'}",
        f"source:{(source or 'unknown').replace('|', '%7C')}",
    ]
    return hard.hard_example_type, model_id, source, "|".join(parts)[:128]


def _visible_global_query(db: Session, query, user: User):
    """Apply the same creator/history/model visibility used by hard examples."""
    if is_task_superuser(user):
        return query
    membership = (
        db.query(ProjectMember.project_id)
        .filter(
            ProjectMember.project_id == EdgeHardExample.project_id,
            ProjectMember.user_id == int(user.id),
            ProjectMember.status == "ACTIVE",
        )
        .exists()
    )
    return (
        query.outerjoin(Project, Project.id == EdgeHardExample.project_id)
        .outerjoin(ModelVersion, ModelVersion.id == EdgeHardExample.model_version_id)
        .filter(
            or_(
                EdgeHardExample.created_by == int(user.id),
                Project.owner_id == int(user.id),
                ModelVersion.created_by == int(user.id),
                membership,
            )
        )
    )


def _scope_hard_examples(db: Session, project_id: int | None, user: User):
    query = db.query(EdgeHardExample).filter(
        EdgeHardExample.scope_key == _scope_key(project_id),
        EdgeHardExample.confidence.isnot(None),
        EdgeHardExample.review_status.in_(("PENDING", "IN_REVIEW", "ACCEPTED")),
        EdgeHardExample.training_status.in_(("NOT_INCLUDED", "READY")),
    )
    if project_id is not None:
        query = query.filter(EdgeHardExample.project_id == int(project_id))
    else:
        query = _visible_global_query(db, query, user)
    return query


def _cluster_response(row: FeedbackCluster) -> FeedbackClusterResponse:
    return FeedbackClusterResponse(
        id=row.id,
        project_id=row.project_id,
        scope_key=row.scope_key,
        created_by=row.created_by,
        cluster_key=row.cluster_key,
        event_type=row.event_type,
        device_id=row.device_id,
        model_version_id=row.model_version_id,
        source_key=row.source_key,
        sample_count=row.sample_count,
        average_confidence=row.average_confidence,
        priority_score=row.priority_score,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _batch_response(
    db: Session, row: ActiveLearningBatch
) -> ActiveLearningBatchResponse:
    policy = {}
    try:
        policy = json.loads(row.scoring_policy_json or "{}")
    except (TypeError, ValueError):
        pass
    if not isinstance(policy, dict):
        policy = {}
    candidate_scores = policy.get("candidate_scores")
    if not isinstance(candidate_scores, dict):
        candidate_scores = {}
    items = []
    for item in crud_active_learning.list_items(db, row.id):
        breakdown = candidate_scores.get(str(item.hard_example_id), {})
        if not isinstance(breakdown, dict):
            breakdown = {}
        # Do not expose the aggregate twice; ``score`` is the canonical value.
        breakdown = {
            str(key): float(value)
            for key, value in breakdown.items()
            if key != "score" and isinstance(value, (int, float))
        }
        items.append(
            ActiveLearningBatchItemResponse(
                id=item.id,
                hard_example_id=item.hard_example_id,
                score=item.score,
                rank=item.rank,
                score_breakdown=breakdown,
            )
        )
    return ActiveLearningBatchResponse(
        id=row.id,
        project_id=row.project_id,
        scope_key=row.scope_key,
        batch_key=row.batch_key,
        model_version_id=row.model_version_id,
        source_key=row.source_key,
        quota=row.quota,
        selected_count=row.selected_count,
        accepted_count=row.accepted_count,
        status=row.status,
        workflow_id=row.workflow_id,
        dataset_version_id=row.dataset_version_id,
        scoring_policy=policy,
        items=items,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _materialize_dataset_version(
    db: Session,
    row: ActiveLearningBatch,
    items: list[ActiveLearningBatchItem],
    user: User,
) -> DatasetVersion:
    """Persist an immutable manifest for an accepted hard-example batch.

    The version intentionally remains ``READY_TO_REVIEW``.  A later review or
    workflow worker must freeze it before a training adapter can consume it.
    This keeps active learning from bypassing the normal dataset quality gate.
    """
    if row.dataset_version_id:
        existing = (
            db.query(DatasetVersion).filter_by(id=row.dataset_version_id).one_or_none()
        )
        if existing is not None:
            return existing
    examples = []
    for item in items:
        hard = crud_edge_feedback.get_hard(db, item.hard_example_id)
        if hard is None or hard.scope_key != row.scope_key:
            continue
        examples.append(
            {
                "hard_example_id": hard.id,
                "inference_event_id": hard.inference_event_id,
                "device_id": hard.device_id,
                "model_version_id": hard.model_version_id,
                "fingerprint": hard.fingerprint,
                "asset_uri": hard.asset_uri,
                "confidence": hard.confidence,
                "hard_example_type": hard.hard_example_type,
            }
        )
    manifest = {
        "format": "7gai.active_learning_manifest.v1",
        "project_id": row.project_id,
        "scope_key": row.scope_key,
        "batch_id": row.id,
        "items": examples,
    }
    content = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    from labelu.internal.common.storage import get_storage_backend

    key = f"active-learning/{row.scope_key}/batch-{row.id}/manifest.json"
    get_storage_backend().save_bytes(content, key, content_type="application/json")
    dataset = Dataset(
        project_id=row.project_id,
        owner_id=user.id,
        name=f"主动学习批次 {row.id}",
        description="由边端难例回流自动生成，需审核后冻结",
        status="ACTIVE",
        created_by=user.id,
        updated_by=user.id,
    )

    db.add(dataset)
    db.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        project_id=row.project_id,
        version_no=1,
        status="READY_TO_REVIEW",
        manifest_rel_path=key,
        manifest_sha256=digest,
        sample_count=len(examples),
        quality_status="PENDING_REVIEW",
        review_status="PENDING",
        metadata_json=json.dumps(
            {
                "source": "active_learning_batch",
                "batch_id": row.id,
                "project_id": row.project_id,
                "scope_key": row.scope_key,
                "series_type": "yolo_detection",
                "lineage": "edge_hard_example",
            },
            separators=(",", ":"),
        ),
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    row.dataset_version_id = version.id
    return version


def refresh_cluster_for_hard_example(
    db: Session, *, hard_example: EdgeHardExample, confidence_threshold: int = 50
) -> FeedbackCluster | None:
    """Refresh the queue cluster affected by one newly ingested edge example.

    Edge feedback is accepted asynchronously and starts in ``PENDING`` review
    status. Updating only the affected ``event_type/device_id`` cluster keeps
    the feedback path cheap while making the active-learning queue visible
    immediately. The full rebuild endpoint remains available for threshold or
    historical-data corrections.
    """
    if hard_example.confidence is None or int(hard_example.confidence) > int(
        confidence_threshold
    ):
        return None
    scope_key = hard_example.scope_key or _scope_key(hard_example.project_id)
    event = (
        db.query(InferenceEvent)
        .filter(InferenceEvent.id == int(hard_example.inference_event_id))
        .one_or_none()
    )
    event_type, model_version_id, source_key, cluster_key = _cluster_dimensions(hard_example, event)
    rows = (
        db.query(EdgeHardExample)
        .filter(
            EdgeHardExample.scope_key == scope_key,
            EdgeHardExample.hard_example_type == hard_example.hard_example_type,
            EdgeHardExample.device_id == hard_example.device_id,
            EdgeHardExample.model_version_id == model_version_id,
            EdgeHardExample.confidence.isnot(None),
            EdgeHardExample.confidence <= int(confidence_threshold),
            EdgeHardExample.review_status.in_(("PENDING", "IN_REVIEW", "ACCEPTED")),
            EdgeHardExample.training_status.in_(("NOT_INCLUDED", "READY")),
        )
        .all()
    )
    matching = []
    for candidate in rows:
        candidate_event = db.query(InferenceEvent).filter(
            InferenceEvent.id == candidate.inference_event_id
        ).one_or_none()
        if _source_key(candidate_event) == source_key:
            matching.append(candidate)
    rows = matching
    cluster = crud_active_learning.find_cluster(
        db, hard_example.project_id, cluster_key, scope_key=scope_key
    )
    if not rows:
        if cluster is not None:
            cluster.sample_count = 0
            cluster.average_confidence = None
            cluster.priority_score = 0
            cluster.status = "EMPTY"
        return cluster
    if cluster is None:
        cluster = crud_active_learning.create_cluster(
            db,
            FeedbackCluster(
                project_id=hard_example.project_id,
                scope_key=scope_key,
                created_by=hard_example.created_by,
                cluster_key=cluster_key,
                event_type=event_type,
                device_id=hard_example.device_id,
                model_version_id=model_version_id,
                source_key=source_key,
            ),
        )
    cluster.sample_count = len(rows)
    cluster.average_confidence = round(
        sum(int(item.confidence or 0) for item in rows) / len(rows)
    )
    cluster.priority_score = max(0, 100 - cluster.average_confidence) * len(rows)
    cluster.status = "READY"
    return cluster


def _mark_batch_items_consumed(
    db: Session, items: list[ActiveLearningBatchItem]
) -> None:
    """Mark accepted hard examples as consumed by the active-learning loop.

    ``READY`` is the admission state for a training candidate.  Once a batch
    has been accepted and linked to a dataset/workflow, retaining that state
    would allow a later batch key to select the same example again after the
    original reservation is no longer considered open.  The transition is
    intentionally idempotent so retries of an accepted command are harmless.
    """
    for item in items:
        hard = crud_edge_feedback.get_hard(db, int(item.hard_example_id))
        if hard is not None and hard.training_status == "READY":
            hard.training_status = "COMPLETED"
    # SessionLocal disables autoflush. Recalculate each affected cluster only
    # after the consumed state is flushed, otherwise the just-consumed rows
    # would still be counted by the queue query.
    db.flush()
    for item in items:
        hard = crud_edge_feedback.get_hard(db, int(item.hard_example_id))
        if hard is None:
            continue
        refresh_cluster_for_hard_example(db, hard_example=hard)


async def rebuild_clusters(
    db: Session, cmd: RebuildFeedbackClustersCommand, user: User
):
    _check_scope(db, cmd.project_id, user, manage=True)
    # Clusters drive the actionable review/active-learning queue.  Exclude
    # rejected or already-consumed examples so their historical volume cannot
    # inflate priority for a cluster that no longer needs operator action.
    rows = _scope_hard_examples(db, cmd.project_id, user).filter(
        EdgeHardExample.confidence <= cmd.confidence_threshold
    ).all()
    event_ids = {row.inference_event_id for row in rows}
    events = {
        event.id: event
        for event in db.query(InferenceEvent).filter(InferenceEvent.id.in_(event_ids)).all()
    } if event_ids else {}
    grouped: dict[str, list[EdgeHardExample]] = defaultdict(list)
    for row in rows:
        _, _, _, key = _cluster_dimensions(row, events.get(row.inference_event_id))
        grouped[key].append(row)
    result = []
    with begin_transaction(db):
        for key, items in grouped.items():
            event_type, model_version_id, source_key, _ = _cluster_dimensions(
                items[0], events.get(items[0].inference_event_id)
            )
            avg = round(sum(int(x.confidence or 0) for x in items) / len(items))
            scope_key = _scope_key(cmd.project_id)
            cluster = crud_active_learning.find_cluster(
                db, cmd.project_id, key, scope_key=scope_key
            )
            if cluster is None:
                cluster = crud_active_learning.create_cluster(
                    db,
                    FeedbackCluster(
                        project_id=cmd.project_id,
                        scope_key=scope_key,
                        created_by=items[0].created_by,
                        cluster_key=key,
                        event_type=event_type,
                        device_id=items[0].device_id,
                        model_version_id=model_version_id,
                        source_key=source_key,
                    ),
                )
            cluster.sample_count = len(items)
            cluster.average_confidence = avg
            cluster.priority_score = max(0, 100 - avg) * len(items)
            cluster.status = "READY"
            result.append(_cluster_response(cluster))
        # Rebuild also retires now-empty clusters in this scope. Keeping the
        # row preserves audit history while removing it from the actionable UI.
        existing = crud_active_learning.list_clusters(
            db, cmd.project_id, scope_key=_scope_key(cmd.project_id)
        )
        for cluster in existing:
            if cluster.cluster_key not in grouped:
                cluster.sample_count = 0
                cluster.average_confidence = None
                cluster.priority_score = 0
                cluster.status = "EMPTY"
    return result


async def list_clusters(db: Session, project_id: int | None, user: User):
    _check_scope(db, project_id, user)
    rows = crud_active_learning.list_clusters(db, project_id, scope_key=_scope_key(project_id))
    if project_id is None and not is_task_superuser(user):
        # A global cluster may contain examples from multiple producers. Use
        # the same creator/history/model visibility as the hard-example queue.
        visible = _scope_hard_examples(db, None, user).all()
        event_ids = {x.inference_event_id for x in visible}
        events = {
            event.id: event
            for event in db.query(InferenceEvent).filter(InferenceEvent.id.in_(event_ids)).all()
        } if event_ids else {}
        allowed = {
            _cluster_dimensions(x, events.get(x.inference_event_id))[3] for x in visible
        }
        rows = [x for x in rows if x.cluster_key in allowed]
    return [_cluster_response(x) for x in rows]


async def list_clusters_page(
    db: Session,
    project_id: int | None,
    user: User,
    page: int,
    size: int,
    search: str | None = None,
):
    """Return a bounded, searchable cluster page for the orchestration UI."""

    _check_scope(db, project_id, user)
    allowed: set[str] | None = None
    if project_id is None and not is_task_superuser(user):
        # Keep the same global visibility contract as the legacy endpoint, but
        # push the final cluster filtering and pagination into SQL.
        visible = _scope_hard_examples(db, None, user).all()
        event_ids = {x.inference_event_id for x in visible}
        events = {
            event.id: event
            for event in db.query(InferenceEvent)
            .filter(InferenceEvent.id.in_(event_ids))
            .all()
        } if event_ids else {}
        allowed = {
            _cluster_dimensions(x, events.get(x.inference_event_id))[3]
            for x in visible
        }
    rows, total = crud_active_learning.list_clusters_page(
        db,
        project_id,
        scope_key=_scope_key(project_id),
        page=page,
        size=size,
        search=search,
        cluster_keys=allowed,
    )
    return [_cluster_response(x) for x in rows], total


async def create_batch(db: Session, cmd: CreateActiveLearningBatchCommand, user: User):
    _check_scope(db, cmd.project_id, user, manage=True)
    scope_key = _scope_key(cmd.project_id)
    key = (cmd.batch_key or "").strip()
    if key:
        existing = crud_active_learning.find_batch(db, cmd.project_id, key, scope_key=scope_key)
        if existing:
            return _batch_response(db, existing)
    query = db.query(EdgeHardExample).filter(
        EdgeHardExample.scope_key == scope_key,
        EdgeHardExample.review_status == "ACCEPTED",
        EdgeHardExample.training_status == "READY",
    )
    if cmd.project_id is not None:
        query = query.filter(EdgeHardExample.project_id == int(cmd.project_id))
    else:
        query = _visible_global_query(db, query, user)
    if cmd.model_version_id is not None:
        query = query.filter(EdgeHardExample.model_version_id == int(cmd.model_version_id))
    if cmd.cluster_ids:
        clusters = [crud_active_learning.get_cluster(db, x) for x in cmd.cluster_ids]
        if any(cluster is None or cluster.scope_key != scope_key for cluster in clusters):
            raise LabelUException(
                ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=403,
                message="不能使用其他项目的反馈聚类",
            )
        types = {x.event_type for x in clusters if x and x.scope_key == scope_key}
        devices = {x.device_id for x in clusters if x and x.scope_key == scope_key}
        if types:
            query = query.filter(EdgeHardExample.hard_example_type.in_(types))
        if devices:
            query = query.filter(EdgeHardExample.device_id.in_(devices))
    # Capture the deterministic input set before excluding already reserved
    # examples. A retry after the first batch is created must resolve to that
    # same batch key instead of producing a second empty batch.
    identity_candidates = query.order_by(
        EdgeHardExample.created_at.asc(), EdgeHardExample.id.asc()
    ).all()
    identity_ids = [int(item.id) for item in identity_candidates]
    reserved_ids = (
        select(ActiveLearningBatchItem.hard_example_id)
        .select_from(ActiveLearningBatchItem)
        .join(
            ActiveLearningBatch,
            ActiveLearningBatch.id == ActiveLearningBatchItem.batch_id,
        )
        .where(
            ActiveLearningBatch.scope_key == scope_key,
            ActiveLearningBatch.status.in_({"READY", "ACCEPTED"}),
        )
    )
    query = query.filter(~EdgeHardExample.id.in_(reserved_ids))
    # Score the complete eligible queue before applying quota so rare classes,
    # scenes and device contexts are not crowded out by low-confidence frames.
    candidates = query.order_by(
        EdgeHardExample.created_at.asc(), EdgeHardExample.id.asc()
    ).all()
    event_ids = {row.inference_event_id for row in candidates}
    events = (
        {
            event.id: event
            for event in db.query(InferenceEvent)
            .filter(InferenceEvent.id.in_(event_ids))
            .all()
        }
        if event_ids
        else {}
    )
    if cmd.source_key:
        candidates = [
            row for row in candidates
            if _source_key(events.get(row.inference_event_id)) == cmd.source_key
        ]
    if cmd.cluster_ids:
        cluster_keys = {x.cluster_key for x in clusters if x is not None}
        candidates = [
            row for row in candidates
            if _cluster_dimensions(row, events.get(row.inference_event_id))[3] in cluster_keys
        ]
    scored = _score_candidates(candidates, events)
    candidates = sorted(
        candidates,
        key=lambda item: (
            -scored[item.id][0],
            item.created_at.isoformat() if item.created_at else "",
            item.id,
        ),
    )[: cmd.quota]
    if not key:
        candidate_ids = ",".join(str(item_id) for item_id in identity_ids)
        model_part = str(cmd.model_version_id or "mixed")
        source_part = cmd.source_key or "mixed"
        raw_key = f"{scope_key}|model:{model_part}|source:{source_part}|items:{candidate_ids}"
        key = "active-learning:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:96]
        existing = crud_active_learning.find_batch(db, cmd.project_id, key, scope_key=scope_key)
        if existing:
            return _batch_response(db, existing)
    candidate_scores = {
        str(item.id): {"score": scored[item.id][0], **scored[item.id][1]}
        for item in candidates
    }
    policy = {
        "version": "7gai.active_learning_scoring.v2",
        "formula": "weighted_sum",
        "weights": _ACTIVE_LEARNING_WEIGHTS,
        "dimensions": list(_ACTIVE_LEARNING_WEIGHTS),
        "quota": cmd.quota,
        "candidate_scores": candidate_scores,
    }
    row = ActiveLearningBatch(
        project_id=cmd.project_id,
        scope_key=scope_key,
        batch_key=key,
        model_version_id=cmd.model_version_id,
        source_key=cmd.source_key,
        quota=cmd.quota,
        selected_count=len(candidates),
        status="READY",
        scoring_policy_json=json.dumps(policy, separators=(",", ":")),
        created_by=user.id,
    )
    try:
        with begin_transaction(db):
            crud_active_learning.create_batch(db, row)
            for rank, candidate in enumerate(candidates, start=1):
                score = scored[candidate.id][0]
                crud_active_learning.add_item(
                    db,
                    ActiveLearningBatchItem(
                        batch_id=row.id,
                        hard_example_id=candidate.id,
                        score=score,
                        rank=rank,
                    ),
                )
    except IntegrityError:
        # Two operators can submit the same idempotency key concurrently. The
        # database uniqueness constraint is authoritative; converge to the
        # committed winner instead of leaking a 500 from the losing insert.
        db.rollback()
        existing = crud_active_learning.find_batch(db, cmd.project_id, key, scope_key=scope_key)
        if existing is not None:
            return _batch_response(db, existing)
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="主动学习批次幂等键已被占用，请使用原批次重试",
        )
    return _batch_response(db, row)


async def get_batch(db: Session, batch_id: int, user: User):
    row = crud_active_learning.get_batch(db, batch_id)
    if row is None:
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=404,
            message="主动学习批次不存在",
        )
    _check_scope(db, row.project_id, user)
    return _batch_response(db, row)


async def accept_batch(
    db: Session, batch_id: int, cmd: AcceptActiveLearningBatchCommand, user: User
):
    row = crud_active_learning.get_batch(db, batch_id)
    if row is None:
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=404,
            message="主动学习批次不存在",
        )
    _check_scope(db, row.project_id, user, manage=True)
    # Accept is a command with a stable batch identity. A client retry after a
    # successful commit must observe the original result, not a spurious 409.
    if row.status == "ACCEPTED":
        # Older accepted rows may have been created before the consumed-state
        # transition existed.  Repair them on a retry while preserving the
        # original workflow and dataset identities.
        items = crud_active_learning.list_items(db, row.id)
        with begin_transaction(db):
            current = crud_active_learning.get_batch(db, int(row.id))
            if current is not None and current.status == "ACCEPTED":
                _mark_batch_items_consumed(db, items)
                db.flush()
                row = current
        return _batch_response(db, row)
    if "ACCEPTED" not in _BATCH_TRANSITIONS.get(str(row.status), set()):
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=409,
            message="主动学习批次当前状态不可接受",
        )
    items = crud_active_learning.list_items(db, row.id)
    if not items:
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=409,
            message="主动学习批次没有可接受的难例",
        )
    # A batch is a review snapshot, but the underlying hard-example review can
    # change while it waits for approval.  Refuse stale items instead of
    # materializing rejected or already-consumed samples into a training set.
    stale_ids = []
    for item in items:
        hard = crud_edge_feedback.get_hard(db, item.hard_example_id)
        if (
            hard is None
            or hard.scope_key != row.scope_key
            or hard.review_status != "ACCEPTED"
            or hard.training_status != "READY"
        ):
            stale_ids.append(item.hard_example_id)
    if stale_ids:
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=409,
            message=f"主动学习批次包含审核状态已变化的难例: {','.join(map(str, stale_ids[:20]))}",
        )
    # Keep only scalar identifiers across service calls.  Both dataset
    # materialization and workflow creation commit their own transactions;
    # reusing ``row``/``version`` after those commits can leave an expired ORM
    # identity in the session and make SQLAlchemy report a zero-row UPDATE.
    project_id = int(row.project_id) if row.project_id is not None else None
    batch_pk = int(row.id)
    hard_example_ids = [int(item.hard_example_id) for item in items]
    with begin_transaction(db):
        version = _materialize_dataset_version(db, row, items, user)
        dataset_version_id = int(version.id)
    workflow = await workflow_service.create(
        db,
        CreateWorkflowCommand(
            project_id=project_id,
            workflow_type="expert_feedback",
            idempotency_key=f"active-learning-batch:{batch_pk}",
            steps=["prepare_dataset", "trigger_training"],
            payload={
                "batch_id": batch_pk,
                "dataset_version_id": dataset_version_id,
                "hard_example_ids": hard_example_ids,
                "reason": cmd.reason,
            },
        ),
        user,
        allow_evolution=True,
    )
    workflow_id = int(workflow.id)
    with begin_transaction(db):
        # Reload after the nested service transaction.  This also makes a
        # concurrent/replayed request converge on the committed ACCEPTED row.
        current = crud_active_learning.get_batch(db, batch_pk)
        if current is None:
            raise LabelUException(
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_404_NOT_FOUND,
                message="主动学习批次不存在",
            )
        if current.status == "ACCEPTED":
            _mark_batch_items_consumed(db, items)
            row = current
        else:
            if "ACCEPTED" not in _BATCH_TRANSITIONS.get(str(current.status), set()):
                raise LabelUException(
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message="主动学习批次当前状态不可接受",
                )
            current.status = "ACCEPTED"
            current.accepted_count = len(items)
            current.workflow_id = workflow_id
            _mark_batch_items_consumed(db, items)
            db.flush()
            db.refresh(current)
            row = current
    return _batch_response(db, row)
