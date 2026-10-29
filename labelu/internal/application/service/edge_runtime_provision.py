from __future__ import annotations

import base64
import hashlib
import os
import shlex
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from labelu.internal.application.service import edge_ssh_validation
from labelu.internal.common.config import settings
from labelu.internal.common.edge_remote_paths import (
    edge_remote_paths,
    remote_edge_path_guard_command,
    require_edge_path,
)

RUNTIME_FORMAT = "labelu_bm1688_edge_runtime_v1"
DEFAULT_DEMO_ROOT = "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det"


class EdgeRuntimeProvisionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "edge_runtime_provision_failed",
        report: Optional[dict[str, Any]] = None,
        result: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.report = report or {}
        self.result = result or {}


_STORAGE_FAILURE_REASONS = {
    "runtime_disk_space_insufficient",
    "runtime_inodes_insufficient",
    "tmp_disk_space_insufficient",
    "tmp_inodes_insufficient",
    "docker_disk_space_insufficient",
    "docker_inodes_insufficient",
    "data_disk_space_insufficient",
    "data_inodes_insufficient",
}

_LEGACY_EDGE_PATHS = (
    "/data/YOLOv11_det-TPU-main",
    "/data/edge-device-service",
    "/data/edge_eval_pack",
    "/data/labelu_board_agent.py",
    "/data/labelu_board_jobs",
    "/data/labelu_edge_runtime",
    "/data/labelu_edge_eval",
    "/data/labelu_bmodel_validator",
    "/data/sophon-demo",
    "/home/linaro/sophon-work",
    "/usr/local/bin/bmodel-validate",
)

# Fixed device log roots. Cleanup is limited to regular log files under these
# exact paths; no configured or caller-provided path can enter this allowlist.
_DEVICE_LOG_ROOTS = ("/data/logs",)
_HISTORICAL_JOB_MIN_AGE_MINUTES = 10


def storage_failure_reasons(report: dict[str, Any] | None) -> list[str]:
    return [
        str(reason)
        for reason in list((report or {}).get("fatal_reasons") or [])
        if str(reason) in _STORAGE_FAILURE_REASONS
    ]


def has_storage_pressure(report: dict[str, Any] | None) -> bool:
    return bool(storage_failure_reasons(report))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            block = fp.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _cache_dir() -> Path:
    path = Path(settings.BASE_DATA_DIR).resolve() / "edge_runtime" / "offline_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_archive(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    filename = Path(parsed.path).name or "bm1688-edge-runtime-offline.tar"
    destination = _cache_dir() / filename
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with (
            urllib.request.urlopen(url, timeout=120) as response,
            temporary.open("wb") as fp,
        ):
            while True:
                block = response.read(8 * 1024 * 1024)
                if not block:
                    break
                fp.write(block)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise EdgeRuntimeProvisionError("边端离线运行时下载结果为空")
        os.replace(temporary, destination)
    except Exception as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, EdgeRuntimeProvisionError):
            raise
        raise EdgeRuntimeProvisionError(
            f"LabelU 服务端下载边端离线运行时失败: {exc}"
        ) from exc
    return destination


def resolve_offline_archive() -> tuple[Path, str]:
    configured = str(
        getattr(settings, "EDGE_RUNTIME_OFFLINE_ARCHIVE", "") or ""
    ).strip()
    configured_url = str(
        getattr(settings, "EDGE_RUNTIME_OFFLINE_ARCHIVE_URL", "") or ""
    ).strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(os.path.expandvars(configured)).expanduser())
    candidates.extend(
        sorted(_cache_dir().glob("*edge-runtime-offline*.tar*"), reverse=True)
    )
    if not configured and not configured_url:
        repo_root = Path(__file__).resolve().parents[4]
        candidates.extend(
            sorted(
                (repo_root / ".tmp" / "edge_delivery").glob(
                    "*edge-runtime-offline*.tar*"
                ),
                reverse=True,
            )
        )

    archive = next((path.resolve() for path in candidates if path.is_file()), None)
    if archive is None and configured_url:
        archive = _download_archive(configured_url).resolve()
    if archive is None:
        raise EdgeRuntimeProvisionError(
            "未找到 BM1688 边端离线运行时包；请配置 EDGE_RUNTIME_OFFLINE_ARCHIVE "
            "或 EDGE_RUNTIME_OFFLINE_ARCHIVE_URL"
        )

    digest = _sha256(archive)
    expected = (
        str(getattr(settings, "EDGE_RUNTIME_OFFLINE_SHA256", "") or "").strip().lower()
    )
    if expected and digest.lower() != expected:
        raise EdgeRuntimeProvisionError(
            f"边端离线运行时 SHA256 校验失败: expected={expected}, actual={digest}"
        )
    return archive, digest


