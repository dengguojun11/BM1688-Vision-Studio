import os
import time

# 必须在导入 labelu 之前设置：避免项目根目录 .env 中的 MySQL 在跑 pytest 时被执行迁移或写入。
# Parallel engineer/CI processes must never clean the same SQLite file.  Keep
# an explicit override for migration/debug jobs, while defaulting to a
# process-specific database for ordinary pytest runs.
TEST_DB_NAME = os.environ.get(
    "LABELU_TEST_DB_NAME",
    f"test-category-scoped-pools-{os.getpid()}-{time.time_ns()}.db",
)
TEST_DB_PATH = os.path.abspath(os.path.join(".pytest_labelu_data", TEST_DB_NAME))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.replace(os.sep, '/')}"
# The development override has precedence over DATABASE_URL in config.py.
# Keep both on the same isolated database before importing the application.
os.environ["LABELU_DEV_DATABASE_URL"] = os.environ["DATABASE_URL"]
os.environ["BASE_DATA_DIR"] = os.path.abspath(".pytest_labelu_data")
# Keep uploaded media isolated from parallel pytest/agent processes.  The
# database is process-scoped above, so media must follow the same boundary or
# two workers can delete/reuse the same task attachment path during a run.
os.environ["MEDIA_ROOT"] = os.path.abspath(
    os.path.join(".pytest_labelu_data", f"media-{os.getpid()}")
)
os.environ["LOG_FILE_ENABLED"] = "false"
os.environ["PASSWORD_SECRET_KEY"] = "pytest-secret-key"
os.environ["LABELU_SKIP_MIGRATIONS"] = "true"
os.environ["STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS"] = "true"
# Existing router tests exercise the legacy internal protocol without HTTP
# headers.  Keep that call shape in the test environment while production and
# normal local processes use the fail-closed default.
os.environ["STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED"] = "false"
# Tests invoke watchdog cycles explicitly.  Keep API lifespan free of daemon
# schedulers so module-scoped TestClient instances cannot race cleanup or
# mutate the shared SQLite fixture while another test is running.
os.environ["LABELU_AUTO_TRAINING_API_WATCHDOG_ENABLED"] = "false"
os.environ["LABELU_DISABLE_BACKGROUND_WATCHDOGS"] = "true"

import pytest
from typing import Dict, Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from labelu.main import app
from labelu.internal.common.config import settings
from labelu.internal.common.db import Base

import labelu.internal.domain.models  # noqa: F401 — 注册 ORM，pytest create_all 含新表
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.db import get_db
from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.user import User
from labelu.internal.adapter.persistence import crud_user

TEST_USERNAME = "test@example.com"
TEST_USER_PASSWORD = "test@123"
ADMIN_USERNAME = "admin@123.com"
ADMIN_USER_PASSWORD = "Admin@123"


