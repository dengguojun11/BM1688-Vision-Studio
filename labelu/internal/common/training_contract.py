"""Canonical names shared by the training creation and iteration contracts.

The first training wizard used ``yolov11_detection`` as the recipe key while
the persisted catalog uses the versioned ``yolo_detection_v1`` key.  Keep the
alias at the contract boundary so old clients remain readable without
creating a second recipe or changing the runtime profile used by publishing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

TRAINING_RECIPE_ALIASES: dict[str, str] = {
    "yolov11_detection": "yolo_detection_v1",
}

# ``yolov11_detection`` is the established runtime profile in publish
# templates.  Accepting the canonical recipe-shaped spelling here makes the
# API tolerant of clients that reuse the recipe key for the runtime choice.
RUNTIME_PROFILE_ALIASES: dict[str, str] = {
    "yolo_detection_v1": "yolov11_detection",
}


def canonical_training_recipe_key(value: str | None) -> str:
    """Return the persisted recipe key for a user/API supplied value."""

    normalized = str(value or "").strip()
    return TRAINING_RECIPE_ALIASES.get(normalized, normalized)


def canonical_runtime_profile(value: str | None) -> str:
    """Return the publish runtime profile for a user/API supplied value."""

    normalized = str(value or "").strip()
    return RUNTIME_PROFILE_ALIASES.get(normalized, normalized)


def _canonical_value(value: Any) -> Any:
    """Normalize JSON-like values without changing meaningful list order.

    Task and label arrays can carry class order, so sorting every list here
    would make two different detection-head contracts look identical.  Callers
    should sort only the explicitly order-insensitive fields before invoking
    this helper.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, set):
        normalized = [_canonical_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def canonical_selection_contract(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deterministic, JSON-safe selection contract.

    The contract is intentionally permissive for forward-compatible fields,
    while guaranteeing a schema version and canonical key ordering.  Unknown
    fields are retained so newer clients can roll out before older workers.
    """

    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("selection_contract must be an object")
    normalized = _canonical_value(dict(value))
    if not isinstance(normalized, dict):  # pragma: no cover - defensive guard
        normalized = {}
    try:
        schema_version = int(normalized.get("schema_version") or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("selection_contract.schema_version must be an integer") from exc
    if schema_version < 1:
        raise ValueError("selection_contract.schema_version must be positive")
    normalized["schema_version"] = schema_version
    return normalized


def selection_contract_json(value: Mapping[str, Any] | None) -> str:
    """Serialize a contract using stable separators for snapshots and hashes."""

    return json.dumps(
        canonical_selection_contract(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def selection_contract_sha256(value: Mapping[str, Any] | None) -> str:
    """Hash the complete canonical selection contract.

    This digest includes the immutable dataset snapshot reference and is used
    as the evidence hash for a particular training run.  It is deliberately
    different from :func:`branch_identity_sha256`: refreshing a dataset
    snapshot must create a new evidence hash without silently creating a new
    model branch.
    """

    return hashlib.sha256(selection_contract_json(value).encode("utf-8")).hexdigest()


def branch_identity_contract(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the stable portion of a selection contract used for a branch.

    A model branch represents *what* is being trained: the logical dataset
    scope, task/label projection, global conditions, class contract and
    recipe.  The concrete frozen dataset version represents *which snapshot*
    was used for one run and therefore belongs only to the full selection
    contract hash.  Keep the logical dataset IDs in the identity, but remove
    snapshot-specific fields so a refreshed version becomes v2 of the same
    branch instead of a new branch.

    Unknown top-level extension fields are retained.  Only fields explicitly
    known to identify a mutable snapshot are removed, which keeps the helper
    forward-compatible with newer contract schemas.
    """

    contract = canonical_selection_contract(value)
    identity = dict(contract)
    dataset_scope = identity.get("dataset_scope")
    if isinstance(dataset_scope, Mapping):
        stable_scope = dict(dataset_scope)
        stable_scope.pop("dataset_version_id", None)
        # Older clients occasionally placed these snapshot coordinates beside
        # dataset_version_id.  They are evidence fields, not branch inputs.
        for key in (
            "version_no",
            "manifest_rel_path",
            "manifest_sha256",
            "content_sha256",
            "dataset_content_signature",
        ):
            stable_scope.pop(key, None)
        identity["dataset_scope"] = stable_scope
    for key in (
        "dataset_version_id",
        "dataset_version_no",
        "dataset_manifest",
        "manifest_rel_path",
        "manifest_sha256",
        "dataset_content_signature",
    ):
        identity.pop(key, None)
    return canonical_selection_contract(identity)


def branch_identity_sha256(value: Mapping[str, Any] | None) -> str:
    """Hash the stable branch identity portion of a selection contract."""

    return hashlib.sha256(
        selection_contract_json(branch_identity_contract(value)).encode("utf-8")
    ).hexdigest()


def build_selection_contract(
    *,
    dataset_id: int | None = None,
    dataset_ids: list[int] | tuple[int, ...] | None = None,
    dataset_version_id: int | None = None,
    mode: str | None = None,
    task_ids: list[int] | tuple[int, ...] | None = None,
    selected_label_ids: list[int] | tuple[int, ...] | None = None,
    sample_conditions: Mapping[str, list[str]] | None = None,
    annotation_contract: Mapping[str, Any] | None = None,
    recipe: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the server-owned subset of a branch selection contract.

    Task IDs and label IDs are set-like for branch identity; class order must
    be carried separately under ``annotation_contract.class_order`` when it is
    meaningful.  Global-tag dimensions are sorted while values within a
    dimension are sorted, implementing the documented OR/AND semantics.
    """

    conditions: list[dict[str, Any]] = []
    for dimension, values in (sample_conditions or {}).items():
        normalized_values = sorted(
            {str(item).strip() for item in (values or []) if str(item).strip()}
        )
        if normalized_values:
            conditions.append(
                {"dimension": str(dimension).strip(), "values": normalized_values}
            )
    conditions.sort(key=lambda item: str(item["dimension"]))
    payload: dict[str, Any] = {
        "schema_version": 1,
        "dataset_scope": {
            "dataset_id": int(dataset_id) if dataset_id is not None else None,
            "dataset_ids": sorted(
                {
                    int(item)
                    for item in (dataset_ids or [])
                    if int(item) > 0
                }
            ),
            "dataset_version_id": (
                int(dataset_version_id) if dataset_version_id is not None else None
            ),
        },
        "mode": str(mode or "").strip() or None,
        "task_ids": sorted({int(item) for item in (task_ids or [])}),
        "selected_label_ids": sorted({int(item) for item in (selected_label_ids or [])}),
        "global_conditions": conditions,
        "annotation_contract": dict(annotation_contract or {}),
        "recipe": dict(recipe or {}),
    }
    if extra:
        payload.update(dict(extra))
    return canonical_selection_contract(payload)

