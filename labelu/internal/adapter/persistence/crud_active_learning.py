from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern

from labelu.internal.domain.models.active_learning import (
    ActiveLearningBatch,
    ActiveLearningBatchItem,
    FeedbackCluster,
)


def get_cluster(db: Session, cluster_id: int):
    return db.query(FeedbackCluster).filter(FeedbackCluster.id == int(cluster_id)).first()


def find_cluster(db: Session, project_id: int | None, cluster_key: str, scope_key: str | None = None):
    scope = scope_key or (f"project:{int(project_id)}" if project_id is not None else "global")
    return db.query(FeedbackCluster).filter_by(scope_key=scope, cluster_key=cluster_key).first()


def list_clusters(db: Session, project_id: int | None = None, scope_key: str | None = None):
    scope = scope_key or (f"project:{int(project_id)}" if project_id is not None else "global")
    return db.query(FeedbackCluster).filter_by(scope_key=scope).order_by(FeedbackCluster.priority_score.desc(), FeedbackCluster.id.asc()).all()


def list_clusters_page(
    db: Session,
    project_id: int | None = None,
    scope_key: str | None = None,
    *,
    page: int = 0,
    size: int = 20,
    search: str | None = None,
    cluster_keys: set[str] | None = None,
):
    """Search and paginate clusters without changing the legacy list API."""

    scope = scope_key or (f"project:{int(project_id)}" if project_id is not None else "global")
    query = db.query(FeedbackCluster).filter(FeedbackCluster.scope_key == scope)
    if cluster_keys is not None:
        if not cluster_keys:
            return [], 0
        # SQLite deployments commonly cap a single statement at 999 bound
        # parameters.  Chunk the visibility set so a large global queue does
        # not fail merely because one user can see many cluster keys.
        keys = list(cluster_keys)
        key_clauses = [
            FeedbackCluster.cluster_key.in_(keys[index : index + 500])
            for index in range(0, len(keys), 500)
        ]
        query = query.filter(or_(*key_clauses))
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                FeedbackCluster.cluster_key.ilike(pattern, escape=LIKE_ESCAPE),
                FeedbackCluster.event_type.ilike(pattern, escape=LIKE_ESCAPE),
                FeedbackCluster.device_id.ilike(pattern, escape=LIKE_ESCAPE),
                FeedbackCluster.source_key.ilike(pattern, escape=LIKE_ESCAPE),
                FeedbackCluster.status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(FeedbackCluster.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(FeedbackCluster.model_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    total = query.count()
    rows = (
        query.order_by(
            FeedbackCluster.priority_score.desc(), FeedbackCluster.id.asc()
        )
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return rows, total


def create_cluster(db: Session, row: FeedbackCluster):
    db.add(row); db.flush(); db.refresh(row); return row


def get_batch(db: Session, batch_id: int):
    return db.query(ActiveLearningBatch).filter(ActiveLearningBatch.id == int(batch_id)).first()


def find_batch(db: Session, project_id: int | None, batch_key: str, scope_key: str | None = None):
    scope = scope_key or (f"project:{int(project_id)}" if project_id is not None else "global")
    return db.query(ActiveLearningBatch).filter_by(scope_key=scope, batch_key=batch_key).first()


def create_batch(db: Session, row: ActiveLearningBatch):
    db.add(row); db.flush(); db.refresh(row); return row


def add_item(db: Session, row: ActiveLearningBatchItem):
    db.add(row); db.flush(); return row


def list_items(db: Session, batch_id: int):
    return db.query(ActiveLearningBatchItem).filter_by(batch_id=int(batch_id)).order_by(ActiveLearningBatchItem.rank.asc()).all()
