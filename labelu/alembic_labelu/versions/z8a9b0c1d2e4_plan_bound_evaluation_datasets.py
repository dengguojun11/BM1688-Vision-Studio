"""associate evaluation datasets with their owning auto-training plan"""

from alembic import op
import sqlalchemy as sa


revision = "z8a9b0c1d2e4"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("training_evaluation_dataset")}
    if "plan_id" not in columns:
        op.add_column(
            "training_evaluation_dataset",
            sa.Column("plan_id", sa.Integer(), nullable=True),
        )
    indexes = {index["name"] for index in inspector.get_indexes("training_evaluation_dataset")}
    if "idx_training_eval_dataset_plan" not in indexes:
        op.create_index(
            "idx_training_eval_dataset_plan",
            "training_evaluation_dataset",
            ["plan_id", "dataset_kind", "status"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("training_evaluation_dataset")}
    if "idx_training_eval_dataset_plan" in indexes:
        op.drop_index("idx_training_eval_dataset_plan", table_name="training_evaluation_dataset")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("training_evaluation_dataset")}
    if "plan_id" in columns:
        op.drop_column("training_evaluation_dataset", "plan_id")
