"""Ensure a default administrator account exists (idempotent)."""

from loguru import logger
from sqlalchemy.exc import IntegrityError

from labelu.internal.adapter.persistence import crud_user
from labelu.internal.common.db import SessionLocal, begin_transaction
from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.user import User

# 须为邮箱形态：内置前端（LabelU-Kit）在校验用户名时使用邮箱规则，纯 "admin" 无法通过前端校验。
DEFAULT_ADMIN_USERNAME = "admin@123.com"
DEFAULT_ADMIN_PASSWORD = "Admin@123"

_PREVIOUS_DEFAULT_EMAILS = (
    "admin@l123.com",
    "admin@labelu.local",
)
_LEGACY_PLAIN_ADMIN = "admin"


def ensure_default_admin() -> None:
    db = SessionLocal()
    try:
        if crud_user.get_user_by_username(db, username=DEFAULT_ADMIN_USERNAME):
            return

        for old_email in _PREVIOUS_DEFAULT_EMAILS:
            previous_email = crud_user.get_user_by_username(db, username=old_email)
            if previous_email:
                with begin_transaction(db):
                    previous_email.username = DEFAULT_ADMIN_USERNAME
                    db.add(previous_email)
                logger.info(
                    "Renamed default login {old!r} -> {new!r}.",
                    old=old_email,
                    new=DEFAULT_ADMIN_USERNAME,
                )
                return

        legacy = crud_user.get_user_by_username(db, username=_LEGACY_PLAIN_ADMIN)
        if legacy:
            with begin_transaction(db):
                legacy.username = DEFAULT_ADMIN_USERNAME
                db.add(legacy)
            logger.info(
                "Renamed legacy login {old!r} -> {new!r} (frontend requires email-shaped username).",
                old=_LEGACY_PLAIN_ADMIN,
                new=DEFAULT_ADMIN_USERNAME,
            )
            return

        with begin_transaction(db):
            try:
                crud_user.create(
                    db,
                    User(
                        username=DEFAULT_ADMIN_USERNAME,
                        hashed_password=get_password_hash(DEFAULT_ADMIN_PASSWORD),
                        account_type="human",
                        is_service_account=False,
                        interactive_login_enabled=True,
                    ),
                )
            except IntegrityError:
                db.rollback()
                if crud_user.get_user_by_username(db, username=DEFAULT_ADMIN_USERNAME):
                    logger.info(
                        "Default admin user {user!r} already exists; skip duplicate create.",
                        user=DEFAULT_ADMIN_USERNAME,
                    )
                    return
                raise
        logger.info(
            "Created default admin user {user!r}; change the password after first login in production.",
            user=DEFAULT_ADMIN_USERNAME,
        )
    finally:
        db.close()
