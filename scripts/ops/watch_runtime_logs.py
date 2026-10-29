"""Read existing LabelU logs without importing or changing the application.

Only classifications and allowlisted metadata are copied to the monitor output.
Request bodies, messages, URL paths/queries and credentials are never copied.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[2]
MAX_READ_BYTES = 1024 * 1024
ROTATION_REPLAY_BYTES = 4 * MAX_READ_BYTES
ANCHOR_BYTES = 128
RECENT_EVENTS = 40
DEDUP_LIMIT = 4096
MAX_LINE_BYTES = 256 * 1024
REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2, 0.4)
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \|\s*(\w+)\s*\|")
LOCATION = re.compile(r"\| ([A-Za-z_][\w.]*):([A-Za-z_<>][\w<>]*):(\d+) - (.*)$")
HTTP_RESPONSE = re.compile(r"^\[http\] (GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) \S+ response rid=([\w-]+) status=(\d{3}|error)(?: elapsed_ms=(\d+))?")
REQUEST_ID = re.compile(r"\brid=([a-fA-F0-9-]{8,64})(?=\s|$)")
PROCESS_ID = re.compile(r"\bpid=(\d+)(?=\s|$)")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def replace_with_retry(source: Path, destination: Path) -> None:
    """Allow short Windows reader/antivirus sharing conflicts to clear."""
    for attempt in range(len(REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            sharing_conflict = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
            if not sharing_conflict or attempt == len(REPLACE_RETRY_DELAYS):
                raise
            time.sleep(REPLACE_RETRY_DELAYS[attempt])


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Keep the complete temporary checkpoint if replacement remains blocked;
    # startup can recover its matching counters, dedup IDs and file offsets.
    replace_with_retry(temporary, path)


class MonitorLock:
    """OS-owned lock; a crash releases it without trusting stale PID files."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.stream.seek(0, os.SEEK_END) == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError("A monitor already owns this state directory") from None
        return self

    def __exit__(self, *_):
        if self.stream is not None:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()


def classify(message: str) -> str:
    lowered = message.lower()
    if any(value in lowered for value in ("cooldown", "cooling down", "冷却")):
        return "upstream_cooldown"
    if any(value in lowered for value in ("arrearage", "billing", "insufficient balance", "欠费", "余额不足")):
        return "upstream_billing_arrearage"
    if any(value in lowered for value in ("modelnotfound", "model_not_found", "模型不存在", "模型不可用")) or (
        "model" in lowered and any(value in lowered for value in ("not found", "not exist", "unavailable"))
    ):
        return "upstream_model_unavailable"
    if any(value in lowered for value in ("429", "ratelimit", "rate_limit", "rate limit", "too many requests", "限流")):
        return "upstream_rate_limited"
    if "unsupportedprotocol" in lowered or "missing an 'http://'" in lowered:
        return "invalid_upstream_url"
    if "no module named" in lowered or ("自动安装" in message and "失败" in message):
        return "dependency_unavailable"
    if "ai_tag_fail" in lowered or "auto-label" in lowered or "auto_label" in lowered:
        return "auto_annotation_failure"
    if "retry_scheduled" in lowered:
        return "retry_scheduled"
    if "sql exception" in lowered or "database" in lowered or "数据库" in message:
        return "database_failure"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "connection" in lowered or "connecterror" in lowered or "连接" in message:
        return "connection_failure"
    if "validation error" in lowered:
        return "validation_failure"
    if "unexpected exception" in lowered:
        return "unhandled_exception"
    if ".failed" in lowered:
        return "business_operation_failed"
    return "application_warning_or_error"


