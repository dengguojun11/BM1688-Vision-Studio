"""Start the local LabelU 8003 service in the current PyCharm console.

The launcher runs the backend in this Python process after the frontend build.
If an external launcher selected another interpreter, it starts this same file
under the project venv while inheriting the current terminal handles. That
keeps Loguru, Uvicorn, and application ``print`` output visible in the console.
"""

from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# Automatic Agent enrollment requires the BM1688 to call back to this service.
# Keep the browser URL local, but bind the server to all interfaces by default;
# operators can restrict it with LABELU_LISTEN_HOST when a firewall/proxy is in
# front of the development server.
HOST = os.environ.get("LABELU_LISTEN_HOST", "0.0.0.0").strip() or "0.0.0.0"
PUBLIC_HOST = os.environ.get("LABELU_PUBLIC_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = 8003
_BACKEND_WRAPPER_PATH = Path(".tmp") / "run_built_backend_8003.py"


def _listener_pids(port: int) -> list[int]:
    """Return PIDs listening on ``port`` when the platform exposes them."""
    pids: set[int] = set()

    try:
        import psutil  # type: ignore

        for connection in psutil.net_connections(kind="tcp"):
            local = connection.laddr
            if (
                local
                and local.port == port
                and connection.status == psutil.CONN_LISTEN
                and connection.pid
            ):
                pids.add(int(connection.pid))
    except Exception:
        # psutil is optional. The netstat fallback below covers the normal
        # Windows PyCharm environment without adding a runtime dependency.
        pass

    if pids or os.name != "nt":
        return sorted(pids)

    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        if fields[3].upper() != "LISTENING":
            continue
        endpoint = fields[1].strip("[]")
        try:
            endpoint_port = int(endpoint.rsplit(":", 1)[1])
            pid = int(fields[4])
        except (IndexError, ValueError):
            continue
        if endpoint_port == port:
            pids.add(pid)
    return sorted(pids)


def _port_accepts_connections(host: str, port: int) -> bool:
    """Check whether a process already accepts TCP connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            return probe.connect_ex((host, port)) == 0
        except OSError:
            return False


def _port_is_busy(port: int) -> tuple[bool, list[int]]:
    pids = _listener_pids(port)
    probe_host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    return bool(pids) or _port_accepts_connections(probe_host, port), pids


def _normalized_path(path: str | Path) -> str:
    """Return a case-insensitive absolute path for Windows comparisons."""
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def _pid_listens_on_port(pid: int, port: int) -> bool:
    """Return whether ``pid`` currently owns a TCP listener on ``port``."""
    try:
        import psutil  # type: ignore

        process = psutil.Process(pid)
        try:
            connections = process.net_connections(kind="tcp")
        except AttributeError:
            # psutil < 5.9 exposes this as the deprecated ``connections``
            # method. Keep the ownership guard working in older venvs.
            connections = process.connections(kind="tcp")
        for connection in connections:
            if (
                connection.laddr
                and connection.laddr.port == port
                and connection.status == psutil.CONN_LISTEN
            ):
                return True
    except Exception:
        return False
    return False


def _is_trusted_backend_wrapper(path: Path, project_root: Path) -> bool:
    """Recognize the temporary wrapper used after a frontend build failure.

    The wrapper is project-local, but a path check alone would allow an
    unrelated script to take over the port if it happened to reuse the same
    filename. Parse the source and require the calls that make it part of
    this launcher chain.
    """
    expected_path = project_root / _BACKEND_WRAPPER_PATH
    if _normalized_path(path) != _normalized_path(expected_path):
        return False

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return False

    imported_launcher = any(
        isinstance(node, ast.Import)
        and any(
            alias.name == "start_labelu_8003" and alias.asname == "launcher"
            for alias in node.names
        )
        for node in ast.walk(tree)
    )
    if not imported_launcher:
        return False

    required_calls = {"_take_over_existing_project_listener", "_run_backend"}
    observed_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "launcher"
        )
    }
    return required_calls.issubset(observed_calls)


def _is_supported_entrypoint(
    candidate_path: Path, script: Path, project_root: Path
) -> bool:
    """Return whether a process script belongs to this 8003 launch chain."""
    candidate = _normalized_path(candidate_path)
    expected_script = _normalized_path(script)
    wrapper = _normalized_path(project_root / _BACKEND_WRAPPER_PATH)

    if candidate == wrapper:
        return _is_trusted_backend_wrapper(candidate_path, project_root)
    return candidate == expected_script


def _process_runs_this_launcher(pid: int, script: Path, project_root: Path) -> bool:
    """Return whether ``pid`` belongs to this launcher's supported entrypoint."""
    try:
        import psutil  # type: ignore

        process = psutil.Process(pid)
        process_cwd = Path(process.cwd())
        if _normalized_path(process_cwd) != _normalized_path(project_root):
            return False
        command = process.cmdline()
        if not command or Path(command[0]).name.lower() not in {
            "python",
            "python.exe",
            "pythonw",
            "pythonw.exe",
        }:
            return False

        arguments = command[1:]
        index = 0
        while index < len(arguments):
            argument = arguments[index].strip().strip('"')
            if argument == "--":
                index += 1
                break
            if not argument.startswith("-"):
                break
            if argument in {"-c", "-m"}:
                return False
            if argument in {"-W", "-X"}:
                index += 2
            else:
                index += 1

        if index >= len(arguments):
            return False
        candidate_path = Path(arguments[index].strip().strip('"'))
        if not candidate_path.is_absolute():
            candidate_path = process_cwd / candidate_path
        return _is_supported_entrypoint(candidate_path, script, project_root)
    except Exception:
        # If ownership cannot be proven, refusing takeover is safer than
        # stopping an unrelated application that happens to use port 8003.
        return False


def _take_over_existing_project_listener(script: Path, project_root: Path) -> None:
    """Stop a previous 8003 instance only when it uses this launch chain."""
    busy, pids = _port_is_busy(PORT)
    if not busy:
        return

    if not pids or any(
        not _process_runs_this_launcher(pid, script, project_root)
        or not _pid_listens_on_port(pid, PORT)
        for pid in pids
    ):
        pid_text = ", ".join(str(pid) for pid in pids) if pids else "未知 PID"
        raise RuntimeError(
            f"端口 {HOST}:{PORT} 已被非本入口进程占用（PID: {pid_text}）。"
            "为避免误停其他程序，本次启动已取消。"
        )

    import psutil  # type: ignore

    processes: list[tuple[object, float]] = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            created_at = process.create_time()
            # Re-check identity and listener ownership immediately before
            # termination to protect against PID reuse and port races.
            if (
                pid == os.getpid()
                or not _process_runs_this_launcher(pid, script, project_root)
                or not _pid_listens_on_port(pid, PORT)
            ):
                raise RuntimeError(
                    f"PID {pid} 的身份或监听状态在接管前发生变化，本次启动已取消。"
                )
            processes.append((process, created_at))
        except psutil.NoSuchProcess:
            continue

    if processes:
        pid_text = ", ".join(str(process.pid) for process, _ in processes)
        print(
            f"[LabelU 8003] 检测到本项目旧实例（PID: {pid_text}），"
            "正在停止并由当前终端接管...",
            flush=True,
        )
        for process, _ in processes:
            process.terminate()
        process_objects = [process for process, _ in processes]
        _, alive = psutil.wait_procs(process_objects, timeout=10)
        created_by_pid = {process.pid: created_at for process, created_at in processes}
        for process in alive:
            if (
                process.create_time() == created_by_pid[process.pid]
                and _process_runs_this_launcher(process.pid, script, project_root)
                and _pid_listens_on_port(process.pid, PORT)
            ):
                process.kill()
        if alive:
            psutil.wait_procs(alive, timeout=5)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _port_is_busy(PORT)[0]:
            print("[LabelU 8003] 旧实例已停止，8003 端口已释放。", flush=True)
            return
        time.sleep(0.1)

    _, remaining_pids = _port_is_busy(PORT)
    remaining_text = (
        ", ".join(str(pid) for pid in remaining_pids) if remaining_pids else "未知 PID"
    )
    raise RuntimeError(f"等待 8003 端口释放超时（PID: {remaining_text}）")


def _build_environment(project_root: Path) -> dict[str, str]:
    env_file = project_root / ".env"
    python_exe = project_root / ".venv" / "Scripts" / "python.exe"
    static_dir = project_root / ".tmp" / "local-runtime-data" / "statics-8003"

    if not env_file.is_file():
        raise FileNotFoundError(f"项目 .env 不存在: {env_file}")
    if not python_exe.is_file():
        raise FileNotFoundError(f"项目 .venv Python 不存在: {python_exe}")

    env = os.environ.copy()
    env.update(
        {
            "LABELU_ENV_FILE": str(env_file),
            "VIRTUAL_ENV": str(project_root / ".venv"),
            "LABELU_STATIC_DIR": str(static_dir),
            "LABELU_STATIC_ONLY": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _running_in_project_python(python_exe: Path) -> bool:
    """Check the executable and runtime prefix of this Python process."""
    target_exe = python_exe.resolve()
    current_exe = Path(sys.executable).resolve()
    if os.path.normcase(str(current_exe)) == os.path.normcase(str(target_exe)):
        return True
    target_prefix = python_exe.parent.parent.resolve()
    current_prefix = Path(sys.prefix).resolve()
    return os.path.normcase(str(current_prefix)) == os.path.normcase(str(target_prefix))


def _handoff_to_project_python(
    project_root: Path, python_exe: Path, env: dict[str, str]
) -> int | None:
    """Run this entrypoint with the project venv when another Python is selected."""
    if _running_in_project_python(python_exe):
        return None

    script = Path(__file__).resolve()
    print(
        "[LabelU 8003] 检测到外部 Python 环境，切换到项目 .venv: " f"{python_exe}",
        flush=True,
    )
    command = [str(python_exe), "-u", str(script), *sys.argv[1:]]
    child_env = dict(env)
    # Keep stdout/stderr attached to the current terminal. Using a child and
    # waiting for it is reliable under uv.exe, which may not preserve exec().
    result = subprocess.run(
        command,
        cwd=project_root,
        env=child_env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        close_fds=False,
        check=False,
    )
    return result.returncode


def _run_frontend_build(
    project_root: Path, python_exe: Path, env: dict[str, str]
) -> None:
    print("[LabelU 8003] 正在构建前端静态资源...", flush=True)
    print(f"[LabelU 8003] 静态目录: {env['LABELU_STATIC_DIR']}", flush=True)
    result = subprocess.run(
        [str(python_exe), "-u", "-m", "labelu.main", "frontend-build"],
        cwd=project_root,
        env=env,
        # None deliberately inherits the current PyCharm terminal handles.
        stdin=None,
        stdout=None,
        stderr=None,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"前端构建失败，后端未启动（退出码 {result.returncode}）")


def _run_backend(project_root: Path, env: dict[str, str]) -> int:
    """Import and run the Click entrypoint without creating a child server."""
    # The environment must be set before importing labelu.main because its
    # settings and static directory are resolved during module import.
    os.environ.update(env)
    sys.path.insert(0, str(project_root))

    from labelu.main import cli

    args = [
        "--host",
        HOST,
        "--port",
        str(PORT),
        "--static-only",
        "--skip-install",
        "--media-host",
        f"http://{PUBLIC_HOST}:{PORT}",
    ]
    print(
        f"[LabelU 8003] 后端监听: {HOST}:{PORT}；浏览器地址: http://{PUBLIC_HOST}:{PORT}",
        flush=True,
    )
    # Calling Click in-process keeps Loguru's sys.stdout sink and Uvicorn's
    # handlers bound to this exact PyCharm console.
    result = cli(args=args, standalone_mode=False)
    return int(result or 0)


def main() -> int:
    project_root = Path(__file__).resolve().parent
    try:
        env = _build_environment(project_root)
        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        handoff_result = _handoff_to_project_python(project_root, python_exe, env)
        if handoff_result is not None:
            return handoff_result
        script = Path(__file__).resolve()
        _take_over_existing_project_listener(script, project_root)
        print(f"[LabelU 8003] 项目目录: {project_root}", flush=True)
        print(f"[LabelU 8003] 环境文件: {env['LABELU_ENV_FILE']}", flush=True)
        _run_frontend_build(project_root, python_exe, env)
        # A second check closes the window in which another copy could start
        # while the frontend build was running. The final bind remains the
        # operating system's last line of defence against concurrent launches.
        _take_over_existing_project_listener(script, project_root)
        print("[LabelU 8003] 前端构建完成，正在加载后端...", flush=True)
        return _run_backend(project_root, env)
    except KeyboardInterrupt:
        print("\n[LabelU 8003] 已收到 Ctrl+C，服务退出。", flush=True)
        return 130
    except Exception as exc:
        print(f"[LabelU 8003] 启动失败: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