SQLALCHEMY_DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)
TestingSessionLocal = sessionmaker(autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)
with engine.begin() as conn:
    inspector = inspect(conn)

    def _refresh_columns(table_name: str) -> set[str]:
        return {item["name"] for item in inspect(conn).get_columns(table_name)}

    if "annotation_label" in inspector.get_table_names():
        columns = _refresh_columns("annotation_label")
        if "attributes_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE annotation_label ADD COLUMN "
                    "attributes_json TEXT DEFAULT '[]' NOT NULL"
                )
            )

    if "training_evaluation_dataset" in inspector.get_table_names():
        columns = _refresh_columns("training_evaluation_dataset")
        if "plan_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE training_evaluation_dataset "
                    "ADD COLUMN plan_id INTEGER"
                )
            )

    if "media_upload_session" in inspector.get_table_names():
        columns = _refresh_columns("media_upload_session")
        media_scan_columns = {
            "scan_status": "VARCHAR(24) DEFAULT 'PENDING' NOT NULL",
            "scan_provider": "VARCHAR(64)",
            "scan_signature": "VARCHAR(256)",
            "scan_error": "TEXT",
            "scanned_at": "DATETIME",
            "quarantined_at": "DATETIME",
            "quarantine_path": "VARCHAR(512)",
            "quarantine_until": "DATETIME",
        }
        for column_name, column_type in media_scan_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE media_upload_session ADD COLUMN {column_name} {column_type}"
                    )
                )

    # 7GAI ingestion compatibility columns are nullable for legacy fixtures;
    # mirror the additive Alembic migration when a persistent test database
    # predates the new ORM metadata.
    if "data_source" in inspector.get_table_names():
        columns = _refresh_columns("data_source")
        if "project_id" not in columns:
            conn.execute(text("ALTER TABLE data_source ADD COLUMN project_id INTEGER"))
        if "status" not in columns:
            conn.execute(
                text("ALTER TABLE data_source ADD COLUMN status VARCHAR(24) DEFAULT 'ACTIVE'")
            )

    if "dataset" in inspector.get_table_names():
        columns = _refresh_columns("dataset")
        # Dataset keeps a nullable project_id compatibility column in the ORM
        # for historical rows.  Some persistent pytest databases were rebuilt
        # by the project-free migration rehearsal and no longer have it;
        # restore the nullable test-only column so ORM inserts remain valid
        # without reopening project-scoped production writes.
        if "project_id" not in columns:
            conn.execute(text("ALTER TABLE dataset ADD COLUMN project_id INTEGER"))
            columns = _refresh_columns("dataset")
        if "dataset_category_id" not in columns:
            conn.execute(text("ALTER TABLE dataset ADD COLUMN dataset_category_id INTEGER"))
            columns = _refresh_columns("dataset")
        if "dataset_label_contract_id" not in columns:
            conn.execute(
                text("ALTER TABLE dataset ADD COLUMN dataset_label_contract_id INTEGER")
            )

    if "dataset_version" in inspector.get_table_names():
        columns = _refresh_columns("dataset_version")
        dataset_version_columns = {
            "project_id": "INTEGER",
            "dataset_category_id": "INTEGER",
            "review_status": "VARCHAR(24) DEFAULT 'PENDING'",
            "review_comment": "TEXT",
            "reviewed_by": "INTEGER",
            "reviewed_at": "DATETIME",
            "dataset_category_snapshot_json": "TEXT",
            "dataset_label_contract_id": "INTEGER",
            "dataset_contract_revision": "INTEGER",
            "labels_snapshot_json": "TEXT",
        }
        for column_name, column_type in dataset_version_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE dataset_version ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("dataset_version")

    # Task annotation contracts gained a nullable dataset label contract
    # reference after some persistent pytest databases had already been
    # created.  Keep the test fixture additive and aligned with the ORM so
    # import-task tests can query the formal label contract on old test DBs.
    if "task_annotation_contract" in inspector.get_table_names():
        columns = _refresh_columns("task_annotation_contract")
        if "dataset_label_contract_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE task_annotation_contract "
                    "ADD COLUMN dataset_label_contract_id INTEGER"
                )
            )
            columns = _refresh_columns("task_annotation_contract")
        if "label_scope_mode" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE task_annotation_contract "
                    "ADD COLUMN label_scope_mode VARCHAR(16) "
                    "DEFAULT 'FULL' NOT NULL"
                )
            )

    if "task_attachment" in inspector.get_table_names():
        columns = _refresh_columns("task_attachment")
        if "media_asset_id" not in columns:
            conn.execute(
                text("ALTER TABLE task_attachment ADD COLUMN media_asset_id INTEGER")
            )

    if "provider_job" in inspector.get_table_names():
        columns = _refresh_columns("provider_job")
        for column_name, column_type in {
            "attempt_count": "INTEGER DEFAULT 0 NOT NULL",
            "lease_owner": "VARCHAR(128)",
            "lease_expires_at": "DATETIME",
        }.items():
            if column_name not in columns:
                conn.execute(text(f"ALTER TABLE provider_job ADD COLUMN {column_name} {column_type}"))

    if "prediction_set" in inspector.get_table_names():
        columns = _refresh_columns("prediction_set")
        for column_name, column_type in {
            "payload_json": "TEXT",
            "reviewed_by": "INTEGER",
            "reviewed_at": "DATETIME",
            "review_reason": "TEXT",
        }.items():
            if column_name not in columns:
                conn.execute(text(f"ALTER TABLE prediction_set ADD COLUMN {column_name} {column_type}"))

    if "workflow_instance" in inspector.get_table_names():
        columns = _refresh_columns("workflow_instance")
        workflow_columns = {
            "scope_key": "VARCHAR(160) DEFAULT 'global'",
            "owner_id": "INTEGER",
            "approver_id": "INTEGER",
            "deadline_at": "DATETIME",
            "input_snapshot_json": "TEXT",
            "callback_url": "VARCHAR(512)",
            "callback_signature": "VARCHAR(128)",
        }
        for column_name, column_type in workflow_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE workflow_instance ADD COLUMN {column_name} {column_type}"
                    )
                )
        conn.execute(
            text("UPDATE workflow_instance SET scope_key = 'global' WHERE scope_key IS NULL")
        )

    # Project-free evolution compatibility for persistent local test DBs.
    # Production uses the formal z9c0d1e2f3a4 migration; pytest intentionally
    # skips Alembic and therefore needs additive columns here as well.
    for table_name in ("inference_event", "edge_hard_example"):
        if table_name in inspector.get_table_names():
            columns = _refresh_columns(table_name)
            for column_name, column_type in {
                "scope_key": "VARCHAR(160) DEFAULT 'global'",
                "created_by": "INTEGER",
            }.items():
                if column_name not in columns:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                        )
                    )
            conn.execute(
                text(
                    f"UPDATE {table_name} SET scope_key = CASE "
                    f"WHEN project_id IS NULL THEN 'global' "
                    f"ELSE 'project:' || CAST(project_id AS VARCHAR) END "
                    f"WHERE scope_key IS NULL"
                )
            )

    # A persistent SQLite test DB created before RUN-0151 may still enforce
    # project_id NOT NULL. Recreate only these test tables so the local schema
    # matches the formal migration; production data is never touched by this
    # fixture.
    if set(("inference_event", "edge_hard_example")).issubset(
        set(inspector.get_table_names())
    ):
        event_info = inspect(conn).get_columns("inference_event")
        project_column = next(
            (item for item in event_info if item["name"] == "project_id"), None
        )
        if project_column and bool(project_column.get("nullable") is False):
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.exec_driver_sql("DROP TABLE edge_hard_example")
            conn.exec_driver_sql("DROP TABLE inference_event")
            Base.metadata.tables["inference_event"].create(conn, checkfirst=False)
            Base.metadata.tables["edge_hard_example"].create(conn, checkfirst=False)
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    # The active-learning scope migration also makes the historical
    # project_id nullable on cluster and batch tables.  Persistent test.db
    # files created before RUN-0152 can retain the old NOT NULL constraint;
    # recreate only these test-owned tables so global rows are testable without
    # weakening the production migration or inventing a project id.
    scope_tables = ("feedback_cluster", "active_learning_batch")
    existing_tables = set(inspect(conn).get_table_names())
    if set(scope_tables).issubset(existing_tables):
        needs_scope_rebuild = any(
            next(
                (item for item in inspect(conn).get_columns(table) if item["name"] == "project_id"),
                {"nullable": True},
            ).get("nullable")
            is False
            for table in scope_tables
        )
        if needs_scope_rebuild:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            if "active_learning_batch_item" in existing_tables:
                conn.exec_driver_sql("DROP TABLE active_learning_batch_item")
            conn.exec_driver_sql("DROP TABLE active_learning_batch")
            conn.exec_driver_sql("DROP TABLE feedback_cluster")
            Base.metadata.tables["feedback_cluster"].create(conn, checkfirst=False)
            Base.metadata.tables["active_learning_batch"].create(conn, checkfirst=False)
            Base.metadata.tables["active_learning_batch_item"].create(conn, checkfirst=False)
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    # Strategy callbacks are gradually projected to the unified workflow
    # state machine. Keep persistent test databases created before that
    # projection usable when migrations are intentionally skipped in pytest.
    if "strategy_task_integration" in inspector.get_table_names():
        columns = _refresh_columns("strategy_task_integration")
        if "callback_workflow_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN callback_workflow_id INTEGER"
                )
            )

    if "algorithm_definition" in inspector.get_table_names():
        columns = _refresh_columns("algorithm_definition")
        algorithm_governance_columns = {
            "algorithm_kind": "VARCHAR(24)",
            "runtime_adapter": "VARCHAR(128)",
            "rule_schema_json": "TEXT",
            "supported_formats_json": "TEXT",
            "min_samples_json": "TEXT",
            "quality_gate_json": "TEXT",
            "risk_level": "VARCHAR(24)",
            "annotation_contract_version": "VARCHAR(64)",
        }
        for column_name, column_type in algorithm_governance_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE algorithm_definition ADD COLUMN {column_name} {column_type}"
                    )
                )

    if "rule_version" in inspector.get_table_names():
        columns = _refresh_columns("rule_version")
        rule_signing_columns = {
            "signing_payload_sha256": "VARCHAR(64)",
            "signature_algorithm": "VARCHAR(32)",
            "signature_key_id": "VARCHAR(128)",
            "signature": "TEXT",
            "server_signature_status": "VARCHAR(32) DEFAULT 'NOT_PROVIDED' NOT NULL",
            "device_verification_status": "VARCHAR(32) DEFAULT 'UNVERIFIED' NOT NULL",
            "signature_verified_at": "DATETIME",
        }
        for column_name, column_type in rule_signing_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE rule_version ADD COLUMN {column_name} {column_type}"
                    )
                )

    if "rule_binding" in inspector.get_table_names():
        columns = _refresh_columns("rule_binding")
        rule_binding_device_report_columns = {
            "device_verification_status": "VARCHAR(32) DEFAULT 'UNVERIFIED' NOT NULL",
            "device_report_sequence": "INTEGER DEFAULT 0 NOT NULL",
            "last_device_report_id": "VARCHAR(160)",
            "device_verified_at": "DATETIME",
        }
        for column_name, column_type in rule_binding_device_report_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE rule_binding ADD COLUMN {column_name} {column_type}"
                    )
                )

    if "auto_training_round" in inspector.get_table_names():
        columns = _refresh_columns("auto_training_round")
        publish_columns = {
            "publish_job_id": "VARCHAR(64)",
            "publish_status": "VARCHAR(32)",
            "published_model_id": "INTEGER",
            "publish_requested_by": "INTEGER",
            "publish_reason": "TEXT",
            "publish_error": "TEXT",
            "publish_requested_at": "DATETIME",
            "published_at": "DATETIME",
        }
        for column_name, column_type in publish_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE auto_training_round "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("auto_training_round")

    if "auto_training_plan" in inspector.get_table_names():
        columns = _refresh_columns("auto_training_plan")
        for column_name, column_type in {
            "project_id": "INTEGER",
            "source_dataset_version_id": "INTEGER",
        }.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE auto_training_plan "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("auto_training_plan")

    if "active_learning_batch" in inspector.get_table_names():
        columns = _refresh_columns("active_learning_batch")
        for column_name, column_type in {
            "scope_key": "VARCHAR(160)",
            "model_version_id": "INTEGER",
            "source_key": "VARCHAR(160)",
        }.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE active_learning_batch "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("active_learning_batch")
        if "dataset_version_id" not in columns:
            try:
                conn.execute(
                    text(
                        "ALTER TABLE active_learning_batch "
                        "ADD COLUMN dataset_version_id INTEGER"
                    )
                )
            except Exception as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            columns = _refresh_columns("active_learning_batch")

    if "feedback_cluster" in inspector.get_table_names():
        columns = _refresh_columns("feedback_cluster")
        for column_name, column_type in {
            "scope_key": "VARCHAR(160)",
            "created_by": "INTEGER",
            "model_version_id": "INTEGER",
            "source_key": "VARCHAR(160)",
        }.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE feedback_cluster "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("feedback_cluster")
        conn.execute(
            text(
                "UPDATE feedback_cluster SET scope_key = "
                "CASE WHEN project_id IS NULL THEN 'global' "
                "ELSE 'project:' || CAST(project_id AS TEXT) END "
                "WHERE scope_key IS NULL"
            )
        )
        conn.execute(
            text(
                "UPDATE active_learning_batch SET scope_key = "
                "CASE WHEN project_id IS NULL THEN 'global' "
                "ELSE 'project:' || CAST(project_id AS TEXT) END "
                "WHERE scope_key IS NULL"
            )
        )

    if "auto_training_evaluation_run" in inspector.get_table_names():
        columns = _refresh_columns("auto_training_evaluation_run")
        evaluator_columns = {
            "request_manifest_rel_path": "VARCHAR(512)",
            "request_manifest_sha256": "VARCHAR(64)",
            "evaluator_worker_id": "VARCHAR(128)",
            "evaluator_claimed_at": "DATETIME",
            "evaluator_lease_expires_at": "DATETIME",
        }
        for column_name, column_type in evaluator_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE auto_training_evaluation_run "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("auto_training_evaluation_run")

    if "yolo_train_job" in inspector.get_table_names():
        columns = {item["name"] for item in inspector.get_columns("yolo_train_job")}
        if "training_task_id" not in columns:
            conn.execute(
                text("ALTER TABLE yolo_train_job ADD COLUMN training_task_id INTEGER")
            )
            columns = _refresh_columns("yolo_train_job")
        if "dataset_manifest_json" not in columns:
            conn.execute(
                text("ALTER TABLE yolo_train_job ADD COLUMN dataset_manifest_json TEXT")
            )
            columns = _refresh_columns("yolo_train_job")
        if "job_type" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_train_job ADD COLUMN job_type "
                    "VARCHAR(32) DEFAULT 'yolo_detection' NOT NULL"
                )
            )
            columns = _refresh_columns("yolo_train_job")
        if "component_status_json" not in columns:
            conn.execute(
                text("ALTER TABLE yolo_train_job ADD COLUMN component_status_json TEXT")
            )
            columns = _refresh_columns("yolo_train_job")
        if "component_artifacts_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_train_job ADD COLUMN component_artifacts_json TEXT"
                )
            )
            columns = _refresh_columns("yolo_train_job")
        system_label_columns = {
            "project_id": "INTEGER",
            "dataset_category_id": "INTEGER",
            "class_names_json": "TEXT",
            "label_set_signature": "VARCHAR(64)",
            "class_order_signature": "VARCHAR(64)",
            "data_yaml_sha256": "VARCHAR(64)",
            "dataset_content_signature": "VARCHAR(64)",
            "source_sample_manifest_json": "TEXT",
            "training_recipe_key": "VARCHAR(128)",
            "training_recipe_version": "INTEGER",
            "quality_policy_snapshot_json": "TEXT",
            "runtime_profile": "VARCHAR(128)",
            "runtime_interface_version": "INTEGER",
            "auto_training_plan_id": "INTEGER",
            "auto_training_round_id": "INTEGER",
            "queue_priority": "INTEGER",
            "queue_source": "VARCHAR(16)",
            "queue_lease_owner": "VARCHAR(128)",
            "queue_lease_expires_at": "DATETIME",
            "execution_lease_owner": "VARCHAR(128)",
            "execution_lease_expires_at": "DATETIME",
            "execution_fencing_token": "INTEGER NOT NULL DEFAULT 0",
            "assigned_gpu_json": "TEXT",
            "execution_runtime_pid": "INTEGER",
            "execution_signature": "VARCHAR(64)",
            "idempotency_key": "VARCHAR(256)",
            "idempotency_request_hash": "VARCHAR(64)",
        }
        for column_name, column_type in system_label_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE yolo_train_job "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("yolo_train_job")
    for table_name in ("yolo_bm1688_export", "yolo_edge_validation_job"):
        if table_name in inspector.get_table_names():
            columns = _refresh_columns(table_name)
            execution_columns = {
                "execution_lease_owner": "VARCHAR(128)",
                "execution_lease_expires_at": "DATETIME",
                "execution_fencing_token": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, column_type in execution_columns.items():
                if column_name not in columns:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )
                    columns = _refresh_columns(table_name)
    if "task" in inspector.get_table_names():
        columns = _refresh_columns("task")
        if "current_annotation_contract_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE task ADD COLUMN current_annotation_contract_id INTEGER"
                )
            )
            columns = _refresh_columns("task")
        if "dataset_category_id" not in columns:
            conn.execute(
                text("ALTER TABLE task ADD COLUMN dataset_category_id INTEGER")
            )
            columns = _refresh_columns("task")
        # ``dataset_id`` was added after the initial task schema.  Pytest
        # intentionally skips Alembic and reuses named SQLite files, so an
        # interrupted or long-lived test database can predate the ORM field.
        # Keep the compatibility column nullable, matching the production
        # migration and preserving legacy tasks that are not dataset-bound.
        if "dataset_id" not in columns:
            conn.execute(text("ALTER TABLE task ADD COLUMN dataset_id INTEGER"))
            columns = _refresh_columns("task")
        if "dataset_label_contract_id" not in columns:
            conn.execute(
                text("ALTER TABLE task ADD COLUMN dataset_label_contract_id INTEGER")
            )
            columns = _refresh_columns("task")
        if "task_label_scope_mode" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE task ADD COLUMN task_label_scope_mode "
                    "VARCHAR(16) DEFAULT 'FULL' NOT NULL"
                )
            )
            columns = _refresh_columns("task")
        if "project_id" not in columns:
            conn.execute(text("ALTER TABLE task ADD COLUMN project_id INTEGER"))
            columns = _refresh_columns("task")
        for column_name, column_type in {
            "idempotency_key": "VARCHAR(256)",
            "idempotency_request_hash": "VARCHAR(64)",
        }.items():
            if column_name not in columns:
                conn.execute(
                    text(f"ALTER TABLE task ADD COLUMN {column_name} {column_type}")
                )
                columns = _refresh_columns("task")
    if "edge_agent_release" in inspector.get_table_names():
        # Test databases use create_all and can predate the latest Alembic
        # revision; keep the ORM's project-scoped OTA release contract usable.
        columns = _refresh_columns("edge_agent_release")
        if "project_id" not in columns:
            conn.execute(
                text("ALTER TABLE edge_agent_release ADD COLUMN project_id INTEGER")
            )
    if "strategy_task_integration" in inspector.get_table_names():
        columns = _refresh_columns("strategy_task_integration")
        if "project_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN project_id INTEGER"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "dataset_category_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN dataset_category_id INTEGER"
                )
            )
    if "training_task" in inspector.get_table_names():
        columns = _refresh_columns("training_task")
        system_label_columns = {
            "dataset_category_id": "INTEGER",
            "dataset_category_snapshot_json": "TEXT",
            "selected_label_ids_json": "TEXT DEFAULT '[]' NOT NULL",
            "class_names_snapshot_json": "TEXT DEFAULT '[]' NOT NULL",
            "label_set_signature": "VARCHAR(64)",
            "class_order_signature": "VARCHAR(64)",
            "source_sample_snapshot_json": "TEXT",
            "training_recipe_key": "VARCHAR(128)",
            "training_recipe_version": "INTEGER",
            "runtime_profile": "VARCHAR(128)",
            "runtime_interface_version": "INTEGER",
            "quality_policy_snapshot_json": "TEXT",
            "auto_training_plan_id": "INTEGER",
            "auto_training_round_id": "INTEGER",
            "source_mode": "VARCHAR(16)",
        }
        for column_name, column_type in system_label_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE training_task "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("training_task")
    if "yolo_consistent_pool_registry" in inspector.get_table_names():
        columns = _refresh_columns("yolo_consistent_pool_registry")
        if "category_scope_key" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_consistent_pool_registry "
                    "ADD COLUMN category_scope_key VARCHAR(64) "
                    "DEFAULT 'uncategorized' NOT NULL"
                )
            )
            columns = _refresh_columns("yolo_consistent_pool_registry")
        if "dataset_category_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_consistent_pool_registry "
                    "ADD COLUMN dataset_category_id INTEGER"
                )
            )
    if "yolo_preannotate_job" in inspector.get_table_names():
        columns = _refresh_columns("yolo_preannotate_job")
        system_label_columns = {
            "task_contract_id": "INTEGER",
            "task_contract_revision": "INTEGER",
            "labels_snapshot_json": "TEXT",
            "label_set_signature": "VARCHAR(64)",
            "payload_json": "TEXT",
            "lease_owner": "VARCHAR(128)",
            "lease_expires_at": "DATETIME",
            "lease_fencing_token": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in system_label_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE yolo_preannotate_job "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("yolo_preannotate_job")
    if "label_preset" in inspector.get_table_names():
        columns = _refresh_columns("label_preset")
        if "training_profile" not in columns:
            conn.execute(
                text("ALTER TABLE label_preset ADD COLUMN training_profile VARCHAR(64)")
            )
            columns = _refresh_columns("label_preset")
        if "training_profile_config" not in columns:
            conn.execute(
                text("ALTER TABLE label_preset ADD COLUMN training_profile_config TEXT")
            )
    if "yolo_edge_validation_job" in inspector.get_table_names():
        columns = {
            item["name"] for item in inspector.get_columns("yolo_edge_validation_job")
        }
        if "progress_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_edge_validation_job ADD COLUMN progress_json TEXT"
                )
            )
            columns = _refresh_columns("yolo_edge_validation_job")
        edge_schedule_columns = {
            "edge_device_id": "VARCHAR(64)",
            "edge_device_name": "VARCHAR(64)",
            "edge_host": "VARCHAR(256)",
            "edge_port": "INTEGER",
            "edge_user": "VARCHAR(128)",
            "queue_reason": "VARCHAR(64)",
            "queued_at": "DATETIME",
            "assigned_at": "DATETIME",
            "lease_expires_at": "DATETIME",
            "auto_training_plan_id": "INTEGER",
            "auto_training_round_id": "INTEGER",
            "deployment_id": "INTEGER",
            "execution_signature": "VARCHAR(64)",
            "pipeline_origin": "VARCHAR(32)",
            "queue_lease_owner": "VARCHAR(128)",
            "queue_lease_expires_at": "DATETIME",
            "execution_lease_owner": "VARCHAR(128)",
            "execution_lease_expires_at": "DATETIME",
            "execution_fencing_token": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in edge_schedule_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE yolo_edge_validation_job "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("yolo_edge_validation_job")
    if "published_model_catalog" in inspector.get_table_names():
        columns = _refresh_columns("published_model_catalog")
        if "download_enabled" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE published_model_catalog "
                    "ADD COLUMN download_enabled BOOLEAN NOT NULL DEFAULT 1"
                )
            )
            columns = _refresh_columns("published_model_catalog")
        if "package_rel_path" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE published_model_catalog "
                    "ADD COLUMN package_rel_path VARCHAR(512)"
                )
            )
            columns = _refresh_columns("published_model_catalog")
        if "precision" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE published_model_catalog "
                    "ADD COLUMN precision VARCHAR(16)"
                )
            )
    if "yolo_bm1688_export" in inspector.get_table_names():
        columns = _refresh_columns("yolo_bm1688_export")
        if "published_model_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_bm1688_export ADD COLUMN published_model_id INTEGER"
                )
            )
            columns = _refresh_columns("yolo_bm1688_export")
        if "business_thresholds_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_bm1688_export ADD COLUMN business_thresholds_json TEXT"
                )
            )
            columns = _refresh_columns("yolo_bm1688_export")
        if "validation_thresholds_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_bm1688_export ADD COLUMN validation_thresholds_json TEXT"
                )
            )
            columns = _refresh_columns("yolo_bm1688_export")
        if "threshold_binding_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_bm1688_export ADD COLUMN threshold_binding_json TEXT"
                )
            )
            columns = _refresh_columns("yolo_bm1688_export")
        if "artifact_manifest_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE yolo_bm1688_export ADD COLUMN artifact_manifest_json TEXT"
                )
            )
            columns = _refresh_columns("yolo_bm1688_export")
        if "quality_gate_json" not in columns:
            conn.execute(
                text("ALTER TABLE yolo_bm1688_export ADD COLUMN quality_gate_json TEXT")
            )
            columns = _refresh_columns("yolo_bm1688_export")
        auto_training_columns = {
            "auto_training_plan_id": "INTEGER",
            "auto_training_round_id": "INTEGER",
            "calibration_manifest_id": "INTEGER",
            "execution_signature": "VARCHAR(64)",
            "pipeline_origin": "VARCHAR(32)",
            "queue_lease_owner": "VARCHAR(128)",
            "queue_lease_expires_at": "DATETIME",
            "execution_lease_owner": "VARCHAR(128)",
            "execution_lease_expires_at": "DATETIME",
            "execution_fencing_token": "INTEGER NOT NULL DEFAULT 0",
            "worker_action": "VARCHAR(32) DEFAULT 'export'",
            "publish_status": "VARCHAR(32)",
            "publish_job_id": "VARCHAR(64)",
            "publish_lease_owner": "VARCHAR(128)",
            "publish_lease_expires_at": "DATETIME",
            "publish_fencing_token": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in auto_training_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE yolo_bm1688_export "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("yolo_bm1688_export")
    if "strategy_task_integration" in inspector.get_table_names():
        columns = _refresh_columns("strategy_task_integration")
        if "request_payload_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN request_payload_json TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "request_sha256" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN request_sha256 VARCHAR(64)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "data_window_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN data_window_json TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "platform_status" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN platform_status VARCHAR(32) DEFAULT 'accepted' NOT NULL"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "platform_progress_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN platform_progress_json TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "platform_result_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN platform_result_json TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "platform_error_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN platform_error_json TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "platform_callback_event_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN platform_callback_event_id VARCHAR(128)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "platform_finished_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN platform_finished_at VARCHAR(64)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_source" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_source VARCHAR(64)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_stage" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_stage VARCHAR(64)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_status" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_status VARCHAR(32)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_result_json" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_result_json TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "outbound_callback_url" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN outbound_callback_url VARCHAR(1024)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "outbound_callback_status" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN outbound_callback_status VARCHAR(32)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "outbound_callback_attempts" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration "
                    "ADD COLUMN outbound_callback_attempts INTEGER DEFAULT 0 NOT NULL"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "outbound_callback_last_error" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN outbound_callback_last_error TEXT"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "source_train_job_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN source_train_job_id INTEGER"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "source_export_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN source_export_id INTEGER"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
    if "third_party_call_audit" in inspector.get_table_names():
        columns = _refresh_columns("third_party_call_audit")
        if "project_id" not in columns:
            conn.execute(
                text("ALTER TABLE third_party_call_audit ADD COLUMN project_id INTEGER")
            )
    if "strategy_task_integration" in inspector.get_table_names():
        columns = _refresh_columns("strategy_task_integration")
        if "derived_train_job_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN derived_train_job_id INTEGER"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "derived_export_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN derived_export_id INTEGER"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "derived_edge_validation_job_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN derived_edge_validation_job_id INTEGER"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_attempts" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_attempts INTEGER NOT NULL DEFAULT 0"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_lease_owner" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_lease_owner VARCHAR(128)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "automation_lease_expires_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN automation_lease_expires_at DATETIME"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "outbound_callback_event_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN outbound_callback_event_id VARCHAR(128)"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "outbound_callback_next_retry_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN outbound_callback_next_retry_at DATETIME"
                )
            )
            columns = _refresh_columns("strategy_task_integration")
        if "source_package_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE strategy_task_integration ADD COLUMN source_package_id VARCHAR(128)"
                )
            )
    if "experiment_dataset_import" in inspector.get_table_names():
        columns = _refresh_columns("experiment_dataset_import")
        experiment_columns = {
            "aggregation_key": "VARCHAR(512)",
            "aggregation_group_id": "INTEGER",
            "valid_sample_count": "INTEGER DEFAULT 0 NOT NULL",
            "dataset_category_id": "INTEGER",
            "source_type": "VARCHAR(32) DEFAULT 'internal'",
            "source_system": "VARCHAR(64)",
            "source_external_task_id": "VARCHAR(128)",
            "source_external_package_id": "VARCHAR(128)",
            "source_integration_id": "INTEGER",
            "worker_lease_owner": "VARCHAR(128)",
            "worker_lease_expires_at": "DATETIME",
            "worker_fencing_token": "INTEGER DEFAULT 0 NOT NULL",
            "attempt_count": "INTEGER DEFAULT 0 NOT NULL",
        }
        for column_name, column_type in experiment_columns.items():
            if column_name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE experiment_dataset_import "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                columns = _refresh_columns("experiment_dataset_import")
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_experiment_dataset_import_worker_scan "
                "ON experiment_dataset_import(status, worker_lease_expires_at, id)"
            )
        )
    if "experiment_dataset_group" in inspector.get_table_names():
        columns = _refresh_columns("experiment_dataset_group")
        if "dataset_category_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE experiment_dataset_group "
                    "ADD COLUMN dataset_category_id INTEGER"
                )
            )


