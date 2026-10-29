from labelu.internal.common import remote_train_nodes_config as cfg_io


def test_save_and_load_remote_nodes(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_io.settings, "BASE_DATA_DIR", str(tmp_path))
    cfg_io.save_remote_train_nodes_config(
        {
            "nodes": [
                {
                    "id": "gpu-21",
                    "name": "训练机",
                    "host": "10.10.11.21",
                    "remote_base": "/data/train",
                    "python_path": "/opt/venv/bin/python",
                    "password": "secret",
                }
            ]
        }
    )
    loaded = cfg_io.load_remote_train_nodes_config()
    assert len(loaded["nodes"]) == 1
    assert loaded["nodes"][0]["id"] == "gpu-21"
    assert loaded["nodes"][0]["password"] == "secret"

    cfg_io.save_remote_train_nodes_config(
        {
            "nodes": [
                {
                    "id": "gpu-21",
                    "name": "训练机-更新",
                    "host": "10.10.11.21",
                    "remote_base": "/data/train",
                    "python_path": "/opt/venv/bin/python",
                }
            ]
        }
    )
    loaded2 = cfg_io.load_remote_train_nodes_config()
    assert loaded2["nodes"][0]["password"] == "secret"
    assert loaded2["nodes"][0]["name"] == "训练机-更新"


def test_save_preserves_private_key_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_io.settings, "BASE_DATA_DIR", str(tmp_path))
    cfg_io.save_remote_train_nodes_config(
        {
            "nodes": [
                {
                    "id": "gpu-54",
                    "host": "10.0.54.99",
                    "remote_base": "/data/train",
                    "python_path": "/opt/venv/bin/python",
                    "private_key_path": "/root/.ssh/id_ed25519_gpu54",
                }
            ]
        }
    )
    cfg_io.save_remote_train_nodes_config(
        {
            "nodes": [
                {
                    "id": "gpu-54",
                    "host": "10.0.54.99",
                    "remote_base": "/data/train",
                    "python_path": "/opt/venv/bin/python",
                }
            ]
        }
    )
    loaded = cfg_io.load_remote_train_nodes_config()
    assert loaded["nodes"][0]["private_key_path"] == "/root/.ssh/id_ed25519_gpu54"


def test_get_node_by_id_missing():
    assert cfg_io.get_node_by_id("none") is None
