from pathlib import Path


def test_deploy_script_contains_experiment_dataset_worker_lifecycle_contract():
    deploy = (Path(__file__).resolve().parents[3] / "deploy.sh").read_text(
        encoding="utf-8"
    )
    required = (
        'readonly EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT="labelu-experiment-dataset-worker.service"',
        'LABELU_ENABLE_EXPERIMENT_DATASET_WORKER="${LABELU_ENABLE_EXPERIMENT_DATASET_WORKER:-0}"',
        "Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=true",
        "Environment=EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false",
        "--preflight",
        "RestartPreventExitStatus=78",
        "write_and_enable_experiment_dataset_worker_systemd",
        "-m labelu.scripts.experiment_dataset_import_worker --limit ${limit}",
        "deploy_experiment_dataset_worker_values_valid",
        'systemctl start "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"',
        'systemctl restart "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"',
        'systemctl stop "${EXPERIMENT_DATASET_WORKER_SYSTEMD_UNIT}"',
        "deploy_checklist_experiment_dataset_worker",
    )
    missing = [value for value in required if value not in deploy]
    assert not missing, f"deploy contract missing: {missing}"


def test_deploy_script_documents_s3_worker_as_opt_in():
    deploy = (Path(__file__).resolve().parents[3] / "deploy.sh").read_text(
        encoding="utf-8"
    )
    assert "LABELU_ENABLE_EXPERIMENT_DATASET_WORKER=0" in deploy
    assert "EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS=5" in deploy
    assert "EXPERIMENT_DATASET_WORKER_LEASE_SECONDS=300" in deploy
    assert "EXPERIMENT_DATASET_WORKER_BATCH_SIZE=1" in deploy
