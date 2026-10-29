from alembic.config import Config
from alembic.script import ScriptDirectory

import labelu.internal.domain.models  # noqa: F401
from labelu.internal.common.db import Base
from labelu.internal.common.phase_one_migration_plan import (
    PHASE_ONE_MIGRATION_SEGMENTS,
    duplicate_project_fk_assignments,
    planned_project_fk_tables,
)
from labelu.internal.common.phase_one_schema import (
    phase_one_foreign_key_violations,
)


PRODUCTION_BASELINE = "h7i8j9k0l1m2"
CURRENT_LOCAL_HEAD = "zf2a3b4c5d6e"


def test_current_alembic_graph_has_known_single_head_and_production_baseline():
    config = Config("labelu/alembic_labelu/alembic.ini")
    config.set_main_option("script_location", "labelu/alembic_labelu")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == [CURRENT_LOCAL_HEAD]
    assert scripts.get_revision(PRODUCTION_BASELINE) is not None
    assert sum(1 for _ in scripts.walk_revisions()) == 145


def test_all_current_project_fk_violations_have_one_migration_owner():
    violations = phase_one_foreign_key_violations(Base.metadata)
    project_tables = frozenset(
        table for table, targets in violations.items() if "project" in targets
    )

    assert len(project_tables) == 20
    assert project_tables == planned_project_fk_tables()
    assert duplicate_project_fk_assignments() == {}
    assert tuple(PHASE_ONE_MIGRATION_SEGMENTS) == (
        "01_data_and_label_contract",
        "02_algorithm_and_model_lineage",
        "03_internal_execution",
        "04_ingestion_and_media",
        "05_feedback_and_active_learning",
        "06_preannotation_and_prediction",
        "07_business_rules",
        "08_compatibility_and_indexes",
    )


def test_first_segment_data_models_are_explicitly_owned_for_project_fk_removal():
    violations = phase_one_foreign_key_violations(Base.metadata)

    assert PHASE_ONE_MIGRATION_SEGMENTS["01_data_and_label_contract"] == frozenset()
    for table in (
        "dataset",
        "dataset_version",
        "dataset_label_contract",
        "dataset_label_contract_item",
    ):
        assert table not in violations
