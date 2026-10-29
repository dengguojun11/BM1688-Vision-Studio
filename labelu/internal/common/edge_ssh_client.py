"""边端 SSH/SFTP（paramiko）。密码仅来自环境变量，勿写入代码库。"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from labelu.internal.common.optional_dependencies import ensure_paramiko_installed


class EdgeSshError(RuntimeError):
    pass


class EdgeSshTransferInterrupted(EdgeSshError):
    """The SSH transport disappeared while a resumable transfer was active."""


def _require_paramiko():
    try:
        import paramiko  # noqa: F401

        return
    except ImportError as exc:
        if not ensure_paramiko_installed(reason="边端设备 SSH 探测与环境准备"):
            raise EdgeSshError(
                "paramiko 自动安装失败或仍无法导入，请检查当前 Python 的 pip 与网络；"
                "也可手动执行: pip install paramiko"
            ) from exc

    # 自动安装只在依赖可用时返回成功；再次导入失败要阻止后续 SSH 流程并给出明确错误。
    try:
        import paramiko  # noqa: F401
    except ImportError as exc:
        raise EdgeSshError("paramiko 安装后仍无法导入，请检查当前服务虚拟环境") from exc


def _strip_openssh_inline_comment(value: Any) -> str:
    """去掉 OpenSSH config 值里同一行的 # 注释（paramiko 不会自动剥离）。"""
    text = str(value or "").strip()
    if "#" in text:
        text = text.split("#", 1)[0].strip()
    return text


