from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from labelu.internal.common.io import get_data_dir

DEV_LOG_RELATIVE_PATH = "docs/06-运维与协作/开发记录.md"
DEFAULT_INTERVAL_SECONDS = 8
DEFAULT_QUIET_SECONDS = 20


def _windows_subprocess_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


@dataclass(frozen=True)
class SnapshotItem:
    path: str
    status: str
    size: int | None
    mtime_ns: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SnapshotItem":
        return cls(
            path=str(data["path"]),
            status=str(data["status"]),
            size=data.get("size"),
            mtime_ns=data.get("mtime_ns"),
        )


def _now() -> datetime:
    return datetime.now().astimezone()


def _normalize_relative_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _repo_key(repo_root: Path) -> str:
    return hashlib.sha1(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:12]


def state_dir_for_repo(repo_root: Path) -> Path:
    return Path(get_data_dir()) / "dev_change_watchdog" / _repo_key(repo_root)


def _status_file(repo_root: Path) -> Path:
    return state_dir_for_repo(repo_root) / "status.json"


def _runtime_log_file(repo_root: Path) -> Path:
    return state_dir_for_repo(repo_root) / "watchdog.log"


def _ensure_state_dir(repo_root: Path) -> Path:
    path = state_dir_for_repo(repo_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        **_windows_subprocess_kwargs(),
    )
    return completed.stdout


def resolve_repo_root(path: Path | str | None = None) -> Path:
    base = Path(path or Path.cwd()).resolve()
    out = _run_git(base, "rev-parse", "--show-toplevel").strip()
    return Path(out).resolve()


def _is_excluded_path(path: str) -> bool:
    normalized = _normalize_relative_path(path)
    return normalized == DEV_LOG_RELATIVE_PATH


def _file_stat(repo_root: Path, relative_path: str) -> tuple[int | None, int | None]:
    full_path = repo_root / relative_path
    if not full_path.exists():
        return None, None
    stat = full_path.stat()
    return stat.st_size, stat.st_mtime_ns


def collect_worktree_snapshot(repo_root: Path) -> dict[str, SnapshotItem]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
        ],
        check=True,
        capture_output=True,
        **_windows_subprocess_kwargs(),
    )
    raw = completed.stdout.decode("utf-8", errors="replace")
    out: dict[str, SnapshotItem] = {}
    for record in raw.split("\x00"):
        if not record:
            continue
        status = record[:2]
        path = _normalize_relative_path(record[3:])
        if not path or _is_excluded_path(path):
            continue
        size, mtime_ns = _file_stat(repo_root, path)
        out[path] = SnapshotItem(
            path=path,
            status=status,
            size=size,
            mtime_ns=mtime_ns,
        )
    return out


def _snapshot_to_jsonable(snapshot: dict[str, SnapshotItem]) -> dict[str, dict[str, Any]]:
    return {path: item.to_dict() for path, item in sorted(snapshot.items())}


def _snapshot_from_jsonable(raw: dict[str, dict[str, Any]]) -> dict[str, SnapshotItem]:
    return {path: SnapshotItem.from_dict(data) for path, data in raw.items()}


def _snapshots_equal(
    left: dict[str, SnapshotItem],
    right: dict[str, SnapshotItem],
) -> bool:
    return _snapshot_to_jsonable(left) == _snapshot_to_jsonable(right)


def _diff_paths(
    before: dict[str, SnapshotItem],
    after: dict[str, SnapshotItem],
) -> set[str]:
    all_paths = set(before) | set(after)
    return {path for path in all_paths if before.get(path) != after.get(path)}


def classify_path(path: str) -> str:
    normalized = _normalize_relative_path(path)
    lower = normalized.lower()
    if lower.startswith("docs/") or lower == "agents.md" or lower.endswith(".md"):
        return "文档"
    if lower.startswith("frontend/") or "/pages/" in lower or "/assets/" in lower:
        return "前端"
    if lower.startswith("labelu/tests/") or "/tests/" in lower or lower.endswith(".test.mjs"):
        return "测试"
    if lower in {
        "pyproject.toml",
        "poetry.lock",
        ".env",
        ".env.example",
        "alembic.ini",
    }:
        return "配置"
    if any(
        token in lower
        for token in (
            "deploy",
            "docker",
            "systemd",
            "nginx",
            "compose",
            "k8s",
            "ops",
        )
    ):
        return "部署"
    if lower.startswith("labelu/") or lower.endswith(".py"):
        return "后端"
    return "其他"


def _scope_for_path(path: str) -> str:
    normalized = _normalize_relative_path(path)
    parts = normalized.split("/")
    if len(parts) >= 3 and parts[0] in {"labelu", "frontend", "docs"}:
        return "/".join(parts[:3])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return normalized


