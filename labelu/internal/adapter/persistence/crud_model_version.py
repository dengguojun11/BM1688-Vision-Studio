import json
import re
from typing import Any

from sqlalchemy import String, and_, cast, exists, func, or_
from sqlalchemy.orm import Session, joinedload

from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.dataset import DatasetVersion
from labelu.internal.domain.models.model_version import (
    ModelArtifact,
    ModelQualityEvidence,
    ModelVersion,
)
from labelu.internal.domain.models.model_series import ModelSeries
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.common.task_access import task_visibility_clause
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.training_contract import (
    TRAINING_RECIPE_ALIASES,
    canonical_training_recipe_key,
)


def _json_object(raw: Any) -> dict[str, Any]:
    """Decode one legacy snapshot field without allowing malformed JSON out."""

    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _non_empty_signature(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _job_contract_signatures(
    db: Session,
    job: YoloTrainJob,
    *,
    dataset_version_id: Any | None = None,
) -> tuple[str | None, str | None]:
    """Resolve label signatures from current columns and legacy snapshots."""

    label_signature = _non_empty_signature(
        getattr(job, "label_set_signature", None)
    )
    order_signature = _non_empty_signature(
        getattr(job, "class_order_signature", None)
    )
    params = _json_object(getattr(job, "hyperparams_json", None))
    manifests: list[dict[str, Any]] = []
    for raw in (
        getattr(job, "dataset_manifest_json", None),
        params.get("dataset_manifest"),
    ):
        manifest = _json_object(raw)
        if manifest and manifest not in manifests:
            manifests.append(manifest)
    for manifest in manifests:
        label_signature = label_signature or _non_empty_signature(
            manifest.get("label_set_signature")
        )
        order_signature = order_signature or _non_empty_signature(
            manifest.get("class_order_signature")
        )
        if label_signature and order_signature:
            break

    if label_signature and order_signature:
        return label_signature, order_signature

    resolved_dataset_version_id = _optional_int(dataset_version_id)
    if resolved_dataset_version_id is None:
        resolved_dataset_version_id = next(
            (
                _optional_int(manifest.get("dataset_version_id"))
                for manifest in manifests
                if _optional_int(manifest.get("dataset_version_id"))
            ),
            None,
        )
    if resolved_dataset_version_id is not None:
        version = (
            db.query(DatasetVersion)
            .filter(DatasetVersion.id == int(resolved_dataset_version_id))
            .one_or_none()
        )
        if version is not None:
            label_signature = label_signature or _non_empty_signature(
                getattr(version, "label_set_signature", None)
            )
            order_signature = order_signature or _non_empty_signature(
                getattr(version, "class_order_signature", None)
            )
    return label_signature, order_signature


def _recipe_fields_from_object(raw: Any) -> tuple[str | None, int | None]:
    """Read recipe lineage from current and historical nested JSON shapes."""

    if not isinstance(raw, dict):
        return None, None

    # The first few shapes are the ones emitted by the training APIs.  A small
    # recursive fallback keeps old snapshots readable when a manifest was
    # wrapped in an additional ``dataset_manifest``/``training_recipe`` object.
    pending: list[tuple[dict[str, Any], bool]] = [(raw, False)]
    seen: set[int] = set()
    key: str | None = None
    version: int | None = None
    while pending and (key is None or version is None):
        current, is_recipe_object = pending.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        if key is None:
            key_fields = ("training_recipe_key", "recipe_key")
            if is_recipe_object:
                key_fields += ("key",)
            for field in key_fields:
                value = str(current.get(field) or "").strip()
                if value:
                    key = value
                    break
            if key is None:
                recipe = current.get("training_recipe")
                if isinstance(recipe, str):
                    key = recipe.strip() or None
                elif isinstance(recipe, dict):
                    pending.insert(0, (recipe, True))

        if version is None:
            version_fields = ("training_recipe_version", "recipe_version")
            if is_recipe_object:
                version_fields += ("version",)
            for field in version_fields:
                parsed = _optional_int(current.get(field))
                if parsed is not None:
                    version = parsed
                    break
            if version is None:
                recipe = current.get("training_recipe")
                if isinstance(recipe, dict):
                    pending.insert(0, (recipe, True))

        for field in ("dataset_manifest", "manifest", "recipe"):
            nested = current.get(field)
            if isinstance(nested, dict):
                pending.append((nested, field == "recipe"))

    return key, version


def _job_recipe_contract(job: YoloTrainJob) -> tuple[str | None, int | None]:
    """Resolve recipe lineage from columns and immutable legacy snapshots."""

    key = str(getattr(job, "training_recipe_key", None) or "").strip() or None
    version = _optional_int(getattr(job, "training_recipe_version", None))
    params = _json_object(getattr(job, "hyperparams_json", None))
    snapshots: list[dict[str, Any]] = []
    for raw in (
        getattr(job, "dataset_manifest_json", None),
        params.get("dataset_manifest"),
    ):
        snapshot = _json_object(raw)
        if snapshot and snapshot not in snapshots:
            snapshots.append(snapshot)

    for source in (params, *snapshots):
        nested_key, nested_version = _recipe_fields_from_object(source)
        key = key or nested_key
        version = version or nested_version
        if key and version:
            break
    return canonical_training_recipe_key(key) or None, version


def _recipe_lookup_keys(value: str | None) -> tuple[str, ...]:
    normalized = str(value or "").strip()
    if not normalized:
        return ()
    canonical = canonical_training_recipe_key(normalized)
    keys = {normalized, canonical}
    keys.update(
        alias
        for alias, target in TRAINING_RECIPE_ALIASES.items()
        if target == canonical
    )
    return tuple(sorted(key for key in keys if key))


def _recipe_matches(
    job: YoloTrainJob | None,
    *,
    recipe_key: str | None,
    recipe_version: int | None,
) -> bool:
    if job is None:
        return False
    actual_key, actual_version = _job_recipe_contract(job)
    if recipe_key and actual_key != canonical_training_recipe_key(recipe_key):
        return False
    if recipe_version is not None and actual_version != int(recipe_version):
        return False
    return True


def _families_from_rows(
    rows: list[ModelVersion],
    jobs_by_id: dict[int, YoloTrainJob],
    *,
    page: int,
    size: int,
) -> tuple[list[tuple[int, int, list[ModelVersion]]], int]:
    """Group already-filtered versions and page families in stable order."""

    # Keep the governed model series as the family boundary.  The task id is
    # retained only as the representative task returned by the legacy family
    # response contract; iterations from different tasks in one series must
    # still appear in one family.
    versions_by_family: dict[tuple[int, int], list[ModelVersion]] = {}
    representative_task: dict[tuple[int, int], int] = {}
    family_order: list[tuple[int, int]] = []
    for row in rows:
        if row.training_job_id is None:
            continue
        job = jobs_by_id.get(int(row.training_job_id))
        if job is None or job.task_id is None:
            continue
        family_id = int(getattr(job, "series_id", None) or job.task_id)
        pair = (family_id, int(row.algorithm_id))
        if pair not in versions_by_family:
            versions_by_family[pair] = []
            representative_task[pair] = int(job.task_id)
            family_order.append(pair)
        versions_by_family[pair].append(row)

    total = len(family_order)
    start = max(0, int(page)) * max(1, int(size))
    selected = family_order[start : start + max(1, int(size))]
    return [
        (
            representative_task[(family_id, algorithm_id)],
            algorithm_id,
            versions_by_family[(family_id, algorithm_id)],
        )
        for family_id, algorithm_id in selected
    ], total


def get(db: Session, model_version_id: int) -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .options(
            joinedload(ModelVersion.artifacts),
            joinedload(ModelVersion.quality_evidence),
        )
        .filter(ModelVersion.id == int(model_version_id))
        .first()
    )


