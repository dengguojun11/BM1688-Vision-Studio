"""Wrap FastAPI route handlers with [biz] api.* logging."""
from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from labelu.internal.common.business_log import biz_log, _extract_loggable_kwargs, resolve_route_path_for_log
from labelu.internal.common.config import settings

QUIET_API_ROUTE_PATHS = {"/local-dev/session"}


def should_skip_api_route_logging(path: str) -> bool:
    return path in QUIET_API_ROUTE_PATHS


def _wrap_endpoint(
    endpoint: Callable[..., Any],
    *,
    path: str,
    methods: set[str],
) -> Callable[..., Any]:
    if getattr(endpoint, "__wrapped_api_route__", False):
        return endpoint
    if should_skip_api_route_logging(path):
        return endpoint

    operation = f"api.{getattr(endpoint, '__name__', 'handler')}"
    method = ",".join(sorted(methods)) if methods else "?"

    if inspect.iscoroutinefunction(endpoint):

        @wraps(endpoint)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            fields = _extract_loggable_kwargs(kwargs)
            log_path = resolve_route_path_for_log(path, fields)
            biz_log(f"{operation}.start", method=method, path=log_path, **fields)
            try:
                result = await endpoint(*args, **kwargs)
                biz_log(f"{operation}.done", method=method, path=log_path, **fields)
                return result
            except Exception as exc:
                biz_log(f"{operation}.failed", method=method, path=log_path, error=type(exc).__name__, **fields)
                raise

        async_wrapper.__wrapped_api_route__ = True  # type: ignore[attr-defined]
        return async_wrapper

    @wraps(endpoint)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        fields = _extract_loggable_kwargs(kwargs)
        log_path = resolve_route_path_for_log(path, fields)
        biz_log(f"{operation}.start", method=method, path=log_path, **fields)
        try:
            result = endpoint(*args, **kwargs)
            biz_log(f"{operation}.done", method=method, path=log_path, **fields)
            return result
        except Exception as exc:
            biz_log(f"{operation}.failed", method=method, path=log_path, error=type(exc).__name__, **fields)
            raise

    sync_wrapper.__wrapped_api_route__ = True  # type: ignore[attr-defined]
    return sync_wrapper


def instrument_router(router) -> None:
    """Attach logging wrappers to all APIRoute endpoints on a router."""
    if not settings.LOG_BUSINESS_DETAIL:
        return
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        route.endpoint = _wrap_endpoint(
            route.endpoint,
            path=route.path,
            methods=set(route.methods or []),
        )


def instrument_routes(routes: list[BaseRoute]) -> None:
    for route in routes:
        if isinstance(route, APIRoute):
            route.endpoint = _wrap_endpoint(
                route.endpoint,
                path=route.path,
                methods=set(route.methods or []),
            )