def _action_for_change(
    before: SnapshotItem | None,
    after: SnapshotItem | None,
) -> str:
    if before is None and after is not None:
        return "新增" if after.status == "??" else "新增变更"
    if before is not None and after is None:
        return "恢复干净"
    if after is None:
        return "变化"
    compact = after.status.replace(" ", "")
    if compact == "??":
        return "新增"
    if "D" in compact:
        return "删除"
    if "A" in compact:
        return "新增变更"
    return "修改"


def _status_label(after: SnapshotItem | None) -> str:
    if after is None:
        return "CLEAN"
    compact = after.status.replace(" ", "") or after.status
    return compact


def summarize_changes(
    before: dict[str, SnapshotItem],
    after: dict[str, SnapshotItem],
    touched_paths: set[str],
) -> dict[str, Any]:
    changed_paths = sorted(path for path in touched_paths if before.get(path) != after.get(path))
    records: list[dict[str, str]] = []
    categories: list[str] = []
    scopes: list[str] = []
    status_counts: dict[str, int] = {}
    for path in changed_paths:
        prev = before.get(path)
        curr = after.get(path)
        category = classify_path(path)
        categories.append(category)
        scopes.append(_scope_for_path(path))
        label = _status_label(curr)
        status_counts[label] = status_counts.get(label, 0) + 1
        records.append(
            {
                "path": path,
                "category": category,
                "scope": _scope_for_path(path),
                "action": _action_for_change(prev, curr),
                "status": label,
            }
        )
    dedup_categories = list(dict.fromkeys(categories))
    dedup_scopes = list(dict.fromkeys(scopes))
    return {
        "count": len(changed_paths),
        "records": records,
        "categories": dedup_categories,
        "scopes": dedup_scopes,
        "status_counts": status_counts,
    }


def _short_join(values: list[str], limit: int = 6) -> str:
    if not values:
        return "无"
    if len(values) <= limit:
        return "、".join(values)
    return "、".join(values[:limit]) + f" 等 {len(values)} 项"


def render_markdown_entry(
    summary: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
) -> str | None:
    if not summary["count"]:
        return None
    title = (
        f"### {finished_at.strftime('%H:%M')} 自动记录："
        f"{_short_join(summary['categories'], limit=3)}变更（{summary['count']}项）"
    )
    status_text = "、".join(
        f"`{status}` {count} 项"
        for status, count in sorted(summary["status_counts"].items())
    )
    file_lines = [
        f"{record['action']} `{record['path']}`"
        for record in summary["records"][:12]
    ]
    if len(summary["records"]) > 12:
        file_lines.append(f"其余 {len(summary['records']) - 12} 项已省略")
    files_text = "；".join(file_lines)
    content_text = (
        f"自动检测到工作区在 {started_at.strftime('%H:%M:%S')} - "
        f"{finished_at.strftime('%H:%M:%S')} 期间发生 {summary['count']} 项文件变更；"
        f"状态统计：{status_text}"
    )
    return "\n".join(
        [
            title,
            "",
            f"- 类型：{_short_join(summary['categories'])}",
            f"- 范围：{_short_join(summary['scopes'])}",
            f"- 内容：{content_text}",
            f"- 文件：{files_text}",
            "- 影响：用于回溯该时段的真实落盘改动，具体业务影响需结合对应文件继续分析",
            "- 备注：自动记录；来源为本地开发记录 watcher；已忽略开发记录文档自身改动",
        ]
    )


def _ensure_quick_index(text: str, date_str: str) -> str:
    month_str = date_str[:7]
    month_heading = f"### {month_str}"
    day_link = f"- [{date_str}](#{date_str})"
    separator = "\n---\n"
    if separator not in text:
        return text
    if month_heading not in text:
        return text.replace(
            separator,
            f"\n{month_heading}\n\n{day_link}\n{separator}",
            1,
        )
    if day_link in text:
        return text
    month_match = re.search(
        rf"(?m)^### {re.escape(month_str)}\s*$",
        text,
    )
    if not month_match:
        return text
    next_match = re.search(r"(?m)^### |\n---\n", text[month_match.end():])
    if next_match:
        insert_at = month_match.end() + next_match.start()
    else:
        insert_at = len(text)
    block = text[month_match.start():insert_at]
    if block.endswith("\n"):
        insertion = f"{day_link}\n"
    else:
        insertion = f"\n{day_link}\n"
    return text[:insert_at] + insertion + text[insert_at:]