def get_by_training_job(db: Session, training_job_id: int) -> ModelVersion | None:
    return (
        db.query(ModelVersion)
        .options(
            joinedload(ModelVersion.artifacts),
            joinedload(ModelVersion.quality_evidence),
        )
        .filter(ModelVersion.training_job_id == int(training_job_id))
        .order_by(ModelVersion.id.desc())
        .first()
    )


def _accessible_query(
    db: Session,
    *,
    user_id: int | None,
    unrestricted: bool,
    status: str | None = None,
    source: str | None = None,
    training_job_id: int | None = None,
    search: str | None = None,
    artifact_type: str | None = None,
    task_access_builtin_admin: bool = False,
):
    query = db.query(ModelVersion)
    source_key = str(source or "").strip().lower()
    if source_key == "training":
        # The PT tab represents weights produced by the platform training
        # center. External registrations are a separate import resource.
        query = query.filter(ModelVersion.training_job_id.isnot(None))
    elif source_key == "external":
        query = query.filter(ModelVersion.training_job_id.is_(None))
    if training_job_id is not None:
        query = query.filter(ModelVersion.training_job_id == int(training_job_id))
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        task_match = (
            db.query(Task.id)
            .join(YoloTrainJob, YoloTrainJob.task_id == Task.id)
            .filter(
                YoloTrainJob.id == ModelVersion.training_job_id,
                or_(
                    Task.name.ilike(pattern, escape=LIKE_ESCAPE),
                    Task.description.ilike(pattern, escape=LIKE_ESCAPE),
                ),
            )
            .exists()
        )
        query = query.filter(
            or_(
                cast(ModelVersion.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                ModelVersion.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                ModelVersion.model_key.ilike(pattern, escape=LIKE_ESCAPE),
                cast(ModelVersion.training_job_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                task_match,
            )
        )
    if artifact_type:
        artifact = (
            db.query(ModelArtifact.id)
            .filter(
                ModelArtifact.model_version_id == ModelVersion.id,
                ModelArtifact.artifact_type == str(artifact_type).lower(),
                # A catalog filter is also used by the training wizard.  A
                # failed/retired artifact must not make its model version look
                # selectable merely because a row of that type exists.
                func.upper(func.coalesce(ModelArtifact.status, "")) == "READY",
            )
            .exists()
        )
        query = query.filter(artifact)
    task_access = task_visibility_clause(
        user_id=int(user_id or 0),
        unrestricted=unrestricted,
        builtin_admin=task_access_builtin_admin,
    )
    training_task_access = exists().where(
        YoloTrainJob.id == ModelVersion.training_job_id,
        YoloTrainJob.task_id == Task.id,
        Task.deleted_at.is_(None),
        task_access,
    ).correlate(ModelVersion)
    # External registrations have no task lineage and remain visible to their
    # owner (or after publication).  Training-derived versions must satisfy the
    # same task visibility contract as the training/compile catalogs, including
    # the administrator-only consistent-pool boundary.
    external_owner = and_(
        ModelVersion.training_job_id.is_(None),
        ModelVersion.created_by == int(user_id or 0),
    )
    external_model = ModelVersion.training_job_id.is_(None)
    published = and_(
        ModelVersion.status == "PUBLISHED",
        or_(external_model, training_task_access),
    )
    if task_access_builtin_admin:
        # The built-in administrator may inspect every *active* task lineage,
        # including consistent pools, but an orphaned model whose source task
        # was deleted must not re-enter the catalog.
        visibility = or_(external_model, training_task_access)
    elif unrestricted:
        # Configured/service superusers retain cross-task access, but must not
        # bypass the consistent-pool boundary. External registrations have no
        # task lineage and remain globally visible as before.
        visibility = or_(external_model, training_task_access)
    else:
        visibility = or_(training_task_access, external_owner, published)
    query = query.filter(visibility)
    if status:
        query = query.filter(ModelVersion.status == str(status).upper())
    return query


_MODEL_FAMILY_KEY_RE = re.compile(
    r"^(task|series)\s*:\s*(\d+)\s*:\s*algorithm\s*:\s*(\d+)\s*$",
    re.IGNORECASE,
)
_MODEL_FAMILY_TASK_TOKEN_RE = re.compile(r"task\s*:\s*(\d+)", re.IGNORECASE)
_MODEL_FAMILY_SERIES_TOKEN_RE = re.compile(r"series\s*:\s*(\d+)", re.IGNORECASE)
_MODEL_FAMILY_ALGORITHM_TOKEN_RE = re.compile(
    r"algorithm\s*:\s*(\d+)", re.IGNORECASE
)


def _model_family_search_clause(keyword: str):
    """Build one portable search predicate for the grouped model catalog.

    ``family_key`` is a derived value (``task:<id>:algorithm:<id>``), so it
    has no physical column to search.  Recognize its structured form and then
    search the fields rendered by the family/version responses.  Keeping this
    predicate in one place prevents the grouped endpoint from drifting away
    from the ungrouped model catalog's search semantics.
    """

    normalized = str(keyword or "").strip()
    pattern = literal_like_pattern(normalized)
    predicates = [
        # Source task and algorithm registry fields.
        Task.name.ilike(pattern, escape=LIKE_ESCAPE),
        Task.description.ilike(pattern, escape=LIKE_ESCAPE),
        Task.status.ilike(pattern, escape=LIKE_ESCAPE),
        Task.media_type.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.algorithm_key.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.name.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.description.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.task_type.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.algorithm_kind.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.runtime_adapter.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.default_recipe.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmDefinition.risk_level.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmGroup.group_key.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmGroup.name.ilike(pattern, escape=LIKE_ESCAPE),
        AlgorithmGroup.description.ilike(pattern, escape=LIKE_ESCAPE),
        # Training-job lineage exposed by each model version row.
        YoloTrainJob.model_weights.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.weights_path.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.category.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.class_names_json.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.label_set_signature.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.class_order_signature.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.data_yaml_sha256.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.dataset_content_signature.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.training_recipe_key.ilike(pattern, escape=LIKE_ESCAPE),
        ModelSeries.name.ilike(pattern, escape=LIKE_ESCAPE),
        ModelSeries.model_family.ilike(pattern, escape=LIKE_ESCAPE),
        cast(YoloTrainJob.series_id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(YoloTrainJob.variant_id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(YoloTrainJob.training_recipe_version, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        YoloTrainJob.runtime_profile.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.execution_mode.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.train_node_id.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.source_type.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.source_system.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.source_external_task_id.ilike(pattern, escape=LIKE_ESCAPE),
        YoloTrainJob.source_external_package_id.ilike(pattern, escape=LIKE_ESCAPE),
        # IDs and scalar version metadata are rendered in the catalog UI as
        # text, therefore they should participate in the same free-text query.
        cast(Task.id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(AlgorithmDefinition.id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(YoloTrainJob.id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(YoloTrainJob.task_id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(YoloTrainJob.training_task_id, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        cast(YoloTrainJob.dataset_category_id, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        cast(ModelVersion.id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(ModelVersion.version_no, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(ModelVersion.training_job_id, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        cast(ModelVersion.dataset_id, String).ilike(pattern, escape=LIKE_ESCAPE),
        cast(ModelVersion.dataset_version_id, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        cast(ModelVersion.parent_model_version_id, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        cast(ModelVersion.published_catalog_id, String).ilike(
            pattern, escape=LIKE_ESCAPE
        ),
        ModelVersion.display_name.ilike(pattern, escape=LIKE_ESCAPE),
        ModelVersion.model_key.ilike(pattern, escape=LIKE_ESCAPE),
        ModelVersion.status.ilike(pattern, escape=LIKE_ESCAPE),
        ModelVersion.chip_type.ilike(pattern, escape=LIKE_ESCAPE),
        ModelVersion.precision.ilike(pattern, escape=LIKE_ESCAPE),
    ]

    family_match = _MODEL_FAMILY_KEY_RE.fullmatch(normalized)
    if family_match:
        family_kind = family_match.group(1).lower()
        family_id = int(family_match.group(2))
        predicates.append(
            and_(
                (
                    ModelSeries.id == family_id
                    if family_kind == "series"
                    else Task.id == family_id
                ),
                AlgorithmDefinition.id == int(family_match.group(3)),
            )
        )
    else:
        # Allow a user to paste one half of the derived key, e.g. ``task:12``
        # or ``algorithm:3``.  The numeric predicates above still support a
        # plain ID search without the prefix.
        task_token = _MODEL_FAMILY_TASK_TOKEN_RE.search(normalized)
        if task_token:
            predicates.append(Task.id == int(task_token.group(1)))
        series_token = _MODEL_FAMILY_SERIES_TOKEN_RE.search(normalized)
        if series_token:
            predicates.append(ModelSeries.id == int(series_token.group(1)))
        algorithm_token = _MODEL_FAMILY_ALGORITHM_TOKEN_RE.search(normalized)
        if algorithm_token:
            predicates.append(
                AlgorithmDefinition.id == int(algorithm_token.group(1))
            )

    return or_(*predicates)


def list_accessible(
    db: Session,
    *,
    user_id: int | None,
    unrestricted: bool,
    page: int,
    size: int,
    status: str | None = None,
    source: str | None = None,
    training_job_id: int | None = None,
    search: str | None = None,
    task_access_builtin_admin: bool = False,
):
    query = _accessible_query(
        db,
        user_id=user_id,
        unrestricted=unrestricted,
        status=status,
        source=source,
        training_job_id=training_job_id,
        search=search,
        task_access_builtin_admin=task_access_builtin_admin,
    )
    total = query.count()
    rows = (
        query.options(
            joinedload(ModelVersion.artifacts),
            joinedload(ModelVersion.quality_evidence),
        )
        .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, int(size)))
        .all()
    )
    return rows, total


def list_accessible_families(
    db: Session,
    *,
    user_id: int | None,
    unrestricted: bool,
    page: int,
    size: int,
    status: str | None = None,
    search: str | None = None,
    artifact_type: str | None = None,
    family_task_id: int | None = None,
    family_algorithm_id: int | None = None,
    training_recipe_key: str | None = None,
    training_recipe_version: int | None = None,
    label_set_signature: str | None = None,
    class_order_signature: str | None = None,
    task_access_builtin_admin: bool = False,
):
    """Page model series first, then load every version on that page.

    A training-generated series is stable across dataset iterations: its source
    task and algorithm define the family. Filtering and permissions are applied
    before both the family count and version loading, so grouped pagination does
    not depend on the current page size or leak inaccessible versions.
    """
    query = _accessible_query(
        db,
        user_id=user_id,
        unrestricted=unrestricted,
        status=status,
        source="training",
        artifact_type=artifact_type,
        task_access_builtin_admin=task_access_builtin_admin,
    ).join(YoloTrainJob, YoloTrainJob.id == ModelVersion.training_job_id)
    keyword = str(search or "").strip()
    if keyword:
        # Search is applied before grouping so the total represents matching
        # series, while the existing visibility predicate still governs every
        # returned version.  The derived family key and algorithm metadata are
        # joined only for an active search, keeping the unfiltered query fast.
        query = (
            query.join(Task, Task.id == YoloTrainJob.task_id)
            .outerjoin(ModelSeries, ModelSeries.id == YoloTrainJob.series_id)
            .join(
                AlgorithmDefinition,
                AlgorithmDefinition.id == ModelVersion.algorithm_id,
            )
            .join(
                AlgorithmGroup,
                AlgorithmGroup.id == AlgorithmDefinition.algorithm_group_id,
            )
            .filter(_model_family_search_clause(keyword))
        )
    if family_task_id is not None:
        query = query.filter(YoloTrainJob.task_id == int(family_task_id))
    if family_algorithm_id is not None:
        query = query.filter(ModelVersion.algorithm_id == int(family_algorithm_id))
    # Compatibility filtering belongs before family grouping and pagination.
    # This keeps the total/page metadata meaningful when the catalog contains
    # many unrelated model series, and prevents an incompatible version from
    # being selected merely because it happened to be on the current page.
    expected_label_signature = str(label_set_signature or "").strip()
    if expected_label_signature:
        # Label-contract signatures belong to the originating training job
        # (and, for legacy rows, its immutable manifest), not ModelVersion.
        # Keep rows with an empty denormalized value so the Python fallback can
        # recover signatures from an immutable manifest or DatasetVersion.
        query = query.filter(
            or_(
                YoloTrainJob.label_set_signature == expected_label_signature,
                YoloTrainJob.label_set_signature.is_(None),
                YoloTrainJob.label_set_signature == "",
            )
        )
    expected_order_signature = str(class_order_signature or "").strip()
    if expected_order_signature:
        query = query.filter(
            or_(
                YoloTrainJob.class_order_signature == expected_order_signature,
                YoloTrainJob.class_order_signature.is_(None),
                YoloTrainJob.class_order_signature == "",
            )
        )
    recipe_key = str(training_recipe_key or "").strip()
    if recipe_key:
        # Historical jobs may have the recipe only in an immutable JSON
        # snapshot.  Keep rows with an empty denormalized column in the SQL
        # candidate set, then resolve the final value in Python below.
        recipe_keys = _recipe_lookup_keys(recipe_key)
        query = query.filter(
            or_(
                YoloTrainJob.training_recipe_key.in_(recipe_keys),
                YoloTrainJob.training_recipe_key.is_(None),
                YoloTrainJob.training_recipe_key == "",
            )
        )
    if training_recipe_version is not None:
        query = query.filter(
            or_(
                YoloTrainJob.training_recipe_version == int(training_recipe_version),
                YoloTrainJob.training_recipe_version.is_(None),
                # A few early SQLite rows used ``0`` as the nullable-column
                # placeholder before recipe versioning was introduced.
                YoloTrainJob.training_recipe_version == 0,
            )
        )

    # JSON lineage cannot be queried portably across SQLite/MySQL versions and
    # several historical snapshots use different nesting.  Resolve it after
    # all visibility/search/task/artifact predicates have already narrowed the
    # candidate set.  The query is ordered by newest version so the first row
    # seen for a family defines stable family pagination order.
    if (
        recipe_key
        or training_recipe_version is not None
        or expected_label_signature
        or expected_order_signature
    ):
        candidate_rows = (
            query.options(
                joinedload(ModelVersion.artifacts),
                joinedload(ModelVersion.quality_evidence),
            )
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .all()
        )
        job_ids = {
            int(row.training_job_id)
            for row in candidate_rows
            if row.training_job_id is not None
        }
        jobs_by_id = (
            {
                int(job.id): job
                for job in db.query(YoloTrainJob)
                .filter(YoloTrainJob.id.in_(job_ids))
                .all()
            }
            if job_ids
            else {}
        )
        signature_cache: dict[int, tuple[str | None, str | None]] = {}
        filtered_rows = []
        for row in candidate_rows:
            if row.training_job_id is None:
                continue
            job_id = int(row.training_job_id)
            job = jobs_by_id.get(job_id)
            if job is None:
                continue
            if expected_label_signature or expected_order_signature:
                if job_id not in signature_cache:
                    signature_cache[job_id] = _job_contract_signatures(
                        db,
                        job,
                        dataset_version_id=getattr(row, "dataset_version_id", None),
                    )
                actual_label_signature, actual_order_signature = signature_cache[job_id]
                if expected_label_signature and actual_label_signature != expected_label_signature:
                    continue
                if expected_order_signature and actual_order_signature != expected_order_signature:
                    continue
            if recipe_key or training_recipe_version is not None:
                if not _recipe_matches(
                    job,
                    recipe_key=recipe_key,
                    recipe_version=(
                        int(training_recipe_version)
                        if training_recipe_version is not None
                        else None
                    ),
                ):
                    continue
            filtered_rows.append(row)
        return _families_from_rows(
            filtered_rows,
            jobs_by_id,
            page=page,
            size=size,
        )

    # A governed model series is the stable family boundary.  Historical
    # rows without a series retain the legacy task-based grouping.
    family_group = func.coalesce(YoloTrainJob.series_id, YoloTrainJob.task_id)
    families = query.with_entities(
        func.min(YoloTrainJob.task_id).label("task_id"),
        family_group.label("family_group_id"),
        ModelVersion.algorithm_id.label("algorithm_id"),
        func.max(ModelVersion.created_at).label("latest_created_at"),
        func.max(ModelVersion.id).label("latest_id"),
    ).group_by(family_group, ModelVersion.algorithm_id)
    total = families.count()
    page_families = (
        families.order_by(func.max(ModelVersion.created_at).desc(), func.max(ModelVersion.id).desc())
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, int(size)))
        .all()
    )
    if not page_families:
        return [], total

    pairs = [
        (int(item.task_id), int(item.family_group_id), int(item.algorithm_id))
        for item in page_families
    ]
    pair_predicates = [
        and_(
            family_group == family_group_id,
            ModelVersion.algorithm_id == algorithm_id,
        )
        for _task_id, family_group_id, algorithm_id in pairs
    ]
    rows = (
        query.filter(or_(*pair_predicates))
        .with_entities(ModelVersion, YoloTrainJob.task_id, YoloTrainJob.series_id)
        .options(
            joinedload(ModelVersion.artifacts),
            joinedload(ModelVersion.quality_evidence),
        )
        .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
        .all()
    )
    versions_by_family: dict[tuple[int, int], list[ModelVersion]] = {
        (family_group_id, algorithm_id): []
        for _task_id, family_group_id, algorithm_id in pairs
    }
    for row, task_id, series_id in rows:
        group_id = int(series_id or task_id)
        versions_by_family[(group_id, int(row.algorithm_id))].append(row)
    return [
        (task_id, algorithm_id, versions_by_family[(family_group_id, algorithm_id)])
        for task_id, family_group_id, algorithm_id in pairs
    ], total


def next_version_no(db: Session, algorithm_id: int) -> int:
    value = (
        db.query(ModelVersion.version_no)
        .filter(
            ModelVersion.algorithm_id == int(algorithm_id),
        )
        .order_by(ModelVersion.version_no.desc())
        .first()
    )
    return int(value[0]) + 1 if value else 1


def next_branch_version_no(
    db: Session,
    series_id: int | None,
    variant_id: int | None = None,
) -> int | None:
    """Return the next sequence number within one model-series branch.

    ``version_no`` predates model branches and is intentionally global per
    algorithm.  New callers use this independent sequence for the visible
    branch version.  Historical rows may not have a value yet, so their count
    participates in the floor to avoid assigning a new branch ``v1`` when
    that branch already contains legacy versions.
    """

    if series_id is None:
        return None
    query = db.query(ModelVersion).filter(ModelVersion.series_id == int(series_id))
    if variant_id is None:
        query = query.filter(ModelVersion.variant_id.is_(None))
    else:
        query = query.filter(ModelVersion.variant_id == int(variant_id))
    maximum = query.with_entities(func.max(ModelVersion.branch_version_no)).scalar()
    count = query.count()
    try:
        maximum_value = int(maximum or 0)
    except (TypeError, ValueError):
        maximum_value = 0
    return max(maximum_value, int(count)) + 1


def create(db: Session, row: ModelVersion) -> ModelVersion:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def create_artifact(db: Session, row: ModelArtifact) -> ModelArtifact:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def create_evidence(db: Session, row: ModelQualityEvidence) -> ModelQualityEvidence:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_evidence(
    db: Session, model_version_id: int, gate_type: str, revision: int | None = None
):
    query = db.query(ModelQualityEvidence).filter(
        ModelQualityEvidence.model_version_id == int(model_version_id),
        ModelQualityEvidence.gate_type == str(gate_type).lower(),
    )
    if revision is not None:
        query = query.filter(ModelQualityEvidence.revision == int(revision))
    return query.order_by(ModelQualityEvidence.revision.desc()).first()
