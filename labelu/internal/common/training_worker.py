"""Worker protocol for the 7GAI training pipeline.

The API process creates commands and persists job state.  A worker consumes
the command envelope and reports a result; it must not create a second job
state machine.  This module deliberately contains no database or Docker
dependency so it can be used by local tests, a queue consumer, or a remote
worker implementation.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

TRAINING_PROTOCOL_VERSION = "7gai.training.v1"
TRAINING_STAGES: tuple[str, ...] = (
    "prepare_dataset",
    "train",
    "evaluate",
    "export",
    "validate_runtime",
)


class TrainingWorker(Protocol):
    """Minimal SPI implemented by queue, container, and test workers."""

    worker_id: str

    def execute(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Consume one validated command and return a result envelope."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def validate_command(command: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy a worker command.

    Validation is intentionally strict for routing fields and permissive for
    model-specific parameters.  This lets a new model family add parameters
    without changing the platform orchestration contract.
    """

    if not isinstance(command, Mapping):
        raise TypeError("worker command must be a mapping")
    # Adapter methods return a compatibility envelope around ``command``.
    # Accepting both shapes lets queue consumers use the adapter result
    # directly while the worker SPI remains a small command contract.
    if "command" in command and "command_id" not in command:
        nested = command.get("command")
        if not isinstance(nested, Mapping):
            raise TypeError("worker command.command must be a mapping")
        command = nested
    payload = dict(command)
    version = _non_empty_string(payload.get("protocol_version"), "protocol_version")
    if version != TRAINING_PROTOCOL_VERSION:
        raise ValueError(f"unsupported training protocol: {version}")
    command_id = _non_empty_string(payload.get("command_id"), "command_id")
    adapter_key = _non_empty_string(payload.get("adapter_key"), "adapter_key").lower()
    stage = _non_empty_string(payload.get("stage"), "stage").lower()
    if stage not in TRAINING_STAGES:
        raise ValueError(f"unsupported training stage: {stage}")
    execution_mode = _non_empty_string(
        payload.get("execution_mode"), "execution_mode"
    ).lower()
    request = payload.get("request")
    if not isinstance(request, Mapping):
        raise TypeError("worker command request must be a mapping")
    payload["protocol_version"] = version
    payload["command_id"] = command_id
    payload["adapter_key"] = adapter_key
    payload["stage"] = stage
    payload["execution_mode"] = execution_mode
    # The envelope owns routing mode. Keep the nested adapter request aligned
    # so workers cannot accidentally choose a different execution path.
    normalized_request = dict(request)
    normalized_request["execution_mode"] = execution_mode
    payload["request"] = normalized_request
    if "fencing_token" in payload:
        try:
            fencing_token = int(payload["fencing_token"])
        except (TypeError, ValueError) as exc:
            raise ValueError("fencing_token must be an integer") from exc
        if fencing_token < 1:
            raise ValueError("fencing_token must be positive")
        payload["fencing_token"] = fencing_token
    if "container" in payload:
        container = payload["container"]
        if not isinstance(container, Mapping):
            raise TypeError("worker command container must be a mapping")
        image = _non_empty_string(container.get("image"), "container.image")
        payload["container"] = {**dict(container), "image": image}
    return payload


def result_envelope(
    command: Mapping[str, Any],
    *,
    status: str,
    worker_id: str,
    message: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
    model_lineage: Mapping[str, Any] | None = None,
    progress: int | float | None = None,
) -> dict[str, Any]:
    """Create the stable worker result shape used by API and queue tests."""

    validated = validate_command(command)
    result: dict[str, Any] = {
        "protocol_version": TRAINING_PROTOCOL_VERSION,
        "command_id": validated["command_id"],
        "adapter_key": validated["adapter_key"],
        "stage": validated["stage"],
        "status": _non_empty_string(status, "status").upper(),
        "worker_id": _non_empty_string(worker_id, "worker_id"),
        "metrics": dict(metrics or {}),
        "artifacts": [dict(item) for item in (artifacts or ())],
    }
    if message:
        result["message"] = str(message)
    if progress is not None:
        try:
            normalized_progress = float(progress)
        except (TypeError, ValueError) as exc:
            raise ValueError("progress must be a number") from exc
        if not math.isfinite(normalized_progress) or not 0 <= normalized_progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        result["progress"] = (
            int(normalized_progress)
            if normalized_progress.is_integer()
            else normalized_progress
        )
    # A worker should not have to reconstruct immutable dataset/model
    # provenance.  The dispatcher puts the canonical lineage on the request;
    # preserve it on every result envelope, including retry/dead-letter
    # responses.  An explicit worker value is allowed for adapters that add
    # validated lineage fields at execution time.
    lineage = model_lineage
    if lineage is None:
        request_lineage = validated["request"].get("model_lineage")
        if isinstance(request_lineage, Mapping):
            lineage = request_lineage
    if isinstance(lineage, Mapping):
        result["model_lineage"] = dict(lineage)
    return result