def append_markdown_record(log_path: Path, entry: str, now: datetime | None = None) -> None:
    timestamp = now or _now()
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else "# 开发记录\n"
    text = _ensure_quick_index(text, timestamp.strftime("%Y-%m-%d"))
    day_heading = f"## {timestamp.strftime('%Y-%m-%d')}"
    day_match = re.search(rf"(?m)^{re.escape(day_heading)}\s*$", text)
    if day_match is None:
        text = text.rstrip() + f"\n\n{day_heading}\n\n{entry}\n"
    else:
        next_day = re.search(r"(?m)^## ", text[day_match.end():])
        if next_day:
            insert_at = day_match.end() + next_day.start()
            insertion = "\n\n" + entry + "\n"
            text = text[:insert_at].rstrip() + insertion + text[insert_at:]
        else:
            text = text.rstrip() + "\n\n" + entry + "\n"
    log_path.write_text(text, encoding="utf-8")


def _load_status(repo_root: Path) -> dict[str, Any]:
    path = _status_file(repo_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("load dev watchdog status failed: {}", path)
        return {}


def _save_status(repo_root: Path, payload: dict[str, Any]) -> None:
    path = _status_file(repo_root)
    _ensure_state_dir(repo_root)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"PID eq {pid}",
                "/FO",
                "CSV",
                "/NH",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            **_windows_subprocess_kwargs(),
        )
        output = (completed.stdout or "").strip()
        return bool(output and "No tasks are running" not in output)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _background_python_executable() -> str:
    if os.name != "nt":
        return sys.executable
    current = Path(sys.executable)
    pythonw = current.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def _running_status(repo_root: Path) -> dict[str, Any]:
    status = _load_status(repo_root)
    pid = int(status.get("pid") or 0)
    status["running"] = _pid_is_running(pid)
    return status


def start_background_watchdog(
    repo_root: Path,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    quiet_seconds: int = DEFAULT_QUIET_SECONDS,
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_root)
    status = _running_status(repo_root)
    if status.get("running"):
        return {
            "started": False,
            "repo_root": str(repo_root),
            "pid": status.get("pid"),
            "message": "watchdog already running",
        }

    _ensure_state_dir(repo_root)
    cmd = [
        _background_python_executable(),
        "-m",
        "labelu.internal.common.dev_change_watchdog",
        "run",
        "--repo-root",
        str(repo_root),
        "--interval-seconds",
        str(int(interval_seconds)),
        "--quiet-seconds",
        str(int(quiet_seconds)),
    ]
    log_file = _runtime_log_file(repo_root).open("a", encoding="utf-8", buffering=1)
    kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        if breakaway:
            creationflags |= breakaway
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)
    _save_status(
        repo_root,
        {
            "repo_root": str(repo_root),
            "pid": process.pid,
            "running": True,
            "interval_seconds": int(interval_seconds),
            "quiet_seconds": int(quiet_seconds),
            "started_at": _now().isoformat(),
            "mode": "background",
        },
    )
    return {
        "started": True,
        "repo_root": str(repo_root),
        "pid": process.pid,
        "message": "watchdog started",
    }


def stop_background_watchdog(repo_root: Path) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_root)
    status = _running_status(repo_root)
    pid = int(status.get("pid") or 0)
    if not status.get("running") or pid <= 0:
        return {
            "stopped": False,
            "repo_root": str(repo_root),
            "message": "watchdog is not running",
        }
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            **_windows_subprocess_kwargs(),
        )
    else:
        os.kill(pid, signal.SIGTERM)
    status["running"] = False
    status["stopped_at"] = _now().isoformat()
    _save_status(repo_root, status)
    return {
        "stopped": True,
        "repo_root": str(repo_root),
        "pid": pid,
        "message": "watchdog stopped",
    }


def watchdog_status(repo_root: Path) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_root)
    status = _running_status(repo_root)
    status.setdefault("repo_root", str(repo_root))
    status.setdefault("log_path", str((repo_root / DEV_LOG_RELATIVE_PATH).resolve()))
    return status


def record_current_changes_once(
    repo_root: Path,
    baseline_snapshot: dict[str, SnapshotItem] | None = None,
) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_root)
    before = baseline_snapshot or {}
    after = collect_worktree_snapshot(repo_root)
    summary = summarize_changes(before, after, _diff_paths(before, after))
    entry = render_markdown_entry(summary, _now(), _now())
    if entry:
        append_markdown_record((repo_root / DEV_LOG_RELATIVE_PATH).resolve(), entry)
    return {
        "repo_root": str(repo_root),
        "count": summary["count"],
        "wrote": bool(entry),
    }


