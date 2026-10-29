import asyncio
import json

from fastapi import status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import _is_spa_navigation_request
from labelu.internal.common.error_code import http_exception_handler
from labelu.internal.common.error_code import sqlalchemy_exception_handler
from labelu.internal.common.error_code import unexpected_exception_handler
from labelu.internal.common.error_code import validation_exception_handler


def _make_request(path: str = "/api/v1/test", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers or [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "root_path": "",
        }
    )


def _json_body(response) -> dict:
    return json.loads(response.body.decode())


def test_http_exception_handler_forbidden_uses_not_authenticated_err_code():
    request = _make_request()
    exc = StarletteHTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authenticated",
    )

    response = asyncio.run(http_exception_handler(request, exc))
    data = _json_body(response)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert data["err_code"] == ErrorCode.CODE_40004_NOT_AUTHENTICATED.value[0]


def test_http_exception_handler_skips_spa_fallback_in_vite_mode(monkeypatch):
    request = _make_request(
        "/node_modules/.vite/deps/i18next-browser-languagedetector.js",
        headers=[(b"accept", b"*/*")],
    )
    exc = StarletteHTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    monkeypatch.setenv("LABELU_VITE_ENABLED", "1")

    response = asyncio.run(http_exception_handler(request, exc))
    data = _json_body(response)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert data["msg"] == "Not Found"
    assert data["err_code"] == ErrorCode.CODE_30003_CLIENT_ERROR.value[0]


def test_is_spa_navigation_request_requires_document_html():
    html_request = _make_request(
        "/projects/1",
        headers=[
            (b"accept", b"text/html,application/xhtml+xml"),
            (b"sec-fetch-dest", b"document"),
        ],
    )
    asset_request = _make_request(
        "/node_modules/.vite/deps/i18next-browser-languagedetector.js",
        headers=[(b"accept", b"*/*")],
    )

    assert _is_spa_navigation_request(html_request) is True
    assert _is_spa_navigation_request(asset_request) is False


def test_validation_exception_handler_returns_structured_errors():
    request = _make_request()
    exc = RequestValidationError(
        [
            {
                "loc": ("body", "username"),
                "msg": "Field required",
                "type": "missing",
                "input": {},
            }
        ]
    )

    response = asyncio.run(validation_exception_handler(request, exc))
    data = _json_body(response)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert data["err_code"] == ErrorCode.CODE_30002_VALIDATION_ERROR.value[0]
    assert data["msg"] == "Field required"
    assert isinstance(data["errors"], list)
    assert data["errors"]


def test_unexpected_exception_handler_hides_internal_details_by_default():
    request = _make_request()
    original = settings.EXPOSE_INTERNAL_ERRORS
    settings.EXPOSE_INTERNAL_ERRORS = False
    try:
        response = asyncio.run(unexpected_exception_handler(request, Exception("sensitive detail")))
    finally:
        settings.EXPOSE_INTERNAL_ERRORS = original

    data = _json_body(response)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert data["err_code"] == ErrorCode.UNEXPECTED_ERROR.value[0]
    assert data["msg"] == ErrorCode.UNEXPECTED_ERROR.value[1]


def test_sqlalchemy_exception_handler_hides_internal_details_by_default():
    request = _make_request()
    original = settings.EXPOSE_INTERNAL_ERRORS
    settings.EXPOSE_INTERNAL_ERRORS = False
    try:
        response = asyncio.run(sqlalchemy_exception_handler(request, SQLAlchemyError("SELECT secret")))
    finally:
        settings.EXPOSE_INTERNAL_ERRORS = original

    data = _json_body(response)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert data["err_code"] == ErrorCode.CODE_30000_SQL_ERROR.value[0]
    assert data["msg"] == ErrorCode.CODE_30000_SQL_ERROR.value[1]