@dataclass(frozen=True)
class PlaceholderTrainingWorker:
    """Side-effect-free worker used until queue/container infrastructure is enabled.

    It validates the exact same command consumed by a real worker and returns
    ``ACCEPTED`` rather than pretending that training completed.
    """

    worker_id: str = "placeholder-worker"

    def execute(self, command: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_command(command)
        return result_envelope(
            validated,
            status="ACCEPTED",
            worker_id=self.worker_id,
            message="command validated; execution delegated to a configured worker",
        )


@dataclass
class ContainerTrainingWorker:
    """Container worker shell with an injectable process runner.

    Production wiring supplies a runner that invokes the approved container
    runtime.  Keeping it injectable makes protocol tests deterministic and
    prevents unit tests from starting Docker or another container runtime.
    """

    worker_id: str = "container-worker"
    runner: Callable[[Sequence[str]], Any] | None = None
    executable: str = "docker"

    def build_argv(self, command: Mapping[str, Any]) -> list[str]:
        validated = validate_command(command)
        container = validated.get("container") or {}
        image = _non_empty_string(container.get("image"), "container.image")
        request_json = json.dumps(
            validated["request"], ensure_ascii=False, sort_keys=True
        )
        return [
            self.executable,
            "run",
            "--rm",
            image,
            "--stage",
            validated["stage"],
            "--command-id",
            validated["command_id"],
            "--request-json",
            request_json,
        ]

    def execute(self, command: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_command(command)
        argv = self.build_argv(validated)
        if self.runner is None:
            return result_envelope(
                validated,
                status="ACCEPTED",
                worker_id=self.worker_id,
                message="container command prepared; runtime runner is not configured",
            )
        raw = self.runner(argv)
        if isinstance(raw, Mapping):
            return result_envelope(
                validated,
                status=str(raw.get("status") or "SUCCEEDED"),
                worker_id=str(raw.get("worker_id") or self.worker_id),
                message=raw.get("message"),
                metrics=raw.get("metrics"),
                artifacts=raw.get("artifacts"),
                model_lineage=raw.get("model_lineage"),
                progress=raw.get("progress"),
            )
        return result_envelope(
            validated,
            status="SUCCEEDED",
            worker_id=self.worker_id,
            message=str(raw) if raw is not None else None,
        )


def make_command(
    *,
    adapter_key: str,
    stage: str,
    request: Mapping[str, Any],
    execution_mode: str = "container",
    command_id: str | None = None,
    container_image: str | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """Build and validate a worker command from adapter input."""

    if not isinstance(request, Mapping):
        raise TypeError("adapter request must be a mapping")
    command: dict[str, Any] = {
        "protocol_version": TRAINING_PROTOCOL_VERSION,
        "command_id": str(command_id or uuid.uuid4()),
        "adapter_key": _non_empty_string(adapter_key, "adapter_key").lower(),
        "execution_mode": _non_empty_string(execution_mode, "execution_mode").lower(),
        "stage": _non_empty_string(stage, "stage").lower(),
        "request": dict(request),
    }
    if container_image is not None:
        command["container"] = {
            "image": _non_empty_string(container_image, "container.image")
        }
    if fencing_token is not None:
        command["fencing_token"] = fencing_token
    return validate_command(command)


__all__ = [
    "TRAINING_PROTOCOL_VERSION",
    "TRAINING_STAGES",
    "TrainingWorker",
    "PlaceholderTrainingWorker",
    "ContainerTrainingWorker",
    "make_command",
    "validate_command",
    "result_envelope",
]
