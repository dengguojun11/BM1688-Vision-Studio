"""Phase-one API gates for retired control-plane capabilities."""

from __future__ import annotations

from fastapi import Depends

from labelu.internal.common.phase_one_contract import (
    ensure_legacy_control_plane_retired,
)
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User


def ensure_legacy_control_plane_open() -> None:
    """Fail before a retired endpoint can call a write service."""
    ensure_legacy_control_plane_retired()


def require_legacy_control_plane_write(
    current_user: User = Depends(require_system_admin),
) -> User:
    """Authenticate administrative callers before applying the retirement gate."""
    ensure_legacy_control_plane_open()
    return current_user


def hide_router_from_openapi(router) -> None:
    """Keep compatibility addresses callable but outside the phase-one contract."""
    for route in router.routes:
        route.include_in_schema = False
