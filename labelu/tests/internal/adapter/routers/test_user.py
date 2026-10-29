from datetime import timedelta

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from labelu.internal.domain.models.user import User
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.common.config import settings
from labelu.internal.common.security import AccessToken
from labelu.internal.common.security import get_password_hash
from labelu.internal.common.security import verify_password
from labelu.internal.common.security import create_access_token
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.service_account import (
    OPEN_SYSTEM_USERNAME,
    ensure_open_system_user,
)

from labelu.tests.conftest import TEST_USERNAME
from labelu.tests.utils.utils import random_username
from labelu.tests.utils.utils import random_lower_string


class TestClassUserRouter:
    def test_user_signup_successful(self, client: TestClient, db: Session):

        # prepare data
        username = random_username()
        password = random_lower_string()
        data = {"username": username, "password": password}

        # run
        r = client.post(f"{settings.API_V1_STR}/users/signup", json=data)

        # check: the account is created with a hashed password.
        assert r.status_code == 201
        payload = r.json()["data"]
        assert payload["username"] == username
        assert payload["id"]
        created = crud_user.get_user_by_username(db, username=username)
        assert created is not None
        assert created.hashed_password != password
        assert verify_password(password, created.hashed_password)

    def test_create_user_existing_username(self, client: TestClient, db: Session):
        # prepare data
        username = random_username()
        password = random_lower_string()
        data = {"username": username, "password": password}
        with begin_transaction(db):
            crud_user.create(
                db=db,
                user=User(username=username, hashed_password=get_password_hash(password)),
            )

        # run
        r = client.post(f"{settings.API_V1_STR}/users/signup", json=data)

        # check: duplicate usernames are rejected.
        assert r.status_code == 400
        assert r.json()["err_code"] == 40001

    def test_user_login_sucessful(self, client: TestClient, db: Session):
        # prepare data
        username = random_username()
        password = random_lower_string()
        data = {"username": username, "password": password}
        with begin_transaction(db):
            crud_user.create(
                db=db,
                user=User(username=username, hashed_password=get_password_hash(password)),
            )

        # run
        r = client.post(f"{settings.API_V1_STR}/users/login", json=data)

        # check
        assert r.status_code == 200
        assert r.json()["data"]["token"].startswith(settings.TOKEN_TYPE)

    def test_user_login_cannot_find_user(self, client: TestClient, db: Session):
        # prepare data
        username = random_username()
        password = random_lower_string()
        data = {"username": username, "password": password}

        # run
        r = client.post(f"{settings.API_V1_STR}/users/login", json=data)

        # check
        user = r.json()
        assert r.status_code == 401
        assert user["err_code"] == 40000

    def test_open_system_service_account_cannot_login(
        self, client: TestClient, db: Session
    ) -> None:
        user = ensure_open_system_user(db)
        assert user.username == OPEN_SYSTEM_USERNAME
        assert user.is_service_account is True
        assert user.interactive_login_enabled is False

        response = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": OPEN_SYSTEM_USERNAME, "password": "any-password"},
        )

        assert response.status_code == 401
        assert response.json()["err_code"] == 40000

    def test_user_logout(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ):

        # run
        r = client.post(
            f"{settings.API_V1_STR}/users/logout", headers=testuser_token_headers
        )

        # check
        assert r.status_code == 200

    def test_user_token_error(self, client: TestClient, db: Session) -> None:

        # prepare data
        headers = {"Authorization": "Bearer token"}
        # run
        r = client.post(f"{settings.API_V1_STR}/users/logout", headers=headers)

        # check
        assert r.status_code == 401
        assert r.json()["err_code"] == 40003

    def test_cannot_not_found_token(self, client: TestClient, db: Session) -> None:

        # run
        r = client.post(f"{settings.API_V1_STR}/users/logout")

        # check（缺省 Authorization：不同版本可能 401/403，err_code 可能为 30003 或 40004）
        assert r.status_code in (401, 403)
        assert r.json()["err_code"] in (30003, 40004)

    def test_user_not_found(self, client: TestClient, db: Session) -> None:

        # prepare data
        username = random_username()
        access_token_expires = timedelta(minutes=settings.TOKEN_ACCESS_EXPIRE_MINUTES)
        access_token = create_access_token(
            token=AccessToken(id=-1, username=username),
            expires_delta=access_token_expires,
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        # run
        r = client.post(f"{settings.API_V1_STR}/users/logout", headers=headers)

        # check
        assert r.status_code == 401
        assert r.json()["err_code"] == 40002

    def test_get_me_includes_is_task_superuser(
        self, client: TestClient, testuser_token_headers: dict, monkeypatch
    ):
        monkeypatch.setattr(
            "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
            "test@example.com",
        )
        r = client.get(f"{settings.API_V1_STR}/users/me", headers=testuser_token_headers)
        assert r.status_code == 200
        body = r.json()["data"]
        assert "is_task_superuser" in body
        assert body["is_task_superuser"] is True
        assert body["username"] == TEST_USERNAME

    def test_user_list_exposes_the_same_role_as_me(
        self, client: TestClient, testuser_token_headers: dict, monkeypatch
    ):
        monkeypatch.setattr(
            "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
            "test@example.com",
        )
        response = client.get(
            f"{settings.API_V1_STR}/users?page=0&size=100",
            headers=testuser_token_headers,
        )
        assert response.status_code == 200
        listed = next(
            item for item in response.json()["data"] if item["username"] == TEST_USERNAME
        )
        assert listed["is_task_superuser"] is True

    def test_about_reports_superuser_field(self, client: TestClient):
        r = client.get(f"{settings.API_V1_STR}/users/about")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["user_me_has_superuser_field"] is True
        assert data["version"]
        assert "frontend_version" in data
        assert data["frontend_version"]
