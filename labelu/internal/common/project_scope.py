"""Explicit project-boundary checks for derived pipeline entities.

The compatibility period contains entities that only carry a ``task_id`` or
point at another derived row.  This module resolves scope through those
declared foreign-key links only; it never uses owner, name, or other heuristics
to assign a project.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from labelu.internal.domain.models.task import Task


class ProjectScopeError(ValueError):
    """Raised when an explicit parent chain crosses project boundaries."""


def task_project_id(db: Session, task_id: int | None) -> int | None:
    """Return the task's explicit project FK, preserving ``NULL`` as unknown."""

    if task_id is None:
        return None
    row = db.query(Task.project_id).filter(Task.id == int(task_id)).first()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def assert_project_chain(project_ids: Iterable[int | None], *, context: str) -> int | None:
    """Require every known parent to have the same explicit project.

    A chain mixing a known project with an unscoped legacy parent is rejected:
    the missing value cannot be safely inferred.  All-legacy ``NULL`` chains
    remain readable and return ``None``.
    """

    values = [None if value is None else int(value) for value in project_ids]
    known = {value for value in values if value is not None}
    if len(known) > 1 or (known and any(value is None for value in values)):
        raise ProjectScopeError(f"{context} project scope is inconsistent")
    return next(iter(known), None)


def assert_task_project_chain(
    db: Session,
    task_ids: Iterable[int | None],
    *,
    direct_project_ids: Iterable[int | None] = (),
    context: str,
) -> int | None:
    """Validate a chain whose task parents expose the project FK explicitly."""

    task_projects = [task_project_id(db, task_id) for task_id in task_ids]
    return assert_project_chain(
        [*task_projects, *list(direct_project_ids)], context=context
    )

