from labelu.internal.common import edge_ssh_client as ssh_io


def test_lookup_openssh_config_resolves_host_alias(tmp_path, monkeypatch):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text(
        "\n".join(
            [
                "Host target-54-99",
                "    HostName 10.0.54.99",
                "    User deng5",
                "    Port 2222",
                "    IdentityFile ~/.ssh/id_ed25519_gpu54",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ssh_io.Path, "home", staticmethod(lambda: tmp_path))

    cfg = ssh_io._lookup_openssh_config("target-54-99")
    assert cfg["hostname"] == "10.0.54.99"
    assert cfg["user"] == "deng5"
    assert int(cfg["port"]) == 2222
    assert cfg["identityfile"][0].endswith("id_ed25519_gpu54")


def test_ssh_connect_for_remote_train_node_uses_openssh_config(monkeypatch):
    monkeypatch.setattr(
        ssh_io,
        "_lookup_openssh_config",
        lambda host: {
            "hostname": "192.168.54.99",
            "user": "ubuntu",
            "port": 22,
            "identityfile": ["/root/.ssh/id_proxyjump"],
            "proxyjump": "root@10.10.11.21",
        },
    )
    monkeypatch.setattr(
        ssh_io,
        "_open_proxy_sock",
        lambda **kwargs: (object(), [object()]),
    )
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ssh_io, "ssh_connect", fake_connect)
    ssh_io.ssh_connect_for_remote_train_node(
        {
            "host": "target-54-99",
            "port": 22,
            "user": "root",
            "password": "",
        }
    )
    assert captured["host"] == "192.168.54.99"
    assert captured["username"] == "ubuntu"
    assert captured["private_key_paths"] == ["/root/.ssh/id_proxyjump"]
    assert captured["sock"] is not None
    assert captured["jump_keepalive"]


def test_parse_jump_target_user_host_port():
    user, host, port = ssh_io._parse_jump_target("root@10.10.11.21:2222")
    assert user == "root"
    assert host == "10.10.11.21"
    assert port == 2222


def test_resolve_jump_target_expands_ssh_host_alias(tmp_path, monkeypatch):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text(
        "\n".join(
            [
                "Host n2n-jump",
                "    HostName 10.10.11.21",
                "    User guojun",
                "    IdentityFile ~/.ssh/id_proxyjump",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ssh_io.Path, "home", staticmethod(lambda: tmp_path))

    user, host, port, keys = ssh_io._resolve_jump_target("n2n-jump")
    assert user == "guojun"
    assert host == "10.10.11.21"
    assert port == 22
    assert keys[0].endswith("id_proxyjump")


def test_strip_openssh_inline_comment_from_proxyjump():
    cfg = {
        "proxyjump": "n2n-jump              # OpenSSH 7.3+ 推荐",
        "identityfile": ["/root/.ssh/id_proxyjump  # comment"],
    }
    assert ssh_io._strip_openssh_inline_comment(cfg["proxyjump"]) == "n2n-jump"
    paths = ssh_io._collect_private_key_paths(private_key_path=None, openssh_cfg=cfg)
    assert paths == ["/root/.ssh/id_proxyjump"]
