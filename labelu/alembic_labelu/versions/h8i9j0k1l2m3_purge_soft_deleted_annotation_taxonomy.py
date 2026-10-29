"""purge soft-deleted annotation taxonomy rows (hard delete tombstones)

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-05-22

"""
from __future__ import annotations

from alembic import op
from sqlalchemy.orm import sessionmaker

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        from labelu.internal.adapter.persistence import crud_annotation_taxonomy

        n_p = crud_annotation_taxonomy.purge_soft_deleted_label_presets(session)
        n_d = crud_annotation_taxonomy.purge_soft_deleted_global_dimensions(session)
        session.commit()
        if n_p or n_d:
            print(
                f"[migration] purged soft-deleted taxonomy: "
                f"label_preset={n_p}, global_dimension={n_d}"
            )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    # 已物理删除的数据无法恢复
    pass