def run_watchdog_loop(
    repo_root: Path,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    quiet_seconds: int = DEFAULT_QUIET_SECONDS,
) -> None:
    repo_root = resolve_repo_root(repo_root)
    log_path = (repo_root / DEV_LOG_RELATIVE_PATH).resolve()
    _ensure_state_dir(repo_root)

    should_stop = False

    def _mark_stop(signum: int, frame: Any) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGTERM, _mark_stop)
    signal.signal(signal.SIGINT, _mark_stop)

    baseline = collect_worktree_snapshot(repo_root)
    pending_snapshot: dict[str, SnapshotItem] | None = None
    pending_paths: set[str] = set()
    pending_started_at: datetime | None = None
    pending_last_changed_at: datetime | None = None

    _save_status(
        repo_root,
        {
            "repo_root": str(repo_root),
            "pid": os.getpid(),
            "running": True,
            "interval_seconds": int(interval_seconds),
            "quiet_seconds": int(quiet_seconds),
            "started_at": _now().isoformat(),
            "last_heartbeat_at": _now().isoformat(),
            "log_path": str(log_path),
        },
    )
    logger.info(
        "dev change watchdog started repo={} interval={} quiet={}",
        repo_root,
        interval_seconds,
        quiet_seconds,
    )

    try:
        while not should_stop:
            current = collect_worktree_snapshot(repo_root)
            now = _now()
            if _snapshots_equal(current, baseline):
                if pending_snapshot is not None:
                    pending_snapshot = None
                    pending_paths.clear()
                    pending_started_at = None
                    pending_last_changed_at = None
            else:
                changed = _diff_paths(pending_snapshot or baseline, current)
                if pending_snapshot is None:
                    pending_started_at = now
                    pending_paths = set()
                    pending_last_changed_at = now
                elif changed:
                    pending_last_changed_at = now
                pending_snapshot = current
                pending_paths.update(changed)

            if (
                pending_snapshot is not None
                and pending_started_at is not None
                and pending_last_changed_at is not None
                and (now - pending_last_changed_at).total_seconds() >= quiet_seconds
            ):
                summary = summarize_changes(baseline, pending_snapshot, pending_paths)
                entry = render_markdown_entry(summary, pending_started_at, now)
                if entry:
                    append_markdown_record(log_path, entry, now=now)
                    logger.info(
                        "dev change watchdog wrote {} change(s) to {}",
                        summary["count"],
                        log_path,
                    )
                baseline = pending_snapshot
                pending_snapshot = None
                pending_paths = set()
                pending_started_at = None
                pending_last_changed_at = None

            _save_status(
                repo_root,
                {
                    "repo_root": str(repo_root),
                    "pid": os.getpid(),
                    "running": True,
                    "interval_seconds": int(interval_seconds),
                    "quiet_seconds": int(quiet_seconds),
                    "started_at": _load_status(repo_root).get("started_at") or _now().isoformat(),
                    "last_heartbeat_at": now.isoformat(),
                    "pending_count": len(pending_paths),
                    "log_path": str(log_path),
                },
            )
            time.sleep(max(2, int(interval_seconds)))
    finally:
        status = _running_status(repo_root)
        status["running"] = False
        status["stopped_at"] = _now().isoformat()
        _save_status(repo_root, status)
        logger.info("dev change watchdog stopped repo={}", repo_root)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LabelU 开发记录自动 watcher")
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_common_options(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--repo-root", default=str(Path.cwd()))

    start = sub.add_parser("start")
    _add_common_options(start)
    start.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    start.add_argument("--quiet-seconds", type=int, default=DEFAULT_QUIET_SECONDS)

    stop = sub.add_parser("stop")
    _add_common_options(stop)

    status = sub.add_parser("status")
    _add_common_options(status)

    once = sub.add_parser("once")
    _add_common_options(once)

    run = sub.add_parser("run")
    _add_common_options(run)
    run.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    run.add_argument("--quiet-seconds", type=int, default=DEFAULT_QUIET_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    if args.command == "start":
        result = start_background_watchdog(
            repo_root,
            interval_seconds=args.interval_seconds,
            quiet_seconds=args.quiet_seconds,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "stop":
        print(json.dumps(stop_background_watchdog(repo_root), ensure_ascii=False))
        return 0
    if args.command == "status":
        print(json.dumps(watchdog_status(repo_root), ensure_ascii=False))
        return 0
    if args.command == "once":
        print(json.dumps(record_current_changes_once(repo_root), ensure_ascii=False))
        return 0
    if args.command == "run":
        run_watchdog_loop(
            repo_root,
            interval_seconds=args.interval_seconds,
            quiet_seconds=args.quiet_seconds,
        )
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
