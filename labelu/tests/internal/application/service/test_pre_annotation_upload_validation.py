"""Malformed pre-annotation uploads must be rejected as client errors."""

from types import SimpleNamespace

import pytest
from fastapi import status

from labelu.internal.application.service import pre_annotation as service
from labelu.internal.common.error_code import LabelUException


class _BadStorage:
    def exists(self, _path: str) -> bool:
        return True

    def read_text(self, _path: str, encoding: str = "utf-8") -> str:
        return '{"foo": 1}'


def test_malformed_json_upload_returns_400(monkeypatch) -> None:
    monkeypatch.setattr(service, "get_storage_backend", lambda: _BadStorage())
    attachment = SimpleNamespace(path="bad.json", filename="bad.json")

    with pytest.raises(LabelUException) as excinfo:
        service.read_pre_annotation_file(attachment)

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "预标注文件格式不正确" in str(excinfo.value.msg)
