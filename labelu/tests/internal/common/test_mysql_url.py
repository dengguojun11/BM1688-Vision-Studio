from labelu.internal.common.mysql_url import (
    normalize_mysql_database_url,
    parse_mysql_database_url,
    pymysql_connect_kwargs,
)


def test_parse_password_with_at_sign():
    url = "mysql+pymysql://root:example@2026@127.0.0.1:3306/labelu"
    parsed = parse_mysql_database_url(url)
    assert parsed is not None
    assert parsed.username == "root"
    assert parsed.password == "example@2026"
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 3306
    assert parsed.database == "labelu"


def test_normalize_encodes_password_and_localhost():
    url = "mysql+pymysql://labelu:pass@word@localhost/labelu"
    out = normalize_mysql_database_url(url)
    assert out == "mysql+pymysql://labelu:pass%40word@127.0.0.1/labelu"
    parsed = parse_mysql_database_url(out)
    assert parsed.password == "pass@word"


def test_pymysql_connect_kwargs():
    url = "mysql+pymysql://root:example%402026@127.0.0.1:3306/labelu"
    kw = pymysql_connect_kwargs(url)
    assert kw["user"] == "root"
    assert kw["password"] == "example@2026"
    assert kw["host"] == "127.0.0.1"
    assert kw["port"] == 3306
