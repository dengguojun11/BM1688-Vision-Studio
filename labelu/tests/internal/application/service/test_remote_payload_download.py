from pathlib import Path

from labelu.internal.application.service import remote_yolo_train as svc


def test_nginx_accel_redirect_path():
    p = svc.nginx_accel_redirect_path(119, Path("remote_job_payload.tar"))
    assert p == "/internal/yolo-remote-payload/job-119/remote_job_payload.tar"


def test_payload_download_base_url_prefers_node(monkeypatch):
    monkeypatch.setattr(svc.settings, "MEDIA_HOST", "http://public:10000")
    monkeypatch.setattr(svc.settings, "YOLO_REMOTE_TRAIN_PAYLOAD_DOWNLOAD_HOST", "http://internal:10000")
    assert svc.payload_download_base_url(node={"payload_download_host": "http://node:10000"}) == "http://node:10000"
    assert svc.payload_download_base_url() == "http://internal:10000"


def test_infer_payload_download_host_same_subnet(monkeypatch):
    monkeypatch.setattr(svc, "_linux_global_ipv4s", lambda: ["10.10.11.25", "10.0.0.5"])
    monkeypatch.setattr(svc, "_resolve_node_host_ip", lambda _node: "10.10.11.21")
    monkeypatch.setattr(svc, "_media_listen_port", lambda: 10000)
    node = {"host": "10.10.11.21"}
    assert svc.infer_payload_download_host_for_node(node) == "http://10.10.11.25:10000"
    assert svc.payload_download_base_url(node=node) == "http://10.10.11.25:10000"


def test_should_use_http_pull_when_internal_host_configured(monkeypatch):
    monkeypatch.setattr(svc.settings, "YOLO_REMOTE_TRAIN_PULL_PAYLOAD", True)
    node = {"host": "10.10.11.21", "payload_download_host": "http://10.10.11.25:10000"}
    assert svc.should_use_http_pull_for_node(node) is True


def test_should_use_sftp_push_when_no_internal_path(monkeypatch):
    monkeypatch.setattr(svc.settings, "YOLO_REMOTE_TRAIN_PULL_PAYLOAD", True)
    monkeypatch.setattr(svc, "infer_payload_download_host_for_node", lambda _n: None)
    node = {"host": "target-54-99", "pull_payload": None}
    assert svc.should_use_http_pull_for_node(node) is False


def test_build_remote_agent_start_command_uses_setsid():
    cmd = svc.build_remote_agent_start_command(
        remote_job_dir="/data/job-1",
        python_path="/usr/bin/python3",
        agent_path="/opt/agent_worker.py",
        extra_args="--job-dir '/data/job-1'",
    )
    assert "setsid" in cmd
    assert "/dev/null" in cmd


def test_start_remote_agent_worker_treats_running_as_success(monkeypatch):
    class FakeClient:
        pass

    def fake_run(_client, command, *, timeout=60):
        if "pgrep" in command:
            return 0, "RUNNING\n", ""
        return 0, "12345\n", ""

    monkeypatch.setattr(
        "labelu.internal.common.edge_ssh_client.ssh_run",
        fake_run,
    )
    ok, msg = svc._start_remote_agent_worker(
        FakeClient(),
        remote_job_dir="/data/job-1",
        python_path="/usr/bin/python3",
        agent_path="/opt/agent_worker.py",
        extra_args="--job-dir '/data/job-1'",
    )
    assert ok is True
    assert "已在运行" in msg
