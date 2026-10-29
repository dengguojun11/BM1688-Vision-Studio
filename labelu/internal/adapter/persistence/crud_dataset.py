from __future__ import annotations

import json
from typing import Tuple

from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator


_USABLE_CONTRACT_STATUSES = ("DRAFT", "ACTIVE", "FROZEN")


def parse_label_snapshot(value) -> list[dict] | None:
    """Parse a contract snapshot and reject empty or malformed history.

    Snapshots are stored as JSON text.  ``json.loads`` also transparently
    decodes legacy ``\\uXXXX`` Chinese strings, so callers can search the
    actual label fields without matching JSON keys or other metadata.
    """

    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, dict) for item in value):
        return None
    # A snapshot that only preserves IDs or annotation metadata cannot drive
    # the governance search (and is not enough to render a label contract).
    # Treat it as unusable so history resolution can fall back to a complete
    # revision instead of silently returning an empty/partial contract.
    if not all(
        any(
            str(item.get(field) or "").strip()
            for field in ("label_key", "label_value")
        )
        for item in value
    ):
        return None
    return value


def _contract_snapshot_is_usable(
    contract: DatasetLabelContract, dataset_id: int
) -> bool:
    return (
        int(getattr(contract, "dataset_id", 0) or 0) == int(dataset_id)
        and str(getattr(contract, "status", "") or "").upper()
        in _USABLE_CONTRACT_STATUSES
        and parse_label_snapshot(getattr(contract, "labels_snapshot_json", None))
        is not None
    )


def effective_dataset_contract(
    db: Session, dataset: Dataset
) -> DatasetLabelContract | None:
    """Return the current usable contract, falling back through bad history."""

    dataset_id = int(dataset.id)
    pointer_id = getattr(dataset, "dataset_label_contract_id", None)
    if pointer_id:
        pointer = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == int(pointer_id))
            .one_or_none()
        )
        if pointer is not None and _contract_snapshot_is_usable(pointer, dataset_id):
            return pointer

    candidates = (
        db.query(DatasetLabelContract)
        .filter(
            DatasetLabelContract.dataset_id == dataset_id,
            DatasetLabelContract.status.in_(_USABLE_CONTRACT_STATUSES),
        )
        .order_by(DatasetLabelContract.revision.desc(), DatasetLabelContract.id.desc())
        .all()
    )
    return next(
        (row for row in candidates if _contract_snapshot_is_usable(row, dataset_id)),
        None,
    )


def _effective_contracts_for_datasets(
    db: Session, dataset_rows: list[tuple[int, int | None]]
) -> dict[int, DatasetLabelContract]:
    """Resolve effective contracts in one batch for a dataset search."""

    dataset_ids = [int(dataset_id) for dataset_id, _ in dataset_rows]
    if not dataset_ids:
        return {}
    pointer_ids = {
        int(dataset_id): int(pointer_id)
        for dataset_id, pointer_id in dataset_rows
        if pointer_id is not None
    }
    contracts = (
        db.query(DatasetLabelContract)
        .filter(
            DatasetLabelContract.dataset_id.in_(dataset_ids),
            DatasetLabelContract.status.in_(_USABLE_CONTRACT_STATUSES),
        )
        .order_by(
            DatasetLabelContract.dataset_id,
            DatasetLabelContract.revision.desc(),
            DatasetLabelContract.id.desc(),
        )
        .all()
    )
    by_dataset: dict[int, list[DatasetLabelContract]] = {}
    by_id: dict[int, DatasetLabelContract] = {}
    for contract in contracts:
        dataset_id = int(contract.dataset_id)
        by_dataset.setdefault(dataset_id, []).append(contract)
        by_id[int(contract.id)] = contract

    resolved: dict[int, DatasetLabelContract] = {}
    for dataset_id in dataset_ids:
        pointer = by_id.get(pointer_ids.get(dataset_id))
        if pointer is not None and _contract_snapshot_is_usable(pointer, dataset_id):
            resolved[dataset_id] = pointer
            continue
        for candidate in by_dataset.get(dataset_id, []):
            if _contract_snapshot_is_usable(candidate, dataset_id):
                resolved[dataset_id] = candidate
                break
    return resolved


def _dataset_label_match_ids(
    db: Session, dataset_rows: list[tuple[int, int | None]], keyword: str
) -> set[int]:
    """Find datasets whose effective snapshot label key/value contains keyword."""

    needle = keyword.casefold()
    matches: set[int] = set()
    for dataset_id, contract in _effective_contracts_for_datasets(
        db, dataset_rows
    ).items():
        snapshot = parse_label_snapshot(contract.labels_snapshot_json) or []
        for label in snapshot:
            values = (label.get("label_key"), label.get("label_value"))
            if any(
                needle in str(value).casefold()
                for value in values
                if value is not None
            ):
                matches.add(dataset_id)
                break
    return matches


def create_dataset(db: Session, dataset: Dataset) -> Dataset:
    db.add(dataset)
    db.flush()
    db.refresh(dataset)
    return dataset


def get_dataset(db: Session, dataset_id: int) -> Dataset | None:
    return (
        db.query(Dataset)
        .filter(Dataset.id == int(dataset_id), Dataset.deleted_at.is_(None))
        .first()
    )


