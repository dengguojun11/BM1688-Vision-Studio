"""Merge the automatic-training migration heads.

Revision ID: q5r6s7t8u9v0
Revises: n2o3p4q5r6s7, p4q5r6s7t8u9
"""

from typing import Sequence, Union


revision: str = "q5r6s7t8u9v0"
down_revision: Union[str, Sequence[str], None] = (
    "n2o3p4q5r6s7",
    "p4q5r6s7t8u9",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
