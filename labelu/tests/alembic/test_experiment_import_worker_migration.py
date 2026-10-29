from pathlib import Path


def test_experiment_import_worker_migration_declares_fencing_contract():
    path = Path(__file__).parents[2] / "alembic_labelu" / "versions" / "z7a8b9c0d1e2_add_experiment_import_worker_lease.py"
    text = path.read_text(encoding="utf-8")
    assert 'revision = "z7a8b9c0d1e2"' in text
    assert 'down_revision = "z6b7c8d9e0f1"' in text
    for field in (
        "worker_lease_owner",
        "worker_lease_expires_at",
        "worker_fencing_token",
        "attempt_count",
        "idx_experiment_dataset_import_worker_scan",
    ):
        assert field in text

