"""边端 sophon-demo 路径探测（SSH / Docker 内）。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from labelu.internal.common.edge_remote_paths import edge_remote_paths

DEFAULT_SOPHON_DEMO_ROOT = (
    "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
)

# Docker 容器 bm1688 常见挂载/安装位置（优先于宿主机 home 路径）
SOPHON_DEMO_FALLBACK_PATHS: Tuple[str, ...] = (
    DEFAULT_SOPHON_DEMO_ROOT,
)


def sophon_demo_candidate_paths(
    configured: str = "", *, restrict_to_configured: bool = False
) -> List[str]:
    """去重后的候选 sophon-demo 根目录（YOLOv8_plus_det）。"""
    seen: set[str] = set()
    out: List[str] = []
    fallbacks = () if restrict_to_configured else SOPHON_DEMO_FALLBACK_PATHS
    for raw in (configured.strip(),) + fallbacks:
        p = (raw or "").strip()
        edge_root = edge_remote_paths().root
        if p and p != edge_root and not p.startswith(edge_root + "/"):
            continue
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def is_valid_sophon_demo_root(path: str) -> bool:
    root = Path(path).expanduser()
    return root.is_dir() and (root / "python" / "yolov8_bmcv.py").is_file()


def _shell_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def probe_sophon_demo_root_via_ssh(
    client,
    *,
    configured: str = "",
    docker_container: str = "",
    docker_use_sudo: bool = True,
    restrict_to_configured: bool = False,
) -> Tuple[str, str]:
    """
    在边端（可选 Docker 内）探测可用的 SOPHON_DEMO_ROOT。
    返回 (resolved_path, note)；若无可用路径则 resolved_path 为空字符串。
    """
    from labelu.internal.common.edge_ssh_client import ssh_run

    dc = (docker_container or "").strip()
    docker_bin = "sudo docker" if docker_use_sudo else "docker"

    for candidate in sophon_demo_candidate_paths(
        configured, restrict_to_configured=restrict_to_configured
    ):
        inner = (
            f"test -d {_shell_quote(candidate)} && "
            f"test -f {_shell_quote(candidate + '/python/yolov8_bmcv.py')} && echo ok"
        )
        if dc:
            cmd = f"{docker_bin} exec {dc} bash -lc {_shell_quote(inner)}"
            where = f"docker:{dc}"
        else:
            cmd = inner
            where = "host"
        try:
            _code, stdout, _stderr = ssh_run(client, cmd, timeout=45)
        except Exception as exc:
            return "", f"探测失败: {exc}"
        if "ok" in (stdout or ""):
            return candidate, f"探测成功 ({where})"

    return "", "未在边端找到含 python/yolov8_bmcv.py 的 sophon-demo 目录"
