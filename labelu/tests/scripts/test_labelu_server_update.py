from pathlib import Path


def test_server_update_reuses_verified_python_for_training_guard():
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "labelu_server_update.sh"
    ).read_text(encoding="utf-8")

    restart_block = script.split("run_restart() {", 1)[1].split(
        "run_http_probe() {", 1
    )[0]
    assert 'runtime_python="$(labelu_migrate_pick_python)"' in restart_block
    assert 'export LABELU_PYTHON="${LABELU_PYTHON:-${runtime_python}}"' in restart_block
    assert 'export VENV_DIR="${VENV_DIR:-$(cd' in restart_block
    assert 'export LABELU_ENV_FILE="${LABELU_ENV_FILE:-${runtime_dir%/}/.env}"' in restart_block
    assert '${LABELU_DEPLOY_RUNTIME_DIR:-}/venv' not in restart_block