def parse_event(line: str) -> tuple[dict | None, str | None]:
    """Never return the raw message, path, query string or payload."""
    stamp = STAMP.match(line)
    timestamp = stamp.group(1) if stamp else None
    location = LOCATION.search(line) if stamp else None
    message = location.group(4) if location else line
    process_match = PROCESS_ID.search(line[:location.start()]) if location else None
    process_id = int(process_match.group(1)) if process_match else None
    http = HTTP_RESPONSE.match(message)
    if http:
        method, request_id, status, elapsed = http.groups()
        if status != "error" and int(status) < 400:
            return None, timestamp
        event = {
            "kind": "http_response",
            "severity": "ERROR" if status == "error" or int(status) >= 500 else "WARNING",
            "category": "http_exception" if status == "error" else "http_server_error" if int(status) >= 500 else "http_client_error",
            "method": method,
            "status": status if status == "error" else int(status),
        }
        if re.fullmatch(r"[a-fA-F0-9-]{8,64}", request_id):
            event["request_id"] = request_id
        if elapsed:
            event["elapsed_ms"] = int(elapsed)
        if timestamp:
            event["log_time"] = timestamp
        if process_id is not None:
            event["process_id"] = process_id
        return event, timestamp
    if not stamp:
        return None, None
    if not location:
        # Timestamped traceback continuation lines are part of the original
        # error, not additional error occurrences.
        return None, timestamp
    module, function, line_number, message = location.groups()
    severity = stamp.group(2)
    if severity not in {"WARNING", "ERROR", "CRITICAL"} and ".failed" not in message:
        return None, timestamp
    event = {
        "kind": "application",
        "severity": severity if severity in {"WARNING", "ERROR", "CRITICAL"} else "ERROR",
        "category": classify(message),
        "location": f"{module}:{function}:{line_number}",
        "log_time": timestamp,
    }
    request_id = REQUEST_ID.search(line)
    if request_id:
        event["request_id"] = request_id.group(1)
    if process_id is not None:
        event["process_id"] = process_id
    return event, timestamp


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise argparse.ArgumentTypeError("--url must be a loopback HTTP(S) origin without credentials, path or query")
    try:
        parsed.port
    except ValueError:
        raise argparse.ArgumentTypeError("Invalid local service port") from None
    return value.rstrip("/") + "/"


def probe_health(url: str) -> dict:
    parsed = urlsplit(url)
    result = {"checked_at": now(), "target": url, "scope": "TCP and HTTP root availability only"}
    try:
        with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=1):
            result["tcp_open"] = True
    except OSError as exc:
        result.update(tcp_open=False, http_status=None, error_type=type(exc).__name__)
        return result
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(Request(url, method="GET"), timeout=2) as response:
            result["http_status"] = response.status
    except HTTPError as exc:
        result["http_status"] = exc.code
    except (OSError, URLError) as exc:
        result.update(http_status=None, error_type=type(exc).__name__)
    return result


