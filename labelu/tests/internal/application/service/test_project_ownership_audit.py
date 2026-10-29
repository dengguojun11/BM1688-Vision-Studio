from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from labelu.internal.application.service.project_ownership_audit import (
    audit_project_ownership,
    project_ownership_backfill_dry_run,
)


def _legacy_schema(metadata: sa.MetaData) -> None:
    sa.Table("project", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "task",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
        sa.Column("created_by", sa.Integer),
    )
    sa.Table(
        "yolo_train_job",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer),
        sa.Column("project_id", sa.Integer),
    )
    sa.Table(
        "strategy_task_integration",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
        sa.Column("labelu_task_id", sa.Integer),
        sa.Column("external_task_id", sa.String(128)),
        sa.Column("source_package_id", sa.String(128)),
        sa.Column("deleted_at", sa.DateTime),
    )
    sa.Table(
        "third_party_call_audit",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
        sa.Column("external_task_id", sa.String(128)),
        sa.Column("external_package_id", sa.String(128)),
    )
    sa.Table(
        "data_source",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
    )


def test_audit_reports_null_rows_without_writing_or_owner_inference():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    _legacy_schema(metadata)
    metadata.create_all(engine)
    with Session(engine) as db:
        db.execute(sa.text("INSERT INTO project (id) VALUES (1), (2)"))
        db.execute(
            sa.text(
                "INSERT INTO task (id, project_id, created_by) VALUES "
                "(10, 1, 7), (11, NULL, 7), (12, NULL, 7)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO yolo_train_job (id, task_id, project_id) VALUES "
                "(20, 10, NULL), (21, 11, NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO strategy_task_integration "
                "(id, project_id, labelu_task_id, external_task_id, source_package_id, deleted_at) VALUES "
                "(30, NULL, 10, 'task-10', 'pkg-10', NULL), "
                "(31, NULL, NULL, 'orphan', 'pkg-orphan', NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO third_party_call_audit "
                "(id, project_id, external_task_id, external_package_id) VALUES "
                "(40, NULL, 'task-10', NULL), "
                "(41, NULL, 'unknown', 'unknown')"
            )
        )
        db.execute(
            sa.text("INSERT INTO data_source (id, project_id) VALUES (50, NULL)")
        )
        db.commit()

        before = {
            name: db.execute(
                sa.text(f"SELECT COUNT(*) FROM {name} WHERE project_id IS NULL")
            ).scalar()
            for name in (
                "task",
                "yolo_train_job",
                "strategy_task_integration",
                "third_party_call_audit",
                "data_source",
            )
        }
        report = audit_project_ownership(db, include_ids=True, sample_limit=10)
        after = {
            name: db.execute(
                sa.text(f"SELECT COUNT(*) FROM {name} WHERE project_id IS NULL")
            ).scalar()
            for name in before
        }

    assert report["read_only"] is True
    assert report["policy"]["allow_owner_inference"] is False
    assert report["policy"]["allow_latest_match"] is False
    assert before == after
    items = {item["entity"]: item for item in report["items"]}
    assert items["task"]["unscoped_count"] == 2
    assert items["task"]["candidate_count"] == 0
    assert items["task"]["sample_ids"] == [11, 12]
    assert items["yolo_train_job"]["candidate_count"] == 1
    assert items["yolo_train_job"]["review_count"] == 2
    assert items["strategy_task_integration"]["candidate_count"] == 1
    assert items["third_party_call_audit"]["candidate_count"] == 1
    assert items["third_party_call_audit"]["ambiguous_count"] == 1


def test_audit_keeps_mixed_project_release_unresolved():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("project", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "edge_agent",
        metadata,
        sa.Column("agent_id", sa.String(128), primary_key=True),
        sa.Column("project_id", sa.Integer),
    )
    sa.Table(
        "edge_agent_release",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
    )
    sa.Table(
        "edge_agent_deployment",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("release_id", sa.Integer),
        sa.Column("agent_id", sa.String(128)),
    )
    metadata.create_all(engine)
    with Session(engine) as db:
        db.execute(
            sa.text(
                "INSERT INTO edge_agent (agent_id, project_id) VALUES ('a', 1), ('b', 2)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO edge_agent_release (id, project_id) VALUES (1, NULL), (2, NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO edge_agent_deployment (release_id, agent_id) VALUES "
                "(1, 'a'), (1, 'b'), (2, 'a')"
            )
        )
        db.commit()
        report = audit_project_ownership(db)
    item = next(
        item for item in report["items"] if item["entity"] == "edge_agent_release"
    )
    assert item["unscoped_count"] == 2
    assert item["candidate_count"] == 1
    assert item["ambiguous_count"] == 1


