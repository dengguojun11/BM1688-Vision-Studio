from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "rehearse_dataset_project_fk_rebuild.py"


def test_dataset_fk_rehearsal_has_strict_sandbox_and_cleanup_contract():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "labelu_migration_sandbox" in source
    assert "LABELU_MYSQL_REHEARSAL_CONFIRM" in source
    assert "DROP TABLE IF EXISTS" in source
    assert "生产写入" in source
    assert "gai_p1_01_project_fk_rebuild.py" in source

