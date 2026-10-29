"""Merge the automatic-training and evaluation-dataset migration heads."""

from typing import Sequence, Union


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = (
    "b9c0d1e2f3a4",
    "z8a9b0c1d2e4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
