"""部署后检查清单：边端 SSH + Docker 内 pythonTest（SOP 3.6.2）。"""
from __future__ import annotations

from pathlib import Path
from typing import List

from labelu.internal.common import edge_ssh_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.edge_ssh_probe import probe_edge_ssh_target
from labelu.internal.common.edge_script_paths import (
    resolve_edge_board_validate_runner,
    resolve_evaluation_utils,
    resolve_python_test_evaluation,
)


def _line(status: str, name: str, detail: str = "") -> str:
    return f"{status}|{name}|{detail}"


def _script_paths() -> tuple[Path, Path, Path]:
    return (
        resolve_edge_board_validate_runner(),
        resolve_python_test_evaluation(),
        resolve_evaluation_utils(),
    )


def _probe_edge_ssh(cfg: dict) -> List[str]:
    """可选：SSH 探测边端 Docker 与 /data 挂载（需已保存密码）。"""
    out: List[str] = []
    host = str(cfg.get("host") or "").strip()
    password = str(cfg.get("password") or "")
    private_key_path = str(cfg.get("private_key_path") or "").strip()
    if not host or not (password or private_key_path):
        out.append(
            _line(
                "skip",
                "边端 SSH 远程探测",
                "未配置主机或密码/私钥路径，跳过连通性检查",
            )
        )
        return out

    try:
        from labelu.internal.common.edge_ssh_client import EdgeSshError, ssh_connect, ssh_run
    except Exception as exc:
        out.append(_line("warn", "边端 SSH 远程探测", f"客户端不可用: {exc}"))
        return out

    port = int(cfg.get("port") or 22)
    user = str(cfg.get("user") or "linaro").strip()
    timeout = min(int(cfg.get("connect_timeout_sec") or 30), 20)
    probe = probe_edge_ssh_target(cfg, remote_base_required=False)
    if probe.get("status") != "ok":
        out.append(
            _line(
                "warn",
                "边端 SSH 连通性",
                f"{probe.get('code')}/{probe.get('stage')}: {probe.get('message')}"[:160],
            )
        )
        return out

    client = None
    try:
        client = ssh_connect(
            host=host,
            port=port,
            username=user,
            password=password,
            private_key_path=private_key_path or None,
            timeout=timeout,
        )
        out.append(_line("ok", "边端 SSH 连通性", f"{user}@{host}:{port}"))

        remote_base = str(cfg.get("remote_base") or "/data/labelu_edge/jobs").strip()
        code, stdout, stderr = ssh_run(
            client,
            f"test -d {_shell_quote(remote_base)} && echo ok || echo missing",
            timeout=30,
        )
        if "ok" in (stdout or ""):
            out.append(_line("ok", "边端工作目录可访问", remote_base))
        else:
            out.append(
                _line(
                    "warn",
                    "边端工作目录可访问",
                    f"{remote_base} 不存在（首次验证会自动创建）",
                )
            )

        docker_container = str(cfg.get("docker_container") or "").strip()
        if not docker_container:
            out.append(
                _line(
                    "skip",
                    "边端 Docker 容器探测",
                    "未配置 docker_container",
                )
            )
            return out

        docker_bin = "sudo docker" if cfg.get("docker_use_sudo", True) else "docker"
        ps_cmd = f"{docker_bin} ps --format '{{{{.Names}}}}' 2>/dev/null | grep -Fx {_shell_quote(docker_container)} || true"
        _, ps_out, _ = ssh_run(client, ps_cmd, timeout=30)
        if docker_container in (ps_out or ""):
            out.append(_line("ok", "边端 Docker 容器运行中", docker_container))
        else:
            out.append(
                _line(
                    "warn",
                    "边端 Docker 容器运行中",
                    f"{docker_container} 未在运行（SOP: docker start {docker_container}）",
                )
            )

        cv_cmd = (
            f"{docker_bin} exec {docker_container} python3 -c "
            f"'import cv2; print(\"ok\")' 2>/dev/null || echo fail"
        )
        _, cv_out, _ = ssh_run(client, cv_cmd, timeout=60)
        if "ok" in (cv_out or ""):
            out.append(_line("ok", "边端容器 pythonTest 依赖", f"{docker_container} 内 cv2 可用"))
        else:
            out.append(
                _line(
                    "warn",
                    "边端容器 pythonTest 依赖",
                    f"{docker_container} 内 cv2 不可用",
                )
            )

        mount_cmd = (
            f"{docker_bin} exec {docker_container} test -d {_shell_quote(remote_base)} "
            f"&& echo ok || echo missing"
        )
        _, mount_out, _ = ssh_run(client, mount_cmd, timeout=30)
        if "ok" in (mount_out or ""):
            out.append(
                _line(
                    "ok",
                    "边端容器 /data 挂载",
                    f"容器内可见 {remote_base}",
                )
            )
        else:
            out.append(
                _line(
                    "warn",
                    "边端容器 /data 挂载",
                    f"容器内不可见 {remote_base}，请确认 -v /data:/data",
                )
            )

        from labelu.internal.common.edge_sophon_demo import probe_sophon_demo_root_via_ssh

        resolved, note = probe_sophon_demo_root_via_ssh(
            client,
            configured=str(cfg.get("sophon_demo_root") or ""),
            docker_container=docker_container,
            docker_use_sudo=bool(cfg.get("docker_use_sudo", True)),
            restrict_to_configured=True,
        )
        if resolved:
            out.append(_line("ok", "边端 SOPHON_DEMO_ROOT", f"{resolved} ({note})"))
        else:
            out.append(
                _line(
                    "warn",
                    "边端 SOPHON_DEMO_ROOT",
                    f"{note}；将跳过 sophon-demo 例程，仅跑 pythonTest",
                )
            )
    except EdgeSshError as exc:
        out.append(_line("warn", "边端 SSH 连通性", str(exc)[:160]))
    except Exception as exc:
        out.append(_line("warn", "边端 SSH 远程探测", str(exc)[:160]))
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
    return out


