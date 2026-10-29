"""Offline gates for replacing the unsafe post-production Alembic suffix."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PRODUCTION_BASELINE = "h7i8j9k0l1m2"
CURRENT_LOCAL_HEAD = "zf2a3b4c5d6e"
REPLACEMENT_REVISIONS = tuple(f"gai_p1_{number:02d}" for number in range(1, 9))
SEGMENTS = frozenset(f"{number:02d}" for number in range(1, 9))


@dataclass(frozen=True)
class LegacyRevisionPlan:
    action: str
    segments: tuple[str, ...]
    objects: tuple[str, ...]


def _replace(segment: str, *objects: str) -> LegacyRevisionPlan:
    return LegacyRevisionPlan("REPLACE", (segment,), objects)


def _exclude(*objects: str) -> LegacyRevisionPlan:
    return LegacyRevisionPlan("EXCLUDE", (), objects)


LEGACY_SUFFIX_PLAN = {
    "i8j9k0l1m2n3": _exclude("project", "project_member"),
    "j9k0l1m2n3o4": _replace("01", "dataset", "dataset_version"),
    "k0l1m2n3o4p5": _replace("02", "algorithm_group", "algorithm_definition"),
    "m2n3o4p5q6r7": _replace(
        "02", "model_version", "model_artifact", "model_quality_evidence"
    ),
    "n3o4p5q6r7s8": _exclude(
        "deployment_release", "deployment_target", "deployment_observation"
    ),
    "aa7gaiwf0001": _replace(
        "03", "workflow_instance", "workflow_step", "workflow_event", "workflow_outbox"
    ),
    "q9r0s1t2u3v4": _replace("05", "inference_event", "edge_hard_example"),
    "r0s1t2u3v4w5": _replace("01", "annotation_review"),
    "s2t3u4v5w6x7": _replace("05", "feedback_cluster", "active_learning_batch"),
    "t3u4v5w6x7y8": _replace("04", "media_upload_session", "sample_asset_fingerprint"),
    "u4v5w6x7y8z9": _replace("03", "scheduler_lease"),
    "v5w6x7y8z9a0": _exclude("task.project_id", "yolo_train_job.project_id"),
    "w6x7y8z9a0b": _replace("05", "active_learning_batch.dataset_version_id"),
    "x7y8z9a0b1c2": _replace("03", "auto_training_plan.dataset_version_id"),
    "y8z9a0b1c2d3": _exclude(
        "strategy_task_integration.project_id", "third_party_call_audit.project_id"
    ),
    "z9a0b1c2d3e4": _exclude(
        "edge_agent", "edge_agent_release", "edge_agent_deployment"
    ),
    "a0b1c2d3e4f5": _replace(
        "04", "media_asset.scan_status", "media_asset.quarantine_status"
    ),
    "b1c2d3e4f5a6": _exclude("unverified_algorithm_catalog_seed"),
    "b7c8d9e0f1a2": _exclude("deployment_transition_audit"),
    "c4d5e6f7a8b9": _replace("03", "workflow_governance_metadata"),
    "d5e6f7a8b9c0": _exclude("edge_agent_release.project_id"),
    "f7a8b9c0d1e2": _replace("02", "algorithm_governance_metadata"),
    "g8h9i0j1k2l3": _replace("06", "provider_registry"),
    "h9i0j1k2l3m4": _replace("04", "ingestion_job", "media_asset"),
    "i0j1k2l3m4n5": _replace("06", "provider_job", "prediction_set"),
    "k7l8m9n0p1q2": _replace(
        "06", "prediction_set_review_audit", "prediction_set_projection"
    ),
    "l8m9n0p1q2r3": _replace("03", "strategy_callback_workflow_bridge"),
    "m9n0p1q2r3s4": _replace("07", "rule_template", "rule_version", "rule_binding"),
    "n0p1q2r3s4t5": _replace("07", "rule_version_audit", "rule_binding_audit"),
    "o1p2q3r4s5t6": _exclude("rule_signing_contract"),
    "p2q3r4s5t6u7": _exclude("rule_device_signature_report"),
    "q3r4s5t6u7v8": _exclude("organization", "scene"),
    "z0a1b2c3d4e5": _exclude("model_optimization_job"),
    "z0a1b2c3d4f6": _replace(
        "01", "dataset.dataset_category_id", "dataset_version.dataset_category_id"
    ),
    "z1a2b3c4d5e6": _replace("02", "algorithm_target_chip_catalog"),
    "z2a3b4c5d6e7": _replace("01", "dataset_version_review"),
    "z3a4b5c6d7e8": _replace("01", "dataset_label_contract", "task_dataset_lineage"),
    "z4a5b6c7d8e9": _replace(
        "08", "execution_leases", "bm1688_worker_actions", "training_runtime_fields"
    ),
    "z5a6b7c8d9e0": _replace("08", "ingestion_project_scope"),
    "z6b7c8d9e0f1": _replace("06", "provider_project_scope"),
    "z7a8b9c0d1e2": _replace("08", "experiment_dataset_import_worker_lease"),
    "z9b0c1d2e3f4": _replace("08", "aicm_publish_claim_lease"),
    "z9c0d1e2f3a4": _replace(
        "05", "inference_event.scope_key", "edge_hard_example.scope_key"
    ),
    "z9d1e2f3a4b5": _replace(
        "05", "feedback_cluster.scope_key", "active_learning_batch.scope_key"
    ),
    "z9e2f3a4b5c6": _replace("03", "workflow_instance.scope_key"),
    "z9f3a4b5c6d7": _replace(
        "01", "dataset.project_id", "dataset_version.project_id"
    ),
    "za0b1c2d3e4f": _replace(
        "08", "task.idempotency_key", "yolo_train_job.idempotency_key"
    ),
    # These four revisions are the post-za0 worker/data-source suffix. They
    # remain in the legacy audit so the object-level mapping covers every
    # revision between the production baseline and the current formal head.
    "zb1c2d3e4f5g": _replace("04", "attachment.s3_source_identity"),
    "zc1d2e3f4a5b": _replace("06", "auto_label_job.execution_leases"),
    "zd1e2f3a4b5c": _replace("06", "yolo_preannotate_job.execution_leases"),
    "ze1f2a3b4c5d": _replace(
        "06", "tagtool_preannotate_job", "locateanything_preannotate_job"
    ),
    "zf2a3b4c5d6e": _replace(
        "08",
        "training_task.auto_training_round_id",
        "yolo_train_job.auto_training_round_id",
        "yolo_bm1688_export.auto_training_round_id",
        "yolo_edge_validation_job.auto_training_round_id",
    ),
}


def audit_legacy_suffix(alembic_ini: str | Path) -> dict[str, object]:
    """Verify every revision after production has an explicit replacement decision."""

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", "labelu/alembic_labelu")
    scripts = ScriptDirectory.from_config(config)
    revisions = tuple(
        revision.revision
        for revision in scripts.iterate_revisions("heads", PRODUCTION_BASELINE)
    )
    discovered = frozenset(revisions)
    planned = frozenset(LEGACY_SUFFIX_PLAN)
    errors: list[str] = []
    if scripts.get_heads() != [CURRENT_LOCAL_HEAD]:
        errors.append(f"unexpected current heads: {scripts.get_heads()}")
    if discovered != planned:
        errors.append(
            f"legacy mapping mismatch missing={sorted(discovered - planned)} "
            f"extra={sorted(planned - discovered)}"
        )
    for revision, plan in LEGACY_SUFFIX_PLAN.items():
        if plan.action not in {"REPLACE", "EXCLUDE"}:
            errors.append(f"{revision}: unsupported action {plan.action}")
        if not plan.objects:
            errors.append(f"{revision}: object mapping is empty")
        if plan.action == "EXCLUDE" and plan.segments:
            errors.append(f"{revision}: excluded revision has replacement segment")
        if plan.action == "REPLACE" and (
            not plan.segments
            or any(segment not in SEGMENTS for segment in plan.segments)
        ):
            errors.append(f"{revision}: invalid replacement segments {plan.segments}")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "production_baseline": PRODUCTION_BASELINE,
        "current_head": CURRENT_LOCAL_HEAD,
        "legacy_revision_count": len(revisions),
        "replace_count": sum(
            plan.action == "REPLACE" for plan in LEGACY_SUFFIX_PLAN.values()
        ),
        "exclude_count": sum(
            plan.action == "EXCLUDE" for plan in LEGACY_SUFFIX_PLAN.values()
        ),
    }


def _migration_identity(path: Path) -> tuple[str, str | None]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {
                "revision",
                "down_revision",
            }:
                values[target.id] = ast.literal_eval(value)
    revision = values.get("revision")
    down_revision = values.get("down_revision")
    if not isinstance(revision, str):
        raise ValueError(f"{path.name}: revision must be a string")
    if down_revision is not None and not isinstance(down_revision, str):
        raise ValueError(f"{path.name}: merge or dynamic down_revision is forbidden")
    return revision, down_revision


def audit_replacement_workspace(versions_dir: str | Path) -> dict[str, object]:
    """Validate an isolated eight-file replacement suffix without executing it."""

    directory = Path(versions_dir)
    files = sorted(
        path for path in directory.glob("*.py") if path.name != "__init__.py"
    )
    identities = [_migration_identity(path) for path in files]
    revisions = [revision for revision, _ in identities]
    if len(files) != 8:
        raise ValueError(
            f"replacement workspace must contain 8 migrations, got {len(files)}"
        )
    if len(set(revisions)) != len(revisions) or set(revisions) != set(
        REPLACEMENT_REVISIONS
    ):
        raise ValueError(f"unexpected replacement revisions: {sorted(revisions)}")
    parents = dict(identities)
    expected_parent = PRODUCTION_BASELINE
    for revision in REPLACEMENT_REVISIONS:
        down_revision = parents[revision]
        if down_revision != expected_parent:
            raise ValueError(
                f"{revision}: expected down_revision {expected_parent}, got {down_revision}"
            )
        expected_parent = revision
    return {
        "production_baseline": PRODUCTION_BASELINE,
        "head": REPLACEMENT_REVISIONS[-1],
        "revision_count": len(REPLACEMENT_REVISIONS),
    }
