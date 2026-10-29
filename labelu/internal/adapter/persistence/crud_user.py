from typing import List, Tuple

from typing import List, Tuple

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.user import User


def create(db: Session, user: User) -> User:
    interactive_login_enabled = getattr(user, "interactive_login_enabled", None)
    db_user = User(
        username=user.username,
        hashed_password=user.hashed_password,
        account_type=getattr(user, "account_type", "human") or "human",
        is_service_account=bool(getattr(user, "is_service_account", False)),
        interactive_login_enabled=(
            True
            if interactive_login_enabled is None
            else bool(interactive_login_enabled)
        ),
    )
    db.add(db_user)
    db.flush()
    db.refresh(db_user)
    return db_user


def get(db: Session, id: int) -> User:
    return db.query(User).filter(User.id == id).first()


def get_user_by_username(db: Session, username: str) -> User:
    return db.query(User).filter(User.username == username).first()

def list_by(
    db: Session,
    page: int,
    size: int,
    username: str = None,
    search: str = None,
) -> Tuple[List[User], int]:
    query_filter = []
    
    if username:
        query_filter.append(User.username == str(username).strip())
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query_filter.append(
            or_(
                cast(User.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                User.username.ilike(pattern, escape=LIKE_ESCAPE),
                User.account_type.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    
    query = db.query(User).filter(*query_filter)
    result = (
        query
        .order_by(User.id.desc())
        .offset(offset=page * size)
        .limit(limit=size)
        .all()
    )
    total = query.count()
    return result, total

def list_by_ids(db: Session, ids: List[int]) -> List[User]:
    return db.query(User).filter(User.id.in_(ids)).all()
