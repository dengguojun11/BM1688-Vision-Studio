"""一期候选迁移的生产基线闭锁。

空数据库引导会按当前 ORM 建表并直接 stamp 当前 head，因此不能作为生产
``h7i8j9k0l1m2`` 的结构证据。候选链在取得脱敏生产快照前，只允许做离线
结构审计；一旦候选段声明创建生产已知表，必须阻止执行，避免 ``if not exists``
掩盖生产字段、索引和外键差异。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PRODUCTION_BASELINE = "h7i8j9k0l1m2"
KNOWN_ANCESTOR = "b2c3d4e5f6a7"
KNOWN_PRODUCTION_TABLES = frozenset({"data_source"})


@dataclass(frozen=True)
class BaselineGuardReport:
    status: str
    reason: str
    production_baseline: str = PRODUCTION_BASELINE
    snapshot_table_count: int | None = None


def guard_candidate_baseline(
    candidate_tables: Iterable[str],
    *,
    snapshot_tables: Iterable[str] | None = None,
    snapshot_revision: str | None = None,
) -> BaselineGuardReport:
    """返回候选链是否允许进入结构演练。

    ``snapshot_tables`` 是脱敏生产快照的表名清单，不是空库 bootstrap 的结果。
    没有快照、版本不匹配或候选声明创建已知生产表时均失败闭锁。
    """

    if snapshot_tables is None:
        return BaselineGuardReport(
            status="阻塞",
            reason="缺少h7生产脱敏快照表清单；空库bootstrap不能替代生产基线证据",
        )
    if snapshot_revision != PRODUCTION_BASELINE:
        return BaselineGuardReport(
            status="阻塞",
            reason=f"快照版本必须为{PRODUCTION_BASELINE}，实际为{snapshot_revision or '未提供'}",
            snapshot_table_count=len(set(snapshot_tables)),
        )

    snapshot = frozenset(snapshot_tables)
    missing_known = sorted(KNOWN_PRODUCTION_TABLES - snapshot)
    if missing_known:
        return BaselineGuardReport(
            status="阻塞",
            reason=f"快照缺少已由祖先迁移/生产盘点确认的表：{','.join(missing_known)}",
            snapshot_table_count=len(snapshot),
        )
    overlap = sorted(set(candidate_tables) & snapshot)
    if overlap:
        return BaselineGuardReport(
            status="阻塞",
            reason=f"候选段声明创建生产已存在表，必须改为兼容增补并逐表对账：{','.join(overlap)}",
            snapshot_table_count=len(snapshot),
        )
    return BaselineGuardReport(
        status="通过",
        reason="候选表均未出现在h7生产快照，可进入隔离DDL演练",
        snapshot_table_count=len(snapshot),
    )


def assert_known_ancestor_evidence(migration_path: str | Path) -> None:
    """确认本地祖先迁移确实创建了生产已知的 data_source 表。"""

    text = Path(migration_path).read_text(encoding="utf-8")
    if KNOWN_ANCESTOR not in text or '"data_source"' not in text:
        raise RuntimeError(
            f"未找到{KNOWN_ANCESTOR}创建data_source的祖先证据，禁止把表存在性写入演练结论"
        )
