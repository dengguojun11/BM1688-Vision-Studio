"""Unauthenticated service health and version probes."""

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from labelu.version import version as package_version
from labelu.internal.application.response.base import OkResp
from labelu.internal.common.db import SessionLocal


router = APIRouter(tags=["system"])


def _revision() -> str:
    """Read a build revision without invoking git in the request path."""
    for name in (
        "SEVEN_GAI_GIT_REVISION",
        "GIT_REVISION",
        "GIT_COMMIT",
        "GIT_SHA",
    ):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return "unknown"


def _payload(*, state: str, checks: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "service": "7GAI Evolution Platform",
        "status": state,
        "version": package_version,
        "git_revision": _revision(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks or {},
    }


def _check_database() -> dict[str, str]:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - exact driver exception varies
        return {"status": "failed", "detail": type(exc).__name__}
    finally:
        db.close()


@router.get("/healthz", response_model=OkResp[dict[str, Any]], include_in_schema=False)
def healthz() -> OkResp[dict[str, Any]]:
    """Liveness probe; deliberately independent of external dependencies."""
    return OkResp[dict[str, Any]](data=_payload(state="ok"))


@router.get("/readyz", response_model=OkResp[dict[str, Any]], include_in_schema=False)
def readyz(response: Response) -> OkResp[dict[str, Any]]:
    """Readiness probe with a minimal DB check.

    Optional services are reported as degraded until their dedicated checks are
    configured. They do not make the platform unavailable, so a missing docs,
    queue, or object-storage integration cannot break the readiness endpoint.
    """
    database = _check_database()
    external = {
        "status": "degraded",
        "detail": "external dependency checks are not configured",
    }
    state = "ready" if database["status"] == "ok" else "not_ready"
    if state != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return OkResp[dict[str, Any]](
        data=_payload(
            state=state,
            checks={"database": database, "external_dependencies": external},
        )
    )


@router.get("/version", response_model=OkResp[dict[str, Any]], include_in_schema=False)
def version() -> OkResp[dict[str, Any]]:
    """Return build and compatibility information for operators."""
    return OkResp[dict[str, Any]](
        data={
            "product": "7GAI Evolution Platform",
            "legacy_product": "LabelU",
            "version": package_version,
            "git_revision": _revision(),
        }
    )
