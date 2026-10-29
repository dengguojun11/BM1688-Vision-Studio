"""Merge automatic-training and third-party audit migration heads.

Revision ID: s5t6u7v8w9x0
Revises: q5r6s7t8u9v0, r5s6t7u8v9w0
"""

from typing import Sequence, Union


revision: str = "s5t6u7v8w9x0"
down_revision: Union[str, Sequence[str], None] = (
    "q5r6s7t8u9v0",
    "r5s6t7u8v9w0",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
