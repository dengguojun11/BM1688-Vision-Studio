#!/usr/bin/env python3
"""
Upload this repository to a Linux server (local tar.gz + SFTP + remote extract).

- Avoids fragile Windows "tar | ssh" pipes and double password prompts.
- Password: use --visible-password to see characters while typing (private machines only).
- Or set env LABELU_SSH_PASSWORD (automation only; do not commit secrets).

Requires: pip install paramiko

Examples:
  python scripts/upload_to_server.py --visible-password
  python scripts/upload_to_server.py --dry-run
  python scripts/upload_to_server.py --include-git
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import os
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    ".venv_run",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "htmlcov",
    "dist",
    "build",
    ".idea",
    ".cursor",
    ".tmp",
    "terminals",
    "coverage",
    "__MACOSX",
    "node_modules",
}

SKIP_FILE_NAMES = {"test.db", ".DS_Store"}
SKIP_FILE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".log")


def _should_skip_file(rel: Path) -> bool:
    name = rel.name
    if name in SKIP_FILE_NAMES:
        return True
    if any(name.endswith(s) for s in SKIP_FILE_SUFFIXES):
        return True
    if fnmatch.fnmatch(name, "tmp_*"):
        return True
    return False


def _prune_walk_dirs(dirs: list[str], include_git: bool) -> None:
    out = []
    for d in dirs:
        if not include_git and d == ".git":
            continue
        if d in SKIP_DIR_NAMES:
            continue
        if d.endswith(".egg-info"):
            continue
        out.append(d)
    dirs[:] = out


def build_archive(repo: Path, dest_tgz: Path, *, include_git: bool) -> None:
    with tarfile.open(dest_tgz, "w:gz", compresslevel=6) as tf:
        for root, dirs, files in os.walk(repo, topdown=True):
            root_path = Path(root)
            _prune_walk_dirs(dirs, include_git)
            for name in files:
                full = root_path / name
                try:
                    rel = full.relative_to(repo)
                except ValueError:
                    continue
                if _should_skip_file(rel):
                    continue
                tf.add(full, arcname=rel.as_posix(), recursive=False)


def main() -> int:
    try:
        import paramiko
    except ImportError:
        print("Missing dependency: pip install paramiko", file=sys.stderr)
        return 1

    p = argparse.ArgumentParser(description="Upload labelU repo (excludes venv, caches, temp files, test.db; includes .env).")
    p.add_argument("--host", default="113.141.72.175")
    p.add_argument("--port", type=int, default=12043)
    p.add_argument("--user", default="root")
    p.add_argument("--remote-path", default="/data/7G_AI/app")
    p.add_argument("--include-git", action="store_true")
    p.add_argument(
        "--visible-password",
        action="store_true",
        help="Echo password while typing (only on trusted machines).",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.dry_run:
        print("Repo:", REPO_ROOT)
        print("Remote:", f"{args.user}@{args.host}:{args.remote_path} port={args.port}")
        print("Skipped dirs:", sorted(SKIP_DIR_NAMES), "(.git kept if --include-git)")
        print("Skipped files: test.db, *.pyc, tmp_*, *.log, .DS_Store; skipped dirs include .tmp and *.egg-info")
        print(".env: INCLUDED")
        return 0

    env_pw = os.environ.get("LABELU_SSH_PASSWORD", "").strip()
    if env_pw:
        password = env_pw
        print("Using LABELU_SSH_PASSWORD from environment.")
    elif args.visible_password:
        password = input("SSH password (visible): ").strip()
    else:
        password = getpass.getpass("SSH password (hidden): ").strip()

    if not password:
        print("Empty password.", file=sys.stderr)
        return 1

    tmp = Path(tempfile.gettempdir()) / f"labelu-upload-{uuid.uuid4().hex}.tar.gz"
    try:
        print("Building archive:", tmp)
        build_archive(REPO_ROOT, tmp, include_git=args.include_git)
        size_mb = tmp.stat().st_size / (1024 * 1024)
        print(f"Archive size: {size_mb:.2f} MiB")

        transport = paramiko.Transport((args.host, args.port))
        transport.connect(username=args.user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_tmp = f"/tmp/labelu-upload-{uuid.uuid4().hex}.tar.gz"
        print("Uploading to", remote_tmp, "...")
        sftp.put(str(tmp), remote_tmp)
        sftp.close()

        cmd = (
            f"mkdir -p '{args.remote_path}' && "
            f"tar -xzf '{remote_tmp}' -C '{args.remote_path}' && "
            f"rm -f '{remote_tmp}'"
        )
        ch = transport.open_session()
        ch.exec_command(cmd)
        stderr = ch.makefile_stderr("r", -1).read().decode("utf-8", "replace")
        stdout = ch.makefile("r", -1).read().decode("utf-8", "replace")
        code = ch.recv_exit_status()
        transport.close()

        if stdout.strip():
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr.strip():
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
        if code != 0:
            print(f"Remote command failed with exit code {code}", file=sys.stderr)
            return code

        print("Done. Remote:", args.remote_path)
        print(
            "Hint: restart the isolated 7GAI supervisor after reviewing the upload: "
            "supervisorctl -c /data/7G_AI/config/supervisord.conf restart 7gai-web"
        )
        return 0
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