def override_get_db():
    db = None
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        if db is not None:
            db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True, scope="session")
def db() -> Generator:
    db = None
    try:
        db = TestingSessionLocal()
        # A previous pytest process may have been interrupted after a test
        # body but before its function-scoped teardown.  Start each session
        # from the same clean non-user state as the per-test fixture so the
        # first test cannot inherit stale projects/tasks or workflow rows.
        with begin_transaction(db):
            for table in reversed(Base.metadata.sorted_tables):
                if table.key != "user":
                    db.execute(table.delete())
        yield db
    finally:
        if db is not None:
            db.close()


@pytest.fixture(autouse=True)
def run_around_tests(db: Session):
    # The shared session fixture can retain ORM identities across tests even
    # after table rows are deleted directly in a separate cleanup session.
    db.rollback()
    db.expunge_all()
    init_db()
    yield
    # Release any autobegin transaction on the session-scoped fixture so SQLite
    # teardown and TestClient requests do not deadlock.
    db.rollback()
    db.expunge_all()
    cleanup = None
    try:
        cleanup = TestingSessionLocal()
        meta = Base.metadata
        with begin_transaction(cleanup):
            for table in reversed(meta.sorted_tables):
                if table.key != "user":
                    cleanup.execute(table.delete())
    finally:
        if cleanup is not None:
            cleanup.close()
    db.expunge_all()


