"""reserve auto training technical switches without enabling execution

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
"""

from alembic import op
import sqlalchemy as sa


revision = "l0m1n2o3p4q5"
down_revision = "k9l0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade():
    # Switch values are resolved by the application config service. The migration
    # intentionally performs no business-row seed, scan, backfill or activation.
    sa.inspect(op.get_bind()).get_table_names()


def downgrade():
    pass
