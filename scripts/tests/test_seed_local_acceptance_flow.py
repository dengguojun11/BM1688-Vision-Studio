"""Static contracts for the local-only acceptance seed."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "seed_local_acceptance_flow.py"


def test_seed_is_no_project_and_keeps_training_lineage_on_new_task():
    source = SOURCE.read_text(encoding="utf-8")

    assert "project_id=1" not in source
    assert "project_id=None" in source
    assert "task_id=1" not in source
    assert "task_id=task.id" in source
    assert source.index("task = Task(") < source.index("job = YoloTrainJob(")


def test_seed_refuses_non_sqlite_databases_before_importing_application_services():
    source = SOURCE.read_text(encoding="utf-8")

    assert 'raise SystemExit("refusing non-SQLite local acceptance database")' in source
    assert 'os.environ["APP_ENV"] = "local"' in source
    assert "settings.AUTO_TRAINING_PLAN_WRITES_ENABLED = True" in source
