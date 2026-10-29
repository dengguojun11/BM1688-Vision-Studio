"""Read-only audit of legacy records without a project boundary.

The migration window deliberately keeps ``project_id`` nullable on a small
set of legacy tables.  This module reports those rows and *only* reports
deterministic lineage candidates.  It never mutates a record and never uses
owner, name, current user, or a "latest" row as an ownership decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from sqlalchemy import MetaData, Table, func, inspect, or_, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class _AuditSpec:
    key: str
    table: str
    id_column: str
    description: str
    candidate: Callable[[Session, Table], int] | None = None


def _scalar(db: Session, statement) -> int:
    value = db.execute(statement).scalar()
    return int(value or 0)


def _linked_task_candidate(db: Session, table: Table, foreign_key: str) -> int:
    """Count rows whose explicit task lineage has one non-NULL project."""
    task = Table("task", table.metadata, autoload_with=db.get_bind())
    query = (
        select(func.count())
        .select_from(table.join(task, table.c[foreign_key] == task.c.id))
        .where(table.c.project_id.is_(None), task.c.project_id.is_not(None))
    )
    return _scalar(db, query)


def _dataset_version_candidate(db: Session, table: Table) -> int:
    dataset_version = Table(
        "dataset_version", table.metadata, autoload_with=db.get_bind()
    )
    query = (
        select(func.count())
        .select_from(
            table.join(
                dataset_version,
                table.c.source_dataset_version_id == dataset_version.c.id,
            )
        )
        .where(
            table.c.project_id.is_(None),
            dataset_version.c.project_id.is_not(None),
        )
    )
    return _scalar(db, query)


def _release_agent_candidate(db: Session, table: Table) -> int:
    """Count releases whose deployed agents all resolve to one project.

    The ``HAVING`` clause intentionally rejects zero-agent and mixed-project
    releases.  A mixed or absent lineage remains a manual review item.
    """
    deployment = Table(
        "edge_agent_deployment", table.metadata, autoload_with=db.get_bind()
    )
    agent = Table("edge_agent", table.metadata, autoload_with=db.get_bind())
    grouped = (
        select(table.c.id)
        .select_from(
            table.join(deployment, deployment.c.release_id == table.c.id).join(
                agent, agent.c.agent_id == deployment.c.agent_id
            )
        )
        .where(table.c.project_id.is_(None))
        .group_by(table.c.id)
        .having(func.count(func.distinct(agent.c.project_id)) == 1)
        .having(func.min(agent.c.project_id).is_not(None))
        .subquery()
    )
    return _scalar(db, select(func.count()).select_from(grouped))


def _third_party_candidate(db: Session, table: Table) -> int:
    """Count audits with exactly one matching integration project.

    A single identifier can map to multiple projects during a migration.  In
    that case this function returns no candidate, so operators must resolve
    the row explicitly instead of silently choosing the newest integration.
    """
    integration = Table(
        "strategy_task_integration", table.metadata, autoload_with=db.get_bind()
    )
    task = None
    available = set(inspect(db.get_bind()).get_table_names())
    if "task" in available:
        task = Table("task", table.metadata, autoload_with=db.get_bind())

    # This is intentionally evaluated as a small read-only map rather than a
    # "latest integration" SQL join.  It lets us include the task's explicit
    # project_id while preserving duplicate/mixed-project ambiguity.
    package_column = (
        integration.c.source_package_id
        if "source_package_id" in integration.c
        else integration.c.source_external_package_id
    )
    integration_rows = db.execute(
        select(
            integration.c.external_task_id,
            package_column.label("source_package_id"),
            integration.c.project_id,
            integration.c.labelu_task_id,
        ).where(integration.c.deleted_at.is_(None))
    ).all()
    task_projects: dict[int, int | None] = {}
    if task is not None:
        task_projects = {
            int(row.id): (int(row.project_id) if row.project_id is not None else None)
            for row in db.execute(select(task.c.id, task.c.project_id)).all()
        }
    projects_by_key: dict[tuple[str, str], set[int]] = {}
    for row in integration_rows:
        project = row.project_id
        if project is None and row.labelu_task_id is not None:
            project = task_projects.get(int(row.labelu_task_id))
        if project is None:
            continue
        project = int(project)
        for key in (
            ("task", str(row.external_task_id or "")),
            ("package", str(row.source_package_id or "")),
        ):
            if key[1]:
                projects_by_key.setdefault(key, set()).add(project)

    count = 0
    for row in db.execute(
        select(
            table.c.external_task_id,
            table.c.external_package_id,
        ).where(table.c.project_id.is_(None))
    ).all():
        projects: set[int] = set()
        if row.external_task_id:
            projects.update(
                projects_by_key.get(("task", str(row.external_task_id)), set())
            )
        if row.external_package_id:
            projects.update(
                projects_by_key.get(("package", str(row.external_package_id)), set())
            )
        if len(projects) == 1:
            count += 1
    return count


_SPECS = (
    _AuditSpec(
        "task",
        "task",
        "id",
        "任务；不得按创建者或名称推断项目",
    ),
    _AuditSpec(
        "yolo_train_job",
        "yolo_train_job",
        "id",
        "YOLO 训练作业；仅可由 source task 的显式 project_id 提供候选",
        lambda db, table: _linked_task_candidate(db, table, "task_id"),
    ),
    _AuditSpec(
        "auto_training_plan",
        "auto_training_plan",
        "id",
        "自动训练计划；仅可由 source DatasetVersion lineage 提供候选",
        _dataset_version_candidate,
    ),
    _AuditSpec(
        "edge_agent",
        "edge_agent",
        "agent_id",
        "边端 Agent；没有可接受的隐式归属来源",
    ),
    _AuditSpec(
        "edge_agent_release",
        "edge_agent_release",
        "id",
        "Agent OTA 发布；仅当所有部署目标明确属于同一项目时提供候选",
        _release_agent_candidate,
    ),
    _AuditSpec(
        "strategy_task_integration",
        "strategy_task_integration",
        "id",
        "第三方任务集成；仅可由 labelu_task_id 的显式项目提供候选",
        lambda db, table: _linked_task_candidate(db, table, "labelu_task_id"),
    ),
    _AuditSpec(
        "third_party_call_audit",
        "third_party_call_audit",
        "id",
        "第三方调用审计；仅当外部任务/包标识唯一映射到一个项目时提供候选",
        _third_party_candidate,
    ),
    _AuditSpec(
        "data_source",
        "data_source",
        "id",
        "数据源；创建者和名称均不是项目归属证据",
    ),
)


def _sample_ids(db: Session, table: Table, *, limit: int) -> list[int | str]:
    rows = db.execute(
        select(table.c.id)
        .where(table.c.project_id.is_(None))
        .order_by(table.c.id.asc())
        .limit(limit)
    ).scalars()
    return [value for value in rows]


def audit_project_ownership(
    db: Session, *, include_ids: bool = False, sample_limit: int = 100
) -> dict[str, Any]:
    """Build a deterministic, read-only historical ownership report.

    Missing tables are represented explicitly (``status=NOT_INSTALLED``),
    which makes the report safe to run against partially upgraded databases.
    ``candidate_count`` is an operator review hint only; this function never
    applies that candidate to a row.
    """
    sample_limit = max(1, min(int(sample_limit), 500))
    inspector = inspect(db.get_bind())
    available = set(inspector.get_table_names())
    items: list[dict[str, Any]] = []
    for spec in _SPECS:
        if spec.table not in available:
            items.append(
                {
                    "entity": spec.key,
                    "table": spec.table,
                    "description": spec.description,
                    "status": "NOT_INSTALLED",
                    "total_count": 0,
                    "unscoped_count": 0,
                    "candidate_count": 0,
                    "ambiguous_count": 0,
                    "review_count": 0,
                    "sample_ids": [],
                }
            )
            continue
        columns = {column["name"] for column in inspector.get_columns(spec.table)}
        if "project_id" not in columns or spec.id_column not in columns:
            items.append(
                {
                    "entity": spec.key,
                    "table": spec.table,
                    "description": spec.description,
                    "status": "SCHEMA_INCOMPLETE",
                    "total_count": 0,
                    "unscoped_count": 0,
                    "candidate_count": 0,
                    "ambiguous_count": 0,
                    "review_count": 0,
                    "sample_ids": [],
                }
            )
            continue
        table = Table(spec.table, MetaData(), autoload_with=db.get_bind())
        total = _scalar(db, select(func.count()).select_from(table))
        unscoped = _scalar(
            db,
            select(func.count()).select_from(table).where(table.c.project_id.is_(None)),
        )
        candidate = 0
        if unscoped and spec.candidate is not None:
            candidate = min(unscoped, max(0, int(spec.candidate(db, table))))
        # Ambiguity is every unscoped row that has lineage signals but not one
        # unique project.  The report intentionally keeps it conservative.
        ambiguous = (
            max(0, unscoped - candidate) if spec.candidate is not None else unscoped
        )
        item: dict[str, Any] = {
            "entity": spec.key,
            "table": spec.table,
            "description": spec.description,
            "status": "REVIEW_REQUIRED" if unscoped else "NO_UNSCOPED",
            "total_count": total,
            "unscoped_count": unscoped,
            "candidate_count": candidate,
            "ambiguous_count": ambiguous,
            "review_count": unscoped,
            "sample_ids": [],
        }
        if include_ids and unscoped:
            item["sample_ids"] = _sample_ids(db, table, limit=sample_limit)
        items.append(item)
    total_unscoped = sum(int(item["unscoped_count"]) for item in items)
    total_candidates = sum(int(item["candidate_count"]) for item in items)
    return {
        "schema_version": "7gai.project_ownership_audit.v1",
        "generated_at": datetime.now(timezone.utc),
        "read_only": True,
        "policy": {
            "allow_owner_inference": False,
            "allow_name_inference": False,
            "allow_latest_match": False,
            "candidate_requires_unique_explicit_lineage": True,
            "unscoped_access": "isolated_pending_review",
        },
        "totals": {
            "entities": len(items),
            "unscoped_count": total_unscoped,
            "deterministic_candidate_count": total_candidates,
            "review_required_count": total_unscoped,
        },
        "items": items,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _snapshot_sha256(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dry_run_row(
    *,
    entity: str,
    source_id: int | str,
    lineage_type: str,
    evidence_fields: dict[str, Any],
    target_project_id: int | None = None,
    isolation_reason: str | None = None,
) -> dict[str, Any]:
    snapshot = {
        "entity": entity,
        "source_id": source_id,
        "source_project_id": None,
        "lineage_type": lineage_type,
        "evidence_fields": evidence_fields,
    }
    candidate = target_project_id is not None and isolation_reason is None
    return {
        "entity": entity,
        "source_id": source_id,
        "status": "CANDIDATE" if candidate else "ISOLATED",
        "proposed_action": "ASSIGN_PROJECT" if candidate else "KEEP_ISOLATED",
        "target_project_id": int(target_project_id) if candidate else None,
        "lineage_type": lineage_type,
        "evidence_fields": evidence_fields,
        "isolation_reason": isolation_reason,
        "source_snapshot": snapshot,
        "source_snapshot_sha256": _snapshot_sha256(snapshot),
    }


def _table_if_available(db: Session, available: set[str], name: str) -> Table | None:
    if name not in available:
        return None
    return Table(name, MetaData(), autoload_with=db.get_bind())


def _linked_project_row(
    *,
    entity: str,
    source_id: int | str,
    foreign_key: str,
    foreign_id: Any,
    parent: Table | None,
    lineage_type: str,
    db: Session,
) -> dict[str, Any]:
    evidence = {foreign_key: foreign_id}
    if foreign_id is None:
        return _dry_run_row(
            entity=entity,
            source_id=source_id,
            lineage_type=lineage_type,
            evidence_fields=evidence,
            isolation_reason="NULL_LINEAGE",
        )
    if parent is None or "project_id" not in parent.c:
        return _dry_run_row(
            entity=entity,
            source_id=source_id,
            lineage_type=lineage_type,
            evidence_fields=evidence,
            isolation_reason="LINEAGE_TABLE_UNAVAILABLE",
        )
    parent_row = (
        db.execute(
            select(parent.c.id, parent.c.project_id).where(parent.c.id == foreign_id)
        )
        .mappings()
        .first()
    )
    if parent_row is None:
        return _dry_run_row(
            entity=entity,
            source_id=source_id,
            lineage_type=lineage_type,
            evidence_fields=evidence,
            isolation_reason="LINEAGE_NOT_FOUND",
        )
    evidence["parent_project_id"] = parent_row["project_id"]
    if parent_row["project_id"] is None:
        return _dry_run_row(
            entity=entity,
            source_id=source_id,
            lineage_type=lineage_type,
            evidence_fields=evidence,
            isolation_reason="NULL_PARENT_PROJECT",
        )
    return _dry_run_row(
        entity=entity,
        source_id=source_id,
        lineage_type=lineage_type,
        evidence_fields=evidence,
        target_project_id=int(parent_row["project_id"]),
    )


def _release_dry_run_row(
    db: Session,
    source_id: int,
    deployment: Table | None,
    agent: Table | None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"release_id": source_id, "agents": []}
    if deployment is None or agent is None or "project_id" not in agent.c:
        return _dry_run_row(
            entity="edge_agent_release",
            source_id=source_id,
            lineage_type="release_deployment_agents",
            evidence_fields=evidence,
            isolation_reason="LINEAGE_TABLE_UNAVAILABLE",
        )
    deployments = (
        db.execute(
            select(deployment.c.agent_id)
            .where(deployment.c.release_id == source_id)
            .order_by(deployment.c.agent_id.asc())
        )
        .scalars()
        .all()
    )
    if not deployments:
        return _dry_run_row(
            entity="edge_agent_release",
            source_id=source_id,
            lineage_type="release_deployment_agents",
            evidence_fields=evidence,
            isolation_reason="LINEAGE_NOT_FOUND",
        )
    if len(deployments) != len(set(deployments)):
        evidence["agent_ids"] = list(deployments)
        return _dry_run_row(
            entity="edge_agent_release",
            source_id=source_id,
            lineage_type="release_deployment_agents",
            evidence_fields=evidence,
            isolation_reason="DUPLICATE_LINEAGE",
        )
    project_ids: set[int] = set()
    for agent_id in deployments:
        agent_row = (
            db.execute(
                select(agent.c.agent_id, agent.c.project_id).where(
                    agent.c.agent_id == agent_id
                )
            )
            .mappings()
            .first()
        )
        evidence["agents"].append(
            {
                "agent_id": agent_id,
                "project_id": agent_row["project_id"] if agent_row else None,
            }
        )
        if agent_row is None:
            reason = "LINEAGE_NOT_FOUND"
        elif agent_row["project_id"] is None:
            reason = "NULL_PARENT_PROJECT"
        else:
            project_ids.add(int(agent_row["project_id"]))
            continue
        return _dry_run_row(
            entity="edge_agent_release",
            source_id=source_id,
            lineage_type="release_deployment_agents",
            evidence_fields=evidence,
            isolation_reason=reason,
        )
    if len(project_ids) != 1:
        return _dry_run_row(
            entity="edge_agent_release",
            source_id=source_id,
            lineage_type="release_deployment_agents",
            evidence_fields=evidence,
            isolation_reason="MIXED_PROJECT_LINEAGE",
        )
    return _dry_run_row(
        entity="edge_agent_release",
        source_id=source_id,
        lineage_type="release_deployment_agents",
        evidence_fields=evidence,
        target_project_id=next(iter(project_ids)),
    )


def _third_party_dry_run_row(
    db: Session,
    row: Any,
    integration: Table | None,
    task: Table | None,
) -> dict[str, Any]:
    source_id = row["id"]
    evidence: dict[str, Any] = {
        "external_task_id": row.get("external_task_id"),
        "external_package_id": row.get("external_package_id"),
        "matching_integrations": [],
    }
    if integration is None:
        return _dry_run_row(
            entity="third_party_call_audit",
            source_id=source_id,
            lineage_type="external_identifiers_to_integration",
            evidence_fields=evidence,
            isolation_reason="LINEAGE_TABLE_UNAVAILABLE",
        )
    package_column = (
        integration.c.source_package_id
        if "source_package_id" in integration.c
        else integration.c.source_external_package_id
    )
    predicates = []
    if row.get("external_task_id"):
        predicates.append(integration.c.external_task_id == row["external_task_id"])
    if row.get("external_package_id"):
        predicates.append(package_column == row["external_package_id"])
    if not predicates:
        return _dry_run_row(
            entity="third_party_call_audit",
            source_id=source_id,
            lineage_type="external_identifiers_to_integration",
            evidence_fields=evidence,
            isolation_reason="NULL_LINEAGE",
        )
    columns = [
        integration.c.id,
        integration.c.project_id,
        integration.c.labelu_task_id,
        integration.c.external_task_id,
        package_column.label("source_package_id"),
    ]
    query = select(*columns).where(or_(*predicates))
    if "deleted_at" in integration.c:
        query = query.where(integration.c.deleted_at.is_(None))
    matches = db.execute(query.order_by(integration.c.id.asc())).mappings().all()
    for match in matches:
        evidence["matching_integrations"].append(dict(match))
    if not matches:
        return _dry_run_row(
            entity="third_party_call_audit",
            source_id=source_id,
            lineage_type="external_identifiers_to_integration",
            evidence_fields=evidence,
            isolation_reason="LINEAGE_NOT_FOUND",
        )
    if len(matches) != 1:
        return _dry_run_row(
            entity="third_party_call_audit",
            source_id=source_id,
            lineage_type="external_identifiers_to_integration",
            evidence_fields=evidence,
            isolation_reason="DUPLICATE_LINEAGE",
        )
    match = matches[0]
    projects: set[int] = set()
    if match["project_id"] is not None:
        projects.add(int(match["project_id"]))
    if match["labelu_task_id"] is not None and task is not None:
        task_project = db.execute(
            select(task.c.project_id).where(task.c.id == match["labelu_task_id"])
        ).scalar()
        evidence["task_project_id"] = task_project
        if task_project is not None:
            projects.add(int(task_project))
    if len(projects) > 1:
        reason = "MIXED_PROJECT_LINEAGE"
    elif not projects:
        reason = "NULL_PARENT_PROJECT"
    else:
        reason = None
    return _dry_run_row(
        entity="third_party_call_audit",
        source_id=source_id,
        lineage_type="external_identifiers_to_integration",
        evidence_fields=evidence,
        target_project_id=next(iter(projects)) if len(projects) == 1 else None,
        isolation_reason=reason,
    )


def project_ownership_backfill_dry_run(
    db: Session, *, row_limit: int = 5000
) -> dict[str, Any]:
    """Return deterministic assignment proposals without mutating the DB."""

    row_limit = max(1, min(int(row_limit), 10000))
    inspector = inspect(db.get_bind())
    available = set(inspector.get_table_names())
    task = _table_if_available(db, available, "task")
    dataset_version = _table_if_available(db, available, "dataset_version")
    deployment = _table_if_available(db, available, "edge_agent_deployment")
    agent = _table_if_available(db, available, "edge_agent")
    integration = _table_if_available(db, available, "strategy_task_integration")
    entity_reports: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    for spec in _SPECS:
        table = _table_if_available(db, available, spec.table)
        if table is None:
            entity_reports.append(
                {"entity": spec.key, "status": "NOT_INSTALLED", "rows": []}
            )
            continue
        if "project_id" not in table.c or spec.id_column not in table.c:
            entity_reports.append(
                {"entity": spec.key, "status": "SCHEMA_INCOMPLETE", "rows": []}
            )
            continue
        source_rows = (
            db.execute(
                select(table)
                .where(table.c.project_id.is_(None))
                .order_by(table.c[spec.id_column].asc())
                .limit(row_limit)
            )
            .mappings()
            .all()
        )
        resolved: list[dict[str, Any]] = []
        for source in source_rows:
            source_id = source[spec.id_column]
            if spec.key == "yolo_train_job":
                item = _linked_project_row(
                    entity=spec.key,
                    source_id=source_id,
                    foreign_key="task_id",
                    foreign_id=source.get("task_id"),
                    parent=task,
                    lineage_type="task_project",
                    db=db,
                )
            elif spec.key == "auto_training_plan":
                item = _linked_project_row(
                    entity=spec.key,
                    source_id=source_id,
                    foreign_key="source_dataset_version_id",
                    foreign_id=source.get("source_dataset_version_id"),
                    parent=dataset_version,
                    lineage_type="dataset_version_project",
                    db=db,
                )
            elif spec.key == "strategy_task_integration":
                item = _linked_project_row(
                    entity=spec.key,
                    source_id=source_id,
                    foreign_key="labelu_task_id",
                    foreign_id=source.get("labelu_task_id"),
                    parent=task,
                    lineage_type="task_project",
                    db=db,
                )
            elif spec.key == "edge_agent_release":
                item = _release_dry_run_row(db, int(source_id), deployment, agent)
            elif spec.key == "third_party_call_audit":
                item = _third_party_dry_run_row(db, source, integration, task)
            else:
                item = _dry_run_row(
                    entity=spec.key,
                    source_id=source_id,
                    lineage_type="none",
                    evidence_fields={spec.id_column: source_id},
                    isolation_reason="NO_EXPLICIT_LINEAGE",
                )
            resolved.append(item)
            all_rows.append(item)
        entity_reports.append(
            {
                "entity": spec.key,
                "status": "REVIEW_REQUIRED" if resolved else "NO_UNSCOPED",
                "candidate_count": sum(
                    row["status"] == "CANDIDATE" for row in resolved
                ),
                "isolated_count": sum(row["status"] == "ISOLATED" for row in resolved),
                "rows": resolved,
            }
        )

    return {
        "schema_version": "7gai.project_ownership_backfill_dry_run.v1",
        "generated_at": datetime.now(timezone.utc),
        "read_only": True,
        "write_count": 0,
        "policy": {
            "allow_owner_inference": False,
            "allow_name_inference": False,
            "allow_latest_match": False,
            "duplicate_lineage": "isolated",
            "mixed_project_lineage": "isolated",
            "missing_or_null_lineage": "isolated",
        },
        "totals": {
            "rows": len(all_rows),
            "candidate_count": sum(row["status"] == "CANDIDATE" for row in all_rows),
            "isolated_count": sum(row["status"] == "ISOLATED" for row in all_rows),
        },
        "entities": entity_reports,
    }