def _shell_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def iter_deploy_checklist_lines(*, probe_remote: bool = True) -> List[str]:
    """供 deploy.sh 导入：每行 status|name|detail。"""
    lines: List[str] = []

    if not settings.YOLO_TRAIN_ENABLED:
        lines.append(_line("skip", "边端 SSH 验证栈", "YOLO_TRAIN_ENABLED=false"))
        return lines

    try:
        import paramiko  # noqa: F401

        lines.append(_line("ok", "边端 SSH 依赖 paramiko", "已安装"))
    except ImportError:
        lines.append(
            _line(
                "warn",
                "边端 SSH 依赖 paramiko",
                "未安装；请 INSTALL_EXTRAS 含 yolo 并 poetry install -E yolo",
            )
        )

    cfg = cfg_io.load_edge_ssh_config()
    lines.append(_line("ok", "边端配置存储", "MySQL system_config_value"))
    host = str(cfg.get("host") or "").strip()
    user = str(cfg.get("user") or "").strip()
    if host:
        lines.append(_line("ok", "边端 SSH 主机", f"{host}:{cfg.get('port', 22)} user={user}"))
    else:
        lines.append(
            _line(
                "warn",
                "边端 SSH 主机",
                "未配置；请在边端设备页面新增设备",
            )
        )

    password_available = bool(cfg_io.resolve_edge_ssh_password(cfg))
    private_key_path = cfg_io.resolve_edge_ssh_private_key_path(cfg)
    private_key_available = bool(
        private_key_path and Path(private_key_path).expanduser().is_file()
    )
    if password_available:
        lines.append(_line("ok", "边端 SSH 密码", "已保存（不回显）"))
    elif private_key_available:
        lines.append(_line("ok", "边端 SSH 私钥", "已配置且可读（路径不回显）"))
    elif private_key_path:
        lines.append(
            _line(
                "warn",
                "边端 SSH 密码或私钥",
                "密码未保存，且私钥路径不可读；请检查凭据配置",
            )
        )
    else:
        lines.append(
            _line(
                "warn",
                "边端 SSH 密码或私钥",
                "未配置；边端自动验证需填写密码或私钥路径",
            )
        )

    if cfg.get("enabled"):
        lines.append(_line("ok", "边端 SSH 启用", "enabled=true"))
    else:
        lines.append(_line("warn", "边端 SSH 启用", "enabled=false"))

    if cfg.get("auto_after_export"):
        lines.append(_line("ok", "边端导出后自动验证", "auto_after_export=true"))
    else:
        lines.append(_line("warn", "边端导出后自动验证", "auto_after_export=false"))

    docker_container = str(cfg.get("docker_container") or "").strip()
    docker_sudo = bool(cfg.get("docker_use_sudo", True))
    if docker_container:
        lines.append(
            _line(
                "ok",
                "边端 Docker 执行环境",
                f"container={docker_container} sudo={docker_sudo}",
            )
        )
    else:
        lines.append(
            _line(
                "warn",
                "边端 Docker 执行环境",
                "docker_container 为空；将在边端宿主机 python3（非 SOP 推荐）",
            )
        )

    remote_base = str(cfg.get("remote_base") or "").strip()
    if remote_base:
        lines.append(_line("ok", "边端 remote_base", remote_base))
    else:
        lines.append(_line("warn", "边端 remote_base", "未配置"))

    lines.append(
        _line(
            "ok",
            "边端配置存储",
            "MySQL system_config_value（edge_device_registry.device_store）",
        )
    )

    runner, main_py, utils_py = _script_paths()
    if runner.is_file():
        lines.append(_line("ok", "边端验证 runner 脚本", str(runner)))
    else:
        lines.append(_line("fail", "边端验证 runner 脚本", f"缺失 {runner}"))

    if main_py.is_file():
        lines.append(_line("ok", "pythonTest_evaluation.py", str(main_py)))
    else:
        lines.append(_line("fail", "pythonTest_evaluation.py", "缺失"))

    if utils_py.is_file():
        lines.append(_line("ok", "evaluation_utils.py", str(utils_py)))
    else:
        lines.append(_line("warn", "evaluation_utils.py", "缺失"))

    if probe_remote:
        lines.extend(_probe_edge_ssh(cfg))

    return lines
