from pathlib import Path


def test_provider_preannotate_worker_migration_declares_all_durable_fields():
    path = Path(__file__).resolve().parents[2] / "alembic_labelu" / "versions" / "ze1f2a3b4c5d_add_provider_preannotate_worker_contract.py"
    source = path.read_text(encoding="utf-8")
    assert 'revision = "ze1f2a3b4c5d"' in source
    assert 'down_revision = "zd1e2f3a4b5c"' in source
    for table in ("tagtool_preannotate_job", "locateanything_preannotate_job"):
        assert f'"{table}"' in source
    for column in ("payload_json", "attempt_count", "lease_owner", "lease_expires_at", "lease_fencing_token"):
        assert f'"{column}"' in source
    assert '"provider"' in source