def list_datasets(
    db: Session,
    page: int,
    size: int,
    *,
    user_id: int | None,
    search: str | None = None,
) -> Tuple[list[Dataset], int]:
    query = db.query(Dataset).filter(Dataset.deleted_at.is_(None))
    if user_id is not None:
        linked_task = (
            db.query(Task.id)
            .outerjoin(
                TaskCollaborator,
                TaskCollaborator.task_id == Task.id,
            )
            .filter(
                Task.dataset_id == Dataset.id,
                Task.deleted_at.is_(None),
                or_(
                    Task.created_by == int(user_id),
                    TaskCollaborator.user_id == int(user_id),
                    Task.source_type == "third_party",
                ),
            )
            .exists()
        )
        query = query.filter(or_(Dataset.owner_id == int(user_id), linked_task))
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        # Resolve contracts from the same visible dataset set before applying
        # pagination.  Parsing the JSON snapshot keeps the search restricted to
        # ``label_key``/``label_value`` and handles legacy ``\\uXXXX`` text
        # without treating JSON keys, colors, or annotation metadata as labels.
        visible_dataset_rows = [
            (int(dataset_id), pointer_id)
            for dataset_id, pointer_id in query.with_entities(
                Dataset.id, Dataset.dataset_label_contract_id
            ).all()
        ]
        label_match_ids = _dataset_label_match_ids(db, visible_dataset_rows, keyword)
        contract_matches = Dataset.id.in_(label_match_ids)
        query = (
            query.outerjoin(
                DatasetCategory,
                DatasetCategory.id == Dataset.dataset_category_id,
            )
            .filter(
                or_(
                    Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                    Dataset.description.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.category_key.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.name.ilike(pattern, escape=LIKE_ESCAPE),
                    DatasetCategory.description.ilike(pattern, escape=LIKE_ESCAPE),
                    contract_matches,
                )
            )
        )
    total = query.count()
    rows = (
        query.order_by(Dataset.updated_at.desc(), Dataset.id.desc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return rows, total


def create_version(db: Session, version: DatasetVersion) -> DatasetVersion:
    db.add(version)
    db.flush()
    db.refresh(version)
    return version


def get_version(db: Session, version_id: int) -> DatasetVersion | None:
    return db.query(DatasetVersion).filter(DatasetVersion.id == int(version_id)).first()


def get_version_by_manifest_sha(
    db: Session, manifest_sha256: str
) -> DatasetVersion | None:
    return (
        db.query(DatasetVersion)
        .filter(DatasetVersion.manifest_sha256 == manifest_sha256)
        .first()
    )


def list_versions(db: Session, dataset_id: int) -> list[DatasetVersion]:
    return (
        db.query(DatasetVersion)
        .filter(DatasetVersion.dataset_id == int(dataset_id))
        .order_by(DatasetVersion.version_no.desc())
        .all()
    )


def list_dataset_versions(
    db: Session,
    page: int,
    size: int,
    *,
    user_id: int | None,
    dataset_id: int | None = None,
    search: str | None = None,
    active_learning_only: bool = False,
) -> Tuple[list[tuple[DatasetVersion, str]], int]:
    """List visible dataset versions with server-side search and pagination.

    The per-dataset ``/datasets/{id}/versions`` endpoint is intentionally kept
    unchanged for detail pages.  This query powers cross-dataset tables such
    as the evolution pipeline and avoids the N+1 request pattern there.
    """

    query = (
        db.query(DatasetVersion, Dataset.name)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .filter(Dataset.deleted_at.is_(None))
    )
    if dataset_id is not None:
        query = query.filter(DatasetVersion.dataset_id == int(dataset_id))
    if user_id is not None:
        linked_task = (
            db.query(Task.id)
            .outerjoin(TaskCollaborator, TaskCollaborator.task_id == Task.id)
            .filter(
                Task.dataset_id == Dataset.id,
                Task.deleted_at.is_(None),
                or_(
                    Task.created_by == int(user_id),
                    TaskCollaborator.user_id == int(user_id),
                    Task.source_type == "third_party",
                ),
            )
            .exists()
        )
        query = query.filter(
            or_(Dataset.owner_id == int(user_id), linked_task)
        )

    if active_learning_only:
        # Active-learning versions are marked by the stable metadata source
        # written by the batch acceptance service.  Keep this predicate broad
        # enough to read legacy JSON formatting while remaining index-friendly
        # for the common no-filter path.
        query = query.filter(
            DatasetVersion.metadata_json.ilike(
                literal_like_pattern("active_learning_batch"),
                escape=LIKE_ESCAPE,
            )
        )

    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetVersion.status.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetVersion.quality_status.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetVersion.review_status.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetVersion.manifest_rel_path.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetVersion.manifest_sha256.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetVersion.metadata_json.ilike(pattern, escape=LIKE_ESCAPE),
                cast(DatasetVersion.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(DatasetVersion.version_no, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )

    total = query.count()
    rows = (
        query.order_by(DatasetVersion.updated_at.desc(), DatasetVersion.id.desc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return [(version, str(dataset_name)) for version, dataset_name in rows], total


def next_version_no(db: Session, dataset_id: int) -> int:
    current = (
        db.query(func.max(DatasetVersion.version_no))
        .filter(DatasetVersion.dataset_id == int(dataset_id))
        .scalar()
    )
    return int(current or 0) + 1
