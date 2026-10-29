from __future__ import annotations

import json
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_algorithm
from labelu.internal.application.command.algorithm import (
    CreateAlgorithmDefinitionCommand,
    UpdateAlgorithmDefinitionCommand,
)
from labelu.internal.application.response.algorithm import (
    AlgorithmCapabilityResponse,
    AlgorithmDefinitionResponse,
    AlgorithmGroupResponse,
)
from labelu.internal.common.algorithm_registry import (
    ALL_ALGORITHM_DEFINITIONS,
    ALGORITHM_GROUPS,
    TARGET_CHIPS,
    adapter_capabilities,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.annotation_taxonomy import (
    RuntimePublishTemplate,
    TrainingRecipe,
)
from labelu.internal.domain.models.user import User
from labelu.internal.common.task_access import is_task_superuser


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _builtin_chip_sync(
    db: Session,
) -> list[tuple[AlgorithmDefinition, tuple[str, ...]]]:
    """Find built-in rows that still contain the pre-7GAI chip list.

    Existing installations are upgraded lazily as well as by Alembic.  Only
    rows represented by the built-in registry are touched; custom operator
    definitions remain unchanged.
    """

    updates: list[tuple[AlgorithmDefinition, tuple[str, ...]]] = []
    for spec in ALL_ALGORITHM_DEFINITIONS:
        row = crud_algorithm.get_definition(db, spec.key)
        if row is None:
            continue
        configured = _decode_json(row.supported_chips_json, [])
        if not isinstance(configured, list):
            configured = []
        normalized = [str(item).strip().lower() for item in configured if str(item).strip()]
        # A legacy row is recognizable by any non-platform chip.  Replace the
        # complete built-in declaration so BM1684/RK1808 are not omitted when
        # the old row only contained BM1688.
        if not normalized or any(item not in TARGET_CHIPS for item in normalized):
            desired = tuple(spec.supported_chips)
            if normalized != list(desired):
                updates.append((row, desired))
    return updates


def capability_state(
    row: AlgorithmDefinition,
    *,
    adapter_available: bool | None = None,
    adapter_detail: dict[str, Any] | None = None,
) -> tuple[str, bool, bool]:
    """Return the canonical registry state and model-use predicates.

    Rule templates and pipelines are runtime capabilities, not trainable model
    algorithms.  Keeping this predicate in the registry service prevents the
    model repository and UI from interpreting ``ACTIVE`` as publishable.
    """
    if adapter_available is None:
        try:
            detail = adapter_detail or adapter_capabilities(row.training_adapter)
            adapter_available = True
        except ValueError:
            detail = adapter_detail or {}
            adapter_available = False
    else:
        detail = adapter_detail or {}
    if str(getattr(row, "status", "ACTIVE") or "").upper() != "ACTIVE" or not adapter_available:
        return "REGISTERED", False, False
    kind = str(row.algorithm_kind or "model").lower()
    if kind in {"rule_template", "pipeline"} or str(row.training_adapter or "").lower() == "rule_template":
        return "RUNTIME_READY", False, False
    implemented = set(detail.get("implemented") or [])
    required_runtime = {"prepare_dataset", "train", "evaluate", "export", "validate_runtime"}
    if not required_runtime.issubset(implemented):
        return "CONFIGURED", False, False
    quality_gate = _decode_json(getattr(row, "quality_gate_json", None), None)
    if quality_gate is None:
        quality_gate = getattr(row, "quality_gate", None)
    if not isinstance(quality_gate, dict) or not quality_gate:
        return "RUNTIME_READY", True, False
    # The algorithm registry only describes the contract required to train and
    # evaluate a model.  It has no model-version evidence or deployable
    # artifact, so a configured gate must not be presented as publishable.
    return "QUALITY_READY", True, False


def ensure_seeded(db: Session) -> None:
    """Synchronize built-in definitions without overwriting operator changes."""
    missing_groups = [
        spec
        for spec in ALGORITHM_GROUPS
        if crud_algorithm.get_group(db, spec.key) is None
    ]
    missing_definitions = [
        spec
        for spec in ALL_ALGORITHM_DEFINITIONS
        if crud_algorithm.get_definition(db, spec.key) is None
    ]
    # Recipe rows are part of the built-in training catalog.  Older databases
    # can legitimately have the table but no rows (for example when created
    # from ``Base.metadata`` in a local verification environment), so seed
    # only missing defaults here as well.  Existing operator-managed rows are
    # never changed.
    recipe_defaults = {
        "yolo_detection": {
            "recipe_key": "yolo_detection_v1",
            "name": "YOLO 通用检测",
            "model_family": "yolov11",
            "annotation_kind": "bbox",
        },
        "classification": {
            "recipe_key": "classification_v1",
            "name": "通用图像分类",
            "model_family": "resnet",
            "annotation_kind": "class_label",
        },
        "segmentation": {
            "recipe_key": "segmentation_v1",
            "name": "通用实例分割",
            "model_family": "yolov11-seg",
            "annotation_kind": "polygon",
        },
        "pose": {
            "recipe_key": "pose_v1",
            "name": "通用姿态估计",
            "model_family": "yolov11-pose",
            "annotation_kind": "keypoints",
        },
        "custom_container": {
            "recipe_key": "custom_container_v1",
            "name": "自定义容器模型",
            "model_family": "custom",
            "annotation_kind": "custom",
        },
    }
    missing_recipes = [
        item
        for item in recipe_defaults.values()
        if db.query(TrainingRecipe)
        .filter(
            TrainingRecipe.recipe_key == item["recipe_key"],
            TrainingRecipe.version == 1,
            TrainingRecipe.deleted_at.is_(None),
        )
        .first()
        is None
    ]
    # The task-dimension wizard validates the runtime contract before it can
    # create a job. Some older/local databases were created from metadata
    # without running the migration seed, so provide the built-in default
    # lazily. Inspect all rows, including inactive/soft-deleted rows, before
    # inserting: an operator-disabled template must stay disabled and its
    # unique contract key must not be duplicated.
    runtime_template_defaults = {
        "template_key": "yolov11_detection",
        "name": "YOLOv11 通用检测运行时",
        "environment_id": "default",
        "runtime_profile": "yolov11_detection",
        "interface_version": 1,
        "expected_model_alg_type": "yoloV11",
        "capability_contract_json": json.dumps({}, ensure_ascii=False),
        "is_default": True,
        "status": "active",
    }
    runtime_template_exists = (
        db.query(RuntimePublishTemplate.id)
        .filter(
            RuntimePublishTemplate.template_key
            == runtime_template_defaults["template_key"],
            RuntimePublishTemplate.environment_id
            == runtime_template_defaults["environment_id"],
            RuntimePublishTemplate.interface_version
            == runtime_template_defaults["interface_version"],
        )
        .first()
        is not None
    )
    chip_updates = _builtin_chip_sync(db)
    if (
        not missing_groups
        and not missing_definitions
        and not missing_recipes
        and not chip_updates
        and runtime_template_exists
    ):
        return
    with begin_transaction(db):
        for spec in missing_groups:
            crud_algorithm.create_group(
                db,
                AlgorithmGroup(
                    group_key=spec.key,
                    name=spec.name,
                    description=spec.description,
                    status="ACTIVE",
                    sort_order=spec.sort_order,
                ),
            )
        for spec in missing_definitions:
            group = crud_algorithm.get_group(db, spec.group_key)
            if group is None:
                continue
            crud_algorithm.create_definition(
                db,
                AlgorithmDefinition(
                    algorithm_key=spec.key,
                    name=spec.name,
                    description=spec.description,
                    task_type=spec.task_type,
                    algorithm_group_id=group.id,
                    required_tools_json=json.dumps(
                        spec.required_tools, ensure_ascii=False
                    ),
                    required_attributes_json=json.dumps(
                        spec.required_attributes, ensure_ascii=False
                    ),
                    training_adapter=spec.training_adapter,
                    evaluation_adapter=spec.evaluation_adapter,
                    supported_chips_json=json.dumps(
                        spec.supported_chips, ensure_ascii=False
                    ),
                    default_recipe=spec.default_recipe,
                    input_contract_json=json.dumps(
                        spec.input_contract, ensure_ascii=False
                    ),
                    output_contract_json=json.dumps(
                        spec.output_contract, ensure_ascii=False
                    ),
                    algorithm_kind=spec.algorithm_kind,
                    runtime_adapter=spec.runtime_adapter,
                    rule_schema_json=json.dumps(spec.rule_schema, ensure_ascii=False)
                    if spec.rule_schema is not None
                    else None,
                    supported_formats_json=json.dumps(
                        spec.supported_formats, ensure_ascii=False
                    ),
                    min_samples_json=json.dumps(spec.min_samples, ensure_ascii=False)
                    if spec.min_samples is not None
                    else None,
                    quality_gate_json=json.dumps(spec.quality_gate, ensure_ascii=False)
                    if spec.quality_gate is not None
                    else None,
                    risk_level=spec.risk_level,
                    annotation_contract_version=spec.annotation_contract_version,
                    status="ACTIVE",
                    metadata_json=json.dumps(
                        {
                            "algorithm_kind": spec.algorithm_kind,
                            "runtime_adapter": spec.runtime_adapter,
                            "rule_schema": spec.rule_schema,
                            "catalog_kind": spec.catalog_kind,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        for item in missing_recipes:
            db.add(
                TrainingRecipe(
                    recipe_key=item["recipe_key"],
                    version=1,
                    name=item["name"],
                    job_type=item["recipe_key"].removesuffix("_v1"),
                    model_family=item["model_family"],
                    annotation_kind=item["annotation_kind"],
                    component_contract_json=json.dumps({}, ensure_ascii=False),
                    default_hyperparams_json=json.dumps({}, ensure_ascii=False),
                    status="active",
                )
            )
        if not runtime_template_exists:
            db.add(RuntimePublishTemplate(**runtime_template_defaults))
        for row, chips in chip_updates:
            row.supported_chips_json = json.dumps(chips, ensure_ascii=False)


def _group_response(row: AlgorithmGroup) -> AlgorithmGroupResponse:
    return AlgorithmGroupResponse(
        id=row.id,
        group_key=row.group_key,
        name=row.name,
        description=row.description,
        status=row.status,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _definition_response(row: AlgorithmDefinition) -> AlgorithmDefinitionResponse:
    group = getattr(row, "group", None)
    metadata = _decode_json(row.metadata_json, {})
    if not isinstance(metadata, dict):
        metadata = {}
    rule_schema = _decode_json(row.rule_schema_json, None)
    if rule_schema is None:
        rule_schema = metadata.get("rule_schema")
    supported_formats = _decode_json(row.supported_formats_json, None)
    if not isinstance(supported_formats, list):
        supported_formats = metadata.get("supported_formats") or []
    min_samples = _decode_json(row.min_samples_json, None)
    if min_samples is None:
        min_samples = metadata.get("min_samples")
    quality_gate = _decode_json(row.quality_gate_json, None)
    if quality_gate is None:
        quality_gate = metadata.get("quality_gate")
    try:
        adapter_detail = adapter_capabilities(row.training_adapter)
        adapter_available = True
    except ValueError:
        adapter_detail = {}
        adapter_available = False
    capability, trainable, publishable = capability_state(
        row,
        adapter_available=adapter_available,
        adapter_detail=adapter_detail,
    )
    quality_ready = capability in {"QUALITY_READY", "PUBLISHABLE"}
    supported_chips = _decode_json(row.supported_chips_json, [])
    if not isinstance(supported_chips, list):
        supported_chips = []
    builtin = next(
        (spec for spec in ALL_ALGORITHM_DEFINITIONS if spec.key == row.algorithm_key),
        None,
    )
    if builtin and (
        not supported_chips
        or any(str(item).strip().lower() not in TARGET_CHIPS for item in supported_chips)
    ):
        supported_chips = list(builtin.supported_chips)
    else:
        supported_chips = [
            str(item).strip().lower()
            for item in supported_chips
            if str(item).strip()
        ]
    return AlgorithmDefinitionResponse(
        id=row.id,
        algorithm_key=row.algorithm_key,
        name=row.name,
        description=row.description,
        task_type=row.task_type,
        algorithm_group_id=row.algorithm_group_id,
        group_key=getattr(group, "group_key", None),
        group_name=getattr(group, "name", None),
        label_set_signature=row.label_set_signature,
        required_tools=_decode_json(row.required_tools_json, []),
        required_attributes=_decode_json(row.required_attributes_json, []),
        training_adapter=row.training_adapter,
        evaluation_adapter=row.evaluation_adapter,
        supported_chips=supported_chips,
        default_recipe=row.default_recipe,
        input_contract=_decode_json(row.input_contract_json, None),
        output_contract=_decode_json(row.output_contract_json, None),
        algorithm_kind=str(row.algorithm_kind or metadata.get("algorithm_kind") or "model"),
        runtime_adapter=row.runtime_adapter or metadata.get("runtime_adapter"),
        rule_schema=rule_schema if isinstance(rule_schema, dict) else None,
        supported_formats=supported_formats,
        min_samples=min_samples if isinstance(min_samples, dict) else None,
        quality_gate=quality_gate if isinstance(quality_gate, dict) else None,
        risk_level=row.risk_level or metadata.get("risk_level") or "standard",
        annotation_contract_version=(
            row.annotation_contract_version
            or metadata.get("annotation_contract_version")
            or "v1"
        ),
        catalog_kind=str(metadata.get("catalog_kind") or "internal"),
        status=row.status,
        adapter_available=adapter_available,
        capability_state=capability,
        trainable=trainable,
        quality_ready=quality_ready,
        publishable=publishable,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _not_found() -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND,
        message="算法不存在",
    )


def _assert_registry_admin(user: User) -> None:
    if not is_task_superuser(user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="只有平台管理员可以修改算法目录",
        )


def _validate_adapter_contract(
    training_adapter: str, evaluation_adapter: str, algorithm_kind: str
) -> None:
    """Reject catalog rows that cannot be executed by the standard workers."""

    normalized_training = str(training_adapter or "").strip().lower()
    normalized_evaluation = str(evaluation_adapter or "").strip().lower()
    normalized_kind = str(algorithm_kind or "model").strip().lower()
    for field, adapter_key in (
        ("training_adapter", normalized_training),
        ("evaluation_adapter", normalized_evaluation),
    ):
        try:
            adapter_capabilities(adapter_key)
        except ValueError as exc:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"{field} 未注册: {adapter_key}",
            ) from exc
    if normalized_kind == "rule_template" and normalized_training != "rule_template":
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="规则模板必须使用 rule_template 适配器",
        )
    if normalized_kind != "rule_template" and normalized_training == "rule_template":
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="模型或流水线不能使用 rule_template 适配器",
        )


def _json_text(value: Any, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"{field} 必须是有效 JSON",
        ) from exc


def _metadata_with_catalog_kind(value: Any, catalog_kind: str | None) -> str | None:
    metadata = _decode_json(value, {}) if isinstance(value, str) else value
    if not isinstance(metadata, dict):
        metadata = {}
    if catalog_kind is not None:
        metadata["catalog_kind"] = catalog_kind
    return _json_text(metadata, "metadata_json") if metadata else None


async def create_definition(
    db: Session, cmd: CreateAlgorithmDefinitionCommand, current_user: User
) -> AlgorithmDefinitionResponse:
    _assert_registry_admin(current_user)
    ensure_seeded(db)
    key = cmd.algorithm_key.strip().lower()
    if crud_algorithm.get_definition(db, key) is not None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="算法标识已存在",
        )
    group = crud_algorithm.get_group_by_id(db, cmd.algorithm_group_id)
    if group is None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="算法分组不存在",
        )
    _validate_adapter_contract(
        cmd.training_adapter, cmd.evaluation_adapter, cmd.algorithm_kind
    )
    row = AlgorithmDefinition(
        algorithm_key=key,
        name=cmd.name.strip(),
        description=cmd.description,
        task_type=cmd.task_type.strip(),
        algorithm_group_id=group.id,
        label_set_signature=cmd.label_set_signature,
        required_tools_json=_json_text(cmd.required_tools, "required_tools"),
        required_attributes_json=_json_text(cmd.required_attributes, "required_attributes"),
        training_adapter=cmd.training_adapter.strip(),
        evaluation_adapter=cmd.evaluation_adapter.strip(),
        supported_chips_json=_json_text(cmd.supported_chips, "supported_chips"),
        default_recipe=cmd.default_recipe,
        input_contract_json=_json_text(cmd.input_contract, "input_contract") if cmd.input_contract is not None else None,
        output_contract_json=_json_text(cmd.output_contract, "output_contract") if cmd.output_contract is not None else None,
        algorithm_kind=cmd.algorithm_kind,
        runtime_adapter=cmd.runtime_adapter,
        rule_schema_json=_json_text(cmd.rule_schema, "rule_schema") if cmd.rule_schema is not None else None,
        supported_formats_json=_json_text(cmd.supported_formats, "supported_formats"),
        min_samples_json=_json_text(cmd.min_samples, "min_samples") if cmd.min_samples is not None else None,
        quality_gate_json=_json_text(cmd.quality_gate, "quality_gate") if cmd.quality_gate is not None else None,
        risk_level=cmd.risk_level,
        annotation_contract_version=cmd.annotation_contract_version,
        status=cmd.status,
        metadata_json=_metadata_with_catalog_kind(None, cmd.catalog_kind),
    )
    with begin_transaction(db):
        crud_algorithm.create_definition(db, row)
    row.group = group
    return _definition_response(row)


async def update_definition(
    db: Session, algorithm_id: int, cmd: UpdateAlgorithmDefinitionCommand, current_user: User
) -> AlgorithmDefinitionResponse:
    _assert_registry_admin(current_user)
    ensure_seeded(db)
    row = crud_algorithm.get_definition_by_id(db, algorithm_id)
    if row is None:
        raise _not_found()
    values = cmd.model_dump(exclude_unset=True)
    _validate_adapter_contract(
        values.get("training_adapter", row.training_adapter),
        values.get("evaluation_adapter", row.evaluation_adapter),
        values.get("algorithm_kind", row.algorithm_kind or "model"),
    )
    group_id = values.pop("algorithm_group_id", None)
    catalog_kind = values.pop("catalog_kind", None)
    if group_id is not None:
        group = crud_algorithm.get_group_by_id(db, group_id)
        if group is None:
            raise LabelUException(code=ErrorCode.CODE_30002_VALIDATION_ERROR, status_code=status.HTTP_400_BAD_REQUEST, message="算法分组不存在")
        row.algorithm_group_id = group.id
    json_fields = {
        "required_tools": "required_tools_json",
        "required_attributes": "required_attributes_json",
        "supported_chips": "supported_chips_json",
        "input_contract": "input_contract_json",
        "output_contract": "output_contract_json",
        "rule_schema": "rule_schema_json",
        "supported_formats": "supported_formats_json",
        "min_samples": "min_samples_json",
        "quality_gate": "quality_gate_json",
    }
    for key, value in values.items():
        column = json_fields.get(key)
        if column:
            value = _json_text(value, key)
        elif key in {"name", "task_type", "training_adapter", "evaluation_adapter"} and isinstance(value, str):
            value = value.strip()
        setattr(row, column or key, value)
    if catalog_kind is not None or row.metadata_json:
        row.metadata_json = _metadata_with_catalog_kind(row.metadata_json, catalog_kind)
    with begin_transaction(db):
        db.flush()
        db.refresh(row)
    row.group = crud_algorithm.get_group_by_id(db, row.algorithm_group_id)
    return _definition_response(row)


async def list_groups(
    db: Session,
    status_filter: str | None = "ACTIVE",
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> list[AlgorithmGroupResponse] | tuple[list[AlgorithmGroupResponse], int]:
    ensure_seeded(db)
    result = crud_algorithm.list_groups(
        db,
        status_filter,
        search=search,
        page=page,
        size=size,
    )
    if page is None or size is None:
        return [_group_response(row) for row in result]  # type: ignore[arg-type]
    rows, total = result  # type: ignore[misc]
    return [_group_response(row) for row in rows], total


async def list_definitions(
    db: Session,
    *,
    status_filter: str | None = "ACTIVE",
    task_type: str | None = None,
    group_key: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> tuple[list[AlgorithmDefinitionResponse], int]:
    ensure_seeded(db)
    rows, total = crud_algorithm.list_definitions(
        db,
        status=status_filter,
        task_type=task_type,
        group_key=group_key,
        search=search,
        page=page,
        size=size,
    )
    # Avoid a second query per row while keeping CRUD independent of ORM
    # relationship declarations in this first registry slice.
    groups = {row.id: row for row in crud_algorithm.list_groups(db, status=None)}
    for row in rows:
        row.group = groups.get(row.algorithm_group_id)
    return [_definition_response(row) for row in rows], total


async def get_definition(
    db: Session, algorithm_key: str
) -> AlgorithmDefinitionResponse:
    ensure_seeded(db)
    row = crud_algorithm.get_definition(db, algorithm_key)
    if row is None:
        raise _not_found()
    group = crud_algorithm.get_group_by_id(db, row.algorithm_group_id)
    row.group = group
    return _definition_response(row)


async def capabilities(db: Session, algorithm_key: str) -> AlgorithmCapabilityResponse:
    ensure_seeded(db)
    row = crud_algorithm.get_definition(db, algorithm_key)
    # Capability discovery must remain available during a rolling migration
    # when the catalog row has not yet been materialized in the local DB.
    # The registry is the canonical source for built-in product definitions.
    if row is None:
        spec = next((item for item in ALL_ALGORITHM_DEFINITIONS if item.key == algorithm_key), None)
        if spec is None:
            raise _not_found()
        try:
            detail = adapter_capabilities(spec.training_adapter)
            available = True
        except ValueError:
            detail = {"adapter_key": spec.training_adapter, "stages": [], "implemented": [], "protocol_version": None, "worker": None}
            available = False
        state, trainable, publishable = capability_state(
            spec, adapter_available=available, adapter_detail=detail
        )
        return AlgorithmCapabilityResponse(
            algorithm_key=spec.key,
            adapter_key=detail["adapter_key"],
            execution_mode=detail.get("execution_mode", "in_process"),
            task_type=spec.task_type,
            supported_chips=list(spec.supported_chips),
            stages=detail["stages"],
            implemented=detail["implemented"],
            available=available,
            capability_state=state,
            trainable=trainable,
            quality_ready=state in {"QUALITY_READY", "PUBLISHABLE"},
            publishable=publishable,
            protocol_version=detail.get("protocol_version"),
            worker=detail.get("worker"),
        )
    try:
        detail = adapter_capabilities(row.training_adapter)
        available = True
    except ValueError:
        detail = {
            "adapter_key": row.training_adapter,
            "stages": [],
            "implemented": [],
            "protocol_version": None,
            "worker": None,
        }
        available = False
    state, trainable, publishable = capability_state(
        row, adapter_available=available, adapter_detail=detail
    )
    return AlgorithmCapabilityResponse(
        algorithm_key=row.algorithm_key,
        adapter_key=detail["adapter_key"],
        execution_mode=detail.get("execution_mode", "in_process"),
        task_type=row.task_type,
        supported_chips=_decode_json(row.supported_chips_json, []),
        stages=detail["stages"],
        implemented=detail["implemented"],
        available=available,
        capability_state=state,
        trainable=trainable,
        quality_ready=state in {"QUALITY_READY", "PUBLISHABLE"},
        publishable=publishable,
        protocol_version=detail.get("protocol_version"),
        worker=detail.get("worker"),
    )
