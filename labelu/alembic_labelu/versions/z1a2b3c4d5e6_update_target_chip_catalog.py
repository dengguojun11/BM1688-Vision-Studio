"""Restrict built-in algorithm targets to the supported edge chips."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "z1a2b3c4d5e6"
down_revision = "z0a1b2c3d4f6"
branch_labels = None
depends_on = None


TARGET_CHIPS = json.dumps(["bm1688", "bm1684", "rk1808"], ensure_ascii=False)
BUILTIN_KEYS = (
    "yolo_detection",
    "classification",
    "segmentation",
    "pose",
    "custom_container",
    "non_motor_vehicle_detection",
    "illegal_parking_detection",
    "traffic_flow_analysis",
    "large_vehicle_detection",
    "smoke_detection",
    "overflowing_bin_detection",
    "street_litter_detection",
    "street_vendor_detection",
    "manhole_detection",
    "construction_waste_detection",
    "corridor_clutter_detection",
    "cat_dog_detection",
    "escalator_luggage_detection",
    "lightweight_floating_object_detection",
    "electric_bike_helmet_detection",
    "face_mask_detection",
    "phone_usage_detection",
    "insulated_glove_detection",
    "safety_helmet_detection",
    "chef_uniform_detection",
    "person_intrusion",
    "boundary_crossing",
    "near_water_danger",
    "fence_climbing_detection",
    "crowd_gathering_detection",
    "passenger_flow_counting",
    "loitering_detection",
    "control_room_absence_detection",
    "fall_detection",
    "fishing_detection",
    "tent_illegal_setup_detection",
)


def upgrade() -> None:
    bind = op.get_bind()
    table = sa.table(
        "algorithm_definition",
        sa.column("algorithm_key", sa.String()),
        sa.column("supported_chips_json", sa.Text()),
    )
    bind.execute(
        table.update()
        .where(table.c.algorithm_key.in_(BUILTIN_KEYS))
        .values(supported_chips_json=TARGET_CHIPS)
    )


def downgrade() -> None:
    # The old list was not a valid 7GAI deployment contract.  Downgrade keeps
    # the safe target list rather than reintroducing CPU/CUDA/RK3588 choices.
    return
