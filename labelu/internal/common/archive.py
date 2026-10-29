"""Safe extraction helpers for ZIP/TAR files received from remote systems.

The standard ``extractall`` methods concatenate archive member names with the
destination path. Model packages, edge results, and remote-training payloads
can cross a trust boundary, so these helpers validate every member first and
write only regular files and directories.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Tuple


class UnsafeArchiveMember(ValueError):
    """Archive member cannot be safely written below the destination."""


def _destination_root(destination: Path) -> Path:
    destination = Path(destination)
    # Check existing ancestors before mkdir: ``mkdir`` would otherwise follow
    # a symlink such as ``/tmp/link/work`` and silently place the extraction
    # outside the caller's intended root.
    raw_destination = Path(os.path.abspath(str(destination)))
    current = raw_destination
    while True:
        if current.is_symlink():
            raise UnsafeArchiveMember(
                "archive destination must not traverse a symbolic link: {}".format(
                    destination
                )
            )
        parent = current.parent
        if parent == current:
            break
        current = parent
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    if root.is_symlink():
        raise UnsafeArchiveMember(
            "archive destination must not resolve through a symbolic link: {}".format(
                destination
            )
        )
    return root


def _member_target(root: Path, name: str) -> Tuple[Path, Tuple[str, ...]]:
    """Return a resolved target and its path components after validation."""
    if not isinstance(name, str) or not name or "\x00" in name:
        raise UnsafeArchiveMember("unsafe archive member path: {!r}".format(name))

    # Archive names are conventionally POSIX paths. Treat backslashes as
    # separators too so a Windows-produced archive cannot bypass checks on
    # Linux (or vice versa).
    normalized = name.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise UnsafeArchiveMember("unsafe archive member path: {!r}".format(name))

    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if any(re.match(r"^[A-Za-z]:", part) for part in parts):
        raise UnsafeArchiveMember("unsafe archive member path: {!r}".format(name))
    # Win32 strips trailing spaces/dots from path components. Reject names
    # that would become an empty, current-directory, or parent-directory
    # component there (for example ``".. "``).
    if any(
        part == ".." or part.rstrip(" .") in ("", ".", "..") for part in parts
    ):
        raise UnsafeArchiveMember("unsafe archive member path: {!r}".format(name))

    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=False)
        inside = os.path.commonpath((str(root), str(resolved))) == str(root)
    except (OSError, ValueError):
        inside = False
        resolved = candidate
    if not inside:
        raise UnsafeArchiveMember("unsafe archive member path: {!r}".format(name))

    # A pre-existing link in the destination could redirect a later member.
    # Check every parent before writing, in addition to resolving the target.
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise UnsafeArchiveMember(
                "archive member traverses a symbolic link: {!r}".format(name)
            )
    if candidate != root and candidate.is_symlink():
        raise UnsafeArchiveMember(
            "archive member targets a symbolic link: {!r}".format(name)
        )
    return resolved, parts


def _zip_member_is_special(member: zipfile.ZipInfo) -> bool:
    mode = (int(member.external_attr) >> 16) & 0xFFFF
    if not mode:
        return False
    file_type = stat.S_IFMT(mode)
    return file_type not in (0, stat.S_IFREG, stat.S_IFDIR)


def safe_extract_zip(zip_path: Path, destination: Path) -> Path:
    """Safely extract a ZIP archive and return the resolved destination."""
    root = _destination_root(destination)
    with zipfile.ZipFile(zip_path, "r") as archive:
        validated = []
        for member in archive.infolist():
            name = member.filename or ""
            target, parts = _member_target(root, name)
            is_dir = member.is_dir() or name.endswith(("/", "\\"))
            if _zip_member_is_special(member):
                raise UnsafeArchiveMember(
                    "unsafe archive special member: {!r}".format(name)
                )
            if not parts and not is_dir:
                raise UnsafeArchiveMember(
                    "archive file member resolves to destination root: {!r}".format(
                        name
                    )
                )
            validated.append((member, target, is_dir))

        for member, target, is_dir in validated:
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return root


def _tar_member_is_regular(member: tarfile.TarInfo) -> bool:
    return member.isfile() or member.isdir()


def safe_extract_tar(
    tar_path: Path,
    destination: Path,
    *,
    mode: str = "r:*",
) -> Path:
    """Safely extract a TAR archive and return the resolved destination.

    Links and special files are rejected instead of being materialized. The
    caller may pass an explicit mode (for example ``r:gz``) when supporting
    older Python/tar implementations.
    """
    root = _destination_root(destination)
    with tarfile.open(tar_path, mode) as archive:
        validated = []
        for member in archive.getmembers():
            target, parts = _member_target(root, member.name or "")
            if not _tar_member_is_regular(member):
                raise UnsafeArchiveMember(
                    "unsafe archive link or special member: {!r}".format(member.name)
                )
            is_dir = member.isdir()
            if not parts and not is_dir:
                raise UnsafeArchiveMember(
                    "archive file member resolves to destination root: {!r}".format(
                        member.name
                    )
                )
            validated.append((member, target, is_dir))

        for member, target, is_dir in validated:
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise UnsafeArchiveMember(
                    "archive member has no file payload: {!r}".format(member.name)
                )
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return root


__all__ = ["UnsafeArchiveMember", "safe_extract_tar", "safe_extract_zip"]
