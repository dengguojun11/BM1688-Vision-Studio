"""Model-series and branch lineage resolution.

The training APIs accept a small set of optional lineage fields for backwards
compatibility.  This module is the single server-side authority that turns
those fields plus the immutable selection contract into a validated series /
variant pair.  It deliberately does not commit; callers own the transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_model_version,
    crud_task,
    crud_yolo_train_job,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser, user_has_task_access
from labelu.internal.common.training_contract import (
    branch_identity_sha256,
    canonical_selection_contract,
    selection_contract_json,
    selection_contract_sha256,
)
from labelu.internal.domain.models.model_series import ModelSeries, ModelVariant
from labelu.internal.domain.models.user import User

_ACTIVE = "ACTIVE"
_INHERITANCE_MODES = {
    "from_scratch",
    "fine_tune",
    "warm_start",
    "parent_model",
    "same_series",
    "new_branch",
}


def _error(
    message: str, http_status: int = status.HTTP_400_BAD_REQUEST
) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=http_status,
        message=message,
    )


def _clean(value: Any, *, limit: int = 128) -> str:
    return str(value or "").strip()[:limit]


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _series_visible(row: ModelSeries, user: User) -> bool:
    return bool(
        row
        and getattr(row, "deleted_at", None) is None
        and str(getattr(row, "status", _ACTIVE) or _ACTIVE).upper() == _ACTIVE
        and (
            is_task_superuser(user)
            or int(getattr(row, "owner_id", 0) or 0) == int(user.id)
        )
    )


def _parent_job_visible(db: Session, user: User, row: Any) -> bool:
    if row is None:
        return False
    if is_task_superuser(user):
        return True
    task_id = getattr(row, "task_id", None)
    task = crud_task.get(db=db, task_id=int(task_id)) if task_id else None
    return user_has_task_access(user, task)


def _parent_model_visible(db: Session, user: User, row: Any) -> bool:
    if row is None:
        return False
    if is_task_superuser(user) or int(getattr(row, "created_by", 0) or 0) == int(
        user.id
    ):
        return True
    training_job_id = getattr(row, "training_job_id", None)
    if training_job_id:
        return _parent_job_visible(
            db,
            user,
            crud_yolo_train_job.get(db, int(training_job_id)),
        )
    return False


def _contract_from_variant(row: ModelVariant) -> dict[str, Any]:
    return _json_object(getattr(row, "selection_contract_json", None))


def _variant_display_name(contract: Mapping[str, Any], digest: str) -> str:
    """Produce a stable, useful default branch label for old clients."""

    # New contracts call this field ``global_conditions``.  Keep the legacy
    # spelling as a fallback so old rows still receive a useful label.
    conditions = contract.get("global_conditions") or contract.get("sample_conditions")
    labels: list[str] = []
    if isinstance(conditions, list):
        for item in conditions:
            if isinstance(item, dict):
                dimension = _clean(item.get("dimension"), limit=32)
                values = item.get("values")
                if isinstance(values, list):
                    values_text = "/".join(
                        _clean(v, limit=24) for v in values if _clean(v, limit=24)
                    )
                else:
                    values_text = _clean(values, limit=48)
                if dimension and values_text:
                    labels.append(f"{dimension}:{values_text}")
    annotation = contract.get("annotation_contract")
    if isinstance(annotation, dict):
        class_order = annotation.get("class_order")
        if isinstance(class_order, list) and class_order:
            labels.append(
                "标签:" + "/".join(_clean(v, limit=20) for v in class_order[:4])
            )
    if labels:
        text = " + ".join(labels)
        return text[:128]
    return f"分支-{digest[:12]}"


@dataclass(frozen=True)
class ResolvedModelLineage:
    series: ModelSeries
    variant: ModelVariant
    branch_key: str
    selection_contract: dict[str, Any]
    selection_contract_json: str
    selection_contract_sha256: str
    parent_model_version_id: int | None
    parent_training_job_id: int | None
    inheritance_mode: str

    @property
    def series_id(self) -> int:
        return int(self.series.id)

    @property
    def variant_id(self) -> int:
        return int(self.variant.id)


def normalize_inheritance_mode(value: str | None, *, has_parent: bool) -> str:
    normalized = _clean(value, limit=32).lower().replace("-", "_")
    if not normalized:
        return "parent_model" if has_parent else "from_scratch"
    if normalized not in _INHERITANCE_MODES:
        raise _error(
            "inheritance_mode 无效，可选值为 from_scratch、fine_tune、warm_start、parent_model、same_series、new_branch"
        )
    if normalized == "from_scratch" and has_parent:
        raise _error("已选择父模型时 inheritance_mode 不能为 from_scratch")
    return normalized


def resolve_model_lineage(
    db: Session,
    *,
    current_user: User,
    selection_contract: Mapping[str, Any] | None,
    branch_key: str | None = None,
    series_id: int | None = None,
    variant_id: int | None = None,
    parent_model_version_id: int | None = None,
    parent_training_job_id: int | None = None,
    inheritance_mode: str | None = None,
    series_name: str | None = None,
    variant_name: str | None = None,
    model_family: str | None = None,
    create_if_missing: bool = True,
    allow_legacy_missing_ids: bool = False,
) -> ResolvedModelLineage:
    """Validate or create the exact series/variant represented by a contract.

    ``branch_key`` is intentionally derived from the canonical contract.  A
    client-supplied value is accepted only as an assertion, preventing a stale
    preview from silently attaching a job to another branch.
    """

    explicit_series_id = series_id is not None
    explicit_variant_id = variant_id is not None
    explicit_parent_training_job_id = parent_training_job_id is not None
    contract = canonical_selection_contract(selection_contract)
    serialized = selection_contract_json(contract)
    selection_digest = selection_contract_sha256(contract)
    branch_digest = branch_identity_sha256(contract)
    supplied_key = _clean(branch_key, limit=64)
    if (
        supplied_key
        and supplied_key not in {branch_digest, selection_digest}
    ):
        raise _error(
            "branch_key 与当前训练选择合同不一致，请刷新预览后重试",
            status.HTTP_409_CONFLICT,
        )

    parent_model = None
    if parent_model_version_id is not None:
        parent_model = crud_model_version.get(db, int(parent_model_version_id))
        if parent_model is None or not _parent_model_visible(
            db, current_user, parent_model
        ):
            raise _error("父模型不存在或当前用户无权使用", status.HTTP_403_FORBIDDEN)
        parent_model_version_id = int(parent_model.id)
        inferred_job_id = getattr(parent_model, "training_job_id", None)
        if parent_training_job_id is None and inferred_job_id:
            parent_training_job_id = int(inferred_job_id)

    parent_job = None
    if parent_training_job_id is not None:
        parent_job = crud_yolo_train_job.get(db, int(parent_training_job_id))
        if parent_job is None or not _parent_job_visible(db, current_user, parent_job):
            raise _error(
                "父训练作业不存在或当前用户无权使用", status.HTTP_403_FORBIDDEN
            )
        parent_status = getattr(parent_job, "status", "")
        parent_status = getattr(parent_status, "value", parent_status)
        if str(parent_status or "").upper() != "COMPLETED":
            raise _error(
                "父训练作业尚未完成，不能作为迭代基线", status.HTTP_409_CONFLICT
            )
        parent_training_job_id = int(parent_job.id)
        if parent_model_version_id is None:
            model = crud_model_version.get_by_training_job(db, int(parent_job.id))
            if model is not None:
                parent_model = model
                parent_model_version_id = int(model.id)
        elif explicit_parent_training_job_id:
            # When both identifiers are supplied they must describe the same
            # completed run. Otherwise a caller could display one parent
            # model while inheriting provenance from a different job.
            model_job_id = getattr(parent_model, "training_job_id", None)
            if model_job_id is None or int(model_job_id) != int(parent_job.id):
                raise _error(
                    "父模型与父训练作业不一致",
                    status.HTTP_409_CONFLICT,
                )

    # A parent carries the strongest lineage hint.  Explicit series IDs must
    # agree with it; otherwise an iteration can accidentally cross families.
    parent_series_id = (
        getattr(parent_model, "series_id", None) if parent_model else None
    )
    if parent_series_id is None and parent_job is not None:
        parent_series_id = getattr(parent_job, "series_id", None)
    # A variant id is itself a stable series reference.  Resolve it before
    # auto-creating a family so callers may submit only variant_id.
    referenced_variant = None
    if variant_id is not None and series_id is None:
        referenced_variant = (
            db.query(ModelVariant)
            .filter(
                ModelVariant.id == int(variant_id), ModelVariant.deleted_at.is_(None)
            )
            .one_or_none()
        )
        if referenced_variant is None:
            if (
                allow_legacy_missing_ids
                and not parent_model_version_id
                and not parent_training_job_id
            ):
                variant_id = None
            else:
                raise _error("模型分支不存在", status.HTTP_404_NOT_FOUND)
        if referenced_variant is not None:
            series_id = int(referenced_variant.series_id)
    if series_id is None and parent_series_id is not None:
        series_id = int(parent_series_id)
    if (
        series_id is not None
        and parent_series_id is not None
        and int(series_id) != int(parent_series_id)
    ):
        raise _error("父模型与目标模型系列不一致", status.HTTP_409_CONFLICT)

    series = None
    if series_id is not None:
        series = (
            db.query(ModelSeries).filter(ModelSeries.id == int(series_id)).one_or_none()
        )
        if series is None:
            # Legacy jobs may contain a numeric lineage id that was written
            # before the catalog tables existed.  If the caller did not
            # explicitly select that id, recover from the current contract;
            # explicit API selections remain strict.
            recoverable_legacy_id = (
                allow_legacy_missing_ids or not explicit_series_id
            ) and not (
                parent_model is not None and getattr(parent_model, "series_id", None)
            )
            if recoverable_legacy_id:
                # Historical training-task rows could briefly persist a
                # numeric id before the catalog transaction was committed.
                # Iterating such a row should recover into the canonical
                # family instead of making the whole legacy task unusable.
                series_id = None
            else:
                raise _error("模型系列不存在", status.HTTP_404_NOT_FOUND)
        if series is not None:
            series_active = (
                getattr(series, "deleted_at", None) is None
                and str(getattr(series, "status", _ACTIVE) or _ACTIVE).upper()
                == _ACTIVE
            )
            # A collaborator may iterate a model that they can access through
            # its training task even when the catalog family is owned by the
            # task owner.  Keep the exception bound to the exact series
            # carried by the already-authorized parent; it must not become a
            # general cross-owner series lookup.
            parent_series_access = bool(
                series_active
                and parent_series_id is not None
                and int(series.id) == int(parent_series_id)
                and (parent_model is not None or parent_job is not None)
            )
            if not series_active or (
                not _series_visible(series, current_user) and not parent_series_access
            ):
                raise _error("无权使用该模型系列", status.HTTP_403_FORBIDDEN)
    if series_id is not None and series is None:
        # Continue through the automatic family resolver after discarding a
        # stale legacy id (the normal explicit API path already raised above).
        pass
    elif not create_if_missing:
        raise _error("未指定模型系列", status.HTTP_400_BAD_REQUEST)

    if series is None:
        explicit_series_name = _clean(series_name, limit=128)
        recipe = contract.get("recipe")
        contract_family = (
            _clean(recipe.get("model_family"), limit=128)
            if isinstance(recipe, dict)
            else ""
        )
        family = _clean(model_family, limit=128) or contract_family
        name = explicit_series_name or family or "模型系列"
        # A missing series id means "use this owner's logical family".  This
        # makes repeated creates converge on one family instead of producing
        # a new series for every branch. Operators can still force a distinct
        # family by supplying an explicit series_name.
        candidate = name[:128]
        existing_series = None
        # The current wizard does not ask for a display name. In that path,
        # the recipe/model family is the stable logical identity, so a prior
        # series with a custom display name must still be reused.
        if not explicit_series_name and family:
            existing_series = (
                db.query(ModelSeries)
                .filter(
                    ModelSeries.owner_id == int(current_user.id),
                    ModelSeries.model_family == family,
                    ModelSeries.deleted_at.is_(None),
                    ModelSeries.status == _ACTIVE,
                )
                .order_by(ModelSeries.updated_at.desc(), ModelSeries.id.asc())
                .first()
            )
        if existing_series is None:
            existing_series = (
                db.query(ModelSeries)
                .filter(
                    ModelSeries.owner_id == int(current_user.id),
                    ModelSeries.name == candidate,
                    ModelSeries.deleted_at.is_(None),
                )
                .one_or_none()
            )
        if existing_series is not None:
            series = existing_series
        else:
            series = None
            series = ModelSeries(
                owner_id=int(current_user.id),
                name=candidate,
                model_family=family or None,
                status=_ACTIVE,
            )
            db.add(series)
            try:
                # Isolate the uniqueness race from the caller's transaction.
                with db.begin_nested():
                    db.flush()
            except IntegrityError:
                series = (
                    db.query(ModelSeries)
                    .filter(
                        ModelSeries.owner_id == int(current_user.id),
                        ModelSeries.name == candidate,
                        ModelSeries.deleted_at.is_(None),
                    )
                    .one_or_none()
                )
                if series is None:
                    raise

    if variant_id is not None:
        variant = (
            db.query(ModelVariant)
            .filter(ModelVariant.id == int(variant_id))
            .one_or_none()
        )
        if variant is None or getattr(variant, "deleted_at", None) is not None:
            if (
                allow_legacy_missing_ids or not explicit_variant_id
            ) and not parent_model_version_id:
                variant_id = None
                variant = None
            else:
                raise _error("模型分支不存在", status.HTTP_404_NOT_FOUND)
        if variant is None:
            # Fall through to branch lookup/creation using the canonical
            # contract on the recovered series.
            pass
        elif int(variant.series_id) != int(series.id):
            raise _error("模型分支不属于所选模型系列", status.HTTP_409_CONFLICT)
        if variant is not None:
            stored_digest = _clean(
                getattr(variant, "selection_contract_sha256", None), limit=64
            )
            stored_contract = _contract_from_variant(variant)
            stored_branch_digest = (
                branch_identity_sha256(stored_contract) if stored_contract else ""
            )
            stored_branch_key = _clean(getattr(variant, "branch_key", None), limit=64)
            if stored_contract and stored_branch_digest not in {
                branch_digest,
                stored_branch_key,
            }:
                raise _error(
                    "模型分支与当前训练选择合同的稳定身份不一致",
                    status.HTTP_409_CONFLICT,
                )
            if str(getattr(variant, "status", _ACTIVE) or _ACTIVE).upper() != _ACTIVE:
                raise _error("模型分支已停用", status.HTTP_409_CONFLICT)
    if variant_id is None:
        variant = (
            db.query(ModelVariant)
            .filter(
                ModelVariant.series_id == int(series.id),
                ModelVariant.branch_key == branch_digest,
                ModelVariant.deleted_at.is_(None),
            )
            .one_or_none()
        )
        # Rows written before the identity/evidence split used the complete
        # selection hash as ``branch_key``.  Reuse such a row when its stored
        # contract proves the same stable branch, avoiding duplicate branches
        # during rollout.
        if variant is None and branch_digest != selection_digest:
            legacy_variant = (
                db.query(ModelVariant)
                .filter(
                    ModelVariant.series_id == int(series.id),
                    ModelVariant.branch_key == selection_digest,
                    ModelVariant.deleted_at.is_(None),
                )
                .one_or_none()
            )
            if legacy_variant is not None:
                stored_contract = _contract_from_variant(legacy_variant)
                if stored_contract and branch_identity_sha256(stored_contract) == branch_digest:
                    variant = legacy_variant
        if variant is None:
            if not create_if_missing:
                raise _error("模型分支不存在", status.HTTP_404_NOT_FOUND)
            variant = ModelVariant(
                series_id=int(series.id),
                name=_clean(variant_name, limit=128)
                or _variant_display_name(contract, branch_digest),
                branch_key=branch_digest,
                selection_contract_json=serialized,
                selection_contract_sha256=selection_digest,
                class_contract_json=json.dumps(
                    contract.get("annotation_contract") or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                recipe_json=json.dumps(
                    contract.get("recipe") or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                parent_model_version_id=parent_model_version_id,
                created_by=int(current_user.id),
                status=_ACTIVE,
            )
            db.add(variant)
            try:
                with db.begin_nested():
                    db.flush()
            except IntegrityError:
                variant = (
                    db.query(ModelVariant)
                    .filter(
                        ModelVariant.series_id == int(series.id),
                        ModelVariant.branch_key == branch_digest,
                        ModelVariant.deleted_at.is_(None),
                    )
                    .one_or_none()
                )
                if variant is None:
                    raise
        else:
            stored_contract = _contract_from_variant(variant)
            if stored_contract and branch_identity_sha256(stored_contract) != branch_digest:
                raise _error(
                    "已存在的模型分支稳定身份与当前请求不一致", status.HTTP_409_CONFLICT
                )

    has_parent = (
        parent_model_version_id is not None or parent_training_job_id is not None
    )
    mode = normalize_inheritance_mode(inheritance_mode, has_parent=has_parent)
    return ResolvedModelLineage(
        series=series,
        variant=variant,
        branch_key=branch_digest,
        selection_contract=contract,
        selection_contract_json=serialized,
        selection_contract_sha256=selection_digest,
        parent_model_version_id=(
            int(parent_model_version_id) if parent_model_version_id else None
        ),
        parent_training_job_id=(
            int(parent_training_job_id) if parent_training_job_id else None
        ),
        inheritance_mode=mode,
    )


def serialize_lineage(lineage: ResolvedModelLineage) -> dict[str, Any]:
    """Return a JSON-safe projection shared by route responses and workers."""

    return {
        "series_id": lineage.series_id,
        "series_name": lineage.series.name,
        "model_family": lineage.series.model_family,
        "variant_id": lineage.variant_id,
        "variant_name": lineage.variant.name,
        "branch_key": lineage.branch_key,
        "selection_contract_sha256": lineage.selection_contract_sha256,
        "selection_contract": lineage.selection_contract,
        "parent_model_version_id": lineage.parent_model_version_id,
        "parent_training_job_id": lineage.parent_training_job_id,
        "inheritance_mode": lineage.inheritance_mode,
    }


def series_variant_names(
    db: Session, series_id: int | None, variant_id: int | None
) -> tuple[str | None, str | None]:
    """Resolve display names without requiring ORM relationships to be loaded."""

    series_name = variant_name = None
    if series_id:
        row = (
            db.query(ModelSeries).filter(ModelSeries.id == int(series_id)).one_or_none()
        )
        if row is not None:
            series_name = row.name
    if variant_id:
        row = (
            db.query(ModelVariant)
            .filter(ModelVariant.id == int(variant_id))
            .one_or_none()
        )
        if row is not None:
            variant_name = row.name
            if series_name is None:
                series = (
                    db.query(ModelSeries)
                    .filter(ModelSeries.id == int(row.series_id))
                    .one_or_none()
                )
                series_name = series.name if series is not None else None
    return series_name, variant_name
