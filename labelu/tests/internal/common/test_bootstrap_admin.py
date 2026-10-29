from labelu.internal.adapter.persistence import crud_user
from labelu.internal.common.bootstrap_admin import (
    DEFAULT_ADMIN_USERNAME,
    ensure_default_admin,
)


def test_ensure_default_admin_is_idempotent(db) -> None:
    ensure_default_admin()
    ensure_default_admin()

    rows, total = crud_user.list_by(db=db, page=0, size=20, username=DEFAULT_ADMIN_USERNAME)
    assert total == 1
    assert len(rows) == 1
    assert rows[0].username == DEFAULT_ADMIN_USERNAME