@pytest.fixture(scope="module")
def client() -> Generator:
    # Seed the ordinary test user before lifespan bootstraps the default admin.
    # Existing tests use created_by=1 for that user and must not depend on admin
    # privileges just because the application now bootstraps in lifespan.
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def testuser_token_headers(client: TestClient) -> Dict[str, str]:
    return get_testuser_token_headers(client)


@pytest.fixture(scope="module")
def testadmin_token_headers(client: TestClient) -> Dict[str, str]:
    return get_testadmin_token_headers(client)


def get_testuser_token_headers(client: TestClient) -> Dict[str, str]:
    init_db()
    data = {
        "username": TEST_USERNAME,
        "password": TEST_USER_PASSWORD,
    }
    r = client.post(f"{settings.API_V1_STR}/users/login", json=data)
    token = r.json()["data"]["token"]
    headers = {"Authorization": f"{token}"}
    return headers


def get_testadmin_token_headers(client: TestClient) -> Dict[str, str]:
    init_db()
    # Some legacy unit tests create the built-in admin with a placeholder
    # password hash. Restore a valid test hash before the module-scoped login
    # fixture is requested; production authentication code remains unchanged.
    admin_db = TestingSessionLocal()
    try:
        admin = crud_user.get_user_by_username(admin_db, username=ADMIN_USERNAME)
        if admin is not None and not str(admin.hashed_password or "").startswith(("$2a$", "$2b$", "$2y$")):
            admin.hashed_password = get_password_hash(ADMIN_USER_PASSWORD)
            admin_db.commit()
    finally:
        admin_db.close()
    data = {
        "username": ADMIN_USERNAME,
        "password": ADMIN_USER_PASSWORD,
    }
    r = client.post(f"{settings.API_V1_STR}/users/login", json=data)
    token = r.json()["data"]["token"]
    return {"Authorization": f"{token}"}


def init_db() -> None:
    db = None
    try:
        db = TestingSessionLocal()
        with begin_transaction(db):
            user = crud_user.get_user_by_username(db, username=TEST_USERNAME)
            if not user:
                user_in = User(
                    username=TEST_USERNAME,
                    hashed_password=get_password_hash(TEST_USER_PASSWORD),
                )
                user = crud_user.create(db=db, user=user_in)
    finally:
        if db is not None:
            db.close()