def probe_listener(url: str, *, prefer_netstat: bool = False) -> dict:
    """Ask Windows for the current listener owner, without process mutations."""
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result = {"checked_at": now(), "port": port, "pid": None, "pids": [], "source": "Get-NetTCPConnection"}
    if sys.platform != "win32":
        return {**result, "source": "unavailable", "error_type": "unsupported_platform"}

    def netstat_fallback(reason: str) -> dict:
        fallback = {**result, "source": "netstat", "fallback_reason": reason}
        try:
            completed = subprocess.run(
                ["netstat.exe", "-ano", "-p", "TCP"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode:
                return {**fallback, "error_type": "listener_query_failed"}
            owners = set()
            for row in completed.stdout.splitlines():
                fields = row.split()
                if len(fields) != 5 or fields[0].upper() != "TCP" or fields[3].upper() != "LISTENING":
                    continue
                if fields[1].rsplit(":", 1)[-1] == str(port) and fields[4].isdigit():
                    owners.add(int(fields[4]))
            pids = sorted(pid for pid in owners if pid > 0)
            fallback["pids"] = pids
            if len(pids) == 1:
                fallback["pid"] = pids[0]
            elif len(pids) > 1:
                fallback["error_type"] = "multiple_listener_owners"
            return fallback
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**fallback, "error_type": type(exc).__name__}

    if prefer_netstat:
        return netstat_fallback("previous_GetNetTCPConnection_failure")
    command = (
        "$ErrorActionPreference='Stop'; "
        f"@(Get-NetTCPConnection -State Listen -LocalPort {port} "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) "
        "| ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            return netstat_fallback("listener_query_failed")
        payload = json.loads(completed.stdout.strip() or "[]")
        candidates = payload if isinstance(payload, list) else [payload]
        pids = sorted({int(value) for value in candidates if isinstance(value, int) and value > 0})
        result["pids"] = pids
        if len(pids) == 1:
            result["pid"] = pids[0]
        elif len(pids) > 1:
            result["error_type"] = "multiple_listener_owners"
        return result
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return netstat_fallback(type(exc).__name__)


class RuntimeMonitor:
    def __init__(self, log_dir: Path, state_dir: Path, *, from_start: bool = False, url: str = "http://127.0.0.1:8003/"):
        self.log_dir = log_dir.resolve()
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.from_start = from_start
        self.cursors: dict[str, dict] = {}
        self.counts = collections.Counter()
        self.severities = collections.Counter()
        self.recent = collections.deque(maxlen=RECENT_EVENTS)
        self.seen = collections.deque(maxlen=DEDUP_LIMIT)
        self.seen_set: set[str] = set()
        self.latest_log_time = None
        self.health: dict = {}
        self.last_health = -float("inf")
        self.listener: dict = {}
        self.last_listener = -float("inf")
        self.process_categories: dict[str, collections.Counter] = {}
        self.attribution_counts = collections.Counter()
        self.transitions = collections.Counter()
        self.started_at = now()
        self.polls = 0
        self.persistence_deferred = False
        self._load()

    def _load(self) -> None:
        if self.from_start:
            return
        primary = self.state_dir / "state.json"
        candidates = []
        for path in [primary, *self.state_dir.glob("state.json.*.tmp")]:
            try:
                candidates.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
        for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Invalid monitor checkpoint")
                if data.get("log_dir") == str(self.log_dir):
                    self.cursors = data.get("cursors", {})
                    self.counts.update(data.get("categories", {}))
                    self.severities.update(data.get("severities", {}))
                    self.recent.extend(data.get("recent_events", []))
                    self.latest_log_time = data.get("latest_log_time")
                    self.transitions.update(data.get("file_transitions", {}))
                    self.listener = data.get("listener", {})
                    self.attribution_counts.update(data.get("attribution_counts", {}))
                    if "attribution_counts" not in data and self.counts:
                        self.attribution_counts["shared_log_process_unconfirmed"] = sum(self.counts.values())
                    self.process_categories = {str(pid): collections.Counter(counts) for pid, counts in data.get("process_categories", {}).items()}
                    for fingerprint in data.get("seen", []):
                        self._remember(fingerprint)
                    if path != primary:
                        self.transitions["recovered_pending_checkpoint"] += 1
                    return
            except (OSError, ValueError, TypeError):
                self.transitions["invalid_checkpoint"] += 1

    def _remember(self, fingerprint: str) -> bool:
        if fingerprint in self.seen_set:
            return False
        if len(self.seen) == self.seen.maxlen:
            self.seen_set.discard(self.seen[0])
        self.seen.append(fingerprint)
        self.seen_set.add(fingerprint)
        return True

    @staticmethod
    def _identity(stat) -> list[int]:
        return [stat.st_dev, stat.st_ino]

    @staticmethod
    def _anchor(stream, offset: int) -> str:
        stream.seek(max(0, offset - ANCHOR_BYTES))
        return hashlib.sha256(stream.read(min(offset, ANCHOR_BYTES))).hexdigest()

    def _record(self, raw: bytes, source: str) -> None:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        event, timestamp = parse_event(line)
        if timestamp:
            self.latest_log_time = timestamp
        if event is None:
            return
        fingerprint = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
        if fingerprint in self.seen_set:
            return
        process_id = event.get("process_id")
        listener_pid = self.listener.get("pid")
        if process_id is None:
            attribution = "shared_log_process_unconfirmed"
        elif listener_pid is None:
            attribution = "listener_pid_unavailable"
        elif process_id == listener_pid:
            attribution = "target_service_at_observation"
        else:
            attribution = "other_process"
        event.update(id=fingerprint, observed_at=now(), source=source, attribution=attribution)
        if listener_pid is not None:
            event["listener_pid_at_observation"] = listener_pid
        alert_path = self.state_dir / "alerts.jsonl"
        if alert_path.exists() and alert_path.stat().st_size > 5 * MAX_READ_BYTES:
            replace_with_retry(alert_path, self.state_dir / "alerts.previous.jsonl")
        with alert_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        # A blocked append must remain retryable, including after restart.
        # Never remember/count an event before its alert has been written.
        self._remember(fingerprint)
        self.attribution_counts[attribution] += 1
        if process_id is not None:
            self.process_categories.setdefault(str(process_id), collections.Counter())[event["category"]] += 1
        self.counts[event["category"]] += 1
        self.severities[event["severity"]] += 1
        self.recent.append(event)

    def _read(self, path: Path, cursor: dict, *, bounded_replay: bool = False, skip_partial: bool = False) -> dict:
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            offset = int(cursor.get("offset", 0))
            if offset > stat.st_size or (cursor.get("anchor") and self._anchor(stream, offset) != cursor["anchor"]):
                offset = max(0, stat.st_size - ROTATION_REPLAY_BYTES)
                self.transitions["truncated_or_rewritten"] += 1
                bounded_replay = offset > 0
                skip_partial = offset > 0
            if skip_partial and offset > 0:
                stream.seek(offset)
                stream.readline(MAX_LINE_BYTES)
                offset = stream.tell()
            stream.seek(offset)
            budget = ROTATION_REPLAY_BYTES if bounded_replay else MAX_READ_BYTES
            while stream.tell() - offset < budget:
                start = stream.tell()
                raw = stream.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE_BYTES:
                    # Oversize payloads are discarded, never persisted or copied.
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(MAX_LINE_BYTES)
                    self.transitions["oversize_lines_skipped"] += 1
                    continue
                if not raw.endswith(b"\n"):
                    stream.seek(start)
                    break
                try:
                    self._record(raw, path.name if path.name in {"labelu.log", "error.log"} else "rotated_log")
                except (OSError, ValueError):
                    # Earlier lines are already committed. Resume at exactly
                    # the failed line, rather than replaying a whole batch.
                    cursor.update(identity=self._identity(stat), offset=start)
                    cursor.pop("anchor", None)
                    cursor["anchor"] = self._anchor(stream, start)
                    raise
            offset = stream.tell()
            return {"identity": self._identity(stat), "offset": offset, "anchor": self._anchor(stream, offset)}

    def _follow(self, filename: str) -> None:
        path = self.log_dir / filename
        try:
            stat = path.stat()
        except FileNotFoundError:
            return
        cursor = self.cursors.get(filename)
        identity = self._identity(stat)
        if cursor is None:
            cursor = {"identity": identity, "offset": 0 if self.from_start else stat.st_size}
        elif cursor.get("identity") != identity:
            # Drain unread data from the renamed file when it is still present.
            # The bound prevents a stale checkpoint scanning unlimited history.
            for old in self.log_dir.glob(path.stem + "*.log"):
                try:
                    if self._identity(old.stat()) == cursor.get("identity"):
                        old_cursor = dict(cursor)
                        skip_partial = False
                        if old.stat().st_size - int(cursor.get("offset", 0)) > ROTATION_REPLAY_BYTES:
                            old_cursor["offset"] = max(0, old.stat().st_size - ROTATION_REPLAY_BYTES)
                            old_cursor.pop("anchor", None)
                            self.transitions["rotation_replay_limited"] += 1
                            skip_partial = True
                        self.cursors[filename] = old_cursor
                        self._read(old, old_cursor, bounded_replay=True, skip_partial=skip_partial)
                        break
                except FileNotFoundError:
                    continue
            cursor = {"identity": identity, "offset": 0}
            self.transitions["rotated"] += 1
        self.cursors[filename] = cursor
        self.cursors[filename] = self._read(path, cursor)

    def tick(self) -> dict:
        self.polls += 1
        self.persistence_deferred = False
        if time.monotonic() - self.last_listener >= 10:
            previous_pid = self.listener.get("pid")
            previous_changed_at = self.listener.get("changed_at")
            self.listener = probe_listener(self.url, prefer_netstat=self.listener.get("source") == "netstat")
            if self.listener.get("pid") != previous_pid:
                self.listener["changed_at"] = now()
                self.listener["previous_pid"] = previous_pid
                self.transitions["listener_pid_changed"] += 1
            else:
                self.listener["changed_at"] = previous_changed_at
            self.last_listener = time.monotonic()
        # labelu.log already contains ERROR records. Read error.log only as a
        # fallback; reading both would count each exception twice.
        source = "labelu.log" if (self.log_dir / "labelu.log").exists() else "error.log"
        try:
            self._follow(source)
        except (OSError, ValueError) as exc:
            self.transitions["read_" + type(exc).__name__] += 1
        if time.monotonic() - self.last_health >= 10:
            self.health = probe_health(self.url)
            self.last_health = time.monotonic()
        files = {}
        for filename in ("labelu.log", "error.log"):
            try:
                stat = (self.log_dir / filename).stat()
                files[filename] = {"exists": True, "bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")}
            except FileNotFoundError:
                files[filename] = {"exists": False}
        target_categories = dict(self.process_categories.get(str(self.listener.get("pid")), {}))
        common = {
            "schema_version": 1,
            "updated_at": now(),
            "monitor_pid": os.getpid(),
            "monitor_started_at": self.started_at,
            "log_dir": str(self.log_dir),
            "read_source": source,
            "attribution": "PID records matched to current listener; legacy records remain unattributed",
            "capture_boundary": "File logs only; existing PyCharm stdout/stderr, print() and native extension output are not captured",
            "health": self.health,
            "listener": self.listener,
            "files": files,
            "polls_this_run": self.polls,
            "event_count": sum(self.counts.values()),
            "event_count_scope": "all observed shared-file events, including other processes and legacy records",
            "categories": dict(self.counts),
            "target_service_event_count": sum(target_categories.values()),
            "target_service_categories": target_categories,
            "target_service_count_scope": "records whose explicit process_id equals the current confirmed listener PID",
            "attribution_counts": dict(self.attribution_counts),
            "severities": dict(self.severities),
            "latest_log_time": self.latest_log_time,
            "recent_events": list(self.recent),
            "file_transitions": dict(self.transitions),
        }
        try:
            atomic_json(self.state_dir / "state.json", {**common, "cursors": self.cursors, "seen": list(self.seen), "process_categories": {pid: dict(counts) for pid, counts in self.process_categories.items()}})
        except OSError as exc:
            self._defer_persistence("checkpoint", exc)
            return common
        try:
            atomic_json(self.state_dir / "status.json", common)
        except OSError as exc:
            self._defer_persistence("status", exc)
        return common

    def _defer_persistence(self, output: str, exc: OSError) -> None:
        self.persistence_deferred = True
        self.transitions[f"{output}_write_{type(exc).__name__}"] += 1
        print(
            f"Monitor {output} write deferred ({type(exc).__name__}); retrying next poll",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=ROOT / ".tmp/local-runtime-data/logs")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".tmp/runtime-log-monitor")
    parser.add_argument("--url", type=validate_url, default="http://127.0.0.1:8003/")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--from-start", action="store_true", help="Replay current log from its beginning instead of resuming or starting at EOF")
    parser.add_argument("--duration", type=float, default=0, help="Seconds to run; 0 runs until interrupted")
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0 or args.duration < 0:
        parser.error("poll-seconds must be positive and duration must be non-negative")
    try:
        with MonitorLock(args.state_dir / "monitor.lock"):
            monitor = RuntimeMonitor(args.log_dir, args.state_dir, from_start=args.from_start, url=args.url)
            started = time.monotonic()
            while True:
                status = monitor.tick()
                if args.once:
                    print(json.dumps({"event_count": status["event_count"], "status_file": str((args.state_dir / "status.json").resolve())}, ensure_ascii=False))
                    if monitor.persistence_deferred:
                        return 1
                    break
                remaining = args.duration - (time.monotonic() - started) if args.duration else None
                if remaining is not None and remaining <= 0:
                    break
                time.sleep(min(args.poll_seconds, remaining) if remaining is not None else args.poll_seconds)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
