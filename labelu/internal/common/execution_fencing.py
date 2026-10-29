"""执行期租约与 fencing token。

调度交接租约只负责把任务交给 worker；本模块约束 worker 在编译、边端验证
期间的实际写入。租约接管时递增 token，旧 worker 即使进程没有退出，也不能
再续租或写入终态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, Type
import threading

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction


class ExecutionFencingUnavailable(RuntimeError):
    """数据库尚未升级到执行期租约字段。"""


class ExecutionFencingLost(RuntimeError):
    """当前 worker 已失去执行期租约，必须停止后续副作用。"""


@dataclass(frozen=True)
class ExecutionLeaseToken:
    model: Type[Any]
    record_id: int
    owner: str
    fencing_token: int
    lease_expires_at: datetime


def _schema_error(exc: SQLAlchemyError) -> bool:
    """只把缺字段错误视为旧库兼容，不吞掉锁等待或连接故障。"""
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "no such column",
            "unknown column",
            "doesn't exist",
            "does not exist",
        )
    )


def _fields(model: Type[Any]) -> tuple[Any, Any, Any]:
    fields = (
        getattr(model, "execution_lease_owner", None),
        getattr(model, "execution_lease_expires_at", None),
        getattr(model, "execution_fencing_token", None),
    )
    if any(field is None for field in fields):
        raise ExecutionFencingUnavailable(
            f"{getattr(model, '__tablename__', model)!r} 缺少执行期租约字段"
        )
    return fields


def claim_execution_lease(
    db: Session,
    model: Type[Any],
    record_id: int,
    *,
    owner: str,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> ExecutionLeaseToken | None:
    """原子领取执行期租约；接管时递增 fencing token。"""
    if not owner.strip():
        raise ValueError("execution lease owner is required")
    if int(lease_seconds) < 1:
        raise ValueError("execution lease seconds must be positive")
    owner_col, expires_col, token_col = _fields(model)
    current = now or datetime.now()
    expires = current + timedelta(seconds=int(lease_seconds))
    try:
        with begin_transaction(db):
            row = (
                db.query(model)
                .filter(model.id == int(record_id))
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            active_other = (
                row.execution_lease_owner
                and row.execution_lease_owner != owner
                and row.execution_lease_expires_at is not None
                and row.execution_lease_expires_at > current
            )
            if active_other:
                return None
            same_active_owner = (
                row.execution_lease_owner == owner
                and row.execution_lease_expires_at is not None
                and row.execution_lease_expires_at > current
            )
            next_token = int(row.execution_fencing_token or 0)
            if not same_active_owner:
                next_token += 1
            setattr(row, owner_col.key, owner)
            setattr(row, expires_col.key, expires)
            setattr(row, token_col.key, next_token)
            db.flush()
            return ExecutionLeaseToken(
                model=model,
                record_id=int(record_id),
                owner=owner,
                fencing_token=next_token,
                lease_expires_at=expires,
            )
    except ExecutionFencingUnavailable:
        raise
    except SQLAlchemyError as exc:
        if not _schema_error(exc):
            raise
        raise ExecutionFencingUnavailable(
            "执行期租约字段尚未进入当前数据库，保留旧兼容路径"
        ) from exc


def renew_execution_lease(
    db: Session,
    token: ExecutionLeaseToken,
    *,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> ExecutionLeaseToken | None:
    """只允许当前 owner、token 且尚未过期的 worker 续租。"""
    if int(lease_seconds) < 1:
        raise ValueError("execution lease seconds must be positive")
    owner_col, expires_col, token_col = _fields(token.model)
    current = now or datetime.now()
    expires = current + timedelta(seconds=int(lease_seconds))
    try:
        with begin_transaction(db):
            updated = (
                db.query(token.model)
                .filter(
                    token.model.id == int(token.record_id),
                    owner_col == token.owner,
                    token_col == int(token.fencing_token),
                    expires_col.isnot(None),
                    expires_col > current,
                )
                .update({expires_col.key: expires}, synchronize_session=False)
            )
            if int(updated or 0) != 1:
                return None
            return ExecutionLeaseToken(
                model=token.model,
                record_id=token.record_id,
                owner=token.owner,
                fencing_token=token.fencing_token,
                lease_expires_at=expires,
            )
    except SQLAlchemyError as exc:
        if not _schema_error(exc):
            raise
        raise ExecutionFencingUnavailable(
            "执行期租约字段尚未进入当前数据库，无法续租"
        ) from exc


def execution_lease_is_current(
    db: Session, token: ExecutionLeaseToken, *, now: datetime | None = None
) -> bool:
    """读取校验当前 token，供副作用和终态写入前调用。"""
    owner_col, expires_col, token_col = _fields(token.model)
    current = now or datetime.now()
    row = (
        db.query(token.model)
        .filter(
            token.model.id == int(token.record_id),
            owner_col == token.owner,
            token_col == int(token.fencing_token),
            expires_col.isnot(None),
            expires_col > current,
        )
        .one_or_none()
    )
    return row is not None


def execution_lease_is_active(record: Any, *, now: datetime | None = None) -> bool:
    """Check a loaded record before legacy code performs a status write.

    This is deliberately a pure in-memory check. Callers that need an atomic
    guarantee must repeat it inside their transaction or use
    ``conditional_execution_update`` with a real ``ExecutionLeaseToken``.
    """
    owner = str(getattr(record, "execution_lease_owner", None) or "").strip()
    expires_at = getattr(record, "execution_lease_expires_at", None)
    if not owner or expires_at is None:
        return False
    try:
        return expires_at > (now or datetime.now())
    except (TypeError, ValueError):
        return False


def conditional_execution_update(
    db: Session,
    token: ExecutionLeaseToken,
    values: Mapping[str, Any],
    *,
    now: datetime | None = None,
    manage_transaction: bool = True,
) -> bool:
    """带 owner/token/未过期条件的状态或结果写入。"""
    if not values:
        return True
    owner_col, expires_col, token_col = _fields(token.model)
    current = now or datetime.now()
    def _update() -> bool:
        updated = (
            db.query(token.model)
            .filter(
                token.model.id == int(token.record_id),
                owner_col == token.owner,
                token_col == int(token.fencing_token),
                expires_col.isnot(None),
                expires_col > current,
            )
            .update(dict(values), synchronize_session=False)
        )
        return int(updated or 0) == 1

    try:
        if manage_transaction:
            with begin_transaction(db):
                return _update()
        return _update()
    except SQLAlchemyError as exc:
        if not _schema_error(exc):
            raise
        raise ExecutionFencingUnavailable(
            "执行期租约字段尚未进入当前数据库，拒绝条件写入"
        ) from exc


def release_execution_lease(
    db: Session, token: ExecutionLeaseToken, *, now: datetime | None = None
) -> bool:
    """只按当前 token 释放；释放后旧 token 因过期不能继续写入。"""
    owner_col, expires_col, token_col = _fields(token.model)
    current = now or datetime.now()
    with begin_transaction(db):
        updated = (
            db.query(token.model)
            .filter(
                token.model.id == int(token.record_id),
                owner_col == token.owner,
                token_col == int(token.fencing_token),
            )
            .update(
                {owner_col.key: None, expires_col.key: current},
                synchronize_session=False,
            )
        )
        return int(updated or 0) == 1


class ExecutionLeaseMonitor:
    """后台长任务的执行期租约监控器。

    ``queue_lease_*`` 只负责派发交接，本监控器只操作 execution_* 字段。
    续租失败会把 ``lost`` 置为真，调用方应在下一处副作用前调用
    :meth:`ensure_current`，避免旧 worker 继续落盘或写终态。
    """

    def __init__(
        self,
        token: ExecutionLeaseToken,
        *,
        session_scope_factory: Callable[[], Any],
        interval_seconds: float = 30.0,
        lease_seconds: int = 900,
        label: str = "execution",
    ) -> None:
        self.token = token
        self._session_scope = session_scope_factory
        self._interval = max(0.05, float(interval_seconds))
        self._lease_seconds = max(1, int(lease_seconds))
        self._label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._lost = False
        self._last_error: Exception | None = None

    @property
    def lost(self) -> bool:
        with self._lock:
            return self._lost

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    def _mark_lost(self, exc: Exception | None = None) -> None:
        with self._lock:
            self._lost = True
            if exc is not None:
                self._last_error = exc

    def ensure_current(self) -> None:
        if self.lost:
            raise ExecutionFencingLost(f"{self._label} 执行租约已失效")
        try:
            with self._session_scope() as db:
                current = execution_lease_is_current(db, self.token)
        except ExecutionFencingUnavailable:
            raise
        except Exception as exc:
            self._mark_lost(exc)
            raise
        if not current:
            self._mark_lost()
            raise ExecutionFencingLost(f"{self._label} 执行租约已被接管或过期")

    def conditional_update(
        self,
        values: Mapping[str, Any],
        *,
        db: Session | None = None,
        manage_transaction: bool = False,
    ) -> None:
        """按当前 fencing token 写入状态。

        传入业务 Session 时不再另开连接。默认加入调用方已有事务；若
        调用方只持有查询产生的读事务、希望本次条件更新独立提交，应显式
        传入 ``manage_transaction=True``。
        """
        self.ensure_current()
        try:
            if db is not None:
                updated = conditional_execution_update(
                    db,
                    self.token,
                    values,
                    manage_transaction=bool(manage_transaction),
                )
            else:
                with self._session_scope() as update_db:
                    updated = conditional_execution_update(
                        update_db, self.token, values
                    )
        except ExecutionFencingUnavailable:
            raise
        except Exception as exc:
            self._mark_lost(exc)
            raise
        if not updated:
            self._mark_lost()
            raise ExecutionFencingLost(f"{self._label} 条件写入被拒绝")

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._session_scope() as db:
                    renewed = renew_execution_lease(
                        db, self.token, lease_seconds=self._lease_seconds
                    )
                if renewed is None:
                    self._mark_lost()
                    return
            except ExecutionFencingUnavailable as exc:
                self._mark_lost(exc)
                return
            except Exception as exc:
                # 数据库异常不能被当成缺字段兼容；先停止副作用，交由调用方记录。
                self._mark_lost(exc)
                return

    def start(self) -> "ExecutionLeaseMonitor":
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=f"{self._label}-execution-lease",
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def __enter__(self) -> "ExecutionLeaseMonitor":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
        try:
            with self._session_scope() as db:
                release_execution_lease(db, self.token)
        except Exception:
            # 释放只是清理动作，不能覆盖真实执行异常。
            pass
