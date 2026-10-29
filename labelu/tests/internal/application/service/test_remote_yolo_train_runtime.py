import json
from types import SimpleNamespace

import pytest

from labelu.internal.application.service import remote_yolo_train as remote_svc
from labelu.internal.application.service import yolo_train as train_svc
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus


def _remote_job(**kwargs):
    defaults = {
        "id": 117,
        "status": YoloTrainStatus.PROCESSING.value,
        "execution_mode": "remote",
        "train_node_id": "gpu-54-99",
        "remote_status_json": '{"phase":"uploading"}',
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_remote_result_contract_rejects_signature_mismatch():
    job = _remote_job(
        class_names_json='["person", "car"]',
        label_set_signature="set-signature",
        class_order_signature="order-signature",
        data_yaml_sha256="yaml-signature",
        dataset_content_signature="dataset-signature",
    )
    expected = {
        "class_names": ["person", "car"],
        "label_set_signature": "set-signature",
        "class_order_signature": "order-signature",
        "data_yaml_sha256": "yaml-signature",
        "dataset_content_signature": "dataset-signature",
    }
    remote_svc._assert_remote_result_class_contract(job, expected)

    mismatched = dict(expected, class_names=["car", "person"])
    with pytest.raises(RuntimeError, match="class_names"):
        remote_svc._assert_remote_result_class_contract(job, mismatched)


def test_remote_status_stale_fencing_token_is_rejected(monkeypatch):
    """远程轮询迟到写入不能覆盖已接管执行器的状态。"""
    class _Scope:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    job = SimpleNamespace(remote_status_json='{"phase":"remote_running"}')
    monkeypatch.setattr(remote_svc, "session_scope", lambda: _Scope())
    monkeypatch.setattr(
        remote_svc.crud_yolo_train_job,
        "get",
        lambda db, job_id: job,
    )
    seen = []

    def conditional(_db, token, values):
        seen.append((token.owner, token.fencing_token, values))
        return int(token.fencing_token) == 2

    monkeypatch.setattr(remote_svc, "conditional_execution_update", conditional)
    old = SimpleNamespace(owner="worker-a", fencing_token=1)
    current = SimpleNamespace(owner="worker-b", fencing_token=2)

    assert not remote_svc._persist_remote_status_patch(
        117, {"phase": "failed"}, old
    )
    assert remote_svc._persist_remote_status_patch(
        117, {"phase": "completed"}, current
    )
    assert [row[:2] for row in seen] == [("worker-a", 1), ("worker-b", 2)]


def test_remote_job_runtime_status_dispatching(monkeypatch):
    monkeypatch.setattr(remote_svc, "_ACTIVE_REMOTE_JOBS", {117})
    assert remote_svc.remote_job_runtime_status(_remote_job()) == "RUNNING"


def test_remote_job_runtime_status_uploading_phase():
    job = _remote_job(remote_status_json='{"phase":"uploading"}')
    assert remote_svc.remote_job_runtime_status(job) == "RUNNING"


def test_remote_job_runtime_status_downloading_phase():
    job = _remote_job(remote_status_json='{"phase":"downloading"}')
    assert remote_svc.remote_job_runtime_status(job) == "RUNNING"


def test_remote_job_runtime_status_dataset_ready_while_uploading(monkeypatch):
    monkeypatch.setattr(
        train_svc,
        "_read_train_phase",
        lambda _jid: ("dataset_ready", "数据集就绪", None),
    )
    job = _remote_job(remote_status_json='{"phase":"uploading"}')
    assert remote_svc.remote_job_runtime_status(job) == "RUNNING"


def test_runtime_fields_remote_processing_not_stopped(monkeypatch):
    monkeypatch.setattr(remote_svc, "_ACTIVE_REMOTE_JOBS", {117})
    monkeypatch.setattr(
        train_svc,
        "_read_train_phase",
        lambda _jid: ("dataset_ready", "数据集就绪", None),
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train_queue.active_yolo_train_job_ids_on_gpu",
        lambda: set(),
    )

    fields = train_svc._runtime_fields_for_job(_remote_job())
    assert fields["runtime_status"] == "RUNNING"
    assert fields["is_restartable"] is False


def test_pull_and_start_remote_job_uses_http_not_sftp(monkeypatch, tmp_path):
    captured = {"upload": 0, "start_cmd": ""}

    def fake_connect(node, *, timeout=30):
        return object()

    def fake_upload(*args, **kwargs):
        captured["upload"] += 1

    def fake_run(client, command, *, timeout=60):
        if "setsid" in command:
            captured["start_cmd"] = command
        if "pgrep" in command:
            return 0, "IDLE\n", ""
        return 0, "ok", ""

    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_connect_for_remote_train_node",
        fake_connect,
    )
    monkeypatch.setattr(
        remote_svc,
        "_build_job_payload_archive",
        lambda job_id, cmd, node=None: tmp_path / "bundle.tar.gz",
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_mkdir_p",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_upload",
        fake_upload,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_run",
        fake_run,
    )
    monkeypatch.setattr(remote_svc, "_persist_remote_status_patch", lambda *a, **k: None)
    monkeypatch.setattr(remote_svc, "_append_train_process_log", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(remote_svc, "schedule_remote_train_poll", lambda *a, **k: None)
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._write_train_phase",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        remote_svc.settings,
        "MEDIA_HOST",
        "http://113.141.72.175:10000",
    )

    (tmp_path / "bundle.tar.gz").write_bytes(b"x" * 100)

    node = {
        "id": "gpu-54-99",
        "host": "target-54-99",
        "user": "ubuntu",
        "remote_base": "/mnt/sda1/labelu_remote_train",
        "python_path": "/home/ubuntu/anaconda3/envs/yolo/bin/python",
        "agent_path": "/mnt/sda1/labelu-remote-train-agent/agent_worker.py",
    }
    cmd = SimpleNamespace(model_weights="yolo11n.pt")
    out = remote_svc._pull_and_start_remote_job(118, cmd, node=node)
    assert captured["upload"] == 1
    assert "setsid" in captured["start_cmd"]
    assert "--payload-url" in captured["start_cmd"]
    assert "--payload-parallel" in captured["start_cmd"]
    assert out["phase"] == "downloading"
    assert out.get("transfer_mode") == "pull"


