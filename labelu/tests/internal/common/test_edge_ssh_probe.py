import builtins
from types import SimpleNamespace
from unittest.mock import Mock

from labelu.internal.common import edge_ssh_client
from labelu.internal.common.edge_ssh_client import EdgeSshError
from labelu.internal.common.edge_ssh_probe import probe_edge_ssh_target


def test_probe_edge_ssh_target_classifies_auth_mode_mismatch(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_probe.ssh_connect",
        lambda **_kwargs: (_ for _ in ()).throw(
            EdgeSshError("Bad authentication type; allowed types: ['publickey']")
        ),
    )

    probe = probe_edge_ssh_target(
        {
            "host": "10.10.11.26",
            "port": 22,
            "user": "linaro",
            "password": "secret-pass",
            "remote_base": "/data/labelu_edge/jobs",
        }
    )

    assert probe["status"] == "fail"
    assert probe["stage"] == "auth"
    assert probe["code"] == "auth_mode_mismatch"


def test_missing_paramiko_auto_installs_then_continues(monkeypatch):
    real_import = builtins.__import__
    import_calls = []
    ensure = Mock(return_value=True)

    def fake_import(name, *args, **kwargs):
        if name == "paramiko":
            import_calls.append(name)
            if len(import_calls) == 1:
                raise ImportError("No module named paramiko")
            return SimpleNamespace()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(edge_ssh_client, "ensure_paramiko_installed", ensure)

    edge_ssh_client._require_paramiko()

    ensure.assert_called_once_with(reason="边端设备 SSH 探测与环境准备")
    assert import_calls == ["paramiko", "paramiko"]
