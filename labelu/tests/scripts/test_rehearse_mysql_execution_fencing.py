import json
import re

import pytest

from labelu.scripts.rehearse_mysql_execution_fencing import (
    _new_table_name,
    _validate_contenders,
    main,
    validate_mysql_sandbox_url,
)


def _url(database="labelu_migration_sandbox_test"):
    return f"mysql+pymysql://user:password@127.0.0.1:3306/{database}"


def test_validate_requires_mysql_and_sandbox_boundary():
    with pytest.raises(ValueError, match="只接受 MySQL"):
        validate_mysql_sandbox_url("sqlite:///tmp/rehearsal.sqlite", allow_write=False)
    with pytest.raises(ValueError, match="数据库名必须"):
        validate_mysql_sandbox_url(_url("labelu"), allow_write=False)
    with pytest.raises(ValueError, match="本机主机"):
        validate_mysql_sandbox_url(
            "mysql+pymysql://user:password@192.0.2.10/labelu_migration_sandbox_test",
            allow_write=False,
        )


def test_rehearsal_table_names_are_unique_and_sql_identifier_safe():
    names = {_new_table_name() for _ in range(100)}
    assert len(names) == 100
    assert all(
        re.fullmatch(r"labelu_execution_fencing_rehearsal_[0-9a-f]{12}", name)
        for name in names
    )


def test_concurrency_contract_requires_at_least_two_and_caps_process_count():
    assert _validate_contenders(2) == 2
    assert _validate_contenders(20) == 20
    assert _validate_contenders(64) == 64
    with pytest.raises(ValueError, match="并发数必须"):
        _validate_contenders(1)
    with pytest.raises(ValueError, match="并发数必须"):
        _validate_contenders(65)


def test_write_requires_explicit_environment_confirmation(monkeypatch):
    monkeypatch.delenv("LABELU_MYSQL_REHEARSAL_CONFIRM", raising=False)
    with pytest.raises(ValueError, match="LABELU_MYSQL_REHEARSAL_CONFIRM"):
        validate_mysql_sandbox_url(_url(), allow_write=True)
    monkeypatch.setenv("LABELU_MYSQL_REHEARSAL_CONFIRM", "YES")
    result = validate_mysql_sandbox_url(_url(), allow_write=True)
    assert result["write_enabled"] == "是"


def test_main_without_mysql_url_is_blocked_not_simulated(monkeypatch, capsys):
    monkeypatch.delenv("LABELU_MYSQL_REHEARSAL_URL", raising=False)
    assert main([]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["状态"] == "阻断"
    assert payload["MySQL通过"] is False
    assert "SQLite 模拟" in payload["原因"]


def test_main_rehearsal_requires_both_write_switches(monkeypatch, capsys):
    monkeypatch.setenv("LABELU_MYSQL_REHEARSAL_URL", _url())
    assert main(["--模式", "演练"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["状态"] == "阻断"
    assert "--允许修改" in payload["原因"]
