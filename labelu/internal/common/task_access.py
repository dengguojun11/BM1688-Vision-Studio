"""任务访问控制：第三方任务全员共享，其余任务按成员和超管授权。"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_, true

from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User


_CONSISTENT_POOL_SOURCE_TYPE = "consistent_pool"
_CONSISTENT_POOL_KIND = "train_filtered_consistent"
_THIRD_PARTY_SOURCE_TYPE = "third_party"


def is_consistent_pool_admin(user: User | None) -> bool:
    """Only the built-in administrator may access internal consistent pools."""
    if not user or not getattr(user, "username", None):
        return False
    return (
        str(user.username).strip().lower()
        == DEFAULT_ADMIN_USERNAME.strip().lower()
    )


def is_admin_only_consistent_pool(task: Task | None) -> bool:
    """Return whether a task is an internal, administrator-only consistent pool."""
    if not task:
        return False
    if str(getattr(task, "source_type", "") or "").strip() == _CONSISTENT_POOL_SOURCE_TYPE:
        return True
    raw = getattr(task, "config", None)
    if not raw:
        return False
    try:
        import json

        config = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    if not isinstance(config, dict):
        return False
    marker = config.get("consistent_pool")
    return isinstance(marker, dict) and marker.get("kind") == _CONSISTENT_POOL_KIND


def is_third_party_task(task: Task | None) -> bool:
    """Return whether a task is intentionally shared with all task users."""
    return bool(
        task
        and str(getattr(task, "source_type", "") or "").strip().lower()
        == _THIRD_PARTY_SOURCE_TYPE
    )


def task_visibility_clause(
    *,
    user_id: int,
    unrestricted: bool = False,
    builtin_admin: bool = False,
):
    """Build the SQL visibility predicate shared by task-owned projections.

    ``is_task_superuser`` is intentionally not enough for the administrator-only
    consistent pool: configured service/superuser identities must not bypass
    that boundary.  ``builtin_admin`` is therefore supplied separately by the
    service layer after checking :func:`is_consistent_pool_admin`.

    The legacy marker predicate mirrors ``crud_task.visible_query`` so rows
    created before ``source_type`` was backfilled remain protected.
    """
    pool_allowed = and_(
        or_(
            Task.source_type.is_(None),
            Task.source_type != _CONSISTENT_POOL_SOURCE_TYPE,
        ),
        or_(
            Task.config.is_(None),
            ~Task.config.contains(_CONSISTENT_POOL_KIND),
        ),
    )
    # The built-in administrator is the sole identity allowed to inspect the
    # internal consistent-pool lineage.  Configured service/superuser
    # identities deliberately remain below this boundary and use
    # ``pool_allowed`` instead.
    if builtin_admin:
        return true()

    collaborator_exists = exists().where(
        TaskCollaborator.task_id == Task.id,
        TaskCollaborator.user_id == int(user_id),
    )
    task_member_or_shared = or_(
        Task.created_by == int(user_id),
        collaborator_exists,
        # Third-party tasks are shared with all authenticated task users.
        Task.source_type == _THIRD_PARTY_SOURCE_TYPE,
    )
    if unrestricted:
        return pool_allowed
    return and_(pool_allowed, task_member_or_shared)


def _superuser_username_set() -> frozenset[str]:
    raw = (settings.TASK_SUPERUSER_USERNAMES or "").strip()
    if not raw:
        return frozenset()
    # 与登录名比较时统一小写，避免 Admin@123.com 与 admin@123.com 不一致
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def is_task_superuser(user: User | None) -> bool:
    if not user or not getattr(user, "username", None):
        return False
    # Non-interactive service identities need cross-task access to execute the
    # contract workflow, but cannot use that privilege to obtain a login token.
    if bool(getattr(user, "is_service_account", False)) and not bool(
        getattr(user, "interactive_login_enabled", True)
    ):
        return True
    uname = str(user.username).strip().lower()
    if not uname:
        return False
    if uname in _superuser_username_set():
        return True
    # 内置默认管理员：TASK_SUPERUSER_USERNAMES 被清空或未正确加载 .env 时仍保留超管能力
    return uname == DEFAULT_ADMIN_USERNAME.strip().lower()


def user_has_task_access(user: User | None, task: Task | None) -> bool:
    """是否可对任务做常规操作（查看、标注、上传、YOLO、协同配置等）。"""
    if not task or not user:
        return False
    if is_admin_only_consistent_pool(task):
        return is_consistent_pool_admin(user)
    if is_task_superuser(user):
        return True
    if is_third_party_task(task):
        return True
    # 无有效创建者（NULL 或历史占位 0）：仅任务超级用户可访问（避免 YOLO/标注数据对全员开放）。
    if not task.created_by:
        return is_task_superuser(user)
    if task.created_by == user.id:
        return True
    collab_ids = {c.id for c in (task.collaborators or [])}
    return user.id in collab_ids


def user_is_task_owner_or_superuser(user: User | None, task: Task | None) -> bool:
    """是否可执行「仅创建者」类操作：删任务、增删协同成员等。"""
    if not task or not user:
        return False
    if is_admin_only_consistent_pool(task):
        return is_consistent_pool_admin(user)
    if is_task_superuser(user):
        return True
    if not task.created_by:
        return False
    return task.created_by == user.id


def user_has_task_member_access(user: User | None, task: Task | None) -> bool:
    """创建者、协同或超级用户；无有效创建者（0/NULL）时拒绝（用于删附件等敏感操作）。"""
    if not task or not user:
        return False
    if is_admin_only_consistent_pool(task):
        return is_consistent_pool_admin(user)
    if is_task_superuser(user):
        return True
    if is_third_party_task(task):
        return True
    if not task.created_by:
        return False
    if task.created_by == user.id:
        return True
    collab_ids = {c.id for c in (task.collaborators or [])}
    return user.id in collab_ids


def assert_user_has_task_member_access(task: Task, user: User) -> None:
    from fastapi import status

    from labelu.internal.common.error_code import ErrorCode, LabelUException

    if user_has_task_member_access(user, task):
        return
    raise LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_403_FORBIDDEN,
    )


def assert_user_has_task_access(task: Task, user: User) -> None:
    from fastapi import status

    from labelu.internal.common.error_code import ErrorCode, LabelUException

    if user_has_task_access(user, task):
        return
    raise LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_403_FORBIDDEN,
    )


def assert_user_is_task_owner_or_superuser(task: Task, user: User) -> None:
    from fastapi import status

    from labelu.internal.common.error_code import ErrorCode, LabelUException

    if user_is_task_owner_or_superuser(user, task):
        return
    raise LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_403_FORBIDDEN,
    )
