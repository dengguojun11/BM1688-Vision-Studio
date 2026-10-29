from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy as sa


PREVIOUS_REVISION = "o3p4q5r6s7t8"
CURRENT_REVISION = "p4q5r6s7t8u9"


def _alembic_config(database_path: Path) -> Config:
    migration_dir = Path(__file__).resolve().parents[2] / "alembic_labelu"
    config = Config(str(migration_dir / "alembic.ini"))
    config.set_main_option("script_location", str(migration_dir))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def _create_previous_schema(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "auto_label_job",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=True),
        sa.Column("filter_by_labels", sa.Boolean(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        conn.execute(
            sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": PREVIOUS_REVISION},
        )
        conn.execute(
            sa.text(
                "INSERT INTO auto_label_job "
                "(task_id, status, sample_count) VALUES (234, 'COMPLETED', 34)"
            )
        )


def test_auto_label_job_durable_migration_preserves_existing_jobs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "auto-label-job-durable.db"
    engine = sa.create_engine(f"sqlite:///{database_path.as_posix()}")
    _create_previous_schema(engine)

    command.upgrade(_alembic_config(database_path), CURRENT_REVISION)

    inspector = sa.inspect(engine)
    columns = {
        column["name"] for column in inspector.get_columns("auto_label_job")
    }
    assert {
        "job_kind",
        "source_hint",
        "source_key",
        "payload_json",
        "attempt_count",
        "lease_owner",
        "lease_expires_at",
    } <= columns
    indexes = {
        index["name"]: index for index in inspector.get_indexes("auto_label_job")
    }
    assert indexes["ix_auto_label_job_source_key"]["unique"] == 1
    assert "ix_auto_label_job_job_kind" in indexes
    assert "ix_auto_label_job_lease_expires_at" in indexes

    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT task_id, status, sample_count, job_kind, attempt_count "
                    "FROM auto_label_job"
                )
            )
            .mappings()
            .one()
        )
        revision = conn.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert dict(row) == {
        "task_id": 234,
        "status": "COMPLETED",
        "sample_count": 34,
        "job_kind": "MANUAL_BATCH",
        "attempt_count": 0,
    }
    assert revision == CURRENT_REVISION
