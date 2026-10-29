"""Add formal project ownership to tasks and YOLO training jobs.

Legacy rows are backfilled only when a deterministic owner project exists;
remaining NULL values are retained for compatibility and can be reviewed via
the migration report before a future NOT NULL enforcement.
"""

from alembic import op
import sqlalchemy as sa


revision = "v5w6x7y8z9a0"
down_revision = "u4v5w6x7y8z9"
branch_labels = None
depends_on = None


def _columns(bind, table):
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "task" in tables and "project_id" not in _columns(bind, "task"):
        op.add_column("task", sa.Column("project_id", sa.Integer(), nullable=True))
        op.create_index("ix_task_project_id", "task", ["project_id"], unique=False)
        if bind.dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_task_project_id_project", "task", "project", ["project_id"], ["id"]
            )
    if "yolo_train_job" in tables and "project_id" not in _columns(bind, "yolo_train_job"):
        op.add_column("yolo_train_job", sa.Column("project_id", sa.Integer(), nullable=True))
        op.create_index("ix_yolo_train_job_project_id", "yolo_train_job", ["project_id"], unique=False)
        if bind.dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_yolo_train_job_project_id_project",
                "yolo_train_job",
                "project",
                ["project_id"],
                ["id"],
            )

    # Deterministic historical backfill: map an owner to their earliest
    # non-deleted project.  Rows without such a project remain NULL and are
    # reported for an explicit data-governance decision instead of guessing.
    if "project" in tables and "task" in tables:
        bind.execute(
            sa.text(
                "UPDATE task SET project_id = ("
                "SELECT MIN(p.id) FROM project p "
                "WHERE p.owner_id = task.created_by AND p.deleted_at IS NULL"
                ") WHERE task.project_id IS NULL"
            )
        )
    if "task" in tables and "yolo_train_job" in tables:
        bind.execute(
            sa.text(
                "UPDATE yolo_train_job SET project_id = ("
                "SELECT t.project_id FROM task t WHERE t.id = yolo_train_job.task_id"
                ") WHERE yolo_train_job.project_id IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "yolo_train_job" in tables and "project_id" in _columns(bind, "yolo_train_job"):
        if bind.dialect.name != "sqlite":
            op.drop_constraint("fk_yolo_train_job_project_id_project", "yolo_train_job", type_="foreignkey")
        op.drop_index("ix_yolo_train_job_project_id", table_name="yolo_train_job")
        op.drop_column("yolo_train_job", "project_id")
    if "task" in tables and "project_id" in _columns(bind, "task"):
        if bind.dialect.name != "sqlite":
            op.drop_constraint("fk_task_project_id_project", "task", type_="foreignkey")
        op.drop_index("ix_task_project_id", table_name="task")
        op.drop_column("task", "project_id")
