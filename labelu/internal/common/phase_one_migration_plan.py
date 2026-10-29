"""Machine-checkable ownership for the eight phase-one migration segments."""

from __future__ import annotations


PHASE_ONE_MIGRATION_SEGMENTS = {
    # Dataset and DatasetVersion keep nullable historical project columns, but
    # their一期 ORM foreign keys have already been removed.  They therefore
    # stay in the compatibility audit, not in the active FK-removal queue.
    "01_data_and_label_contract": frozenset(),
    "02_algorithm_and_model_lineage": frozenset({"yolo_train_job"}),
    "03_internal_execution": frozenset(
        {"auto_training_plan", "workflow_instance"}
    ),
    "04_ingestion_and_media": frozenset({"ingestion_job", "media_asset"}),
    "05_feedback_and_active_learning": frozenset(
        {
            "active_learning_batch",
            "edge_hard_example",
            "feedback_cluster",
            "inference_event",
        }
    ),
    "06_preannotation_and_prediction": frozenset(
        {
            "prediction_set",
            "prediction_set_projection",
            "prediction_set_review_audit",
            "provider_job",
        }
    ),
    "07_business_rules": frozenset(
        {
            "rule_binding",
            "rule_binding_audit",
            "rule_template",
            "rule_version_audit",
        }
    ),
    "08_compatibility_and_indexes": frozenset(
        {
            "data_source",
            "task",
            "third_party_call_audit",
        }
    ),
}


def planned_project_fk_tables() -> frozenset[str]:
    """Return every current business table assigned to project-FK removal."""

    return frozenset().union(*PHASE_ONE_MIGRATION_SEGMENTS.values())


def duplicate_project_fk_assignments() -> dict[str, tuple[str, ...]]:
    """Return tables accidentally assigned to more than one migration segment."""

    owners: dict[str, list[str]] = {}
    for segment, tables in PHASE_ONE_MIGRATION_SEGMENTS.items():
        for table in tables:
            owners.setdefault(table, []).append(segment)
    return {
        table: tuple(segments)
        for table, segments in sorted(owners.items())
        if len(segments) > 1
    }
