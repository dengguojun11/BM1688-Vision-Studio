#!/usr/bin/env python3
"""部署/启动时准备 BM1688 Docker 量化环境（由 deploy.sh 与 seed 调用）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _bootstrap_script() -> Path:
    import labelu

    # labelu 包目录的上一级为仓库根（含 scripts/bm1688_docker_bootstrap.sh）
    return Path(labelu.__file__).resolve().parent.parent / "scripts" / "bm1688_docker_bootstrap.sh"


def bootstrap_bm1688_docker(*, mount_path: str | None = None) -> tuple[bool, str]:
    script = _bootstrap_script()
    if not script.is_file():
        return False, f"缺少脚本: {script}"

    mount = (mount_path or os.environ.get("LABELU_DEPLOY_RESOLVED_BASE") or "").strip()
    if not mount:
        from labelu.internal.common.bm1688_deploy_bootstrap import _deploy_base_dir

        mount = str(_deploy_base_dir())

    env = os.environ.copy()
    env.setdefault("LABELU_DEPLOY_RESOLVED_BASE", mount)

    try:
        proc = subprocess.run(
            ["bash", str(script), mount],
            capture_output=True,
            text=True,
            timeout=7200,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "BM1688 Docker bootstrap 超时（>7200s）"
    except OSError as exc:
        return False, str(exc)

    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return False, log[-4000:] or f"exit={proc.returncode}"
    return True, log[-2000:] or "ok"


def main() -> int:
    ok, msg = bootstrap_bm1688_docker()
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
