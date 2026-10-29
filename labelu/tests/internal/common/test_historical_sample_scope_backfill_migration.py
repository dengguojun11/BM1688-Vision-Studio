from __future__ import annotations

import importlib
import json

import sqlalchemy as sa


def _create_schema(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "task",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("training_ready", sa.Boolean()),
        sa.Column("annotation_audit_json", sa.Text()),
        sa.Column("current_annotation_contract_id", sa.Integer()),
        sa.Column("deleted_at", sa.DateTime()),
    )
    sa.Table(
        "task_sample",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32)),
        sa.Column("data", sa.Text()),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("deleted_at", sa.DateTime()),
    )
    sa.Table(
        "task_annotation_contract",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("label_ids_json", sa.Text(), nullable=False),
        sa.Column("labels_snapshot_json", sa.Text(), nullable=False),
    )
    sa.Table(
        "sample_annotation_scope",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sample_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("completion_status", sa.String(32), nullable=False),
        sa.Column("explicit_negative", sa.Boolean(), nullable=False),
        sa.Column("negative_label_ids_json", sa.Text(), nullable=False),
        sa.Column("annotation_sha256", sa.String(64)),
        sa.Column("reviewed_by", sa.Integer()),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    metadata.create_all(engine)


def _annotation(label: str | None, *, explicit_negative: bool = False) -> str:
    rects = (
        []
        if label is None
        else [{"label": label, "x": 1, "y": 2, "width": 3, "height": 4}]
    )
    outer = {"result": json.dumps({"rectTool": {"result": rects}})}
    if explicit_negative:
        outer["training_sample_type"] = "negative"
    return json.dumps(outer)


def test_upgrade_backfills_only_verified_positive_or_explicit_negative_samples(
    monkeypatch,
):
    migration = importlib.import_module(
        "labelu.alembic_labelu.versions.g5h6i7j8k9l0_backfill_verified_historical_sample_scopes"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    _create_schema(engine)

    with engine.begin() as connection:
        metadata = sa.MetaData()
        task = sa.Table("task", metadata, autoload_with=connection)
        sample = sa.Table("task_sample", metadata, autoload_with=connection)
        contract = sa.Table(
            "task_annotation_contract", metadata, autoload_with=connection
        )
        scope = sa.Table("sample_annotation_scope", metadata, autoload_with=connection)
        audit = json.dumps(
            {
                "training_ready": True,
                "total_samples": 3,
                "passed_samples": 3,
                "failed_samples_count": 0,
            }
        )
        connection.execute(
            task.insert(),
            {
                "id": 43,
                "training_ready": True,
                "annotation_audit_json": audit,
                "current_annotation_contract_id": 17,
            },
        )
        connection.execute(
            contract.insert(),
            {
                "id": 17,
                "task_id": 43,
                "revision": 1,
                "status": "review_required",
                "label_ids_json": "[11, 12]",
                "labels_snapshot_json": json.dumps(
                    [
                        {"label_id": 11, "label_value": "garbage_dump"},
                        {"label_id": 12, "label_value": "garbage_pack"},
                    ]
                ),
            },
        )
        connection.execute(
            sample.insert(),
            [
                {
                    "id": 1,
                    "task_id": 43,
                    "state": "DONE",
                    "data": _annotation("garbage_dump"),
                },
                {
                    "id": 2,
                    "task_id": 43,
                    "state": "DONE",
                    "data": _annotation(None, explicit_negative=True),
                },
                {"id": 3, "task_id": 43, "state": "DONE", "data": _annotation(None)},
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        migration.upgrade()
        migration.upgrade()

        rows = (
            connection.execute(sa.select(scope).order_by(scope.c.sample_id))
            .mappings()
            .all()
        )
        contract_status = connection.execute(
            sa.select(contract.c.status).where(contract.c.id == 17)
        ).scalar()

    assert [int(row["sample_id"]) for row in rows] == [1, 2]
    assert rows[0]["completion_status"] == "complete"
    assert rows[0]["explicit_negative"] is False
    assert len(rows[0]["annotation_sha256"]) == 64
    assert rows[1]["explicit_negative"] is True
    assert json.loads(rows[1]["negative_label_ids_json"]) == [11, 12]
    assert contract_status == "review_required"


def test_upgrade_activates_contract_when_every_done_sample_is_verified(monkeypatch):
    backfill_migration = importlib.import_module(
        "labelu.alembic_labelu.versions.g5h6i7j8k9l0_backfill_verified_historical_sample_scopes"
    )
    activation_migration = importlib.import_module(
        "labelu.alembic_labelu.versions.h6i7j8k9l0m1_activate_verified_historical_contracts"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    _create_schema(engine)

    with engine.begin() as connection:
        metadata = sa.MetaData()
        task = sa.Table("task", metadata, autoload_with=connection)
        sample = sa.Table("task_sample", metadata, autoload_with=connection)
        contract = sa.Table(
            "task_annotation_contract", metadata, autoload_with=connection
        )
        scope = sa.Table("sample_annotation_scope", metadata, autoload_with=connection)
        connection.execute(
            task.insert(),
            {
                "id": 43,
                "training_ready": True,
                "annotation_audit_json": json.dumps(
                    {
                        "training_ready": True,
                        "total_samples": 2,
                        "passed_samples": 2,
                        "failed_samples_count": 0,
                    }
                ),
                "current_annotation_contract_id": 17,
            },
        )
        connection.execute(
            contract.insert(),
            {
                "id": 17,
                "task_id": 43,
                "revision": 1,
                "status": "review_required",
                "label_ids_json": "[11]",
                "labels_snapshot_json": json.dumps(
                    [{"label_id": 11, "label_value": "garbage_dump"}]
                ),
            },
        )
        connection.execute(
            sample.insert(),
            [
                {
                    "id": 1,
                    "task_id": 43,
                    "state": "DONE",
                    "data": _annotation("garbage_dump"),
                },
                {
                    "id": 2,
                    "task_id": 43,
                    "state": "DONE",
                    "data": _annotation("garbage_dump"),
                },
            ],
        )
        monkeypatch.setattr(backfill_migration.op, "get_bind", lambda: connection)
        monkeypatch.setattr(activation_migration.op, "get_bind", lambda: connection)

        backfill_migration.upgrade()

        assert (
            connection.execute(sa.select(sa.func.count()).select_from(scope)).scalar()
            == 2
        )
        assert (
            connection.execute(
                sa.select(contract.c.status).where(contract.c.id == 17)
            ).scalar()
            == "review_required"
        )

        activation_migration.upgrade()
        activation_migration.upgrade()

        assert (
            connection.execute(
                sa.select(contract.c.status).where(contract.c.id == 17)
            ).scalar()
            == "active"
        )


def test_upgrade_skips_tasks_without_a_complete_matching_audit(monkeypatch):
    migration = importlib.import_module(
        "labelu.alembic_labelu.versions.g5h6i7j8k9l0_backfill_verified_historical_sample_scopes"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    _create_schema(engine)

    with engine.begin() as connection:
        metadata = sa.MetaData()
        task = sa.Table("task", metadata, autoload_with=connection)
        sample = sa.Table("task_sample", metadata, autoload_with=connection)
        contract = sa.Table(
            "task_annotation_contract", metadata, autoload_with=connection
        )
        scope = sa.Table("sample_annotation_scope", metadata, autoload_with=connection)
        connection.execute(
            task.insert(),
            {
                "id": 44,
                "training_ready": True,
                "annotation_audit_json": json.dumps(
                    {
                        "training_ready": True,
                        "total_samples": 2,
                        "passed_samples": 1,
                        "failed_samples_count": 1,
                    }
                ),
                "current_annotation_contract_id": 18,
            },
        )
        connection.execute(
            contract.insert(),
            {
                "id": 18,
                "task_id": 44,
                "revision": 1,
                "status": "review_required",
                "label_ids_json": "[11]",
                "labels_snapshot_json": json.dumps(
                    [{"label_id": 11, "label_value": "garbage_dump"}]
                ),
            },
        )
        connection.execute(
            sample.insert(),
            [
                {
                    "id": 4,
                    "task_id": 44,
                    "state": "DONE",
                    "data": _annotation("garbage_dump"),
                },
                {
                    "id": 5,
                    "task_id": 44,
                    "state": "DONE",
                    "data": _annotation("garbage_dump"),
                },
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        migration.upgrade()
        rows = connection.execute(sa.select(scope)).all()

    assert rows == []
