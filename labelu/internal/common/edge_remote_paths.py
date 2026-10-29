from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass


DEFAULT_EDGE_REMOTE_ROOT = "/data/labelu_edge"


def normalize_edge_root(value: str | None = None) -> str:
    if value is None:
        from labelu.internal.common.config import settings

        value = getattr(settings, "EDGE_REMOTE_ROOT", DEFAULT_EDGE_REMOTE_ROOT)
    raw = str(value or DEFAULT_EDGE_REMOTE_ROOT).strip().replace("\\", "/")
    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/") or normalized in ("/", "/data"):
        raise ValueError("edge remote root must be a safe absolute directory")
    return normalized.rstrip("/")


def require_edge_path(path: str, *, root: str | None = None) -> str:
    edge_root = normalize_edge_root(root)
    normalized = posixpath.normpath(str(path or "").strip().replace("\\", "/"))
    if normalized != edge_root and not normalized.startswith(edge_root + "/"):
        raise ValueError(f"remote path escapes edge root: {path}")
    return normalized


def remote_edge_path_guard_command(path: str, *, root: str | None = None) -> str:
    edge_root = normalize_edge_root(root)
    target = require_edge_path(path, root=edge_root)
    root_q = shlex.quote(edge_root)
    target_q = shlex.quote(target)
    return (
        f"test ! -L {root_q}; "
        f"edge_root_real=$(readlink -m {root_q}); "
        f"edge_target_real=$(readlink -m {target_q}); "
        'case "$edge_target_real" in "$edge_root_real"|"$edge_root_real"/*) ;; '
        '*) echo "remote path escapes edge root" >&2; exit 73;; esac'
    )


@dataclass(frozen=True)
class EdgeRemotePaths:
    root: str
    jobs: str
    runtime: str
    runtime_archives: str
    runtime_versions: str
    sophon_demo_install: str
    sophon_demo_root: str
    validator: str
    python_packages: str
    bin: str
    tmp: str
    cache: str
    logs: str
    results: str

    def job(self, name: str) -> str:
        safe_name = str(name or "").strip()
        if not safe_name or "/" in safe_name or safe_name in (".", ".."):
            raise ValueError(f"invalid edge job directory name: {name}")
        return require_edge_path(posixpath.join(self.jobs, safe_name), root=self.root)


def edge_remote_paths(root: str | None = None) -> EdgeRemotePaths:
    edge_root = normalize_edge_root(root)
    runtime = require_edge_path(posixpath.join(edge_root, "runtime"), root=edge_root)
    demo_install = require_edge_path(
        posixpath.join(runtime, "sophon-demo"), root=edge_root
    )
    return EdgeRemotePaths(
        root=edge_root,
        jobs=require_edge_path(posixpath.join(edge_root, "jobs"), root=edge_root),
        runtime=runtime,
        runtime_archives=require_edge_path(
            posixpath.join(runtime, "archives"), root=edge_root
        ),
        runtime_versions=require_edge_path(
            posixpath.join(runtime, "versions"), root=edge_root
        ),
        sophon_demo_install=demo_install,
        sophon_demo_root=require_edge_path(
            posixpath.join(demo_install, "sample", "YOLOv8_plus_det"),
            root=edge_root,
        ),
        validator=require_edge_path(
            posixpath.join(runtime, "validator"), root=edge_root
        ),
        python_packages=require_edge_path(
            posixpath.join(runtime, "python-packages"), root=edge_root
        ),
        bin=require_edge_path(posixpath.join(edge_root, "bin"), root=edge_root),
        tmp=require_edge_path(posixpath.join(edge_root, "tmp"), root=edge_root),
        cache=require_edge_path(posixpath.join(edge_root, "cache"), root=edge_root),
        logs=require_edge_path(posixpath.join(edge_root, "logs"), root=edge_root),
        results=require_edge_path(posixpath.join(edge_root, "results"), root=edge_root),
    )
