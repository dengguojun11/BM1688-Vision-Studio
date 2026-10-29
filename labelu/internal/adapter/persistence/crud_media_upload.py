from sqlalchemy.orm import Session

from labelu.internal.domain.models.media_upload import MediaUploadSession


def create(db: Session, row: MediaUploadSession) -> MediaUploadSession:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, session_id: str) -> MediaUploadSession | None:
    return db.query(MediaUploadSession).filter(MediaUploadSession.session_id == session_id).first()


def get_completed(db: Session, session_id: str) -> MediaUploadSession | None:
    return (
        db.query(MediaUploadSession)
        .filter(
            MediaUploadSession.session_id == session_id,
            MediaUploadSession.status.in_(("COMPLETED", "DUPLICATE")),
        )
        .first()
    )
