"""add annotation taxonomy tables (label presets, global tag dimensions)

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b8
Create Date: 2026-05-10 12:00:00.000000

"""
from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

revision = "d4e5f6a7b8c0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


_GLOBAL_DIMENSIONS: list[tuple[str, str, int, list[tuple[str, str]]]] = [
    (
        "sceneType",
        "场景类型",
        0,
        [
            ("城市主干道", "urbanArterial"),
            ("小区道路", "residentialRoad"),
            ("背街小巷", "backAlley"),
            ("公园广场", "parkSquare"),
            ("商圈街道", "commercialStreet"),
            ("城中村道路", "urbanVillage"),
            ("学校周边", "schoolArea"),
            ("工地周边", "constructionSite"),
            ("室内大厅 / 主干道", "indoorHall"),
            ("室内通道 / 走廊", "indoorCorridor"),
            ("楼梯间 / 电梯", "indoorStairwell"),
            ("室内地下车库", "indoorParking"),
        ],
    ),
    (
        "lighting",
        "光照 / 拍摄时间",
        1,
        [
            ("白天正常光", "normalDaylight"),
            ("白天强光", "strongDaylight"),
            ("白天弱光", "weakDaylight"),
            ("傍晚黄昏", "dusk"),
            ("夜间开灯", "nightWithLight"),
            ("夜间无光", "nightNoLight"),
            ("逆光", "backlight"),
            ("阴影较重", "heavyShadow"),
        ],
    ),
    (
        "weather",
        "天气环境",
        2,
        [
            ("晴天", "sunny"),
            ("阴天", "cloudy"),
            ("小雨", "lightRain"),
            ("大雨", "heavyRain"),
            ("雾天", "foggy"),
            ("雪天", "snowy"),
            ("沙尘", "dust"),
        ],
    ),
    (
        "quality",
        "图片质量",
        3,
        [
            ("清晰", "clear"),
            ("轻微模糊", "slightBlurry"),
            ("严重模糊", "severeBlurry"),
            ("过曝", "overexposed"),
            ("欠曝", "underexposed"),
            ("画面遮挡", "occluded"),
            ("抖动花屏", "motionBlur"),
        ],
    ),
    (
        "angle",
        "拍摄角度",
        4,
        [
            ("平视", "eyeLevel"),
            ("俯视", "topView"),
            ("仰视", "lowAngle"),
            ("斜侧视角", "obliqueView"),
            ("高空视角", "aerialView"),
        ],
    ),
    (
        "source",
        "数据来源",
        5,
        [
            ("摄像头抓拍", "cameraCapture"),
            ("人工拍摄", "manualShoot"),
            ("网络采集", "webCollect"),
            ("合成数据", "syntheticData"),
        ],
    ),
]

