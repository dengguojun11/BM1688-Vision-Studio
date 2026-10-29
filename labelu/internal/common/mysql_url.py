"""MySQL DATABASE_URL 解析与规范化（密码含 @、localhost→127.0.0.1）。"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote


@dataclass(frozen=True)
class ParsedMySQL:
    scheme: str
    username: str
    password: str
    host: str
    port: int | None
    database: str


def _split_mysql_url(url: str) -> tuple[str, str, str] | None:
    u = (url or "").strip()
    if not u.lower().startswith("mysql"):
        return None
    marker = "://"
    idx = u.find(marker)
    if idx < 0:
        return None
    scheme_part = u[: idx + len(marker)]
    rest = u[idx + len(marker) :]
    slash = rest.find("/")
    if slash < 0:
        return scheme_part, rest, ""
    return scheme_part, rest[:slash], rest[slash:]


def parse_mysql_database_url(url: str) -> ParsedMySQL | None:
    """解析 mysql(+driver)://user:password@host:port/db；密码中的 @ 按最右侧 @ 切分 host。"""
    parts = _split_mysql_url(url)
    if not parts:
        return None
    scheme_part, netloc, path = parts
    marker = "://"
    scheme = (
        scheme_part[: -len(marker)]
        if scheme_part.endswith(marker)
        else scheme_part.rstrip(":/")
    )
    if not scheme:
        scheme = "mysql+pymysql"
    if "+" not in scheme:
        scheme = "mysql+pymysql"

    if "@" not in netloc:
        return None
    at = netloc.rfind("@")
    cred = netloc[:at]
    hostport = netloc[at + 1 :]

    if ":" in cred:
        colon = cred.find(":")
        username = unquote(cred[:colon])
        password = unquote(cred[colon + 1 :])
    else:
        username = unquote(cred)
        password = ""

    host = hostport
    port: int | None = None
    if hostport.startswith("["):
        end = hostport.find("]")
        if end > 0:
            host = hostport[1:end]
            tail = hostport[end + 1 :]
            if tail.startswith(":") and tail[1:].isdigit():
                port = int(tail[1:])
    elif ":" in hostport:
        h, p = hostport.rsplit(":", 1)
        if p.isdigit():
            host = h
            port = int(p)

    if host in ("", "localhost"):
        host = "127.0.0.1"

    database = path.lstrip("/").split("?")[0].split("#")[0]

    return ParsedMySQL(
        scheme=scheme,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def build_mysql_database_url(parsed: ParsedMySQL) -> str:
    scheme = parsed.scheme if "+" in (parsed.scheme or "") else "mysql+pymysql"
    host = parsed.host or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    user = quote(parsed.username, safe="")
    password = quote(parsed.password, safe="")
    port_part = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{user}:{password}@{host}{port_part}/{parsed.database}"


def normalize_mysql_database_url(url: str) -> str:
    """URL 编码密码并将 localhost 改为 127.0.0.1，供 SQLAlchemy / deploy 使用。"""
    parsed = parse_mysql_database_url(url)
    if not parsed:
        return (url or "").strip()
    return build_mysql_database_url(parsed)


def pymysql_connect_kwargs(url: str) -> dict:
    parsed = parse_mysql_database_url(url)
    if not parsed:
        raise ValueError("not a mysql DATABASE_URL")
    return {
        "host": parsed.host,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.database,
        "charset": "utf8mb4",
    }
