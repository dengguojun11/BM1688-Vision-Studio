"""真实 MySQL 多进程执行租约演练（严格沙箱门禁）。

该入口和迁移演练分开：默认只做只读能力探针；只有显式传入
``--模式 演练 --允许修改``，并且 URL 同时满足本机主机和
``labelu_migration_sandbox*`` 库名时，才会在沙箱库创建临时 InnoDB 表。
没有 MySQL、驱动或可连接沙箱时返回“阻断”，绝不把 SQLite 结果写成
MySQL 通过，也不会尝试连接生产数据库。

演练顺序：进程 A 持有行锁，N 个竞争进程同时领取同一行；释放后只允许一个
进程获得递增 token，其他进程因有效租约返回空；随后旧 token 的迟到写入被
拒绝。锁等待耗时和最终行状态均从真实 MySQL 读取并输出 JSON 证据。N 默认为
2，生产放行前必须显式用 ``--并发数 20`` 在同版本 MySQL 沙箱执行。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import secrets
import time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SANDBOX_PREFIX = "labelu_migration_sandbox"
TABLE_NAME_PREFIX = "labelu_execution_fencing_rehearsal"
MIN_CONTENDERS = 2
MAX_CONTENDERS = 64


def _new_table_name() -> str:
    """生成仅含固定前缀和十六进制字符的临时表名，支持并行演练。"""

    return f"{TABLE_NAME_PREFIX}_{secrets.token_hex(6)}"


def validate_mysql_sandbox_url(url: str, *, allow_write: bool) -> dict[str, str]:
    """校验 URL；所有失败都在建连接前抛出，避免误触生产。"""

    if not str(url or "").strip():
        raise ValueError("未提供 MySQL 沙箱 URL")
    parsed = make_url(str(url))
    if parsed.get_backend_name() != "mysql":
        raise ValueError("只接受 MySQL URL；不会用 SQLite 代替真实并发验证")
    host = str(parsed.host or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("MySQL 演练仅允许本机主机，拒绝生产或远程地址")
    database = str(parsed.database or "")
    if not database.startswith(SANDBOX_PREFIX):
        raise ValueError(
            f"数据库名必须以 {SANDBOX_PREFIX} 开头，拒绝连接业务库或生产库"
        )
    if allow_write and os.getenv("LABELU_MYSQL_REHEARSAL_CONFIRM") != "YES":
        raise ValueError(
            "写入演练还需要设置 LABELU_MYSQL_REHEARSAL_CONFIRM=YES；"
            "该确认不会写入页面、日志或作业清单"
        )
    return {
        "backend": "mysql",
        "host": host,
        "database": database,
        "write_enabled": "是" if allow_write else "否",
    }


def _engine(url: str):
    # 连接超时必须有限，能力探针不能卡住 Web/测试进程。
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def probe_mysql(url: str) -> dict[str, Any]:
    """只读探针：不建表、不写行、不执行迁移。"""

    details = validate_mysql_sandbox_url(url, allow_write=False)
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT VERSION() AS version, DATABASE() AS database_name, "
                        "@@transaction_isolation AS isolation_level, "
                        "@@innodb_lock_wait_timeout AS lock_wait_timeout"
                    )
                )
                .mappings()
                .one()
            )
        return {
            "状态": "可用",
            "数据库": details,
            "版本": str(row["version"]),
            "当前库": str(row["database_name"]),
            "事务隔离级别": str(row["isolation_level"]),
            "锁等待超时秒数": int(row["lock_wait_timeout"]),
            "写入": "未执行",
        }
    finally:
        engine.dispose()


def _create_sandbox_table(engine, table_name: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {table_name} ("
                "id BIGINT NOT NULL PRIMARY KEY, "
                "status VARCHAR(32) NOT NULL, "
                "lease_owner VARCHAR(128) NULL, "
                "lease_expires_at DATETIME NULL, "
                "fencing_token BIGINT NOT NULL, "
                "updated_at DATETIME NOT NULL"
                ") ENGINE=InnoDB"
            )
        )
        connection.execute(text(f"DELETE FROM {table_name} WHERE id = 1"))
        connection.execute(
            text(
                f"INSERT INTO {table_name} "
                "(id, status, lease_owner, lease_expires_at, fencing_token, updated_at) "
                "VALUES (1, 'PENDING', NULL, NULL, 0, CURRENT_TIMESTAMP)"
            )
        )


def _locker(url: str, table_name: str, ready, release, result_queue) -> None:
    """进程 A：锁住同一行并制造真实 InnoDB 锁等待。"""

    engine = _engine(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET SESSION innodb_lock_wait_timeout = 15"))
            connection.execute(
                text(f"SELECT id FROM {table_name} WHERE id = 1 FOR UPDATE")
            )
            connection.execute(
                text(
                    f"UPDATE {table_name} SET status='HELD_BY_A', lease_owner='worker-a', "
                    "lease_expires_at = CURRENT_TIMESTAMP - INTERVAL 1 SECOND, "
                    "fencing_token=1, updated_at=CURRENT_TIMESTAMP WHERE id=1"
                )
            )
            ready.set()
            release.wait(timeout=30)
            transaction.commit()
            result_queue.put({"worker": "worker-a", "locked": True})
    except Exception as exc:  # pragma: no cover - only exercised with live MySQL
        result_queue.put({"worker": "worker-a", "error": repr(exc)})
    finally:
        engine.dispose()


def _contender(
    url: str, table_name: str, owner: str, ready, go, query_started, result_queue
) -> None:
    """进程 B/C：在同一行上竞争领取，记录真实锁等待时间。"""

    engine = _engine(url)
    try:
        with engine.connect() as connection:
            ready.set()
            go.wait(timeout=20)
            transaction = connection.begin()
            connection.execute(text("SET SESSION innodb_lock_wait_timeout = 15"))
            started = time.perf_counter()
            query_started.set()
            row = (
                connection.execute(
                    text(
                        f"SELECT lease_owner, lease_expires_at, fencing_token "
                        f"FROM {table_name} WHERE id=1 FOR UPDATE"
                    )
                )
                .mappings()
                .one()
            )
            wait_seconds = round(time.perf_counter() - started, 3)
            active_other = (
                row["lease_owner"]
                and row["lease_owner"] != owner
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"].timestamp() > time.time()
            )
            if active_other:
                transaction.commit()
                result_queue.put(
                    {"worker": owner, "claimed": False, "wait_seconds": wait_seconds}
                )
                return
            next_token = int(row["fencing_token"] or 0) + 1
            connection.execute(
                text(
                    f"UPDATE {table_name} SET status='PROCESSING', lease_owner=:owner, "
                    "lease_expires_at = CURRENT_TIMESTAMP + INTERVAL 60 SECOND, "
                    "fencing_token=:token, updated_at=CURRENT_TIMESTAMP WHERE id=1"
                ),
                {"owner": owner, "token": next_token},
            )
            transaction.commit()
            result_queue.put(
                {
                    "worker": owner,
                    "claimed": True,
                    "fencing_token": next_token,
                    "wait_seconds": wait_seconds,
                }
            )
    except Exception as exc:  # pragma: no cover - only exercised with live MySQL
        result_queue.put({"worker": owner, "error": repr(exc)})
    finally:
        engine.dispose()


def _validate_contenders(value: int) -> int:
    """限制真实进程数，避免误把验收脚本变成无限制压力工具。"""

    count = int(value)
    if count < MIN_CONTENDERS or count > MAX_CONTENDERS:
        raise ValueError(
            f"并发数必须在 {MIN_CONTENDERS} 到 {MAX_CONTENDERS} 之间，"
            "生产标准验收值为 20"
        )
    return count


def run_rehearsal(
    url: str, *, hold_seconds: float = 1.0, concurrency: int = MIN_CONTENDERS
) -> dict[str, Any]:
    """运行真实 MySQL 多进程接管演练；调用前必须通过写入 URL 门禁。"""

    details = validate_mysql_sandbox_url(url, allow_write=True)
    concurrency = _validate_contenders(concurrency)
    engine = _engine(url)
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    locker_ready = context.Event()
    contender_ready = [context.Event() for _ in range(concurrency)]
    contender_query_started = [context.Event() for _ in range(concurrency)]
    go = context.Event()
    release = context.Event()
    table_name = _new_table_name()
    processes = []
    try:
        _create_sandbox_table(engine, table_name)
        locker = context.Process(
            target=_locker,
            args=(url, table_name, locker_ready, release, result_queue),
        )
        contenders = [
            context.Process(
                target=_contender,
                args=(
                    url,
                    table_name,
                    f"worker-{index + 2}",
                    contender_ready[index],
                    go,
                    contender_query_started[index],
                    result_queue,
                ),
            )
            for index in range(concurrency)
        ]
        processes = [locker, *contenders]
        locker.start()
        if not locker_ready.wait(timeout=15):
            raise RuntimeError("worker-a 未能取得 MySQL 行锁")
        for contender in contenders:
            contender.start()
        if not all(event.wait(timeout=15) for event in contender_ready):
            raise RuntimeError("竞争进程未能启动")
        go.set()
        if not all(event.wait(timeout=15) for event in contender_query_started):
            raise RuntimeError("竞争进程未进入行锁等待")
        time.sleep(max(0.1, float(hold_seconds)))
        release.set()
        records = [result_queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=15)
        if any(process.is_alive() for process in processes):
            raise RuntimeError("MySQL 并发演练进程未退出")
        if any("error" in record for record in records):
            raise RuntimeError(json.dumps(records, ensure_ascii=False))

        contenders = [
            record
            for record in records
            if record.get("worker") != "worker-a"
        ]
        winners = [record for record in contenders if record.get("claimed")]
        losers = [record for record in contenders if not record.get("claimed")]
        if len(winners) != 1 or len(losers) != concurrency - 1:
            raise RuntimeError(f"唯一领取断言失败：{records!r}")
        winner = winners[0]
        with engine.begin() as connection:
            stale = connection.execute(
                text(
                    f"UPDATE {table_name} SET status='OLD_WORKER_WRITE' "
                    "WHERE id=1 AND lease_owner='worker-a' AND fencing_token=1"
                )
            ).rowcount
            fresh = connection.execute(
                text(
                    f"UPDATE {table_name} SET status='COMPLETED', updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=1 AND lease_owner=:owner AND fencing_token=:token "
                    "AND lease_expires_at > CURRENT_TIMESTAMP"
                ),
                {"owner": winner["worker"], "token": winner["fencing_token"]},
            ).rowcount
            final = (
                connection.execute(
                    text(
                        f"SELECT status, lease_owner, fencing_token FROM {table_name} WHERE id=1"
                    )
                )
                .mappings()
                .one()
            )
        if stale != 0 or fresh != 1 or final["status"] != "COMPLETED":
            raise RuntimeError(
                f"旧token/新token终态断言失败：stale={stale}, fresh={fresh}, final={dict(final)}"
            )
        return {
            "状态": "通过",
            "数据库": details,
            "演练表": table_name,
            "锁等待": [record for record in contenders],
            "并发数": concurrency,
            "唯一领取": f"通过（{concurrency} 个竞争者仅一个获得新 token）",
            "旧 token=1 写入": "拒绝（重启接管后的迟到写入）",
            "新 token 写入": "通过",
            "最终状态": dict(final),
            "清理": "已删除演练行和表",
        }
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
        try:
            with engine.begin() as connection:
                connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        finally:
            result_queue.close()
            result_queue.join_thread()
            engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实 MySQL 执行租约多进程演练")
    parser.add_argument(
        "--url",
        default=os.getenv("LABELU_MYSQL_REHEARSAL_URL"),
        help="仅允许本机 labelu_migration_sandbox* MySQL URL；也可用环境变量",
    )
    parser.add_argument(
        "--模式",
        choices=("探针", "演练"),
        default="探针",
        help="默认只读探针；演练模式会在沙箱库建表并清理",
    )
    parser.add_argument("--允许修改", action="store_true", help="演练模式的写入授权")
    parser.add_argument("--持锁秒数", type=float, default=1.0)
    parser.add_argument(
        "--并发数",
        type=int,
        default=MIN_CONTENDERS,
        help="真实竞争进程数；生产标准验收值为 20，允许范围 2-64",
    )
    args = parser.parse_args(argv)
    try:
        if not args.url:
            raise ValueError(
                "未配置 LABELU_MYSQL_REHEARSAL_URL；未执行 SQLite 模拟，MySQL 能力为阻断"
            )
        if args.模式 == "演练":
            if not args.允许修改:
                raise ValueError("演练模式必须显式传入 --允许修改")
            report = run_rehearsal(
                args.url,
                hold_seconds=args.持锁秒数,
                concurrency=args.并发数,
            )
        else:
            report = probe_mysql(args.url)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        report = {"状态": "阻断", "原因": str(exc), "MySQL通过": False}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
