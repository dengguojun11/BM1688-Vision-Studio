"""Idempotent local/bootstrap data for the annotation taxonomy."""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


DEFAULT_GLOBAL_DIMENSIONS: tuple[tuple[str, str, int, tuple[tuple[str, str], ...]], ...] = (
    (
        "sceneType",
        "场景类型",
        0,
        (
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
        ),
    ),
    (
        "lighting",
        "光照 / 拍摄时间",
        1,
        (
            ("白天正常光", "normalDaylight"),
            ("白天强光", "strongDaylight"),
            ("白天弱光", "weakDaylight"),
            ("傍晚黄昏", "dusk"),
            ("夜间开灯", "nightWithLight"),
            ("夜间无光", "nightNoLight"),
            ("逆光", "backlight"),
            ("阴影较重", "heavyShadow"),
        ),
    ),
    (
        "weather",
        "天气环境",
        2,
        (
            ("晴天", "sunny"),
            ("阴天", "cloudy"),
            ("小雨", "lightRain"),
            ("大雨", "heavyRain"),
            ("雾天", "foggy"),
            ("雪天", "snowy"),
            ("沙尘", "dust"),
        ),
    ),
    (
        "quality",
        "图片质量",
        3,
        (
            ("清晰", "clear"),
            ("轻微模糊", "slightBlurry"),
            ("严重模糊", "severeBlurry"),
            ("过曝", "overexposed"),
            ("欠曝", "underexposed"),
            ("画面遮挡", "occluded"),
            ("抖动花屏", "motionBlur"),
        ),
    ),
    (
        "angle",
        "拍摄角度",
        4,
        (
            ("平视", "eyeLevel"),
            ("俯视", "topView"),
            ("仰视", "lowAngle"),
            ("斜侧视角", "obliqueView"),
            ("高空视角", "aerialView"),
        ),
    ),
    (
        "source",
        "数据来源",
        5,
        (
            ("摄像头抓拍", "cameraCapture"),
            ("人工拍摄", "manualShoot"),
            ("网络采集", "webCollect"),
            ("合成数据", "syntheticData"),
        ),
    ),
)


def seed_default_global_tag_dimensions(db_url: str) -> int:
    """Insert the built-in dimensions only when the table is completely empty."""

    from labelu.internal.domain.models.annotation_taxonomy import (
        GlobalTagDimension,
        GlobalTagOption,
    )

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        if session.query(GlobalTagDimension).count() > 0:
            return 0
        now = datetime.now()
        inserted = 0
        for field_key, name_cn, sort_order, options in DEFAULT_GLOBAL_DIMENSIONS:
            dimension = GlobalTagDimension(
                field_key=field_key,
                name_cn=name_cn,
                sort_order=sort_order,
                created_at=now,
                updated_at=now,
            )
            session.add(dimension)
            session.flush()
            for option_order, (option_name, option_value) in enumerate(options):
                session.add(
                    GlobalTagOption(
                        dimension_id=dimension.id,
                        name_cn=option_name,
                        value_en=option_value,
                        sort_order=option_order,
                        created_at=now,
                    )
                )
            inserted += 1
        session.commit()
        return inserted
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()