def test_backfill_dry_run_is_read_only_and_deterministic():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    _legacy_schema(metadata)
    sa.Table(
        "dataset_version",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
    )
    sa.Table(
        "auto_training_plan",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
        sa.Column("source_dataset_version_id", sa.Integer),
    )
    metadata.create_all(engine)
    with Session(engine) as db:
        db.execute(sa.text("INSERT INTO project (id) VALUES (1), (2)"))
        db.execute(
            sa.text(
                "INSERT INTO task (id, project_id, created_by) VALUES "
                "(10, 1, 7), (11, NULL, 7)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO yolo_train_job (id, task_id, project_id) VALUES "
                "(20, 10, NULL), (21, 11, NULL), (22, 999, NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO dataset_version (id, project_id) VALUES (60, 2), (61, NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO auto_training_plan (id, project_id, source_dataset_version_id) VALUES "
                "(70, NULL, 60), (71, NULL, 61), (72, NULL, NULL)"
            )
        )
        db.commit()
        before = {
            table: db.execute(sa.text(f"SELECT * FROM {table} ORDER BY id"))
            .mappings()
            .all()
            for table in (
                "task",
                "yolo_train_job",
                "dataset_version",
                "auto_training_plan",
            )
        }

        first = project_ownership_backfill_dry_run(db)
        second = project_ownership_backfill_dry_run(db)
        after = {
            table: db.execute(sa.text(f"SELECT * FROM {table} ORDER BY id"))
            .mappings()
            .all()
            for table in before
        }

    assert before == after
    assert first["read_only"] is True
    assert first["write_count"] == 0
    assert first["entities"] == second["entities"]
    yolo = next(
        item for item in first["entities"] if item["entity"] == "yolo_train_job"
    )
    rows = {row["source_id"]: row for row in yolo["rows"]}
    assert rows[20]["target_project_id"] == 1
    assert rows[20]["status"] == "CANDIDATE"
    assert rows[21]["isolation_reason"] == "NULL_PARENT_PROJECT"
    assert rows[22]["isolation_reason"] == "LINEAGE_NOT_FOUND"
    assert len(rows[20]["source_snapshot_sha256"]) == 64
    plans = next(
        item for item in first["entities"] if item["entity"] == "auto_training_plan"
    )
    plan_rows = {row["source_id"]: row for row in plans["rows"]}
    assert plan_rows[70]["target_project_id"] == 2
    assert plan_rows[71]["isolation_reason"] == "NULL_PARENT_PROJECT"
    assert plan_rows[72]["isolation_reason"] == "NULL_LINEAGE"


def test_backfill_dry_run_rejects_duplicate_and_mixed_lineage():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    _legacy_schema(metadata)
    sa.Table(
        "edge_agent",
        metadata,
        sa.Column("agent_id", sa.String(128), primary_key=True),
        sa.Column("project_id", sa.Integer),
    )
    sa.Table(
        "edge_agent_release",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer),
    )
    sa.Table(
        "edge_agent_deployment",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("release_id", sa.Integer),
        sa.Column("agent_id", sa.String(128)),
    )
    metadata.create_all(engine)
    with Session(engine) as db:
        db.execute(sa.text("INSERT INTO project (id) VALUES (1), (2)"))
        db.execute(
            sa.text(
                "INSERT INTO task (id, project_id, created_by) VALUES (10, 1, 7), (11, 2, 7)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO strategy_task_integration "
                "(id, project_id, labelu_task_id, external_task_id, source_package_id, deleted_at) VALUES "
                "(30, NULL, 10, 'duplicate', 'pkg-a', NULL), "
                "(31, NULL, 10, 'duplicate', 'pkg-b', NULL), "
                "(32, 1, 11, 'mixed', 'pkg-mixed', NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO third_party_call_audit "
                "(id, project_id, external_task_id, external_package_id) VALUES "
                "(40, NULL, 'duplicate', NULL), (41, NULL, 'mixed', NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO edge_agent (agent_id, project_id) VALUES "
                "('a', 1), ('b', 2), ('c', 1)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO edge_agent_release (id, project_id) VALUES "
                "(50, NULL), (51, NULL)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO edge_agent_deployment (id, release_id, agent_id) VALUES "
                "(1, 50, 'a'), (2, 50, 'b'), "
                "(3, 51, 'c'), (4, 51, 'c')"
            )
        )
        db.commit()
        report = project_ownership_backfill_dry_run(db)

    third_party = next(
        item
        for item in report["entities"]
        if item["entity"] == "third_party_call_audit"
    )
    audit_rows = {row["source_id"]: row for row in third_party["rows"]}
    assert audit_rows[40]["isolation_reason"] == "DUPLICATE_LINEAGE"
    assert audit_rows[41]["isolation_reason"] == "MIXED_PROJECT_LINEAGE"
    releases = next(
        item for item in report["entities"] if item["entity"] == "edge_agent_release"
    )
    release_rows = {row["source_id"]: row for row in releases["rows"]}
    assert release_rows[50]["isolation_reason"] == "MIXED_PROJECT_LINEAGE"
    assert release_rows[51]["isolation_reason"] == "DUPLICATE_LINEAGE"