def _lookup_openssh_config(host_alias: str) -> Dict[str, Any]:
    """读取 ~/.ssh/config，解析 Host 别名（如 target-54-99）。"""
    alias = str(host_alias or "").strip()
    if not alias:
        return {}
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.is_file():
        return {}
    _require_paramiko()
    import paramiko

    cfg = paramiko.SSHConfig()
    try:
        cfg.parse(config_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return {}
    looked = cfg.lookup(alias)
    return looked if isinstance(looked, dict) else {}


def _collect_private_key_paths(
    *,
    private_key_path: Optional[str],
    openssh_cfg: Dict[str, Any],
) -> List[str]:
    paths: List[str] = []
    for raw in [private_key_path, *(openssh_cfg.get("identityfile") or [])]:
        expanded = os.path.expanduser(_strip_openssh_inline_comment(raw))
        if expanded and expanded not in paths:
            paths.append(expanded)
    return paths


def _parse_jump_target(jump_spec: str, default_user: str = "root") -> Tuple[str, str, int]:
    spec = (jump_spec or "").strip()
    if not spec:
        raise EdgeSshError("ProxyJump 为空")
    user = default_user
    hostport = spec
    if "@" in spec:
        user_part, hostport = spec.rsplit("@", 1)
        if user_part.strip():
            user = user_part.strip()
    port = 22
    host = hostport
    if hostport.count(":") == 1:
        host_part, port_str = hostport.rsplit(":", 1)
        if port_str.isdigit():
            host = host_part
            port = int(port_str)
    return user, host.strip(), port


def _resolve_jump_target(
    jump_spec: str,
    *,
    fallback_user: str = "root",
    fallback_key_paths: Optional[List[str]] = None,
) -> Tuple[str, str, int, List[str]]:
    """解析 ProxyJump 项，并通过 ~/.ssh/config 展开 Host 别名（如 n2n-jump）。"""
    explicit_user, host_token, port = _parse_jump_target(jump_spec, fallback_user)
    jump_cfg = _lookup_openssh_config(host_token)
    jump_keys = _collect_private_key_paths(private_key_path=None, openssh_cfg=jump_cfg)
    key_paths = jump_keys or list(fallback_key_paths or [])
    if jump_cfg:
        host = str(jump_cfg.get("hostname") or host_token).strip()
        port = int(jump_cfg.get("port") or port)
        if "@" in (jump_spec or ""):
            user = explicit_user
        else:
            user = str(jump_cfg.get("user") or explicit_user).strip() or explicit_user
    else:
        host = host_token
        user = explicit_user
    return user, host, port, key_paths


def _ssh_auth_connect_kw(
    *,
    key_paths: List[str],
    password: str,
    timeout: int,
) -> Dict[str, Any]:
    kw: Dict[str, Any] = {
        "password": password or None,
        "timeout": timeout,
        "allow_agent": True,
        "look_for_keys": True,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    if key_paths:
        kw["key_filename"] = key_paths
    return kw


def _open_proxy_sock(
    *,
    openssh_cfg: Dict[str, Any],
    dest_host: str,
    dest_port: int,
    key_paths: List[str],
    password: str,
    timeout: int,
) -> Tuple[Optional[Any], List[Any]]:
    """按 ~/.ssh/config 的 ProxyCommand / ProxyJump 打开到目标 SSH 端口的 sock。"""
    proxycommand = _strip_openssh_inline_comment(openssh_cfg.get("proxycommand"))
    proxyjump = _strip_openssh_inline_comment(openssh_cfg.get("proxyjump"))
    keepalive: List[Any] = []
    if proxycommand:
        from paramiko.proxy import ProxyCommand

        return ProxyCommand(proxycommand), keepalive
    if not proxyjump:
        return None, keepalive

    _require_paramiko()
    import paramiko

    jumps = [j.strip() for j in proxyjump.split(",") if j.strip()]
    if not jumps:
        return None, keepalive

    j_user, j_host, j_port, j_keys = _resolve_jump_target(
        jumps[0],
        fallback_key_paths=key_paths,
    )
    jump_auth_kw = _ssh_auth_connect_kw(
        key_paths=j_keys,
        password=password,
        timeout=timeout,
    )
    jump_client = paramiko.SSHClient()
    jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jump_client.connect(j_host, port=j_port, username=j_user, **jump_auth_kw)
    keepalive.append(jump_client)

    for jump_spec in jumps[1:]:
        j_user, j_host, j_port, j_keys = _resolve_jump_target(
            jump_spec,
            fallback_key_paths=key_paths,
        )
        hop_auth_kw = _ssh_auth_connect_kw(
            key_paths=j_keys,
            password=password,
            timeout=timeout,
        )
        tr = keepalive[-1].get_transport()
        if tr is None:
            raise EdgeSshError("ProxyJump 中转 transport 不可用")
        hop_sock = tr.open_channel("direct-tcpip", (j_host, j_port), ("127.0.0.1", 0))
        hop_client = paramiko.SSHClient()
        hop_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        hop_client.connect(
            j_host,
            port=j_port,
            username=j_user,
            sock=hop_sock,
            **hop_auth_kw,
        )
        keepalive.append(hop_client)

    tr = keepalive[-1].get_transport()
    if tr is None:
        raise EdgeSshError("ProxyJump 末跳 transport 不可用")
    dest_sock = tr.open_channel("direct-tcpip", (dest_host, dest_port), ("127.0.0.1", 0))
    return dest_sock, keepalive


def ssh_connect(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: int = 30,
    private_key_path: Optional[str] = None,
    private_key_paths: Optional[List[str]] = None,
    sock: Optional[Any] = None,
    jump_keepalive: Optional[List[Any]] = None,
):
    _require_paramiko()
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kw: Dict[str, Any] = {
        "hostname": host,
        "port": int(port),
        "username": username,
        "password": password or None,
        "timeout": timeout,
        "allow_agent": True,
        "look_for_keys": True,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    key_files: List[str] = []
    if private_key_paths:
        key_files.extend(
            os.path.expanduser(str(path).strip())
            for path in private_key_paths
            if str(path or "").strip()
        )
    key_path = str(private_key_path or "").strip()
    if key_path and key_path not in key_files:
        key_files.append(os.path.expanduser(key_path))
    if key_files:
        connect_kw["key_filename"] = key_files
    if sock is not None:
        connect_kw["sock"] = sock
    try:
        client.connect(**connect_kw)
    except (socket.error, paramiko.SSHException, OSError) as exc:
        raise EdgeSshError(f"SSH 连接失败 {username}@{host}:{port}: {exc}") from exc
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(30)
    if jump_keepalive:
        client._labelu_jump_clients = jump_keepalive
    return client


def ssh_connect_for_remote_train_node(node: Dict[str, Any], *, timeout: int = 30):
    """远程训练节点 SSH：支持密码、private_key_path、平台 ~/.ssh/config 别名与默认密钥。"""
    host_alias = str(node["host"]).strip()
    openssh_cfg = _lookup_openssh_config(host_alias)
    resolved_host = str(openssh_cfg.get("hostname") or host_alias).strip()
    resolved_port = int(node.get("port") or openssh_cfg.get("port") or 22)
    node_user = str(node.get("user") or "").strip()
    cfg_user = str(openssh_cfg.get("user") or "").strip()
    if cfg_user and host_alias != resolved_host and (not node_user or node_user == "root"):
        username = cfg_user
    else:
        username = node_user or cfg_user or "root"
    key_paths = _collect_private_key_paths(
        private_key_path=str(node.get("private_key_path") or "").strip() or None,
        openssh_cfg=openssh_cfg,
    )
    password = str(node.get("password") or "")
    proxy_sock, jump_keepalive = _open_proxy_sock(
        openssh_cfg=openssh_cfg,
        dest_host=resolved_host,
        dest_port=resolved_port,
        key_paths=key_paths,
        password=password,
        timeout=timeout,
    )
    return ssh_connect(
        host=resolved_host,
        port=resolved_port,
        username=username,
        password=password,
        private_key_paths=key_paths or None,
        sock=proxy_sock,
        jump_keepalive=jump_keepalive or None,
        timeout=timeout,
    )


def ssh_run(
    client,
    command: str,
    *,
    timeout: int = 7200,
) -> Tuple[int, str, str]:
    _require_paramiko()
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        del stdin
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err
    except Exception as exc:
        raise EdgeSshError(f"SSH 命令执行失败: {exc}") from exc


def sftp_stat_size(client, remote_path: str) -> int:
    """返回远端文件字节数；不存在时抛 EdgeSshError。"""
    _require_paramiko()
    sftp = client.open_sftp()
    try:
        return int(sftp.stat(str(remote_path)).st_size)
    except FileNotFoundError as exc:
        raise EdgeSshError(f"远端文件不存在: {remote_path}") from exc
    except OSError as exc:
        raise EdgeSshError(f"无法读取远端文件大小: {remote_path}: {exc}") from exc
    finally:
        sftp.close()


def _sftp_oserror_message(exc: OSError, *, remote_path: str, action: str) -> str:
    """将 paramiko SFTP OSError(Failure) 转为可操作的磁盘/权限提示。"""
    errno = getattr(exc, "errno", None)
    text = str(exc).strip() or repr(exc)
    low = text.lower()
    hints: list[str] = []
    if errno in (13, 28) or "permission denied" in low or "access denied" in low:
        hints.append(
            f"SFTP {action} 权限不足：请确认 SSH 用户对目录 {remote_path} 有写权限"
        )
    elif errno == 28 or "no space" in low or "disk full" in low:
        hints.append("边端磁盘空间不足，请 SSH 登录执行 df -h 清理后再试")
    elif "failure" in low or errno == 4:
        hints.append(
            "SFTP 通用 Failure：常见为磁盘满、目录不可写或 home 配额；"
            "请检查 df -h 与目标目录权限"
        )
    if not hints:
        hints.append(f"SFTP {action} 失败: {text}")
    return "; ".join(hints)


def sftp_upload(
    client,
    local_path: Path,
    remote_path: str,
    *,
    progress_callback: Optional[Any] = None,
    mode: Optional[int] = None,
) -> None:
    """Upload one file and optionally apply an explicit remote mode.

    Paramiko derives the remote mode from the local file by default.  That is
    convenient for ordinary artifacts, but it is unsafe for temporary files
    containing credentials: the local umask is not a sufficient contract for
    the remote host.  Callers that transfer secrets can therefore request a
    deterministic mode and have it applied before the SFTP handle is closed.
    """
    _require_paramiko()
    local_path = Path(local_path)
    if not local_path.is_file():
        raise EdgeSshError(f"本地文件不存在: {local_path}")
    sftp = client.open_sftp()
    try:
        remote_dir = str(remote_path).replace("\\", "/").rsplit("/", 1)[0]
        if remote_dir:
            _sftp_mkdirs(sftp, remote_dir)
        total_bytes = int(local_path.stat().st_size)
        if progress_callback is not None:

            def _cb(sent: int, total: int) -> None:
                progress_callback(int(sent), int(total or total_bytes))

            sftp.put(str(local_path), remote_path, callback=_cb)
        else:
            sftp.put(str(local_path), remote_path)
        if mode is not None:
            sftp.chmod(str(remote_path), int(mode))
    except OSError as exc:
        raise EdgeSshError(
            _sftp_oserror_message(exc, remote_path=remote_path, action="上传")
        ) from exc
    finally:
        sftp.close()


def sftp_upload_resumable(
    client,
    local_path: Path,
    remote_path: str,
    *,
    progress_callback: Optional[Any] = None,
    chunk_bytes: int = 1024 * 1024,
) -> int:
    """Resume an upload from the current remote size and return uploaded bytes."""
    _require_paramiko()
    import paramiko

    local_path = Path(local_path)
    if not local_path.is_file():
        raise EdgeSshError(f"本地文件不存在: {local_path}")

    sftp = client.open_sftp()
    remote_file = None
    local_size = int(local_path.stat().st_size)
    try:
        remote_dir = str(remote_path).replace("\\", "/").rsplit("/", 1)[0]
        if remote_dir:
            _sftp_mkdirs(sftp, remote_dir)
        try:
            remote_size = int(sftp.stat(str(remote_path)).st_size)
        except FileNotFoundError:
            remote_size = 0
        except OSError as exc:
            if getattr(exc, "errno", None) == 2 or "no such file" in str(exc).lower():
                remote_size = 0
            else:
                raise

        # A larger .part cannot belong to this local bundle. Rebuild only the
        # temporary file; the verified final bundle is never overwritten here.
        if remote_size > local_size:
            remote_size = 0
        if progress_callback is not None:
            progress_callback(remote_size, local_size)
        if remote_size == local_size:
            return remote_size

        mode = "r+b" if remote_size else "wb"
        remote_file = sftp.open(str(remote_path), mode)
        if remote_size:
            remote_file.seek(remote_size)
        if hasattr(remote_file, "set_pipelined"):
            remote_file.set_pipelined(True)

        sent = remote_size
        with local_path.open("rb") as local_file:
            local_file.seek(remote_size)
            while True:
                chunk = local_file.read(max(64 * 1024, int(chunk_bytes)))
                if not chunk:
                    break
                remote_file.write(chunk)
                sent += len(chunk)
                if progress_callback is not None:
                    progress_callback(sent, local_size)
        remote_file.flush()
        return sent
    except (paramiko.SSHException, EOFError, socket.error) as exc:
        raise EdgeSshTransferInterrupted(f"SFTP 上传连接中断: {exc}") from exc
    except OSError as exc:
        low = str(exc).lower()
        if getattr(exc, "errno", None) in (32, 54, 104, 110, 111) or any(
            marker in low
            for marker in (
                "connection reset",
                "connection dropped",
                "socket is closed",
                "server connection dropped",
                "timed out",
            )
        ):
            raise EdgeSshTransferInterrupted(f"SFTP 上传连接中断: {exc}") from exc
        raise EdgeSshError(
            _sftp_oserror_message(exc, remote_path=remote_path, action="续传")
        ) from exc
    finally:
        if remote_file is not None:
            try:
                remote_file.close()
            except Exception:
                pass
        try:
            sftp.close()
        except Exception:
            pass


def sftp_download(client, remote_path: str, local_path: Path) -> bool:
    _require_paramiko()
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    sftp = client.open_sftp()
    try:
        sftp.get(remote_path, str(local_path))
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("sftp get {} failed: {}", remote_path, exc)
        return False
    finally:
        sftp.close()


def sftp_download_tree(client, remote_dir: str, local_dir: Path) -> int:
    """递归下载 remote_dir 到 local_dir，返回文件数。"""
    _require_paramiko()
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    sftp = client.open_sftp()
    count = 0

    def _walk(rdir: str, ldir: Path) -> None:
        nonlocal count
        try:
            entries = sftp.listdir_attr(rdir)
        except OSError:
            return
        for ent in entries:
            rpath = f"{rdir.rstrip('/')}/{ent.filename}"
            lpath = ldir / ent.filename
            if str(ent.longname).startswith("d"):
                lpath.mkdir(parents=True, exist_ok=True)
                _walk(rpath, lpath)
            else:
                lpath.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(rpath, str(lpath))
                count += 1

    try:
        _walk(remote_dir.replace("\\", "/"), local_dir)
    finally:
        sftp.close()
    return count


def _sftp_mkdirs(sftp, remote_dir: str) -> None:
    parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
    cur = ""
    for part in parts:
        cur = f"{cur}/{part}" if cur else f"/{part}"
        try:
            sftp.stat(cur)
        except OSError:
            try:
                sftp.mkdir(cur)
            except OSError:
                pass


def ssh_mkdir_p(client, remote_dir: str) -> None:
    code, _, err = ssh_run(client, f"mkdir -p { _shell_quote(remote_dir) }", timeout=60)
    if code != 0:
        raise EdgeSshError(f"mkdir 失败: {err[:500]}")


def _shell_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"
