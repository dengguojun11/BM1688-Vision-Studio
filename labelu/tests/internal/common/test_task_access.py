"""任务访问控制回归测试。"""
import json

from labelu.internal.common.task_access import (
    user_has_task_access,
    user_has_task_member_access,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def test_no_created_by_denies_regular_user(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "admin@example.com",
    )
    task = Task(id=1, created_by=None)
    user = User(id=2, username="test@example.com")
    assert user_has_task_access(user, task) is False


def test_no_created_by_allows_superuser(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "admin@example.com",
    )
    task = Task(id=1, created_by=None)
    user = User(id=1, username="admin@example.com")
    assert user_has_task_access(user, task) is True


def test_builtin_admin_is_superuser_when_env_empty(monkeypatch):
    from labelu.internal.common.task_access import is_task_superuser

    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "",
    )
    user = User(id=1, username="admin@123.com")
    assert is_task_superuser(user) is True


def test_third_party_task_is_shared_with_regular_users():
    task = Task(id=3, created_by=99, source_type="third_party")
    user = User(id=2, username="regular@example.com")

    assert user_has_task_access(user, task) is True
    assert user_has_task_member_access(user, task) is True


def test_consistent_pool_rejects_configured_superuser_and_allows_builtin_admin(
    monkeypatch,
):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "other@admin.com",
    )
    task = Task(
        id=2,
        created_by=2,
        source_type="consistent_pool",
        config=json.dumps(
            {"consistent_pool": {"kind": "train_filtered_consistent"}}
        ),
    )
    configured_superuser = User(id=2, username="other@admin.com")
    builtin_admin = User(id=1, username="admin@123.com")

    assert user_has_task_access(configured_superuser, task) is False
    assert user_has_task_access(builtin_admin, task) is True
