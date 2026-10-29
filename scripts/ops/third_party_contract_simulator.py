"""Local-only simulator for the strategy-evolution HTTP contract.

The simulator is deliberately a client, not a second implementation of the
LabelU service.  It sends the same JSON a strategy platform would send and
records the responses so the complete local contract can be accepted without
waiting for a real third-party platform or real model-training result.

Safety rules:

* ``--base-url`` must resolve to ``localhost``, ``127.0.0.1`` or ``::1``.
* Credentials are read from environment variables and are never printed.
* Evidence is written below ``.tmp/third-party-simulation`` by default.

Example (the local service token is read from the environment)::

    python scripts/ops/third_party_contract_simulator.py \
        --base-url http://127.0.0.1:8003 \
        --import-s3-uri s3://acity-artifacts/strategy-datasets/.../

The import request is queued asynchronously.  Pass an S3 URI containing real
local acceptance objects when the Worker path should be exercised; without it
the simulator still sends a realistic package and records the accepted queue
response, which is useful for contract-only verification.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_EVIDENCE = Path(".tmp/third-party-simulation")
DEFAULT_SERVICE_TOKEN_ENV = "STRATEGY_EVOLUTION_SERVICE_TOKEN"
DEFAULT_HMAC_SECRET_ENV = "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET"
DEFAULT_S3_ENDPOINT = "http://192.168.54.190:29100"
DEFAULT_S3_BUCKET = "acity-artifacts"
LOCAL_S3_ENDPOINTS = {
    "http://192.168.54.190:29100",
    "http://127.0.0.1:9000",
    "http://localhost:9000",
}
# A tiny valid JPEG keeps the simulator independent of Pillow/OpenCV.
SIMULATOR_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IYf/2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


class SimulatorError(RuntimeError):
    """Raised when the simulated contract cannot be completed safely."""


def _loopback_host(host: str) -> bool:
    """Return whether *host* is a loopback name or address.

    DNS resolution is intentionally avoided.  A hostname which happens to
    resolve to loopback today could resolve to a production address later.
    """

    normalized = str(host or "").strip().lower().rstrip(".")
    if normalized in LOOPBACK_HOSTS:
        return True
    try:
        return bool(normalized) and socket.inet_pton(socket.AF_INET6, normalized) == b"\x00" * 15 + b"\x01"
    except OSError:
        pass
    try:
        return socket.inet_aton(normalized) == b"\x7f\x00\x00\x01"
    except OSError:
        return False


def validate_local_base_url(base_url: str) -> str:
    """Validate and normalize the simulator's target URL.

    This is intentionally stricter than checking private RFC1918 ranges: the
    task is local acceptance and accidentally pointing at a staging or
    production IP must fail before any request is sent.
    """

    parsed = urllib.parse.urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SimulatorError("--base-url 必须是带 http/https 的完整地址")
    if parsed.username or parsed.password:
        raise SimulatorError("--base-url 不允许包含用户名或密码")
    if not _loopback_host(parsed.hostname or ""):
        raise SimulatorError(
            "第三方模拟器仅允许访问 localhost、127.0.0.1 或 ::1；已拒绝非本机地址"
        )
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, path, "", parsed.query, "")
    ).rstrip("/")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"不能序列化值：{type(value).__name__}")


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, Any] | list[Any] | None]:
    """Perform one JSON request and return ``(status, decoded_body)``.

    HTTP errors are returned to the caller rather than hidden in a traceback;
    the evidence file then shows the response body that explains a contract
    failure (for example a deliberate 409 conflict check).
    """

    body = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update({str(k): str(v) for k, v in headers.items()})
    if payload is not None:
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=str(method).upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SimulatorError(f"本地接口无法访问：{url}: {exc}") from exc
    if not raw:
        return status, None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SimulatorError(f"接口返回不是有效 JSON：{url}") from exc
    return status, decoded


def _service_headers(service_token: str) -> dict[str, str]:
    if not service_token.strip():
        raise SimulatorError(
            "未找到本地服务令牌；请配置 STRATEGY_EVOLUTION_SERVICE_TOKEN，"
            "不要在脚本中硬编码令牌"
        )
    return {"X-7GAI-Service-Token": service_token.strip()}


def _path_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_task_payload(external_task_id: str, *, task_type: str, model_id: str = "536") -> dict[str, Any]:
    """Build a realistic strategy-platform task request."""

    if task_type not in {"threshold_experiment", "model_capability"}:
        raise ValueError("task_type 必须为 threshold_experiment 或 model_capability")
    return {
        "schema_version": "v1",
        "external_task_id": external_task_id,
        "task_name": "本地模拟-非机动车误报优化",
        "task_type": task_type,
        "source_package_id": "",
        "target": {
            "device_type": "BM1688",
            "event_type": "illegalParkingofNonMotorVehicle",
            "modelId": model_id,
            "scene_type": "道路监控",
            "strategy_id": "strategy-local-simulation",
        },
        "data_window": {
            "start_time": "2026-08-03 00:00:00",
            "end_time": "2026-08-04 00:00:00",
        },
        "callback_url": None,
        "experiment": {
            "goal": "reduce_false_positive",
            "current_thresholds": {
                "edge_low_threshold": 0.35,
                "edge_high_threshold": 0.60,
                "flink_filter_threshold": 0.45,
            },
            "search_space": {
                "edge_low_threshold": [0.30, 0.35, 0.40],
                "edge_high_threshold": [0.55, 0.60, 0.65],
            },
            "sample_count": 50,
        },
    }


def _s3_parts(s3_uri: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(str(s3_uri or "").strip())
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise SimulatorError("--import-s3-uri 必须是 s3://bucket/prefix/ 格式")
    return parsed.netloc, parsed.path.strip("/").rstrip("/") + "/"


def validate_local_s3_endpoint(endpoint: str) -> str:
    """Allow only the explicitly documented local MinIO endpoints."""

    normalized = str(endpoint or "").strip().rstrip("/")
    if normalized not in LOCAL_S3_ENDPOINTS:
        raise SimulatorError(
            "--seed-local-s3 仅允许本地 MinIO 地址 "
            + "、".join(sorted(LOCAL_S3_ENDPOINTS))
        )
    return normalized


def seed_local_s3_package(
    *,
    package_id: str,
    endpoint: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    sample_count: int,
) -> str:
    """Upload a self-contained 50+ sample package to the local MinIO only."""

    endpoint = validate_local_s3_endpoint(endpoint)
    if not str(bucket or "").strip():
        raise SimulatorError("本地 S3 bucket 不能为空")
    count = max(1, int(sample_count))
    if not access_key.strip() or not secret_key.strip():
        raise SimulatorError("本地 S3 凭据未配置，请通过环境变量提供")
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depends on local runtime
        raise SimulatorError("缺少 boto3，无法执行 --seed-local-s3") from exc

    prefix = f"strategy-datasets/local-simulator/{package_id}/"
    image_prefix = f"{prefix}images/"
    metadata_samples = []
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        verify=False,
    )
    for index in range(1, count + 1):
        file_name = f"sample-{index:04d}.jpg"
        client.put_object(
            Bucket=bucket,
            Key=f"{image_prefix}{file_name}",
            # The trailing marker keeps every object content-hash distinct;
            # JPEG decoders ignore bytes after the EOI marker.
            Body=SIMULATOR_JPEG + f"\nlocal-sim-{index}".encode("ascii"),
            ContentType="image/jpeg",
        )
        metadata_samples.append(
            {
                "image_name": file_name,
                "width": 640,
                "height": 480,
                "target_boxes": [
                    {
                        "class_name": "nonvehicle_tricycle",
                        "x1": 80,
                        "y1": 100,
                        "x2": 360,
                        "y2": 300,
                    }
                ],
            }
        )

    metadata = {
        "schema_version": "v1",
        "samples": metadata_samples,
        "source": "third_party_contract_simulator",
    }
    client.put_object(
        Bucket=bucket,
        Key=f"{prefix}metadata.json",
        Body=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{bucket}/{prefix}"


def make_import_payload(
    package_id: str,
    s3_uri: str,
    *,
    sample_count: int = 50,
    model_id: str = "536",
    dataset_category_key: str = "",
) -> dict[str, Any]:
    """Build a realistic S3 package envelope for the asynchronous Worker."""

    bucket, prefix = _s3_parts(s3_uri)
    image_prefix = f"{prefix}images/"
    metadata_key = f"{prefix}metadata.json"
    return {
        "schema_version": "v1",
        "package_id": package_id,
        "source_type": "third_party",
        "source_system": "strategy_evolution",
        "source_external_task_id": "",
        "dataset_type": "reviewed_false_positive_daily",
        "dataset_date": "2026-08-03",
        "window_start": "2026-08-03 00:00:00",
        "window_end": "2026-08-04 00:00:00",
        "dataset": {
            "s3_uri": s3_uri,
            "bucket": bucket,
            "prefix": prefix,
            "image_prefix": image_prefix,
            "image_s3_prefix": f"s3://{bucket}/{image_prefix}",
            "metadata_key": metadata_key,
            "metadata_s3_uri": f"s3://{bucket}/{metadata_key}",
            "metadata_file": "metadata.json",
            "image_directory": "images/",
            "sample_alignment": "samples[].image.file_name matches the object name under dataset.image_prefix",
        },
        "labeling": {
            "required": True,
            "media_type": "IMAGE",
            "annotation_type": "bbox",
            "labels": ["nonvehicle_tricycle"],
            "completeness": "partial",
        },
        "target": {
            "device_type": "BM1688",
            "event_type": "illegalParkingofNonMotorVehicle",
            "modelId": model_id,
            "scene_type": "道路监控",
            "strategy_id": "strategy-local-simulation",
        },
        "sample_count": max(0, int(sample_count)),
        "target_boxes_required": True,
        "dataset_category_key": dataset_category_key,
    }


def make_callback_payload(
    external_task_id: str,
    *,
    callback_event_id: str,
    status: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "v1",
        "callback_event_id": callback_event_id,
        "external_task_id": external_task_id,
        "task_type": "threshold_experiment",
        "status": status,
        "progress": {
            "stage": "threshold_search" if status == "running" else "completed",
            "processed_samples": 50 if status == "completed" else 25,
            "total_samples": 50,
        },
    }
    if status == "completed":
        payload.update(
            {
                "finished_at": "2026-08-03 12:00:00",
                "result": {
                    "metrics": {
                        "baseline_false_positive_rate": 0.082,
                        "candidate_false_positive_rate": 0.041,
                        "recall": 0.963,
                    },
                    "current_thresholds": {"edge_low_threshold": 0.35},
                    "recommended_thresholds": {"edge_low_threshold": 0.40},
                    "threshold_unit": "ratio_0_1",
                },
            }
        )
    return payload


def make_callback_headers(payload: Mapping[str, Any], secret: str, *, timestamp: int | None = None) -> dict[str, str]:
    """Create headers for the platform callback HMAC contract."""

    if not secret.strip():
        raise ValueError("secret 不能为空")
    event_id = str(payload.get("callback_event_id") or "").strip()
    if not event_id:
        raise ValueError("callback_event_id 不能为空")
    timestamp_text = str(int(time.time()) if timestamp is None else int(timestamp))
    raw_body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    signed_content = b".".join(
        (timestamp_text.encode("utf-8"), event_id.encode("utf-8"), raw_body)
    )
    signature = hmac.new(secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-LabelU-Timestamp": timestamp_text,
        "X-LabelU-Event-Id": event_id,
        "X-LabelU-Signature": f"sha256={signature}",
    }


def _record(
    evidence: dict[str, Any],
    *,
    name: str,
    method: str,
    path: str,
    status: int,
    body: Any,
    expected: list[int],
) -> None:
    evidence["requests"].append(
        {
            "name": name,
            "method": method,
            "path": path,
            "status": status,
            "expected_status": expected,
            "passed": status in expected,
            "response": body,
        }
    )


IMPORT_TERMINAL_STATUSES = {
    "COMPLETED",
    "PARTIAL_COMPLETED",
    "FAILED",
    "WAITING_FOR_SAMPLES",
    "WAITING_FOR_MODEL",
    "WAITING_FOR_CATEGORY",
}


def _poll_import_until_terminal(
    *,
    request: Callable[..., tuple[int, dict[str, Any] | list[Any] | None]],
    base_url: str,
    package_id: str,
    headers: Mapping[str, str],
    timeout: float,
    wait_seconds: float,
    poll_interval: float,
) -> tuple[int, dict[str, Any] | list[Any] | None, list[str]]:
    """Poll the asynchronous import detail endpoint until it reaches a stable state.

    A successful ``202`` only means that the package entered the queue.  This
    helper deliberately follows the status URL returned by the API so the
    evidence can distinguish queue acceptance from an actual Worker result.
    """

    deadline = time.monotonic() + max(0.1, float(wait_seconds))
    interval = max(0.05, float(poll_interval))
    history: list[str] = []
    last_status = 200
    last_body: dict[str, Any] | list[Any] | None = None
    while True:
        last_status, last_body = request(
            _path_url(base_url, f"/internal/v1/experiment-datasets/{package_id}"),
            method="GET",
            payload=None,
            headers=headers,
            timeout=timeout,
        )
        if last_status != 200:
            return last_status, last_body, history
        detail = last_body.get("data") if isinstance(last_body, dict) else None
        current = str(detail.get("status") or "").strip() if isinstance(detail, dict) else ""
        if current:
            if not history or history[-1] != current:
                history.append(current)
            if current in IMPORT_TERMINAL_STATUSES:
                return last_status, last_body, history
        if time.monotonic() >= deadline:
            return last_status, last_body, history
        time.sleep(interval)


def run(
    args: argparse.Namespace,
    *,
    request: Callable[..., tuple[int, dict[str, Any] | list[Any] | None]] = _request_json,
) -> int:
    base_url = validate_local_base_url(args.base_url)
    service_token = os.getenv(args.service_token_env, "")
    headers = _service_headers(service_token)
    run_id = str(args.run_id or f"{int(time.time())}-{uuid.uuid4().hex[:8]}")
    external_task_id = f"local-sim-threshold-{run_id}"
    model_task_id = f"local-sim-model-{run_id}"
    package_id = f"local-sim-package-{run_id}"
    evidence: dict[str, Any] = {
        "simulator": "third_party_contract_simulator",
        "started_at": _now_iso(),
        "base_url": base_url,
        "run_id": run_id,
        "requests": [],
        "checks": {},
    }

    seeded_s3_uri = ""
    if args.seed_local_s3:
        if args.skip_import:
            raise SimulatorError("--seed-local-s3 不能与 --skip-import 同时使用")
        s3_access_key = os.getenv(args.s3_access_key_env, "minioadmin")
        s3_secret_key = os.getenv(args.s3_secret_key_env, "minioadmin")
        seeded_s3_uri = seed_local_s3_package(
            package_id=package_id,
            endpoint=args.s3_endpoint,
            bucket=args.s3_bucket,
            access_key=s3_access_key,
            secret_key=s3_secret_key,
            sample_count=max(50, int(args.sample_count)),
        )
        evidence["seeded_s3"] = {
            "endpoint": validate_local_s3_endpoint(args.s3_endpoint),
            "bucket": args.s3_bucket,
            "s3_uri": seeded_s3_uri,
            "sample_count": max(50, int(args.sample_count)),
        }

    def send(name: str, method: str, path: str, payload: Any, *, expected: list[int], extra_headers: Mapping[str, str] | None = None):
        request_headers = dict(headers)
        if extra_headers:
            request_headers.update(extra_headers)
        status, body = request(
            _path_url(base_url, path),
            method=method,
            payload=payload,
            headers=request_headers,
            timeout=float(args.timeout),
        )
        _record(evidence, name=name, method=method, path=path, status=status, body=body, expected=expected)
        if status not in expected:
            raise SimulatorError(f"{name} 返回 HTTP {status}，期望 {expected}")
        return body

    threshold_path = "/api/v1/strategy-tasks"
    threshold_payload = make_task_payload(
        external_task_id, task_type="threshold_experiment", model_id=args.model_id
    )
    send("official_create_threshold", "POST", threshold_path, threshold_payload, expected=[202])
    send("official_create_threshold_idempotent", "POST", threshold_path, threshold_payload, expected=[202])
    conflict_payload = dict(threshold_payload)
    conflict_payload["task_name"] = "本地模拟-冲突请求"
    send("official_create_conflict", "POST", threshold_path, conflict_payload, expected=[409])

    send(
        "internal_create_threshold",
        "POST",
        "/internal/v1/strategy-evolution/threshold-experiments",
        make_task_payload(
            f"{external_task_id}-internal", task_type="threshold_experiment", model_id=args.model_id
        ),
        expected=[202],
    )
    send(
        "internal_create_model_training",
        "POST",
        "/internal/v1/strategy-evolution/model-trainings",
        make_task_payload(model_task_id, task_type="model_capability", model_id=args.model_id),
        expected=[202],
    )
    send("official_get_threshold", "GET", f"{threshold_path}/{external_task_id}", None, expected=[200])
    send("official_list_tasks", "GET", threshold_path, None, expected=[200])

    legacy_external_task_id = f"local-sim-legacy-{run_id}"
    legacy_s3_uri = seeded_s3_uri or f"s3://local-simulator/strategy-datasets/{legacy_external_task_id}/"
    legacy_payload = {
        "schema_version": "v1",
        "external_task_id": legacy_external_task_id,
        "task_name": "本地模拟-兼容接口任务",
        "task_type": "threshold_experiment",
        "dataset": {
            "s3_uri": legacy_s3_uri,
            "image_prefix": "images/",
            "metadata_key": "metadata.json",
        },
        "labeling": {
            "required": True,
            "media_type": "IMAGE",
            "annotation_type": "bbox",
            "labels": ["nonvehicle_tricycle"],
        },
        "target": {
            "device_type": "BM1688",
            "event_type": "illegalParkingofNonMotorVehicle",
            "model_id": args.model_id,
            "scene_type": "道路监控",
            "strategy_id": "strategy-local-simulation",
        },
        "experiment": threshold_payload["experiment"],
        "source_system": "strategy_evolution",
        "dataset_category_key": args.dataset_category_key,
    }
    legacy_create_response = send(
        "legacy_create_strategy_task",
        "POST",
        "/api/v1/integrations/strategy-tasks",
        legacy_payload,
        expected=[201],
    )
    send("legacy_get_strategy_task", "GET", f"/api/v1/integrations/strategy-tasks/{legacy_external_task_id}", None, expected=[200])
    legacy_package_id = ""
    if isinstance(legacy_create_response, dict):
        legacy_data = legacy_create_response.get("data")
        if isinstance(legacy_data, dict):
            legacy_package_id = str(legacy_data.get("package_id") or "").strip()

    if not args.skip_import:
        s3_uri = seeded_s3_uri or str(args.import_s3_uri or "").strip()
        if not s3_uri:
            # A valid envelope is still valuable when no real S3 package is
            # available.  The Worker will report the missing object in its
            # status, rather than silently claiming that images were imported.
            s3_uri = f"s3://local-simulator/strategy-datasets/{package_id}/"
        import_payload = make_import_payload(
            package_id,
            s3_uri,
            sample_count=args.sample_count,
            model_id=args.model_id,
            dataset_category_key=args.dataset_category_key,
        )
        send("internal_import_dataset", "POST", "/internal/v1/experiment-datasets", import_payload, expected=[202])
        send("internal_get_import", "GET", f"/internal/v1/experiment-datasets/{package_id}", None, expected=[200])
        if bool(getattr(args, "wait_for_import", False)):
            poll_status, poll_body, poll_history = _poll_import_until_terminal(
                request=request,
                base_url=base_url,
                package_id=package_id,
                headers=headers,
                timeout=float(args.timeout),
                wait_seconds=float(getattr(args, "import_wait_seconds", 120.0)),
                poll_interval=float(getattr(args, "import_poll_interval", 2.0)),
            )
            _record(
                evidence,
                name="internal_import_terminal",
                method="GET",
                path=f"/internal/v1/experiment-datasets/{package_id}",
                status=poll_status,
                body=poll_body,
                expected=[200],
            )
            evidence["import_worker"] = {
                "status_history": poll_history,
                "terminal_status": (
                    poll_body.get("data", {}).get("status")
                    if isinstance(poll_body, dict)
                    and isinstance(poll_body.get("data"), dict)
                    else None
                ),
            }
            if poll_status != 200 or not poll_history or poll_history[-1] not in IMPORT_TERMINAL_STATUSES:
                raise SimulatorError(
                    f"导入 Worker 在 {getattr(args, 'import_wait_seconds', 120.0)} 秒内未到达终态"
                )
            if bool(getattr(args, "require_import_completed", False)):
                terminal_status = str(evidence["import_worker"].get("terminal_status") or "")
                if terminal_status not in {"COMPLETED", "PARTIAL_COMPLETED"}:
                    raise SimulatorError(
                        f"导入 Worker 终态为 {terminal_status or '未知'}，未达到完成要求"
                    )
        if bool(getattr(args, "wait_for_import", False)) and legacy_package_id:
            legacy_poll_status, legacy_poll_body, legacy_poll_history = _poll_import_until_terminal(
                request=request,
                base_url=base_url,
                package_id=legacy_package_id,
                headers=headers,
                timeout=float(args.timeout),
                wait_seconds=float(getattr(args, "import_wait_seconds", 120.0)),
                poll_interval=float(getattr(args, "import_poll_interval", 2.0)),
            )
            _record(
                evidence,
                name="legacy_import_terminal",
                method="GET",
                path=f"/internal/v1/experiment-datasets/{legacy_package_id}",
                status=legacy_poll_status,
                body=legacy_poll_body,
                expected=[200],
            )
            legacy_terminal = (
                legacy_poll_body.get("data", {}).get("status")
                if isinstance(legacy_poll_body, dict)
                and isinstance(legacy_poll_body.get("data"), dict)
                else None
            )
            evidence["legacy_import_worker"] = {
                "package_id": legacy_package_id,
                "status_history": legacy_poll_history,
                "terminal_status": legacy_terminal,
            }
            if legacy_poll_status != 200 or not legacy_poll_history or legacy_poll_history[-1] not in IMPORT_TERMINAL_STATUSES:
                raise SimulatorError(
                    f"兼容入口导入 Worker 在 {getattr(args, 'import_wait_seconds', 120.0)} 秒内未到达终态"
                )

    secret = os.getenv(args.hmac_secret_env, "").strip()
    callback_headers: dict[str, str] | None = None
    callback_payload = make_callback_payload(external_task_id, callback_event_id=f"local-sim-callback-{run_id}-running", status="running")
    if secret:
        callback_headers = make_callback_headers(callback_payload, secret)
    elif not args.allow_unsigned_local:
        raise SimulatorError(
            "未配置回调 HMAC 密钥；若本地环境明确启用无签名兼容模式，请显式传 --allow-unsigned-local"
        )
    send("callback_running", "POST", "/api/v1/strategy-evolution/platform-callbacks", callback_payload, expected=[200], extra_headers=callback_headers)
    # Same callback_event_id must be idempotent and must not regress state.
    send("callback_running_idempotent", "POST", "/api/v1/strategy-evolution/platform-callbacks", callback_payload, expected=[200], extra_headers=callback_headers)
    completed = make_callback_payload(external_task_id, callback_event_id=f"local-sim-callback-{run_id}-completed", status="completed")
    completed_headers = make_callback_headers(completed, secret) if secret else None
    send("callback_completed", "POST", "/api/v1/strategy-evolution/platform-callbacks", completed, expected=[200], extra_headers=completed_headers)
    conflict_callback = make_callback_payload(external_task_id, callback_event_id=f"local-sim-callback-{run_id}-conflict", status="running")
    conflict_headers = make_callback_headers(conflict_callback, secret) if secret else None
    send("callback_terminal_conflict", "POST", "/api/v1/strategy-evolution/platform-callbacks", conflict_callback, expected=[409], extra_headers=conflict_headers)
    retry_payload: dict[str, Any] = {}
    if args.callback_receiver_url:
        receiver_url = validate_local_base_url(args.callback_receiver_url)
        retry_payload = {"callback_url": receiver_url}
        evidence["callback_receiver_url"] = receiver_url
    send(
        "internal_callback_retry",
        "POST",
        f"/internal/v1/strategy-evolution/tasks/{external_task_id}/callback-retry",
        retry_payload,
        expected=[200],
    )
    if args.callback_receiver_url:
        receiver_url = validate_local_base_url(args.callback_receiver_url)
        receiver_parsed = urllib.parse.urlparse(receiver_url)
        receiver_base = urllib.parse.urlunparse(
            (receiver_parsed.scheme, receiver_parsed.netloc, "", "", "", "")
        ).rstrip("/")
        receiver_recent_path = "/api/v1/strategy-evolution/platform-callbacks/recent?limit=20"
        receiver_status, receiver_body = request(
            f"{receiver_base}{receiver_recent_path}",
            method="GET",
            payload=None,
            # Do not forward the LabelU service token to the simulated peer.
            headers={"Accept": "application/json"},
            timeout=float(args.timeout),
        )
        _record(
            evidence,
            name="callback_receiver_recent",
            method="GET",
            path=receiver_recent_path,
            status=receiver_status,
            body=receiver_body,
            expected=[200],
        )
        receiver_items = (
            receiver_body.get("data")
            if isinstance(receiver_body, dict)
            else None
        )
        latest_external_task_id = ""
        if isinstance(receiver_items, list) and receiver_items:
            latest_payload = receiver_items[-1].get("payload")
            if isinstance(latest_payload, dict):
                latest_external_task_id = str(
                    latest_payload.get("external_task_id") or ""
                ).strip()
        evidence["callback_receiver"] = {
            "status": receiver_status,
            "latest_external_task_id": latest_external_task_id,
            "matched_external_task_id": latest_external_task_id == external_task_id,
        }
        if receiver_status != 200 or latest_external_task_id != external_task_id:
            raise SimulatorError("本地第三方回调接收器未收到本次任务的回调")

    passed = all(item["passed"] for item in evidence["requests"])
    checks = {
        "all_requests_passed": passed,
        "idempotency": evidence["requests"][1]["passed"],
        "conflict_rejected": any(item["name"] == "official_create_conflict" and item["passed"] for item in evidence["requests"]),
        "callback_idempotency": any(item["name"] == "callback_running_idempotent" and item["passed"] for item in evidence["requests"]),
        "callback_terminal_conflict": any(item["name"] == "callback_terminal_conflict" and item["passed"] for item in evidence["requests"]),
    }
    if bool(getattr(args, "wait_for_import", False)):
        checks["import_worker_terminal"] = bool(
            evidence.get("import_worker", {}).get("terminal_status")
        )
        if evidence.get("legacy_import_worker") is not None:
            checks["legacy_import_worker_terminal"] = bool(
                evidence.get("legacy_import_worker", {}).get("terminal_status")
            )
    if args.callback_receiver_url:
        checks["callback_receiver_received"] = bool(
            evidence.get("callback_receiver", {}).get("matched_external_task_id")
        )
    evidence["checks"].update(checks)
    evidence["finished_at"] = _now_iso()
    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{run_id}.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"passed": passed, "evidence": str(evidence_path), "request_count": len(evidence["requests"])}, ensure_ascii=False))
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--service-token-env", default=DEFAULT_SERVICE_TOKEN_ENV)
    parser.add_argument("--hmac-secret-env", default=DEFAULT_HMAC_SECRET_ENV)
    parser.add_argument("--allow-unsigned-local", action="store_true")
    parser.add_argument("--import-s3-uri", default="", help="可选：本地可访问的真实 S3 数据包地址")
    parser.add_argument("--sample-count", type=int, default=50)
    parser.add_argument("--model-id", default="536", help="模拟目标模型 ID；本地已有发布模型时可指定其 ID")
    parser.add_argument("--dataset-category-key", default="", help="第三方数据包分类 key；本地 Worker 要创建任务时应显式提供")
    parser.add_argument("--seed-local-s3", action="store_true", help="显式向白名单本地 MinIO 上传 50+ 个小 JPEG 与 metadata.json")
    parser.add_argument("--s3-endpoint", default=DEFAULT_S3_ENDPOINT)
    parser.add_argument("--s3-bucket", default=DEFAULT_S3_BUCKET)
    parser.add_argument("--s3-access-key-env", default="STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID")
    parser.add_argument("--s3-secret-key-env", default="STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument(
        "--wait-for-import",
        action="store_true",
        help="轮询异步导入详情，直到 Worker 到达终态并写入验收证据",
    )
    parser.add_argument(
        "--require-import-completed",
        action="store_true",
        help="与 --wait-for-import 一起使用，要求终态为 COMPLETED 或 PARTIAL_COMPLETED",
    )
    parser.add_argument("--import-wait-seconds", type=float, default=120.0)
    parser.add_argument("--import-poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--callback-receiver-url",
        default="",
        help="可选：本机 strategy_callback_receiver 的完整回调地址，用于验证出站回调",
    )
    parser.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except SimulatorError as exc:
        print(f"模拟器失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