def _parse_probe_output(output: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for raw in str(output or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()

    bool_keys = {
        "architecture_ok",
        "bm1688_device_ok",
        "libsophon_ok",
        "docker_ok",
        "docker_image_ok",
        "container_ok",
        "python_runtime_ok",
        "sophon_demo_ok",
        "validator_ok",
        "noninteractive_root_ok",
    }
    report: dict[str, Any] = {
        "format": "labelu_bm1688_environment_report_v1",
        "architecture": values.get("architecture") or "unknown",
        "os": values.get("os") or "unknown",
        "os_version": values.get("os_version") or "unknown",
        "container": values.get("container") or "bm1688",
        "sophon_demo_root": values.get("sophon_demo_root") or None,
        "runtime_path": values.get("runtime_path") or None,
        "runtime_mount": values.get("runtime_mount") or None,
        "runtime_source": values.get("runtime_source") or None,
        "tmp_mount": values.get("tmp_mount") or None,
        "tmp_source": values.get("tmp_source") or None,
        "docker_mount": values.get("docker_mount") or None,
        "docker_source": values.get("docker_source") or None,
        "data_mount": values.get("data_mount") or None,
        "data_source": values.get("data_source") or None,
    }
    for key in bool_keys:
        report[key] = values.get(key) == "true"
    try:
        report["free_mb"] = int(values.get("free_mb") or 0)
    except ValueError:
        report["free_mb"] = 0
    for key in (
        "free_inodes",
        "tmp_free_mb",
        "tmp_free_inodes",
        "docker_free_mb",
        "docker_free_inodes",
        "data_free_mb",
        "data_free_inodes",
    ):
        if key not in values:
            report[key] = None
            continue
        try:
            report[key] = int(values.get(key) or 0)
        except ValueError:
            report[key] = 0
    report["docker_root"] = values.get("docker_root") or None

    fatal: list[str] = []
    if not report["architecture_ok"]:
        fatal.append("architecture_not_aarch64")
    if not report["bm1688_device_ok"]:
        fatal.append("bm1688_device_node_missing")
    if not report["libsophon_ok"]:
        fatal.append("libsophon_missing")
    if not report["docker_ok"]:
        fatal.append("docker_unavailable")

    min_runtime_mb = int(getattr(settings, "EDGE_RUNTIME_MIN_FREE_MB", 2048) or 2048)
    min_tmp_mb = int(getattr(settings, "EDGE_RUNTIME_MIN_TMP_FREE_MB", 256) or 256)
    min_docker_mb = int(
        getattr(settings, "EDGE_RUNTIME_MIN_DOCKER_FREE_MB", 512) or 512
    )
    min_data_mb = int(
        getattr(settings, "EDGE_RUNTIME_MIN_DATA_FREE_MB", 2048) or 2048
    )
    min_inodes = int(getattr(settings, "EDGE_RUNTIME_MIN_FREE_INODES", 1024) or 1024)
    report["minimum_free_mb"] = min_runtime_mb
    report["minimum_tmp_free_mb"] = min_tmp_mb
    report["minimum_docker_free_mb"] = min_docker_mb
    report["minimum_data_free_mb"] = min_data_mb
    report["minimum_free_inodes"] = min_inodes
    if "free_mb" in values and report["free_mb"] < min_runtime_mb:
        fatal.append("runtime_disk_space_insufficient")
    if report["free_inodes"] is not None and report["free_inodes"] < min_inodes:
        fatal.append("runtime_inodes_insufficient")
    if report["tmp_free_mb"] is not None and report["tmp_free_mb"] < min_tmp_mb:
        fatal.append("tmp_disk_space_insufficient")
    if report["tmp_free_inodes"] is not None and report["tmp_free_inodes"] < min_inodes:
        fatal.append("tmp_inodes_insufficient")
    if (
        report["docker_free_mb"] is not None
        and report["docker_free_mb"] < min_docker_mb
    ):
        fatal.append("docker_disk_space_insufficient")
    if (
        report["docker_free_inodes"] is not None
        and report["docker_free_inodes"] < min_inodes
    ):
        fatal.append("docker_inodes_insufficient")
    if report["data_free_mb"] is not None and report["data_free_mb"] < min_data_mb:
        fatal.append("data_disk_space_insufficient")
    if (
        report["data_free_inodes"] is not None
        and report["data_free_inodes"] < min_inodes
    ):
        fatal.append("data_inodes_insufficient")

    repairable = [
        reason
        for reason, key in (
            ("docker_image_missing", "docker_image_ok"),
            ("container_not_ready", "container_ok"),
            ("python_runtime_not_ready", "python_runtime_ok"),
            ("sophon_demo_missing", "sophon_demo_ok"),
            ("validator_missing", "validator_ok"),
        )
        if not report[key]
    ]
    core_ready = not fatal
    report["environment_ready"] = core_ready and not repairable
    if repairable and not report["noninteractive_root_ok"]:
        fatal.append("noninteractive_root_unavailable")
    report["fatal_reasons"] = fatal
    report["repairable_reasons"] = repairable
    report["provisionable"] = bool(repairable) and not fatal
    return report


def probe_edge_runtime(
    client,
    *,
    configured_demo_root: str = "",
    docker_container: str = "bm1688",
    remote_root: str = "",
) -> dict[str, Any]:
    paths = edge_remote_paths()
    if remote_root:
        require_edge_path(remote_root, root=paths.root)
    demo_paths = [paths.sophon_demo_root]
    demo_words = " ".join(shlex.quote(path) for path in demo_paths)
    container = str(docker_container or "bm1688").strip() or "bm1688"
    disk_path = paths.runtime
    script = f"""
set +e
echo LABELU_EDGE_PREFLIGHT_V1=1
arch=$(uname -m 2>/dev/null || echo unknown)
echo architecture=$arch
[ "$arch" = aarch64 ] && echo architecture_ok=true || echo architecture_ok=false
os_id=unknown; os_version=unknown
[ -r /etc/os-release ] && . /etc/os-release && os_id=${{ID:-unknown}} && os_version=${{VERSION_ID:-unknown}}
echo os=$os_id
echo os_version=$os_version
[ -e /dev/bmdev-ctl ] || [ -e /dev/bm-tpu0 ] || [ -e /dev/bm-sophon0 ]; [ $? -eq 0 ] && echo bm1688_device_ok=true || echo bm1688_device_ok=false
[ -d /opt/sophon/libsophon-current ]; [ $? -eq 0 ] && echo libsophon_ok=true || echo libsophon_ok=false
D=""
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then D=docker; fi
if [ -z "$D" ] && command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then D="sudo -n docker"; fi
[ -n "$D" ] && echo docker_ok=true || echo docker_ok=false
if [ -n "$D" ] && $D image inspect 7g >/dev/null 2>&1; then echo docker_image_ok=true; else echo docker_image_ok=false; fi
if [ -n "$D" ] && [ "$($D inspect -f "{{{{.State.Running}}}}" {shlex.quote(container)} 2>/dev/null)" = true ]; then echo container_ok=true; else echo container_ok=false; fi
if [ -n "$D" ] && $D exec {shlex.quote(container)} python3 -c "import cv2,numpy,sophon.sail" >/dev/null 2>&1; then echo python_runtime_ok=true; else echo python_runtime_ok=false; fi
demo_root=""
for p in {demo_words}; do [ -f "$p/python/yolov8_bmcv.py" ] && demo_root="$p" && break; done
[ -n "$demo_root" ] && echo sophon_demo_ok=true || echo sophon_demo_ok=false
echo sophon_demo_root=$demo_root
[ -f {shlex.quote(paths.validator + '/app/validate.py')} ] && [ -x {shlex.quote(paths.bin + '/bmodel-validate')} ]; [ $? -eq 0 ] && echo validator_ok=true || echo validator_ok=false
if [ "$(id -u)" = 0 ] || (command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1); then echo noninteractive_root_ok=true; else echo noninteractive_root_ok=false; fi
probe_path={shlex.quote(disk_path)}
while [ ! -e "$probe_path" ] && [ "$probe_path" != / ]; do probe_path=$(dirname "$probe_path"); done
echo runtime_path=$probe_path
df -Pm "$probe_path" 2>/dev/null | awk 'NR==2 {{print "free_mb=" $4}}'
df -Pi "$probe_path" 2>/dev/null | awk 'NR==2 {{print "free_inodes=" $4}}'
findmnt -n -o TARGET -T "$probe_path" 2>/dev/null | head -n 1 | sed 's/^/runtime_mount=/'
findmnt -n -o SOURCE -T "$probe_path" 2>/dev/null | head -n 1 | sed 's/^/runtime_source=/'
df -Pm /tmp 2>/dev/null | awk 'NR==2 {{print "tmp_free_mb=" $4}}'
df -Pi /tmp 2>/dev/null | awk 'NR==2 {{print "tmp_free_inodes=" $4}}'
findmnt -n -o TARGET -T /tmp 2>/dev/null | head -n 1 | sed 's/^/tmp_mount=/'
findmnt -n -o SOURCE -T /tmp 2>/dev/null | head -n 1 | sed 's/^/tmp_source=/'
if [ -e /data ]; then
  df -Pm /data 2>/dev/null | awk 'NR==2 {{print "data_free_mb=" $4}}'
  df -Pi /data 2>/dev/null | awk 'NR==2 {{print "data_free_inodes=" $4}}'
  findmnt -n -o TARGET -T /data 2>/dev/null | head -n 1 | sed 's/^/data_mount=/'
  findmnt -n -o SOURCE -T /data 2>/dev/null | head -n 1 | sed 's/^/data_source=/'
fi
if [ -n "$D" ]; then
  docker_root=$($D info --format '{{{{.DockerRootDir}}}}' 2>/dev/null)
  echo docker_root=$docker_root
  if [ -n "$docker_root" ]; then
    df -Pm "$docker_root" 2>/dev/null | awk 'NR==2 {{print "docker_free_mb=" $4}}'
    df -Pi "$docker_root" 2>/dev/null | awk 'NR==2 {{print "docker_free_inodes=" $4}}'
    findmnt -n -o TARGET -T "$docker_root" 2>/dev/null | head -n 1 | sed 's/^/docker_mount=/'
    findmnt -n -o SOURCE -T "$docker_root" 2>/dev/null | head -n 1 | sed 's/^/docker_source=/'
  fi
fi
echo container={shlex.quote(container)}
"""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    command = f"printf %s {shlex.quote(encoded)} | base64 -d | sh"
    code, out, err = edge_ssh_validation.ssh_run(client, command, timeout=120)
    if code != 0:
        raise EdgeRuntimeProvisionError(
            f"边端运行时预检失败 (exit={code}): {(err or out)[-800:]}"
        )
    report = _parse_probe_output(out)
    report["raw_output_tail"] = str(out or "")[-2000:]
    return report


def _prepare_remote_runtime_root(client, remote_root: str) -> None:
    paths = edge_remote_paths()
    remote_root = require_edge_path(remote_root, root=paths.root)
    quoted = shlex.quote(remote_root)
    writable_dirs = " ".join(
        shlex.quote(path)
        for path in (
            paths.root,
            paths.jobs,
            paths.runtime,
            paths.runtime_archives,
            paths.runtime_versions,
            paths.python_packages,
            paths.bin,
            paths.tmp,
            paths.cache,
            paths.logs,
            paths.results,
        )
    )
    command = (
        f"{remote_edge_path_guard_command(paths.root, root=paths.root)}; "
        f"mkdir -p {writable_dirs} 2>/dev/null || "
        f"sudo -n install -d -o \"$(id -u)\" -g \"$(id -g)\" {writable_dirs}; "
        f"test -w {quoted}"
    )
    code, out, err = edge_ssh_validation.ssh_run(client, command, timeout=120)
    if code != 0:
        raise EdgeRuntimeProvisionError(
            f"无法创建边端运行时缓存目录 {remote_root}: {(err or out)[-800:]}"
        )


def cleanup_legacy_edge_installations(client) -> dict[str, Any]:
    """Remove only explicitly known LabelU layouts from before the single-root policy."""
    allowed = "|".join(_LEGACY_EDGE_PATHS)
    targets = " ".join(shlex.quote(path) for path in _LEGACY_EDGE_PATHS)
    script = f"""
set -eu
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop labelu-board-agent.service >/dev/null 2>&1 || true
  systemctl disable labelu-board-agent.service >/dev/null 2>&1 || true
fi
pkill -f '^/usr/bin/python3 /data/labelu_board_agent[.]py($| )' >/dev/null 2>&1 || true
rm -f -- /etc/systemd/system/labelu-board-agent.service \
  /etc/systemd/system/multi-user.target.wants/labelu-board-agent.service
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload >/dev/null 2>&1 || true
fi
for legacy_path in {targets}; do
  case "$legacy_path" in
    {allowed}) ;;
    *) echo "rejected=$legacy_path" >&2; exit 91 ;;
  esac
  if [ -e "$legacy_path" ] || [ -L "$legacy_path" ]; then
    rm -rf -- "$legacy_path"
    echo "removed=$legacy_path"
  fi
done
"""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    payload = f"printf %s {shlex.quote(encoded)} | base64 -d"
    command = (
        f'if [ "$(id -u)" = 0 ]; then {payload} | sh; '
        f"else {payload} | sudo -n sh; fi"
    )
    code, out, err = edge_ssh_validation.ssh_run(client, command, timeout=600)
    if code != 0:
        raise EdgeRuntimeProvisionError(
            f"清理边端历史 LabelU 目录失败 (exit={code}): {(err or out)[-800:]}",
            code="legacy_cleanup_failed",
        )
    return {
        "format": "labelu_edge_legacy_cleanup_v1",
        "status": "completed",
        "removed": [
            line.split("=", 1)[1].strip()
            for line in str(out or "").splitlines()
            if line.startswith("removed=")
        ],
    }


def prepare_edge_root_and_cleanup_legacy(client) -> dict[str, Any]:
    paths = edge_remote_paths()
    _prepare_remote_runtime_root(client, paths.runtime)
    return cleanup_legacy_edge_installations(client)


def _storage_cleanup_paths(
    report: dict[str, Any], *, remote_root: str
) -> list[str]:
    if not storage_failure_reasons(report):
        return []
    paths = edge_remote_paths()
    require_edge_path(remote_root, root=paths.root)
    return [paths.logs, *_DEVICE_LOG_ROOTS]


def cleanup_edge_storage(
    client,
    report: dict[str, Any],
    *,
    remote_root: str,
) -> dict[str, Any]:
    """First-stage cleanup: delete only expired files from fixed log roots."""
    paths = edge_remote_paths()
    low_paths = _storage_cleanup_paths(report, remote_root=remote_root)
    cleanup: dict[str, Any] = {
        "format": "labelu_edge_storage_cleanup_v1",
        "attempted": bool(low_paths),
        "low_paths": low_paths,
        "actions": [],
        "errors": [],
    }
    if not low_paths:
        cleanup["status"] = "not_required"
        return cleanup
    if not bool(getattr(settings, "EDGE_RUNTIME_AUTO_LOG_CLEANUP_ENABLED", True)):
        cleanup["status"] = "disabled"
        return cleanup

    retention_days = max(
        1, int(getattr(settings, "EDGE_RUNTIME_LOG_RETENTION_DAYS", 7) or 7)
    )
    log_roots = " ".join(shlex.quote(path) for path in low_paths)
    allowed_roots = "|".join(low_paths)
    script = rf"""
set +e
echo LABELU_EDGE_STORAGE_CLEANUP_V1=1
freed_kb=0
for log_root in {log_roots}; do
  case "$log_root" in
    {allowed_roots}) ;;
    *) echo error=rejected_log_root; continue ;;
  esac
  [ -d "$log_root" ] || continue
  [ ! -L "$log_root" ] || {{ echo error=symlink_log_root_rejected; continue; }}
  before=$(du -skx "$log_root" 2>/dev/null | awk '{{print $1}}')
  find "$log_root" -xdev -type f -mtime +{retention_days} \
    \( -name 'log' -o -name 'log.*' -o -name '*.log' -o -name '*.log.*' \
       -o -name '*.out' -o -name '*.out.*' -o -name '*.err' -o -name '*.err.*' \) \
    -delete >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo action=expired_log_files_older_than_{retention_days}d:$log_root
  else
    echo error=log_cleanup_failed:$log_root
  fi
  after=$(du -skx "$log_root" 2>/dev/null | awk '{{print $1}}')
  if [ -n "$before" ] && [ -n "$after" ] && [ "$before" -ge "$after" ]; then
    freed_kb=$((freed_kb+before-after))
  fi
done
echo log_files_freed_kb=$freed_kb
"""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    payload = f"printf %s {shlex.quote(encoded)} | base64 -d"
    command = (
        f"{remote_edge_path_guard_command(low_paths[0], root=paths.root)}; "
        f'if [ "$(id -u)" = 0 ]; then {payload} | sh; '
        f"else {payload} | sudo -n sh; fi"
    )
    code, out, err = edge_ssh_validation.ssh_run(client, command, timeout=600)
    values: dict[str, str] = {}
    for raw_line in str(out or "").splitlines():
        if raw_line.startswith("action="):
            cleanup["actions"].append(raw_line.split("=", 1)[1].strip())
        elif raw_line.startswith("error="):
            cleanup["errors"].append(raw_line.split("=", 1)[1].strip())
        elif "=" in raw_line:
            key, value = raw_line.split("=", 1)
            values[key.strip()] = value.strip()
    for key in ("log_files_freed_kb",):
        try:
            cleanup[key] = max(0, int(values.get(key) or 0))
        except ValueError:
            cleanup[key] = 0
    if code != 0:
        cleanup["errors"].append(((err or out) or f"exit={code}")[-800:])
    cleanup["status"] = "completed" if not cleanup["errors"] else "completed_with_errors"
    return cleanup


def cleanup_edge_historical_jobs(
    client,
    report: dict[str, Any],
    *,
    remote_root: str,
) -> dict[str, Any]:
    """Second-stage cleanup: remove only stale LabelU validation job folders."""
    cleanup: dict[str, Any] = {
        "format": "labelu_edge_historical_job_cleanup_v1",
        "attempted": has_storage_pressure(report),
        "removed": [],
        "errors": [],
    }
    if not cleanup["attempted"]:
        cleanup["status"] = "not_required"
        return cleanup

    paths = edge_remote_paths()
    require_edge_path(remote_root, root=paths.root)
    jobs_root = require_edge_path(paths.jobs, root=paths.root)
    quoted_jobs = shlex.quote(jobs_root)
    script = rf"""
set +e
echo LABELU_EDGE_HISTORICAL_JOB_CLEANUP_V1=1
before=$(du -skx {quoted_jobs} 2>/dev/null | awk '{{print $1}}')
if [ -d {quoted_jobs} ] && [ ! -L {quoted_jobs} ]; then
  cd {quoted_jobs} || exit 1
  for name in $(find . -mindepth 1 -maxdepth 1 -type d -mmin +{_HISTORICAL_JOB_MIN_AGE_MINUTES} \
    \( -name 'manual-job-[0-9]*' -o -name 'export-[0-9]*' \) -printf '%f\n'); do
    case "$name" in
      manual-job-[0-9]*|export-[0-9]*)
        rm -rf -- "$name"
        [ $? -eq 0 ] && echo removed=$name || echo error=job_cleanup_failed:$name
        ;;
      *) echo error=rejected_job_name:$name ;;
    esac
  done
fi
after=$(du -skx {quoted_jobs} 2>/dev/null | awk '{{print $1}}')
if [ -n "$before" ] && [ -n "$after" ] && [ "$before" -ge "$after" ]; then
  echo historical_jobs_freed_kb=$((before-after))
else
  echo historical_jobs_freed_kb=0
fi
"""
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    payload = f"printf %s {shlex.quote(encoded)} | base64 -d"
    command = (
        f"{remote_edge_path_guard_command(jobs_root, root=paths.root)}; "
        f'if [ "$(id -u)" = 0 ]; then {payload} | sh; '
        f"else {payload} | sudo -n sh; fi"
    )
    code, out, err = edge_ssh_validation.ssh_run(client, command, timeout=600)
    values: dict[str, str] = {}
    for raw_line in str(out or "").splitlines():
        if raw_line.startswith("removed="):
            cleanup["removed"].append(raw_line.split("=", 1)[1].strip())
        elif raw_line.startswith("error="):
            cleanup["errors"].append(raw_line.split("=", 1)[1].strip())
        elif "=" in raw_line:
            key, value = raw_line.split("=", 1)
            values[key.strip()] = value.strip()
    try:
        cleanup["historical_jobs_freed_kb"] = max(
            0, int(values.get("historical_jobs_freed_kb") or 0)
        )
    except ValueError:
        cleanup["historical_jobs_freed_kb"] = 0
    if code != 0:
        cleanup["errors"].append(((err or out) or f"exit={code}")[-800:])
    cleanup["status"] = "completed" if not cleanup["errors"] else "completed_with_errors"
    return cleanup


def _freed_space_mb(before: dict[str, Any], after: dict[str, Any]) -> int:
    deltas = []
    for key in ("free_mb", "tmp_free_mb", "docker_free_mb", "data_free_mb"):
        old = before.get(key)
        new = after.get(key)
        if old is None or new is None:
            continue
        try:
            deltas.append(max(0, int(new) - int(old)))
        except (TypeError, ValueError):
            continue
    return max(deltas, default=0)


def ensure_edge_runtime(
    client,
    *,
    configured_demo_root: str,
    docker_container: str,
    reconnect: Callable[[], Any],
    progress_callback: Optional[Callable[[int, int], None]] = None,
    retry_callback: Optional[Callable[[int, int, Exception], None]] = None,
) -> tuple[Any, dict[str, Any]]:
    paths = edge_remote_paths()
    remote_root = paths.runtime
    legacy_cleanup = prepare_edge_root_and_cleanup_legacy(client)
    before = probe_edge_runtime(
        client,
        configured_demo_root=paths.sophon_demo_root,
        docker_container=docker_container,
        remote_root=remote_root,
    )
    result: dict[str, Any] = {
        "before": before,
        "provisioned": False,
        "legacy_cleanup": legacy_cleanup,
    }
    current = before
    if has_storage_pressure(current):
        cleanup = cleanup_edge_storage(client, current, remote_root=remote_root)
        after_log_cleanup = probe_edge_runtime(
            client,
            configured_demo_root=paths.sophon_demo_root,
            docker_container=docker_container,
            remote_root=remote_root,
        )
        cleanup["freed_mb"] = _freed_space_mb(current, after_log_cleanup)
        cleanup["remaining_reasons"] = storage_failure_reasons(after_log_cleanup)
        result["storage_cleanup"] = cleanup
        result["after_log_cleanup"] = after_log_cleanup
        current = after_log_cleanup

        if has_storage_pressure(current):
            historical_cleanup = cleanup_edge_historical_jobs(
                client,
                current,
                remote_root=remote_root,
            )
            after_historical_cleanup = probe_edge_runtime(
                client,
                configured_demo_root=paths.sophon_demo_root,
                docker_container=docker_container,
                remote_root=remote_root,
            )
            historical_cleanup["freed_mb"] = _freed_space_mb(
                current, after_historical_cleanup
            )
            historical_cleanup["remaining_reasons"] = storage_failure_reasons(
                after_historical_cleanup
            )
            result["historical_job_cleanup"] = historical_cleanup
            result["after_historical_cleanup"] = after_historical_cleanup
            current = after_historical_cleanup

        cleanup["manual_intervention_required"] = has_storage_pressure(current)
        current["storage_cleanup"] = cleanup
        if result.get("historical_job_cleanup"):
            current["historical_job_cleanup"] = result["historical_job_cleanup"]
        current["manual_intervention_required"] = has_storage_pressure(current)
        result["after_cleanup"] = current

    if current["environment_ready"]:
        result["after"] = current
        result["environment_ready"] = True
        result["sophon_demo_root"] = current.get("sophon_demo_root")
        return client, result
    if has_storage_pressure(current):
        raise EdgeRuntimeProvisionError(
            "安全日志自动清理后空间或 inode 仍不足，需要人工检查: "
            + ", ".join(storage_failure_reasons(current)),
            code="manual_cleanup_required",
            report=current,
            result=result,
        )
    if current["fatal_reasons"]:
        raise EdgeRuntimeProvisionError(
            "边端环境存在不可自动修复项: " + ", ".join(current["fatal_reasons"]),
            code="hardware_prerequisite_missing",
            report=current,
            result=result,
        )
    if not bool(getattr(settings, "EDGE_RUNTIME_AUTO_PROVISION_ENABLED", True)):
        raise EdgeRuntimeProvisionError(
            "边端环境缺少可修复组件，但 EDGE_RUNTIME_AUTO_PROVISION_ENABLED 未开启: "
            + ", ".join(current["repairable_reasons"]),
            code="runtime_components_missing",
            report=current,
            result=result,
        )

    archive, digest = resolve_offline_archive()
    minimum_free = max(
        int(getattr(settings, "EDGE_RUNTIME_MIN_FREE_MB", 2048) or 2048),
        int(archive.stat().st_size / (1024 * 1024)) + 256,
    )
    if int(current.get("free_mb") or 0) < minimum_free:
        raise EdgeRuntimeProvisionError(
            f"边端磁盘空间不足: available={current.get('free_mb')}MB, required={minimum_free}MB",
            code="manual_cleanup_required",
            report=current,
            result=result,
        )

    remote_archive = require_edge_path(
        f"{paths.runtime_archives}/{digest}.tar", root=paths.root
    )
    client = edge_ssh_validation._upload_edge_bundle_with_reconnect(
        client,
        archive,
        remote_archive,
        reconnect=reconnect,
        progress_callback=progress_callback,
        retry_callback=retry_callback,
    )
    version_root = require_edge_path(
        f"{paths.runtime_versions}/{digest}", root=paths.root
    )
    package_root = f"{version_root}/bm1688_edge_runtime"
    setup_env = " ".join(
        (
            f"LABELU_EDGE_ROOT={shlex.quote(paths.root)}",
            f"LABELU_VALIDATOR_ROOT={shlex.quote(paths.validator)}",
            f"LABELU_SOPHON_DEMO_INSTALL_BASE={shlex.quote(paths.sophon_demo_install)}",
            f"LABELU_EDGE_BIN_DIR={shlex.quote(paths.bin)}",
            f"LABELU_PYTHON_PACKAGES={shlex.quote(paths.python_packages)}",
            f"TMPDIR={shlex.quote(paths.tmp)}",
        )
    )
    setup_command = (
        f"set -e; {remote_edge_path_guard_command(version_root, root=paths.root)}; "
        f"mkdir -p {shlex.quote(version_root)}; "
        f"if [ ! -f {shlex.quote(package_root + '/setup.sh')} ]; then "
        f"tar -xf {shlex.quote(remote_archive)} -C {shlex.quote(version_root)}; fi; "
        f"if [ \"$(id -u)\" = 0 ]; then env {setup_env} bash {shlex.quote(package_root + '/setup.sh')}; "
        f"else sudo -n env {setup_env} bash {shlex.quote(package_root + '/setup.sh')}; fi"
    )
    code, out, err = edge_ssh_validation.ssh_run(
        client,
        setup_command,
        timeout=int(
            getattr(settings, "EDGE_RUNTIME_PROVISION_TIMEOUT_SEC", 3600) or 3600
        ),
    )
    result.update(
        {
            "provisioned": True,
            "archive": str(archive),
            "archive_sha256": digest,
            "remote_archive": remote_archive,
            "setup_exit_code": code,
            "setup_stdout_tail": str(out or "")[-3000:],
            "setup_stderr_tail": str(err or "")[-2000:],
        }
    )
    if code != 0:
        failure_text = (err or out)[-1200:]
        failure_code = (
            "manual_cleanup_required"
            if "no space left on device" in failure_text.lower()
            else "edge_runtime_install_failed"
        )
        raise EdgeRuntimeProvisionError(
            f"边端离线运行时安装失败 (exit={code}): {failure_text}",
            code=failure_code,
            report=current,
            result=result,
        )

    after = probe_edge_runtime(
        client,
        configured_demo_root=paths.sophon_demo_root,
        docker_container=docker_container,
        remote_root=remote_root,
    )
    result["after"] = after
    if isinstance(result.get("storage_cleanup"), dict):
        after["storage_cleanup"] = result["storage_cleanup"]
        after["manual_intervention_required"] = False
    result["environment_ready"] = bool(after["environment_ready"])
    result["sophon_demo_root"] = after.get("sophon_demo_root")
    if not after["environment_ready"]:
        reasons = list(after["fatal_reasons"]) + list(after["repairable_reasons"])
        raise EdgeRuntimeProvisionError(
            "边端离线安装后复检仍未通过: " + ", ".join(reasons),
            code="environment_verification_failed",
            report=after,
            result=result,
        )
    return client, result
