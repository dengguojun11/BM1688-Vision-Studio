"""Let external models bind a logical dataset without freezing sample data."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "zg3h4i5j6k7l"
down_revision = "zf2a3b4c5d6e"
branch_labels = None
depends_on = None


def _sqlite_rebuild_model_version() -> None:
    """Rebuild without reflecting foreign keys to the retired project table."""
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("DROP TABLE IF EXISTS _alembic_tmp_model_version")
    bind.exec_driver_sql("""
        CREATE TABLE _alembic_tmp_model_version (
            id INTEGER NOT NULL PRIMARY KEY,
            algorithm_id INTEGER NOT NULL,
            dataset_id INTEGER,
            dataset_version_id INTEGER,
            training_job_id INTEGER,
            parent_model_version_id INTEGER,
            published_catalog_id INTEGER,
            model_key VARCHAR(160) NOT NULL,
            version_no INTEGER NOT NULL,
            display_name VARCHAR(256) NOT NULL,
            status VARCHAR(32) NOT NULL,
            chip_type VARCHAR(64),
            precision VARCHAR(16),
            input_contract_json TEXT,
            output_contract_json TEXT,
            preprocess_contract_json TEXT,
            postprocess_contract_json TEXT,
            metrics_json TEXT,
            manifest_json TEXT,
            created_by INTEGER NOT NULL,
            published_by INTEGER,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            published_at DATETIME,
            retired_at DATETIME,
            CONSTRAINT uq_model_version_number UNIQUE (algorithm_id, version_no),
            CONSTRAINT uq_model_version_key UNIQUE (model_key),
            CONSTRAINT uq_model_version_training_job UNIQUE (training_job_id),
            CONSTRAINT fk_model_version_algorithm_id_algorithm_definition
                FOREIGN KEY(algorithm_id) REFERENCES algorithm_definition (id),
            CONSTRAINT fk_model_version_dataset_id_dataset
                FOREIGN KEY(dataset_id) REFERENCES dataset (id),
            CONSTRAINT fk_model_version_dataset_version_id_dataset_version
                FOREIGN KEY(dataset_version_id) REFERENCES dataset_version (id),
            CONSTRAINT fk_model_version_training_job_id_yolo_train_job
                FOREIGN KEY(training_job_id) REFERENCES yolo_train_job (id),
            CONSTRAINT fk_model_version_parent_model_version_id_model_version
                FOREIGN KEY(parent_model_version_id) REFERENCES model_version (id),
            CONSTRAINT fk_model_version_published_catalog_id_published_model_catalog
                FOREIGN KEY(published_catalog_id) REFERENCES published_model_catalog (id),
            CONSTRAINT fk_model_version_created_by_user
                FOREIGN KEY(created_by) REFERENCES user (id),
            CONSTRAINT fk_model_version_published_by_user
                FOREIGN KEY(published_by) REFERENCES user (id)
        )
        """)
    source_columns = (
        "id, algorithm_id, dataset_version_id, training_job_id, "
        "parent_model_version_id, published_catalog_id, model_key, version_no, "
        "display_name, status, chip_type, precision, input_contract_json, "
        "output_contract_json, preprocess_contract_json, postprocess_contract_json, "
        "metrics_json, manifest_json, created_by, published_by, created_at, updated_at, "
        "published_at, retired_at"
    )
    bind.exec_driver_sql(
        "INSERT INTO _alembic_tmp_model_version (" + source_columns + ") "
        "SELECT " + source_columns + " FROM model_version"
    )
    bind.exec_driver_sql("DROP TABLE model_version")
    bind.exec_driver_sql(
        "ALTER TABLE _alembic_tmp_model_version RENAME TO model_version"
    )
    for statement in (
        "CREATE INDEX idx_model_version_algorithm_status ON model_version (algorithm_id, status)",
        "CREATE INDEX idx_model_version_creator_status ON model_version (created_by, status)",
        "CREATE INDEX idx_model_version_dataset ON model_version (dataset_version_id)",
        "CREATE INDEX ix_model_version_algorithm_id ON model_version (algorithm_id)",
        "CREATE INDEX ix_model_version_dataset_version_id ON model_version (dataset_version_id)",
        "CREATE INDEX ix_model_version_parent_model_version_id ON model_version (parent_model_version_id)",
        "CREATE INDEX ix_model_version_published_catalog_id ON model_version (published_catalog_id)",
        "CREATE INDEX ix_model_version_status ON model_version (status)",
        "CREATE INDEX ix_model_version_training_job_id ON model_version (training_job_id)",
    ):
        bind.exec_driver_sql(statement)
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {item["name"]: item for item in inspector.get_columns("model_version")}
    if "dataset_id" not in columns:
        if bind.dialect.name == "sqlite":
            _sqlite_rebuild_model_version()
        else:
            op.add_column(
                "model_version", sa.Column("dataset_id", sa.Integer(), nullable=True)
            )
            op.create_foreign_key(
                "fk_model_version_dataset_id_dataset",
                "model_version",
                "dataset",
                ["dataset_id"],
                ["id"],
            )
            op.alter_column(
                "model_version",
                "dataset_version_id",
                existing_type=sa.Integer(),
                nullable=True,
            )
    elif not bool(columns["dataset_version_id"].get("nullable")):
        if bind.dialect.name == "sqlite":
            _sqlite_rebuild_model_version()
        else:
            op.alter_column(
                "model_version",
                "dataset_version_id",
                existing_type=sa.Integer(),
                nullable=True,
            )

    op.execute(
        sa.text(
            "UPDATE model_version SET dataset_id = ("
            "SELECT dataset_version.dataset_id FROM dataset_version "
            "WHERE dataset_version.id = model_version.dataset_version_id"
            ") WHERE dataset_id IS NULL AND dataset_version_id IS NOT NULL"
        )
    )

    indexes = {item["name"] for item in inspect(bind).get_indexes("model_version")}
    if "idx_model_version_logical_dataset" not in indexes:
        op.create_index(
            "idx_model_version_logical_dataset",
            "model_version",
            ["dataset_id"],
            unique=False,
        )


def downgrade() -> None:
    raise RuntimeError(
        "外部模型可直接关联逻辑数据集；回退会丢失该血缘，必须从迁移前备份恢复。"
    )