def test_upload_uses_ssh_connect_for_remote_train_node(monkeypatch, tmp_path):
    captured = {}

    def fake_connect(node, *, timeout=30):
        captured["node"] = node
        captured["timeout"] = timeout
        return object()

    upload_kw = {}

    def fake_upload(client, local_path, remote_path, *, progress_callback=None):
        upload_kw["progress_callback"] = progress_callback
        if progress_callback:
            progress_callback(50, 100)

    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_connect_for_remote_train_node",
        fake_connect,
    )
    monkeypatch.setattr(
        remote_svc,
        "_build_job_payload_archive",
        lambda job_id, cmd, node=None: tmp_path / "bundle.tar.gz",
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_mkdir_p",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_upload",
        fake_upload,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_run",
        lambda *a, **k: (0, "ok", ""),
    )
    monkeypatch.setattr(remote_svc, "_append_train_process_log", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(remote_svc, "_persist_remote_status_patch", lambda *a, **k: None)

    def noop_phase(*a, **k):
        pass

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._write_train_phase",
        noop_phase,
    )

    (tmp_path / "bundle.tar.gz").write_bytes(b"x" * 100)

    node = {
        "id": "gpu-54-99",
        "host": "target-54-99",
        "user": "ubuntu",
        "remote_base": "/mnt/sda1/labelu_remote_train",
        "python_path": "/home/ubuntu/anaconda3/envs/yolo/bin/python",
        "agent_path": "/mnt/sda1/labelu-remote-train-agent/agent_worker.py",
    }
    cmd = SimpleNamespace(model_weights="yolo11n.pt")
    out = remote_svc._upload_and_start_remote_job(117, cmd, node=node)
    assert captured["node"]["host"] == "target-54-99"
    assert upload_kw.get("progress_callback") is not None
    assert out["phase"] == "remote_running"


def test_poll_remote_empty_phase_keeps_local_phase(monkeypatch, tmp_path):
    work = tmp_path / "job-124"
    work.mkdir()
    local_phase = {"phase": "running", "detail": "remote pull node=54.120"}
    (work / "phase.json").write_text(json.dumps(local_phase), encoding="utf-8")

    class FakeClient:
        def close(self):
            pass

    def fake_download(_client, remote_path, local_path):
        if remote_path.endswith("/phase.json"):
            local_path.write_bytes(b"")
            return True
        return False

    monkeypatch.setattr(
        remote_svc,
        "get_node_by_id",
        lambda _node_id: {"id": "54.120", "remote_base": "/remote/train"},
    )
    monkeypatch.setattr(remote_svc, "_ssh_client_for_node", lambda _node: FakeClient())
    monkeypatch.setattr(remote_svc, "_job_work_root", lambda _job_id: work)
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_download",
        fake_download,
    )
    monkeypatch.setattr(remote_svc, "session_scope", None)

    remote_svc._poll_remote_train_once(124, "54.120")

    assert json.loads((work / "phase.json").read_text(encoding="utf-8")) == local_phase
    assert not (work / "remote_phase.json").exists()


