from pathlib import Path

from labelu.internal.common.phase_one_candidate_baseline_guard import (
    PRODUCTION_BASELINE,
    guard_candidate_baseline,
)


def test_empty_bootstrap_is_not_accepted_as_production_baseline():
    report = guard_candidate_baseline(["dataset"])
    assert report.status == "阻塞"
    assert "空库bootstrap" in report.reason


def test_known_existing_table_blocks_create_table_candidate():
    report = guard_candidate_baseline(
        ["data_source", "new_candidate_table"],
        snapshot_tables=["data_source", "task"],
        snapshot_revision=PRODUCTION_BASELINE,
    )
    assert report.status == "阻塞"
    assert "data_source" in report.reason


def test_unseen_candidate_table_can_enter_isolated_ddl_rehearsal():
    report = guard_candidate_baseline(
        ["new_candidate_table"],
        snapshot_tables=["data_source", "task"],
        snapshot_revision=PRODUCTION_BASELINE,
    )
    assert report.status == "通过"
    assert report.snapshot_table_count == 2


def test_partial_snapshot_missing_known_production_table_stays_blocked():
    report = guard_candidate_baseline(
        ["new_candidate_table"],
        snapshot_tables=["task"],
        snapshot_revision=PRODUCTION_BASELINE,
    )
    assert report.status == "阻塞"
    assert "data_source" in report.reason


def test_ancestor_migration_evidence_is_required():
    root = Path(__file__).parents[3]
    path = root / "labelu" / "alembic_labelu" / "versions" / "b2c3d4e5f6a7_add_data_source.py"
    from labelu.internal.common.phase_one_candidate_baseline_guard import (
        assert_known_ancestor_evidence,
    )

    assert_known_ancestor_evidence(path)
