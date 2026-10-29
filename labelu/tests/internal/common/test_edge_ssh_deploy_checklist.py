from labelu.internal.common.edge_ssh_deploy_checklist import iter_deploy_checklist_lines


def test_checklist_skip_when_yolo_disabled(monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", False)
    lines = iter_deploy_checklist_lines(probe_remote=False)
    assert any("skip|边端 SSH 验证栈" in ln for ln in lines)


def test_checklist_warns_missing_password(tmp_path, monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(config.settings, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_deploy_checklist.cfg_io.load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "",
            "remote_base": "/data/labelu_edge/jobs",
            "docker_container": "bm1688",
            "docker_use_sudo": True,
            "auto_after_export": True,
        },
    )

    lines = iter_deploy_checklist_lines(probe_remote=False)
    text = "\n".join(lines)
    assert "边端 SSH 密码" in text
    assert "warn|边端 SSH 密码" in text or "未保存" in text
    assert "边端 Docker 执行环境" in text
    assert "bm1688" in text
    assert "MySQL system_config_value" in text
    assert "pythonTest_evaluation.py" in text


def test_checklist_remote_probe_uses_shared_probe_result(tmp_path, monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(config.settings, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_deploy_checklist.cfg_io.load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "port": 22,
            "user": "linaro",
            "password": "secret-pass",
            "remote_base": "/data/labelu_edge/jobs",
            "docker_container": "bm1688",
            "docker_use_sudo": True,
        },
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_deploy_checklist.probe_edge_ssh_target",
        lambda *_args, **_kwargs: {
            "status": "fail",
            "stage": "auth",
            "code": "auth_failed",
            "message": "Authentication failed",
        },
    )

    lines = iter_deploy_checklist_lines(probe_remote=True)
    assert any("warn|边端 SSH 连通性|auth_failed/auth: Authentication failed" in ln for ln in lines)


def test_checklist_accepts_a_readable_private_key_without_password(tmp_path, monkeypatch):
    from labelu.internal.common import config

    key_path = tmp_path / "id_ed25519"
    key_path.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_deploy_checklist.cfg_io.load_edge_ssh_config",
        lambda: {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "",
            "private_key_path": str(key_path),
            "remote_base": "/data/labelu_edge/jobs",
            "auto_after_export": True,
        },
    )

    lines = iter_deploy_checklist_lines(probe_remote=False)
    assert any("ok|边端 SSH 私钥|已配置且可读" in line for line in lines)
