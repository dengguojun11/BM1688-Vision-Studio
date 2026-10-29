"""add 7gai algorithm groups and train adapter registry"""

from datetime import datetime
import json

from alembic import op
import sqlalchemy as sa


revision = "k0l1m2n3o4p5"
down_revision = "j9k0l1m2n3o4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "algorithm_group",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_key", name="uq_algorithm_group_key"),
    )
    op.create_index("ix_algorithm_group_group_key", "algorithm_group", ["group_key"], unique=False)
    op.create_index("ix_algorithm_group_status", "algorithm_group", ["status"], unique=False)
    op.create_index(
        "idx_algorithm_group_status_sort",
        "algorithm_group",
        ["status", "sort_order"],
        unique=False,
    )

    op.create_table(
        "algorithm_definition",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("algorithm_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("algorithm_group_id", sa.Integer(), nullable=False),
        sa.Column("label_set_signature", sa.String(length=64), nullable=True),
        sa.Column("required_tools_json", sa.Text(), nullable=False),
        sa.Column("required_attributes_json", sa.Text(), nullable=False),
        sa.Column("training_adapter", sa.String(length=128), nullable=False),
        sa.Column("evaluation_adapter", sa.String(length=128), nullable=False),
        sa.Column("supported_chips_json", sa.Text(), nullable=False),
        sa.Column("default_recipe", sa.String(length=128), nullable=True),
        sa.Column("input_contract_json", sa.Text(), nullable=True),
        sa.Column("output_contract_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["algorithm_group_id"], ["algorithm_group.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("algorithm_key", name="uq_algorithm_definition_key"),
    )
    op.create_index(
        "ix_algorithm_definition_algorithm_key",
        "algorithm_definition",
        ["algorithm_key"],
        unique=False,
    )
    op.create_index(
        "ix_algorithm_definition_algorithm_group_id",
        "algorithm_definition",
        ["algorithm_group_id"],
        unique=False,
    )
    op.create_index(
        "ix_algorithm_definition_label_set_signature",
        "algorithm_definition",
        ["label_set_signature"],
        unique=False,
    )
    op.create_index(
        "ix_algorithm_definition_task_type",
        "algorithm_definition",
        ["task_type"],
        unique=False,
    )
    op.create_index(
        "ix_algorithm_definition_status", "algorithm_definition", ["status"], unique=False
    )
    op.create_index(
        "idx_algorithm_definition_group_status",
        "algorithm_definition",
        ["algorithm_group_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_algorithm_definition_task_type",
        "algorithm_definition",
        ["task_type", "status"],
        unique=False,
    )

    now = datetime.now()
    group_table = sa.table(
        "algorithm_group",
        sa.column("id", sa.Integer()),
        sa.column("group_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("metadata_json", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    op.bulk_insert(
        group_table,
        [
            {"id": 1, "group_key": "person_vehicle_detection", "name": "通用人车检测", "description": "人员、车辆及其基础目标检测能力", "status": "ACTIVE", "sort_order": 10, "metadata_json": None, "created_at": now, "updated_at": now},
            {"id": 2, "group_key": "person_behavior", "name": "人员属性与行为", "description": "人员属性、行为和安全相关模型", "status": "ACTIVE", "sort_order": 20, "metadata_json": None, "created_at": now, "updated_at": now},
            {"id": 3, "group_key": "urban_management", "name": "城市管理杂物", "description": "垃圾、占道和城市管理目标模型", "status": "ACTIVE", "sort_order": 30, "metadata_json": None, "created_at": now, "updated_at": now},
            {"id": 4, "group_key": "fire_environment", "name": "消防安全与环境", "description": "消防、安全和环境目标模型", "status": "ACTIVE", "sort_order": 40, "metadata_json": None, "created_at": now, "updated_at": now},
        ],
    )
    definition_table = sa.table(
        "algorithm_definition",
        sa.column("algorithm_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("task_type", sa.String()),
        sa.column("algorithm_group_id", sa.Integer()),
        sa.column("label_set_signature", sa.String()),
        sa.column("required_tools_json", sa.Text()),
        sa.column("required_attributes_json", sa.Text()),
        sa.column("training_adapter", sa.String()),
        sa.column("evaluation_adapter", sa.String()),
        sa.column("supported_chips_json", sa.Text()),
        sa.column("default_recipe", sa.String()),
        sa.column("input_contract_json", sa.Text()),
        sa.column("output_contract_json", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("metadata_json", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    op.bulk_insert(
        definition_table,
        [
            {
                "algorithm_key": "yolo_detection",
                "name": "YOLO 通用目标检测",
                "description": "兼容现有 YoloTrainJob 的目标检测训练、评估和导出能力",
                "task_type": "object_detection",
                "algorithm_group_id": 1,
                "label_set_signature": None,
                "required_tools_json": json.dumps(["rectTool", "tagTool"]),
                "required_attributes_json": "[]",
                "training_adapter": "yolo_detection",
                "evaluation_adapter": "yolo_detection",
                "supported_chips_json": json.dumps(["bm1688", "bm1684", "rk1808"]),
                "default_recipe": "yolo_detection_v1",
                "input_contract_json": json.dumps({"media": ["image", "video_frame"], "annotation": "bbox"}),
                "output_contract_json": json.dumps({"type": "detections", "fields": ["class_id", "score", "bbox"]}),
                "status": "ACTIVE",
                "metadata_json": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_algorithm_definition_task_type", table_name="algorithm_definition")
    op.drop_index("idx_algorithm_definition_group_status", table_name="algorithm_definition")
    op.drop_index("ix_algorithm_definition_status", table_name="algorithm_definition")
    op.drop_index("ix_algorithm_definition_task_type", table_name="algorithm_definition")
    op.drop_index("ix_algorithm_definition_label_set_signature", table_name="algorithm_definition")
    op.drop_index("ix_algorithm_definition_algorithm_group_id", table_name="algorithm_definition")
    op.drop_index("ix_algorithm_definition_algorithm_key", table_name="algorithm_definition")
    op.drop_table("algorithm_definition")
    op.drop_index("idx_algorithm_group_status_sort", table_name="algorithm_group")
    op.drop_index("ix_algorithm_group_status", table_name="algorithm_group")
    op.drop_index("ix_algorithm_group_group_key", table_name="algorithm_group")
    op.drop_table("algorithm_group")