_GARBAGE_ITEMS: list[tuple[str, str, str, int]] = [
    ("垃圾堆", "garbage_street_dump", "#E74C3C", 0),
    ("打包垃圾", "garbage_street_pack", "#3498DB", 1),
    ("零散垃圾", "garbage_street_scattered", "#2ECC71", 2),
    ("纸皮箱子", "object_box", "#F39C12", 3),
    ("瓶罐", "garbage_street_can", "#9B59B6", 4),
    ("非垃圾类", "non_garbage", "#1ABC9C", 5),
    ("大件垃圾", "garbage_bulky", "#E67E22", 6),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "global_tag_dimension" not in tables:
        op.create_table(
            "global_tag_dimension",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("field_key", sa.String(length=64), nullable=False),
            sa.Column("name_cn", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_global_tag_dimension_field_key", "global_tag_dimension", ["field_key"], unique=True
        )
        op.create_index("ix_global_tag_dimension_id", "global_tag_dimension", ["id"])
        op.create_index(
            "ix_global_tag_dimension_deleted_at", "global_tag_dimension", ["deleted_at"]
        )

    if "global_tag_option" not in tables:
        op.create_table(
            "global_tag_option",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("dimension_id", sa.Integer(), nullable=False),
            sa.Column("name_cn", sa.String(length=128), nullable=False),
            sa.Column("value_en", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["dimension_id"], ["global_tag_dimension.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_global_tag_option_dimension_id", "global_tag_option", ["dimension_id"])
        op.create_index("ix_global_tag_option_id", "global_tag_option", ["id"])

    if "label_preset" not in tables:
        op.create_table(
            "label_preset",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.String(length=512), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_label_preset_slug", "label_preset", ["slug"], unique=True)
        op.create_index("ix_label_preset_id", "label_preset", ["id"])
        op.create_index("ix_label_preset_deleted_at", "label_preset", ["deleted_at"])

    if "label_preset_item" not in tables:
        op.create_table(
            "label_preset_item",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("preset_id", sa.Integer(), nullable=False),
            sa.Column("label_key", sa.String(length=128), nullable=False),
            sa.Column("label_value", sa.String(length=128), nullable=False),
            sa.Column("color", sa.String(length=32), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["preset_id"], ["label_preset.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_label_preset_item_preset_id", "label_preset_item", ["preset_id"])
        op.create_index("ix_label_preset_item_id", "label_preset_item", ["id"])

    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        from labelu.internal.domain.models.annotation_taxonomy import (
            GlobalTagDimension,
            GlobalTagOption,
            LabelPreset,
            LabelPresetItem,
        )

        now = datetime.now()

        if session.query(GlobalTagDimension).count() == 0:
            for field_key, name_cn, sort_order, opts in _GLOBAL_DIMENSIONS:
                dim = GlobalTagDimension(
                    field_key=field_key,
                    name_cn=name_cn,
                    sort_order=sort_order,
                    created_at=now,
                    updated_at=now,
                )
                session.add(dim)
                session.flush()
                for oi, (ok, ov) in enumerate(opts):
                    session.add(
                        GlobalTagOption(
                            dimension_id=dim.id,
                            name_cn=ok,
                            value_en=ov,
                            sort_order=oi,
                            created_at=now,
                        )
                    )

        if session.query(LabelPreset).filter(LabelPreset.slug == "garbageExposure").first() is None:
            preset = LabelPreset(
                slug="garbageExposure",
                name="垃圾暴露",
                description="垃圾暴露检测拉框类别",
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
            session.add(preset)
            session.flush()
            for lk, lv, color, so in _GARBAGE_ITEMS:
                session.add(
                    LabelPresetItem(
                        preset_id=preset.id,
                        label_key=lk,
                        label_value=lv,
                        color=color,
                        sort_order=so,
                        created_at=now,
                    )
                )

        session.flush()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "label_preset_item" in tables:
        op.drop_index("ix_label_preset_item_id", table_name="label_preset_item")
        op.drop_index("ix_label_preset_item_preset_id", table_name="label_preset_item")
        op.drop_table("label_preset_item")

    if "label_preset" in tables:
        op.drop_index("ix_label_preset_deleted_at", table_name="label_preset")
        op.drop_index("ix_label_preset_id", table_name="label_preset")
        op.drop_index("ix_label_preset_slug", table_name="label_preset")
        op.drop_table("label_preset")

    if "global_tag_option" in tables:
        op.drop_index("ix_global_tag_option_id", table_name="global_tag_option")
        op.drop_index("ix_global_tag_option_dimension_id", table_name="global_tag_option")
        op.drop_table("global_tag_option")

    if "global_tag_dimension" in tables:
        op.drop_index("ix_global_tag_dimension_deleted_at", table_name="global_tag_dimension")
        op.drop_index("ix_global_tag_dimension_id", table_name="global_tag_dimension")
        op.drop_index("ix_global_tag_dimension_field_key", table_name="global_tag_dimension")
        op.drop_table("global_tag_dimension")