def test_poll_remote_valid_phase_writes_remote_phase_not_local_phase(monkeypatch, tmp_path):
    work = tmp_path / "job-124"
    work.mkdir()
    local_phase = {"phase": "running", "detail": "remote pull node=54.120"}
    remote_phase = {"phase": "downloading", "detail": "pulling", "download_pct": 7}
    (work / "phase.json").write_text(json.dumps(local_phase), encoding="utf-8")

    class FakeClient:
        def close(self):
            pass

    def fake_download(_client, remote_path, local_path):
        if remote_path.endswith("/phase.json"):
            local_path.write_text(json.dumps(remote_phase), encoding="utf-8")
            return True
        return False

    class FakeSessionScope:
        def __enter__(self):
            return SimpleNamespace()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        remote_svc,
        "get_node_by_id",
        lambda _node_id: {"id": "54.120", "remote_base": "/remote/train"},
    )
    monkeypatch.setattr(remote_svc, "_ssh_client_for_node", lambda _node: FakeClient())
    monkeypatch.setattr(remote_svc, "_job_work_root", lambda _job_id: work)
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_download",
        fake_download,
    )
    monkeypatch.setattr(remote_svc, "session_scope", lambda: FakeSessionScope())
    monkeypatch.setattr(
        remote_svc.crud_yolo_train_job,
        "get",
        lambda db, job_id: SimpleNamespace(remote_status_json="{}"),
    )
    monkeypatch.setattr(remote_svc, "begin_transaction", lambda _db: FakeSessionScope())
    monkeypatch.setattr(remote_svc, "_write_remote_status", lambda *args, **kwargs: None)

    remote_svc._poll_remote_train_once(124, "54.120")

    assert json.loads((work / "phase.json").read_text(encoding="utf-8")) == local_phase
    assert json.loads((work / "remote_phase.json").read_text(encoding="utf-8")) == remote_phase


def test_poll_remote_stale_operation_phase_is_ignored(monkeypatch, tmp_path):
    work = tmp_path / "job-125"
    work.mkdir()
    stale_phase = {
        "phase": "completed",
        "detail": "old worker",
        "operation_id": "worker-a:1",
    }

    class FakeClient:
        def close(self):
            pass

    def fake_download(_client, remote_path, local_path):
        if remote_path.endswith("/phase.json"):
            local_path.write_text(json.dumps(stale_phase), encoding="utf-8")
            return True
        return False

    token = SimpleNamespace(owner="worker-b", fencing_token=2)
    persisted = []
    monkeypatch.setattr(
        remote_svc,
        "get_node_by_id",
        lambda _node_id: {"id": "54.120", "remote_base": "/remote/train"},
    )
    monkeypatch.setattr(remote_svc, "_ssh_client_for_node", lambda _node: FakeClient())
    monkeypatch.setattr(remote_svc, "_job_work_root", lambda _job_id: work)
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_download",
        fake_download,
    )
    monkeypatch.setattr(remote_svc, "_renew_remote_execution", lambda _jid, value: value)
    monkeypatch.setattr(remote_svc, "_assert_remote_execution_current", lambda *args: None)
    monkeypatch.setattr(
        remote_svc,
        "_remote_artifact_matches",
        lambda _jid, _token, artifact: artifact.get("operation_id") == "worker-b:2",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_train._append_train_process_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        remote_svc,
        "_persist_remote_status_patch",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    remote_svc._poll_remote_train_once(125, "54.120", token)

    assert persisted == []
    assert not (work / "remote_phase.json").exists()


def test_sync_remote_train_artifacts_includes_analysis_images(monkeypatch, tmp_path):
    downloads = []
    tree_downloads = []

    def fake_download(_client, remote_path, local_path):
        downloads.append((remote_path, local_path))
        return False

    def fake_download_tree(_client, remote_path, local_path):
        tree_downloads.append((remote_path, local_path))
        return 0

    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_download",
        fake_download,
    )
    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.sftp_download_tree",
        fake_download_tree,
    )

    work = tmp_path / "job-130"
    remote_svc._sync_remote_train_artifacts(
        object(),
        remote_job_dir="/remote/train/job-130",
        work=work,
    )

    remote_paths = {row[0] for row in downloads}
    assert "/remote/train/job-130/train/results.png" in remote_paths
    assert "/remote/train/job-130/train/confusion_matrix.png" in remote_paths
    assert "/remote/train/job-130/train/BoxPR_curve.png" in remote_paths
    assert "/remote/train/job-130/train/val_batch0_pred.jpg" in remote_paths
    assert tree_downloads == [
        (
            "/remote/train/job-130/train/weights",
            work / "train" / "weights",
        )
    ]
