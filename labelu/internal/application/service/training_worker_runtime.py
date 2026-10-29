"""Safe execution and dispatch primitives for 7GAI training workers.

This module keeps the worker execution contract separate from domain state.
The existing auto-training outbox remains the persistence/retry source of
truth; the command/result helpers below provide the queue bridge for a
resident worker process without introducing a second job state machine.
"""

from __future__ import annotations

import re
import subprocess
import time
import argparse
import json
import sys
import os
import socket
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from sqlalchemy import String, cast, exists, func, or_

from labelu.internal.common.config import settings
from labelu.internal.common.training_worker import (
    ContainerTrainingWorker,
    TrainingWorker,
    result_envelope,
    validate_command,
)

_SENSITIVE = re.compile(
    r"(?i)(authorization|token|password|passwd|secret|api[_-]?key|access[_-]?key)"
    r"([\"']?\s*[=:]\s*)([\"']?)([^\"'\s,;}]+)"
)


def redact_text(value: Any, *, max_chars: int = 8000) -> str:
    """Remove common credentials from worker logs and cap log size."""

    text = str(value or "")
    text = _SENSITIVE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text
    )
    return text[: max(0, int(max_chars))]


@dataclass(frozen=True)
class ContainerRuntimePolicy:
    """Allowlisted, bounded Docker invocation policy.

    The policy is data-only so deployments can construct it from settings and
    tests can assert the complete argv without starting a container runtime.
    """

    allowed_images: tuple[str, ...] = ()
    timeout_seconds: int = 3600
    cpus: float = 1.0
    memory_mb: int = 4096
    pids_limit: int = 512
    tmpfs_mb: int = 512
    network: str = "none"
    user: str = "65532:65532"
    read_only: bool = True

    def validate(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.cpus <= 0:
            raise ValueError("cpus must be positive")
        if self.memory_mb < 128 or self.pids_limit < 1 or self.tmpfs_mb < 16:
            raise ValueError("container resource limits are too small")
        if self.network not in {"none", "host", "bridge"}:
            raise ValueError("unsupported container network mode")
        if not re.fullmatch(r"[0-9]+:[0-9]+", self.user):
            raise ValueError("container user must be uid:gid")

    def allows(self, image: str) -> bool:
        self.validate()
        normalized = str(image or "").strip()
        return bool(normalized and normalized in set(self.allowed_images))


@dataclass
class SafeContainerRunner:
    """Build and optionally execute a restricted container command.

    ``process_runner`` is injectable and receives ``(argv, timeout_seconds)``.
    The default uses ``subprocess.run`` with shell execution disabled.  A
    production deployment should additionally isolate the Docker daemon and
    provide an explicit image allowlist through ``ContainerRuntimePolicy``.
    """

    policy: ContainerRuntimePolicy = field(default_factory=ContainerRuntimePolicy)
    executable: str = "docker"
    process_runner: Callable[[Sequence[str], int], Any] | None = None

    def build_argv(self, command: Mapping[str, Any]) -> list[str]:
        validated = validate_command(command)
        container = validated.get("container") or {}
        image = str(container.get("image") or "").strip()
        if not image:
            raise ValueError("container.image is required")
        if not self.policy.allows(image):
            raise PermissionError(f"container image is not allowlisted: {image}")
        request_json = ContainerTrainingWorker(executable=self.executable).build_argv(
            validated
        )[-1]
        argv = [
            self.executable,
            "run",
            "--rm",
            "--network",
            self.policy.network,
            "--cpus",
            str(self.policy.cpus),
            "--memory",
            f"{self.policy.memory_mb}m",
            "--pids-limit",
            str(self.policy.pids_limit),
            "--user",
            self.policy.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
        ]
        if self.policy.read_only:
            argv.append("--read-only")
        argv.extend(
            [
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,size={self.policy.tmpfs_mb}m",
                image,
                "--stage",
                validated["stage"],
                "--command-id",
                validated["command_id"],
                "--request-json",
                request_json,
            ]
        )
        return argv

    def execute(self, command: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_command(command)
        argv = self.build_argv(validated)
        runner = self.process_runner or self._default_process_runner
        raw = runner(argv, int(self.policy.timeout_seconds))
        if isinstance(raw, Mapping):
            return result_envelope(
                validated,
                status=str(raw.get("status") or "SUCCEEDED"),
                worker_id=str(raw.get("worker_id") or "container-worker"),
                message=redact_text(raw.get("message")),
                metrics=raw.get("metrics"),
                artifacts=raw.get("artifacts"),
            )
        return result_envelope(
            validated,
            status="SUCCEEDED",
            worker_id="container-worker",
            message=redact_text(raw),
        )

    @staticmethod
    def _default_process_runner(argv: Sequence[str], timeout_seconds: int) -> Any:
        completed = subprocess.run(
            list(argv),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
        # A worker may print a structured JSON result; callers can also return
        # a simple string from an injected runner for local integrations.
        output = completed.stdout.strip()
        if not output:
            return None
        try:
            import json

            return json.loads(output)
        except (TypeError, ValueError):
            return redact_text(output)


class FencingTokenChecker(Protocol):
    def __call__(self, token: int) -> bool:
        """Return true while the worker is allowed to report results."""


class WorkerResultReporter(Protocol):
    def report(
        self, result: Mapping[str, Any], *, fencing_token: int | None = None
    ) -> None:
        """Persist or publish one result envelope."""


@dataclass
class MemoryResultReporter:
    """Test/local reporter; production wiring can publish to Outbox/API."""

    results: list[dict[str, Any]] = field(default_factory=list)

    def report(
        self, result: Mapping[str, Any], *, fencing_token: int | None = None
    ) -> None:
        item = dict(result)
        if fencing_token is not None:
            item["fencing_token"] = int(fencing_token)
        self.results.append(item)


@dataclass
class OutboxResultReporter:
    """Persist worker results as idempotent events for the API-side consumer."""

    db: Any
    job_id: int

    def report(
        self, result: Mapping[str, Any], *, fencing_token: int | None = None
    ) -> None:
        from labelu.internal.application.service.auto_training_events import (
            enqueue_auto_training_event,
        )

        payload = dict(result)
        payload["job_id"] = int(self.job_id)
        if fencing_token is not None:
            payload["fencing_token"] = int(fencing_token)
        command_id = str(payload.get("command_id") or "").strip()
        if not command_id:
            raise ValueError("worker result command_id is required")
        enqueue_auto_training_event(
            self.db,
            event_name="training.worker.result.v1",
            aggregate_type="yolo_train_job",
            aggregate_id=str(self.job_id),
            payload=payload,
            idempotency_key=f"training-worker-result:{self.job_id}:{command_id}",
        )


def _command_payload(row: Any) -> tuple[int, dict[str, Any]]:
    """Decode a persisted worker command event without trusting its shape."""
    try:
        payload = json.loads(getattr(row, "payload_json", "{}") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("worker command payload is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("worker command payload must be an object")
    command = payload.get("command")
    if not isinstance(command, Mapping):
        command = payload
    command = dict(command)
    try:
        job_id = int(payload.get("job_id") or command.get("job_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("worker command job_id is required") from exc
    if job_id < 1:
        raise ValueError("worker command job_id must be positive")
    # ``job_id`` is routing metadata and must not leak into model-specific
    # command validation unless the adapter explicitly wants it in request.
    command.pop("job_id", None)
    return job_id, command


def _has_automatic_training_lineage(job: Any) -> bool:
    return bool(
        str(getattr(job, "queue_source", "") or "").strip().upper() == "AUTO"
        or getattr(job, "auto_training_plan_id", None) is not None
        or getattr(job, "auto_training_round_id", None) is not None
    )


def _manual_worker_event_claim_predicate():
    """Exclude automatic jobs without parsing vendor-specific JSON columns."""
    from labelu.internal.domain.models.auto_training import AutoTrainingOutbox
    from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

    automatic_job = exists().where(
        cast(YoloTrainJob.id, String) == AutoTrainingOutbox.aggregate_id,
        or_(
            func.upper(func.coalesce(YoloTrainJob.queue_source, "MANUAL"))
            == "AUTO",
            YoloTrainJob.auto_training_plan_id.isnot(None),
            YoloTrainJob.auto_training_round_id.isnot(None),
        ),
    )
    return ~automatic_job


def _assert_worker_event_allowed(job: Any) -> None:
    if (
        not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
        and _has_automatic_training_lineage(job)
    ):
        raise RuntimeError("automatic training worker event is locked in phase one")


def process_worker_command_event(
    db: Any,
    row: Any,
    *,
    worker: TrainingWorker,
    fencing_check: FencingTokenChecker | None = None,
    retry_policy: WorkerRetryPolicy | None = None,
) -> dict[str, Any] | None:
    """Execute one ``training.worker.command.v1`` event and enqueue its result.

    The result event is written in the same transaction as the command ack.
    If a process dies after execution but before the ack, the command remains
    replayable and the adapter/container must therefore be idempotent by
    ``command_id``.  Existing result events are detected before execution to
    avoid a second run after a successful report.
    """
    job_id, command = _command_payload(row)
    validated = validate_command(command)
    # Verify routing metadata against the persisted legacy job before running
    # a worker.  This prevents a copied/replayed command from crossing project
    # or round boundaries while keeping legacy rows with nullable lineage
    # fields compatible.
    from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

    job = db.query(YoloTrainJob).filter_by(id=job_id).one_or_none()
    if job is None:
        raise ValueError(f"training job not found: {job_id}")
    _assert_worker_event_allowed(job)
    try:
        raw_payload = json.loads(getattr(row, "payload_json", "{}") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("worker command payload is not valid JSON") from exc
    if not isinstance(raw_payload, Mapping):
        raise TypeError("worker command payload must be an object")
    for field, actual in (
        ("project_id", getattr(job, "project_id", None)),
        ("plan_id", getattr(job, "auto_training_plan_id", None)),
        ("round_id", getattr(job, "auto_training_round_id", None)),
    ):
        expected = raw_payload.get(field)
        if expected is None:
            expected = validated.get("request", {}).get(field)
        if expected is None or actual is None:
            continue
        try:
            if int(expected) != int(actual):
                raise ValueError(
                    f"worker command {field} does not match training job {job_id}"
                )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "does not match" in str(exc):
                raise
            raise ValueError(f"worker command {field} must be an integer") from exc
    command_id = validated["command_id"]
    result_key = f"training-worker-result:{job_id}:{command_id}"
    from labelu.internal.domain.models.auto_training import AutoTrainingOutbox

    existing = (
        db.query(AutoTrainingOutbox)
        .filter(AutoTrainingOutbox.idempotency_key == result_key)
        .one_or_none()
    )
    if existing is not None and str(existing.status or "").upper() in {
        "PENDING",
        "PROCESSING",
        "RETRY",
        "PROCESSED",
        "DEAD_LETTER",
    }:
        return None

    reporter = OutboxResultReporter(db=db, job_id=job_id)
    return dispatch_with_retry(
        validated,
        worker=worker,
        reporter=reporter,
        fencing_token=validated.get("fencing_token"),
        fencing_check=fencing_check,
        retry_policy=retry_policy,
    )


@dataclass(frozen=True)
class WorkerOutboxBatchResult:
    """Results for command execution followed by result persistence."""

    commands: Any
    results: Any


def process_worker_outbox_batch(
    db: Any,
    *,
    worker: TrainingWorker,
    owner: str,
    lease_seconds: int = 3600,
    limit: int = 20,
    fencing_check: FencingTokenChecker | None = None,
    retry_policy: WorkerRetryPolicy | None = None,
) -> WorkerOutboxBatchResult:
    """Consume worker commands, then apply result events through the API bridge."""
    from labelu.internal.application.service.auto_training_outbox import (
        process_outbox_batch,
        renew_record_lease,
    )
    from labelu.internal.application.service.auto_training_reconciliation import (
        handle_outbox_event,
    )

    claim_predicates = None
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        claim_predicates = (_manual_worker_event_claim_predicate(),)

    def heartbeat(record_id: int, current_owner: str) -> bool:
        """Renew from a short-lived session; SQLAlchemy sessions are not thread-safe."""
        try:
            from sqlalchemy.orm import sessionmaker

            bind = db.get_bind()
        except (AttributeError, RuntimeError):
            # Lightweight test doubles do not expose a bind and cannot be
            # heartbeated; their lease duration is bounded by the test call.
            return True
        session = sessionmaker(autoflush=False, bind=bind)()
        try:
            from labelu.internal.common.db import begin_transaction

            with begin_transaction(session):
                return renew_record_lease(
                    session,
                    record_id=record_id,
                    owner=current_owner,
                    lease_seconds=lease_seconds,
                )
        finally:
            session.close()

    def handle_worker_result(current_db: Any, row: Any) -> Any:
        from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

        try:
            job_id = int(getattr(row, "aggregate_id", "") or 0)
        except (TypeError, ValueError):
            job_id = 0
        job = (
            current_db.query(YoloTrainJob).filter_by(id=job_id).one_or_none()
            if job_id > 0
            else None
        )
        if job is not None:
            _assert_worker_event_allowed(job)
        return handle_outbox_event(
            current_db,
            row,
            fencing_check=fencing_check,
        )

    lease_heartbeat = heartbeat if hasattr(db, "get_bind") else None
    command_result = process_outbox_batch(
        db,
        owner=owner,
        handler=lambda current_db, row: process_worker_command_event(
            current_db,
            row,
            worker=worker,
            fencing_check=fencing_check,
            retry_policy=retry_policy,
        ),
        lease_seconds=lease_seconds,
        limit=limit,
        event_names=("training.worker.command.v1",),
        claim_predicates=claim_predicates,
        heartbeat=lease_heartbeat,
    )
    result_result = process_outbox_batch(
        db,
        owner=owner,
        handler=handle_worker_result,
        lease_seconds=lease_seconds,
        limit=limit,
        event_names=("training.worker.result.v1",),
        claim_predicates=claim_predicates,
        heartbeat=lease_heartbeat,
    )
    return WorkerOutboxBatchResult(commands=command_result, results=result_result)


def default_worker_owner(prefix: str = "training-worker") -> str:
    host = socket.gethostname() or "unknown-host"
    return f"{prefix}:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def run_worker_outbox_loop(
    db_factory: Callable[[], Any],
    *,
    worker: TrainingWorker,
    owner: str | None = None,
    interval_seconds: float = 5.0,
    lease_seconds: int = 3600,
    limit: int = 20,
    fencing_check: FencingTokenChecker | None = None,
    retry_policy: WorkerRetryPolicy | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run the command/result Outbox consumer as a resident process."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if lease_seconds < 1 or limit < 1:
        raise ValueError("lease_seconds and limit must be positive")
    worker_owner = owner or default_worker_owner()
    should_stop = stop or (lambda: False)
    while not should_stop():
        db = db_factory()
        try:
            process_worker_outbox_batch(
                db,
                worker=worker,
                owner=worker_owner,
                lease_seconds=lease_seconds,
                limit=limit,
                fencing_check=fencing_check,
                retry_policy=retry_policy,
            )
        except Exception:
            # Keep the resident process alive across transient DB/runtime
            # failures.  ``process_outbox_batch`` rolls back per event; this
            # rollback covers failures before it can establish a transaction.
            import logging

            logging.getLogger(__name__).exception(
                "training worker outbox cycle failed owner=%s", worker_owner
            )
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()
        sleep(interval_seconds)


@dataclass(frozen=True)
class WorkerRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0

    def validate(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if (
            self.base_delay_seconds < 0
            or self.max_delay_seconds < self.base_delay_seconds
        ):
            raise ValueError("invalid retry delays")


def dispatch_with_retry(
    command: Mapping[str, Any],
    *,
    worker: TrainingWorker,
    reporter: WorkerResultReporter,
    fencing_token: int | None = None,
    fencing_check: FencingTokenChecker | None = None,
    retry_policy: WorkerRetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute one command, report success, or return retry/DLQ result.

    Fencing is checked before every attempt and immediately before reporting;
    a stale worker therefore cannot publish a late result after leadership
    changes.  The persisted Outbox may still decide when to redeliver a RETRY.
    """

    validated = validate_command(command)
    policy = retry_policy or WorkerRetryPolicy()
    policy.validate()
    if fencing_token is None:
        raw_fencing_token = validated.get("fencing_token")
        fencing_token = (
            int(raw_fencing_token) if raw_fencing_token is not None else None
        )
    last_error = ""
    for attempt in range(1, policy.max_attempts + 1):
        if (
            fencing_token is not None
            and fencing_check is not None
            and not fencing_check(int(fencing_token))
        ):
            return result_envelope(
                validated,
                status="FENCING_REJECTED",
                worker_id=getattr(worker, "worker_id", "worker"),
                message="stale fencing token; result was not reported",
            )
        try:
            result = dict(worker.execute(validated))
            result.setdefault("attempt", attempt)
            if (
                fencing_token is not None
                and fencing_check is not None
                and not fencing_check(int(fencing_token))
            ):
                return result_envelope(
                    validated,
                    status="FENCING_REJECTED",
                    worker_id=getattr(worker, "worker_id", "worker"),
                    message="fencing token expired before result report",
                )
            reporter.report(result, fencing_token=fencing_token)
            return result
        except Exception as exc:  # worker failures are retryable by default
            last_error = redact_text(exc)
            if attempt >= policy.max_attempts:
                return result_envelope(
                    validated,
                    status="DEAD_LETTER",
                    worker_id=getattr(worker, "worker_id", "worker"),
                    message=last_error,
                )
            delay = min(
                policy.base_delay_seconds * (2 ** (attempt - 1)),
                policy.max_delay_seconds,
            )
            if delay:
                sleep(delay)
    # Defensive fallback; the loop always returns.
    return result_envelope(
        validated,
        status="DEAD_LETTER",
        worker_id=getattr(worker, "worker_id", "worker"),
        message=last_error or "worker dispatch failed",
    )


__all__ = [
    "ContainerRuntimePolicy",
    "SafeContainerRunner",
    "WorkerRetryPolicy",
    "WorkerResultReporter",
    "MemoryResultReporter",
    "OutboxResultReporter",
    "process_worker_command_event",
    "WorkerOutboxBatchResult",
    "process_worker_outbox_batch",
    "default_worker_owner",
    "run_worker_outbox_loop",
    "FencingTokenChecker",
    "dispatch_with_retry",
    "redact_text",
]


def main(argv: list[str] | None = None) -> int:
    """Consume one JSON command for a smoke test or a worker sidecar.

    Dry-run is the default.  Real container execution requires both
    ``--execute-container`` and one or more exact ``--allow-image`` values.
    """

    parser = argparse.ArgumentParser(description="7GAI training worker")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--command-json", help="command JSON string")
    source.add_argument("--command-file", help="path to command JSON")
    parser.add_argument("--execute-container", action="store_true")
    parser.add_argument("--allow-image", action="append", default=[])
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--outbox",
        action="store_true",
        help="run as a resident worker consuming training.worker.command.v1",
    )
    parser.add_argument("--once", action="store_true", help="process one Outbox cycle")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=3600)
    parser.add_argument("--batch-limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.outbox:
            if not args.execute_container:
                raise ValueError(
                    "--outbox requires --execute-container; configure an explicit image allowlist"
                )
            worker = SafeContainerRunner(
                policy=ContainerRuntimePolicy(allowed_images=tuple(args.allow_image))
            )
            from labelu.internal.common.db import SessionLocal

            if args.once:
                db = SessionLocal()
                try:
                    result = process_worker_outbox_batch(
                        db,
                        worker=worker,
                        owner=default_worker_owner(),
                        lease_seconds=args.lease_seconds,
                        limit=args.batch_limit,
                        retry_policy=WorkerRetryPolicy(max_attempts=args.max_attempts),
                    )
                finally:
                    db.close()
                print(json.dumps(asdict(result), ensure_ascii=False, default=str))
                return 0 if result.commands.dead_lettered == 0 else 2
            run_worker_outbox_loop(
                SessionLocal,
                worker=worker,
                interval_seconds=args.interval,
                lease_seconds=args.lease_seconds,
                limit=args.batch_limit,
                retry_policy=WorkerRetryPolicy(max_attempts=args.max_attempts),
            )
            return 0
        if args.command_file:
            with open(args.command_file, "r", encoding="utf-8") as handle:
                command = json.load(handle)
        elif args.command_json:
            command = json.loads(args.command_json)
        else:
            command = json.load(sys.stdin)
        if args.execute_container:
            worker: TrainingWorker = SafeContainerRunner(
                policy=ContainerRuntimePolicy(allowed_images=tuple(args.allow_image))
            )
        else:
            from labelu.internal.common.training_worker import PlaceholderTrainingWorker

            worker = PlaceholderTrainingWorker()
        reporter = MemoryResultReporter()
        result = dispatch_with_retry(
            command,
            worker=worker,
            reporter=reporter,
            retry_policy=WorkerRetryPolicy(max_attempts=args.max_attempts),
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result.get("status") in {"ACCEPTED", "SUCCEEDED"} else 2
    except Exception as exc:
        print(
            json.dumps(
                {"status": "INVALID_COMMAND", "message": redact_text(exc)},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":  # pragma: no cover - smoke-tested via wrapper
    raise SystemExit(main())
