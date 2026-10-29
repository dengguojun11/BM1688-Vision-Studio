from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

PREVIOUS_REVISION = "r9s0t1u2v3w4"
CURRENT_REVISION = "s1t2u3v4w5x6"


def _create_previous_schema(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "label_preset",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    sa.Table(
        "label_preset_item",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("preset_id", sa.Integer(), nullable=False),
        sa.Column("label_key", sa.String(128), nullable=False),
        sa.Column("label_value", sa.String(128), nullable=False),
        sa.Column("color", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    sa.Table(
        "yolo_train_job",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(32), nullable=True),
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


def _alembic_config(database_path: Path) -> Config:
    migration_dir = Path(__file__).resolve().parents[2] / "alembic_labelu"
    config = Config(str(migration_dir / "alembic.ini"))
    config.set_main_option("script_location", str(migration_dir))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite:///{database_path.as_posix()}",
    )
    return config


def _assert_upgraded_schema(engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    preset_columns = {
        column["name"] for column in inspector.get_columns("label_preset")
    }
    job_columns = {column["name"] for column in inspector.get_columns("yolo_train_job")}
    preset_indexes = {index["name"] for index in inspector.get_indexes("label_preset")}
    job_indexes = {index["name"] for index in inspector.get_indexes("yolo_train_job")}

    assert {"training_profile", "training_profile_config"} <= preset_columns
    assert {
        "job_type",
        "component_status_json",
        "component_artifacts_json",
    } <= job_columns
    assert "ix_label_preset_training_profile" in preset_indexes
    assert "ix_yolo_train_job_job_type" in job_indexes

    with engine.connect() as conn:
        revision = conn.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        preset = (
            conn.execute(
                sa.text(
                    "SELECT id, name, training_profile, training_profile_config "
                    "FROM label_preset WHERE slug = 'plateRecognition'"
                )
            )
            .mappings()
            .one()
        )
        item = (
            conn.execute(
                sa.text(
                    "SELECT label_key, label_value, color FROM label_preset_item "
                    "WHERE preset_id = :preset_id"
                ),
                {"preset_id": preset["id"]},
            )
            .mappings()
            .one()
        )

    assert revision == CURRENT_REVISION
    assert preset["name"] == "双层电动车牌识别"
    assert preset["training_profile"] == "plate_recognition_v1"
    assert (
        json.loads(preset["training_profile_config"])["polygon_label"] == "platePoint"
    )
    assert item == {
        "label_key": "双层车牌",
        "label_value": "doubleEP",
        "color": "#ff6600",
    }


def test_plate_recognition_migration_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "plate-recognition-migration.db"
    engine = sa.create_engine(f"sqlite:///{database_path.as_posix()}")
    _create_previous_schema(engine)
    config = _alembic_config(database_path)

    command.upgrade(config, CURRENT_REVISION)
    _assert_upgraded_schema(engine)

    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO yolo_train_job (status) VALUES ('PENDING')"))
        assert (
            conn.execute(sa.text("SELECT job_type FROM yolo_train_job")).scalar_one()
            == "yolo_detection"
        )

    command.downgrade(config, PREVIOUS_REVISION)
    inspector = sa.inspect(engine)
    assert "training_profile" not in {
        column["name"] for column in inspector.get_columns("label_preset")
    }
    assert "job_type" not in {
        column["name"] for column in inspector.get_columns("yolo_train_job")
    }

    command.upgrade(config, CURRENT_REVISION)
    _assert_upgraded_schema(engine)
