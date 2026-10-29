from labelu.internal.adapter.persistence import crud_user, crud_yolo_imported_model
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.user import User


def _user(db, username: str) -> User:
    row = crud_user.get_user_by_username(db, username=username)
    if row:
        return row
    with begin_transaction(db):
        return crud_user.create(db, User(username=username, hashed_password="x"))


def test_admin_owned_imported_models_are_available_to_other_users(db):
    admin = _user(db, "admin@123.com")
    owner = _user(db, "owner@example.com")
    other = _user(db, "other@example.com")
    with begin_transaction(db):
        admin_model = crud_yolo_imported_model.create(
            db,
            user_id=admin.id,
            display_name="admin-model",
            category="",
            original_filename="admin.pt",
            weights_rel_path="yolo/imported/admin.pt",
            file_size=1,
        )
        owner_model = crud_yolo_imported_model.create(
            db,
            user_id=owner.id,
            display_name="owner-model",
            category="",
            original_filename="owner.pt",
            weights_rel_path="yolo/imported/owner.pt",
            file_size=1,
        )

    public_owner_ids = crud_yolo_imported_model.public_owner_ids(db)
    rows, total = crud_yolo_imported_model.list_for_user(
        db,
        other.id,
        page=0,
        size=20,
        public_owner_ids=public_owner_ids,
    )

    assert total == 1
    assert [row.id for row in rows] == [admin_model.id]
    assert (
        crud_yolo_imported_model.get_available_for_user(
            db,
            model_id=admin_model.id,
            user_id=other.id,
            public_owner_ids=public_owner_ids,
        )
        is not None
    )
    assert (
        crud_yolo_imported_model.get_available_for_user(
            db,
            model_id=owner_model.id,
            user_id=other.id,
            public_owner_ids=public_owner_ids,
        )
        is None
    )


def test_imported_model_q_search_includes_category(db):
    owner = _user(db, "imported-search-owner@example.com")
    with begin_transaction(db):
        matching = crud_yolo_imported_model.create(
            db,
            user_id=owner.id,
            display_name="road detector",
            category="road-scene",
            original_filename="detector.pt",
            weights_rel_path="yolo/imported/road-search.pt",
            file_size=1,
        )
        crud_yolo_imported_model.create(
            db,
            user_id=owner.id,
            display_name="unrelated",
            category="classification",
            original_filename="other.pt",
            weights_rel_path="yolo/imported/other-search.pt",
            file_size=1,
        )

    rows, total = crud_yolo_imported_model.list_for_user(
        db,
        owner.id,
        search="road-scene",
        page=0,
        size=20,
    )

    assert total == 1
    assert [row.id for row in rows] == [matching.id]
