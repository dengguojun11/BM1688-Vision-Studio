from pathlib import Path
import re


DEPLOY_SH = Path(__file__).resolve().parents[2] / ".." / "deploy.sh"


def _function_body(source: str, name: str) -> str:
    start_match = re.search(rf"(?m)^{re.escape(name)}\(\) \{{", source)
    assert start_match, f"missing deploy function: {name}"
    start = start_match.end()
    next_match = re.search(r"(?m)^\w+\(\) \{", source[start:])
    end = start + next_match.start() if next_match else len(source)
    return source[start:end]


def test_dedicated_workers_are_opt_in_and_restart_safe():
    source = DEPLOY_SH.read_text(encoding="utf-8")
    contracts = {
        "write_and_enable_training_worker_systemd": (
            "LABELU_ENABLE_TRAINING_WORKER=\"${LABELU_ENABLE_TRAINING_WORKER:-0}\"",
            "WORKER_SYSTEMD_UNIT",
        ),
        "write_and_enable_bm1688_worker_systemd": (
            "LABELU_ENABLE_BM1688_WORKER=\"${LABELU_ENABLE_BM1688_WORKER:-0}\"",
            "BM1688_WORKER_SYSTEMD_UNIT",
        ),
        "write_and_enable_edge_validation_worker_systemd": (
            "LABELU_ENABLE_EDGE_VALIDATION_WORKER=\"${LABELU_ENABLE_EDGE_VALIDATION_WORKER:-0}\"",
            "EDGE_VALIDATION_WORKER_SYSTEMD_UNIT",
        ),
        "write_and_enable_experiment_dataset_worker_systemd": (
            "LABELU_ENABLE_EXPERIMENT_DATASET_WORKER=\"${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER:-0}\"",
            "EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT",
        ),
    }
    for function_name, (default_switch, unit_name) in contracts.items():
        body = _function_body(source, function_name)
        assert default_switch in source
        assert f"systemctl disable --now \"${{{unit_name}}}\"" in body
        assert "Restart=on-failure" in body
        assert "RestartSec=5" in body
        assert "TimeoutStopSec=120" in body
        assert "KillSignal=SIGTERM" in body
        assert f"systemctl daemon-reload" in body
        assert f"systemctl enable \"${{{unit_name}}}\"" in body


def test_worker_switches_fence_web_side_consumers_when_enabled():
    source = DEPLOY_SH.read_text(encoding="utf-8")
    # The API unit must explicitly turn off each local consumer when its
    # dedicated systemd worker is enabled; otherwise restart creates two
    # consumers for the same queue.
    assert "Environment=LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED=false" in source
    assert "Environment=LABELU_BM1688_DEDICATED_WORKER_ENABLED=false" in source
    assert "Environment=YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=false" in source
    assert "Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false" in source
