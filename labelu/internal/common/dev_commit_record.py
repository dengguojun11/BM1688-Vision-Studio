from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from labelu.internal.common.dev_change_watchdog import (
    DEV_LOG_RELATIVE_PATH,
    _scope_for_path,
    append_markdown_record,
    classify_path,
)


STATE_FILE_RELATIVE_PATH = ".git/labelu_dev_record_commit_state.json"


def _run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def resolve_repo_root(path: Path | str | None = None) -> Path:
    base = Path(path or Path.cwd()).resolve()
    out = _run_git(base, "rev-parse", "--show-toplevel").strip()
    return Path(out).resolve()


def _state_file(repo_root: Path) -> Path:
    return repo_root / STATE_FILE_RELATIVE_PATH


def _load_state(repo_root: Path) -> dict[str, Any]:
    path = _state_file(repo_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(repo_root: Path, payload: dict[str, Any]) -> None:
    path = _state_file(repo_root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_commit_subject(commit_msg_file: Path) -> str:
    lines = commit_msg_file.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return "未命名提交"


def _parse_name_status_z(raw: str) -> list[dict[str, str]]:
    parts = [part for part in raw.split("\x00") if part]
    records: list[dict[str, str]] = []
    idx = 0
    while idx + 1 < len(parts):
        status = parts[idx].strip()
        path = parts[idx + 1].replace("\\", "/").lstrip("./")
        idx += 2
        if not path or path == DEV_LOG_RELATIVE_PATH:
            continue
        records.append(
            {
                "status": status,
                "path": path,
                "category": classify_path(path),
                "scope": _scope_for_path(path),
            }
        )
    return records


def collect_staged_changes(repo_root: Path) -> list[dict[str, str]]:
    raw = _run_git(
        repo_root,
        "diff",
        "--cached",
        "--name-status",
        "--no-renames",
        "-z",
    )
    return _parse_name_status_z(raw)


def _fingerprint(subject: str, records: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {
            "subject": subject,
            "records": records,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _short_join(values: list[str], limit: int = 6) -> str:
    if not values:
        return "无"
    if len(values) <= limit:
        return "、".join(values)
    return "、".join(values[:limit]) + f" 等 {len(values)} 项"


def _action_from_status(status: str) -> str:
    normalized = (status or "").upper()
    if normalized.startswith("A"):
        return "新增"
    if normalized.startswith("D"):
        return "删除"
    if normalized.startswith("R"):
        return "重命名"
    return "修改"


def render_commit_entry(
    records: list[dict[str, str]],
    subject: str,
    now: datetime,
) -> str | None:
    if not records:
        return None
    categories = list(dict.fromkeys(record["category"] for record in records))
    scopes = list(dict.fromkeys(record["scope"] for record in records))
    files_text = "；".join(
        f"{_action_from_status(record['status'])} `{record['path']}`"
        for record in records[:12]
    )
    if len(records) > 12:
        files_text += f"；其余 {len(records) - 12} 项已省略"
    return "\n".join(
        [
            f"### {now.strftime('%H:%M')} 提交记录：{_short_join(categories, 3)}变更（{len(records)}项）",
            "",
            f"- 类型：{_short_join(categories)}",
            f"- 范围：{_short_join(scopes)}",
            f"- 内容：提交 `{subject}`，本次提交共包含 {len(records)} 项已暂存文件变更",
            f"- 文件：{files_text}",
            "- 影响：用于回溯本次提交实际落地了哪些改动，具体影响仍需结合对应代码或文档分析",
            "- 备注：来源为 git commit 自动记录；在提交钩子阶段生成",
        ]
    )


def append_commit_record(repo_root: Path, commit_msg_file: Path) -> dict[str, Any]:
    repo_root = resolve_repo_root(repo_root)
    commit_msg_file = commit_msg_file.resolve()
    subject = _read_commit_subject(commit_msg_file)
    records = collect_staged_changes(repo_root)
    if not records:
        return {"wrote": False, "reason": "no staged changes"}

    fingerprint = _fingerprint(subject, records)
    state = _load_state(repo_root)
    if state.get("fingerprint") == fingerprint:
        return {"wrote": False, "reason": "duplicate fingerprint"}

    now = datetime.now().astimezone()
    entry = render_commit_entry(records, subject, now)
    if not entry:
        return {"wrote": False, "reason": "empty entry"}

    log_path = (repo_root / DEV_LOG_RELATIVE_PATH).resolve()
    append_markdown_record(log_path, entry, now=now)
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "add", DEV_LOG_RELATIVE_PATH],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except Exception:
        # 开发记录追加失败不应阻断提交；下次可手动补记
        pass
    _save_state(
        repo_root,
        {
            "fingerprint": fingerprint,
            "subject": subject,
            "updated_at": now.isoformat(),
        },
    )
    return {
        "wrote": True,
        "count": len(records),
        "subject": subject,
        "log_path": str(log_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="git commit 自动追加开发记录")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--commit-msg-file", required=True)
    args = parser.parse_args(argv)
    try:
        result = append_commit_record(
            Path(args.repo_root),
            Path(args.commit_msg_file),
        )
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "wrote": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
