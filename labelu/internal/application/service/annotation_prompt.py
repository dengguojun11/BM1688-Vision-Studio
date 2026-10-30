"""Contract-bound AI prompt compilation and publication.

Prompts are execution artifacts, not replacements for annotation contracts.
Every published row is tied to one immutable dataset/task contract revision;
runtime resolution therefore cannot silently reuse a prompt from another label
set.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from fastapi import status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from labelu.internal.application.command.annotation_prompt import (
    AnnotationPromptPreviewCommand,
    PublishAnnotationPromptCommand,
    SaveAnnotationPromptCommand,
)
from labelu.internal.application.response.annotation_prompt import (
    AnnotationPromptResponse,
)
from labelu.internal.application.service import annotation_contract
from labelu.internal.application.service import dataset as dataset_service
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.annotation_contract import TaskAnnotationContract
from labelu.internal.domain.models.annotation_prompt import AnnotationPromptBinding
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User

DEFAULT_OPERATION = "bbox_detection"
DEFAULT_PROVIDER = "volcengine"
DEFAULT_PROFILE_KEY = "annotation_bbox_v1"
DEFAULT_PROFILE_VERSION = "1"
COMPILER_VERSION = "1"
PUBLISHED = "PUBLISHED"
DRAFT = "DRAFT"
SUPERSEDED = "SUPERSEDED"
INVALIDATED = "INVALIDATED"


def _error(
    message: str,
    code=ErrorCode.CODE_30002_VALIDATION_ERROR,
    http_status=status.HTTP_400_BAD_REQUEST,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        value = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _contract_for_revision(
    db: Session,
    dataset_id: int,
    revision: int,
    current_user: User,
    *,
    manage: bool = False,
    prefer_current: bool = False,
    require_current: bool = False,
) -> tuple[Any, DatasetLabelContract]:
    dataset = dataset_service._dataset_or_raise(
        db, int(dataset_id), current_user, manage=manage
    )
    # The global taxonomy is mutable, while dataset contracts are immutable
    # snapshots. Materialize the next revision before any prompt operation so
    # a stale UI revision cannot silently compile the old vocabulary.
    with begin_transaction(db):
        current = dataset_service.ensure_current_dataset_contract(
            db,
            dataset,
            created_by=getattr(current_user, "id", None),
        )
        db.refresh(dataset)
    if current is not None and int(revision) != int(current.revision):
        if require_current:
            raise _error(
                "数据集标签契约已更新，请刷新后重试",
                http_status=status.HTTP_409_CONFLICT,
            )
        if prefer_current:
            revision = int(current.revision)
    contract = (
        db.query(DatasetLabelContract)
        .filter(
            DatasetLabelContract.dataset_id == int(dataset.id),
            DatasetLabelContract.revision == int(revision),
        )
        .one_or_none()
    )
    if contract is None:
        raise _error("数据集标签契约修订不存在", http_status=status.HTTP_404_NOT_FOUND)
    return dataset, contract


def _label_rows(contract: DatasetLabelContract) -> list[dict[str, Any]]:
    rows = []
    for index, raw in enumerate(_json_list(contract.labels_snapshot_json)):
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("label_value") or raw.get("value") or "").strip()
        key = str(raw.get("label_key") or raw.get("key") or value).strip()
        if not value:
            continue
        rows.append(
            {
                "label_id": raw.get("label_id"),
                "label_value": value,
                "label_key": key,
                "annotation_kind": raw.get("annotation_kind")
                or contract.annotation_kind,
                "attributes": (
                    raw.get("attributes")
                    if isinstance(raw.get("attributes"), list)
                    else []
                ),
                "description": raw.get("description"),
                "prompt_guidance": raw.get("prompt_guidance"),
                "sort_order": index,
            }
        )
    return rows


def _build_prompt_spec(
    contract: DatasetLabelContract,
    *,
    operation: str,
    provider_key: str,
    model_name: str | None,
    profile_key: str,
    profile_version: str,
    image_width: int,
    image_height: int,
) -> tuple[dict[str, Any], str, list[str]]:
    operation = str(operation or DEFAULT_OPERATION).strip()
    if operation not in {"bbox_detection", "global_tag"}:
        raise _error(f"不支持的 AI 提示词操作类型: {operation}")

    labels = _label_rows(contract) if operation == "bbox_detection" else []
    warnings: list[str] = []
    if operation == "bbox_detection" and not labels:
        raise _error("当前标签契约没有可用于目标检测的标签")
    if operation == "bbox_detection":
        incomplete = [
            row["label_value"]
            for row in labels
            if not row.get("description") and not row.get("prompt_guidance")
        ]
        if incomplete:
            warnings.append(
                "部分标签没有语义定义，提示词只能保证闭集类别和几何格式："
                + ",".join(incomplete)
            )

    prompt_payload = {
        "labels": [
            {
                "name": row["label_value"],
                "display_name": row["label_key"],
                "tool": "rectTool",
                "color": None,
                "description": row.get("description"),
                "attributes": row.get("attributes") or [],
            }
            for row in labels
        ],
        "tag_dimensions": [],
        "constraints": {
            "allowed_tools": (
                ["rectTool"] if operation == "bbox_detection" else ["tagTool"]
            ),
            "max_results_per_label": 100,
            "filter_by_labels": True,
        },
        "prompt": "",
    }

    # Use placeholders in the persisted template.  The runtime renderer
    # replaces them with the dimensions of the transient image sent upstream.
    from labelu.internal.application.service.vision_llm_chain import _build_instruction

    base = _build_instruction(
        prompt_payload,
        "{{image_width}}",
        "{{image_height}}",
        include_runtime_suffix=False,
        include_request_prompt=False,
    )
    header = (
        "你必须严格遵守以下标签契约，不得使用契约之外的类别。\n"
        f"契约编号：{int(contract.id)}；契约修订：{int(contract.revision)}；"
        f"标注类型：{contract.annotation_kind}；标签范围：任务契约快照。\n"
        f"label_set_signature：{contract.label_set_signature}\n"
        f"class_order_signature：{contract.class_order_signature}\n"
        "契约身份由服务端校验，模型不要自行修改或回显契约信息。\n\n"
    )
    prompt = header + base
    spec = {
        "contract_id": int(contract.id),
        "contract_revision": int(contract.revision),
        "annotation_kind": str(contract.annotation_kind),
        "label_scope_mode": "DATASET_CONTRACT",
        "label_set_signature": str(contract.label_set_signature),
        "class_order_signature": str(contract.class_order_signature),
        "operation": operation,
        "provider_key": str(provider_key or DEFAULT_PROVIDER),
        "model_name": model_name,
        "profile_key": str(profile_key or DEFAULT_PROFILE_KEY),
        "profile_version": str(profile_version or DEFAULT_PROFILE_VERSION),
        "compiler_version": COMPILER_VERSION,
        "image": {
            "width": int(image_width),
            "height": int(image_height),
            "coordinate_space": "pixel_xywh",
        },
        "labels": labels,
        "output_schema_version": "annotation.v2",
    }
    return spec, prompt, warnings


def render_prompt_template(prompt: str, image_width: int, image_height: int) -> str:
    """Render dynamic image dimensions without changing the published snapshot."""

    return (
        str(prompt or "")
        .replace("{{image_width}}", str(int(image_width)))
        .replace("{{image_height}}", str(int(image_height)))
    )


def _prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def _prompt_mentions_label(prompt: str, label_value: str) -> bool:
    """Match a stable label value as a complete token, not a substring."""

    value = str(label_value or "").strip()
    if not value:
        return False
    # ``\\w`` is Unicode-aware in Python, so identifiers such as Chinese
    # labels and ASCII values containing underscores cannot match inside a
    # larger identifier (e.g. ``person`` inside ``person_electricbike``).
    return (
        re.search(
            rf"(?<!\w){re.escape(value)}(?!\w)",
            str(prompt or ""),
            flags=re.UNICODE,
        )
        is not None
    )


def _validate_effective_prompt(prompt: str, spec: dict[str, Any]) -> None:
    text = str(prompt or "").strip()
    if not text:
        raise _error("提示词不能为空")
    if len(text) > 200000:
        raise _error("提示词长度不能超过 200000 个字符")
    if spec.get("operation") == "bbox_detection":
        missing = [
            str(row.get("label_value"))
            for row in spec.get("labels") or []
            if not _prompt_mentions_label(text, str(row.get("label_value") or ""))
        ]
        if missing:
            raise _error("提示词不能删除契约类别: " + ",".join(missing))
        for required in ("rectTool", '"results"', "x", "y", "width", "height"):
            if required not in text:
                raise _error(f"提示词缺少契约要求字段: {required}")
    elif spec.get("operation") == "global_tag":
        for required in ("tagTool", '"tag_values"', '"results"'):
            if required not in text:
                raise _error(f"提示词缺少全局标签契约字段: {required}")


def _prompt_spec_label_values(spec: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for row in spec.get("labels") or []:
        if not isinstance(row, dict):
            continue
        value = str(
            row.get("label_value") or row.get("name") or row.get("value") or ""
        ).strip()
        if value:
            values.append(value)
    return values


def _contract_label_values(contract: Any) -> list[str]:
    values: list[str] = []
    for row in _json_list(getattr(contract, "labels_snapshot_json", "[]")):
        if not isinstance(row, dict):
            continue
        value = str(
            row.get("label_value") or row.get("name") or row.get("value") or ""
        ).strip()
        if value:
            values.append(value)
    return values


def _row_response(
    row: AnnotationPromptBinding | None, *, preview: dict[str, Any] | None = None
) -> AnnotationPromptResponse:
    if row is None:
        assert preview is not None
        return AnnotationPromptResponse(**preview)
    return AnnotationPromptResponse(
        id=int(row.id),
        dataset_id=int(row.dataset_id) if row.dataset_id is not None else None,
        contract_id=int(
            row.dataset_label_contract_id or row.task_annotation_contract_id
        ),
        contract_revision=int(row.contract_revision),
        operation=str(row.operation),
        provider_key=str(row.provider_key),
        model_name=row.model_name,
        profile_key=str(row.profile_key),
        profile_version=str(row.profile_version),
        compiler_version=str(row.compiler_version),
        label_set_signature=str(row.label_set_signature),
        class_order_signature=str(row.class_order_signature),
        prompt_spec=_json_object(row.prompt_spec_json),
        generated_prompt=str(row.generated_prompt or ""),
        edited_prompt=row.edited_prompt,
        effective_prompt=str(row.effective_prompt or ""),
        prompt_sha256=str(row.prompt_sha256 or ""),
        warnings=_json_list(row.warnings_json),
        status=str(row.status),
        created_by=row.created_by,
        published_by=row.published_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        published_at=row.published_at,
    )


def _preview_payload(
    contract: DatasetLabelContract,
    cmd: AnnotationPromptPreviewCommand,
) -> dict[str, Any]:
    spec, generated, warnings = _build_prompt_spec(
        contract,
        operation=cmd.operation,
        provider_key=cmd.provider_key,
        model_name=cmd.model_name,
        profile_key=cmd.profile_key,
        profile_version=cmd.profile_version,
        image_width=cmd.image_width,
        image_height=cmd.image_height,
    )
    return {
        "id": None,
        "dataset_id": int(contract.dataset_id),
        "contract_id": int(contract.id),
        "contract_revision": int(contract.revision),
        "operation": cmd.operation,
        "provider_key": cmd.provider_key,
        "model_name": cmd.model_name,
        "profile_key": cmd.profile_key,
        "profile_version": cmd.profile_version,
        "compiler_version": COMPILER_VERSION,
        "label_set_signature": str(contract.label_set_signature),
        "class_order_signature": str(contract.class_order_signature),
        "prompt_spec": spec,
        "generated_prompt": generated,
        "edited_prompt": None,
        "effective_prompt": generated,
        "prompt_sha256": _prompt_sha256(generated),
        "warnings": warnings,
        "status": "PREVIEW",
    }


async def preview(
    db: Session,
    dataset_id: int,
    revision: int,
    cmd: AnnotationPromptPreviewCommand,
    current_user: User,
) -> AnnotationPromptResponse:
    _, contract = _contract_for_revision(
        db,
        dataset_id,
        revision,
        current_user,
        prefer_current=True,
    )
    return _row_response(None, preview=_preview_payload(contract, cmd))


async def save_draft(
    db: Session,
    dataset_id: int,
    revision: int,
    cmd: SaveAnnotationPromptCommand,
    current_user: User,
) -> AnnotationPromptResponse:
    dataset, contract = _contract_for_revision(
        db,
        dataset_id,
        revision,
        current_user,
        manage=True,
        require_current=True,
    )
    if (
        cmd.expected_label_set_signature
        and cmd.expected_label_set_signature != contract.label_set_signature
    ):
        raise _error(
            "标签契约已变化，请刷新后重新生成提示词",
            http_status=status.HTTP_409_CONFLICT,
        )
    if (
        cmd.expected_class_order_signature
        and cmd.expected_class_order_signature != contract.class_order_signature
    ):
        raise _error(
            "标签顺序已变化，请刷新后重新生成提示词",
            http_status=status.HTTP_409_CONFLICT,
        )

    spec, generated, warnings = _build_prompt_spec(
        contract,
        operation=cmd.operation,
        provider_key=cmd.provider_key,
        model_name=cmd.model_name,
        profile_key=cmd.profile_key,
        profile_version=cmd.profile_version,
        image_width=cmd.image_width,
        image_height=cmd.image_height,
    )
    edited = cmd.edited_prompt
    effective = str(cmd.effective_prompt or edited or generated)
    _validate_effective_prompt(effective, spec)
    now = datetime.now()
    row = None
    if cmd.expected_prompt_id:
        row = (
            db.query(AnnotationPromptBinding)
            .filter(
                AnnotationPromptBinding.id == int(cmd.expected_prompt_id),
                AnnotationPromptBinding.dataset_id == int(dataset.id),
                AnnotationPromptBinding.dataset_label_contract_id == int(contract.id),
            )
            .one_or_none()
        )
        if row is None:
            raise _error("提示词草稿不存在", http_status=status.HTTP_404_NOT_FOUND)
        if str(row.status).upper() == PUBLISHED:
            raise _error(
                "已发布提示词不可直接修改，请另存为新草稿",
                http_status=status.HTTP_409_CONFLICT,
            )
    if row is None:
        row = AnnotationPromptBinding(
            dataset_id=int(dataset.id),
            dataset_label_contract_id=int(contract.id),
            contract_revision=int(contract.revision),
            created_by=current_user.id,
            created_at=now,
        )
    # The contract/row lookups above may have implicitly opened a read
    # transaction.  Start the write transaction before mutating an existing
    # ORM row: begin_transaction rolls back that read transaction, and doing
    # the assignments before it would silently discard an in-place draft edit.
    with begin_transaction(db):
        row.label_set_signature = str(contract.label_set_signature)
        row.class_order_signature = str(contract.class_order_signature)
        row.operation = str(cmd.operation)
        row.provider_key = str(cmd.provider_key)
        row.model_name = cmd.model_name
        row.profile_key = str(cmd.profile_key)
        row.profile_version = str(cmd.profile_version)
        row.compiler_version = COMPILER_VERSION
        row.prompt_spec_json = json.dumps(
            spec, ensure_ascii=False, separators=(",", ":")
        )
        row.generated_prompt = generated
        row.edited_prompt = edited
        row.effective_prompt = effective
        row.prompt_sha256 = _prompt_sha256(effective)
        row.warnings_json = json.dumps(warnings, ensure_ascii=False)
        row.status = DRAFT
        row.updated_at = now
        db.add(row)
        db.flush()
    return _row_response(row)


async def list_prompts(
    db: Session,
    dataset_id: int,
    revision: int,
    current_user: User,
) -> list[AnnotationPromptResponse]:
    _, contract = _contract_for_revision(
        db,
        dataset_id,
        revision,
        current_user,
        prefer_current=True,
    )
    rows = (
        db.query(AnnotationPromptBinding)
        .filter(
            AnnotationPromptBinding.dataset_label_contract_id == int(contract.id),
            AnnotationPromptBinding.contract_revision == int(contract.revision),
        )
        .order_by(
            AnnotationPromptBinding.updated_at.desc(), AnnotationPromptBinding.id.desc()
        )
        .all()
    )
    return [_row_response(row) for row in rows]


async def list_prompt_history(
    db: Session,
    dataset_id: int,
    current_user: User,
) -> list[AnnotationPromptResponse]:
    """List every prompt artifact for a dataset, including invalidated rows.

    The revision-scoped endpoint remains the runtime/editor API.  This
    history view deliberately does not filter by ``PUBLISHED`` or the current
    revision so operators can inspect why an older prompt stopped being
    executable after a contract change.
    """

    dataset = dataset_service._dataset_or_raise(
        db, int(dataset_id), current_user, manage=False
    )
    with begin_transaction(db):
        dataset_service.ensure_current_dataset_contract(
            db,
            dataset,
            created_by=getattr(current_user, "id", None),
        )
        db.refresh(dataset)
    rows = (
        db.query(AnnotationPromptBinding)
        .outerjoin(
            DatasetLabelContract,
            AnnotationPromptBinding.dataset_label_contract_id
            == DatasetLabelContract.id,
        )
        .filter(
            or_(
                AnnotationPromptBinding.dataset_id == int(dataset.id),
                DatasetLabelContract.dataset_id == int(dataset.id),
            )
        )
        .order_by(
            AnnotationPromptBinding.contract_revision.desc(),
            AnnotationPromptBinding.updated_at.desc(),
            AnnotationPromptBinding.id.desc(),
        )
        .all()
    )
    return [_row_response(row) for row in rows]


async def publish(
    db: Session,
    dataset_id: int,
    revision: int,
    prompt_id: int,
    cmd: PublishAnnotationPromptCommand,
    current_user: User,
) -> AnnotationPromptResponse:
    _, contract = _contract_for_revision(
        db,
        dataset_id,
        revision,
        current_user,
        manage=True,
        require_current=True,
    )
    if cmd.expected_contract_revision is not None and int(
        cmd.expected_contract_revision
    ) != int(contract.revision):
        raise _error(
            "标签契约已变化，请刷新后重试", http_status=status.HTTP_409_CONFLICT
        )
    row = (
        db.query(AnnotationPromptBinding)
        .filter(
            AnnotationPromptBinding.id == int(prompt_id),
            AnnotationPromptBinding.dataset_label_contract_id == int(contract.id),
        )
        .one_or_none()
    )
    if row is None:
        raise _error("提示词不存在", http_status=status.HTTP_404_NOT_FOUND)
    if int(row.contract_revision) != int(contract.revision):
        raise _error(
            "提示词不属于当前标签契约修订，请重新生成",
            http_status=status.HTTP_409_CONFLICT,
        )
    if str(row.operation) not in {"bbox_detection", "global_tag"}:
        raise _error(
            "提示词操作类型无效，请重新生成",
            http_status=status.HTTP_409_CONFLICT,
        )
    if (
        row.label_set_signature != contract.label_set_signature
        or row.class_order_signature != contract.class_order_signature
    ):
        raise _error(
            "提示词与当前标签契约签名不一致，请重新生成",
            http_status=status.HTTP_409_CONFLICT,
        )
    if cmd.expected_prompt_sha256 and cmd.expected_prompt_sha256 != row.prompt_sha256:
        raise _error(
            "提示词草稿已被其他人修改，请刷新后重试",
            http_status=status.HTTP_409_CONFLICT,
        )
    _validate_effective_prompt(row.effective_prompt, _json_object(row.prompt_spec_json))
    now = datetime.now()
    with begin_transaction(db):
        db.query(AnnotationPromptBinding).filter(
            AnnotationPromptBinding.dataset_label_contract_id == int(contract.id),
            AnnotationPromptBinding.contract_revision == int(contract.revision),
            AnnotationPromptBinding.operation == row.operation,
            AnnotationPromptBinding.provider_key == row.provider_key,
            AnnotationPromptBinding.model_name == row.model_name,
            AnnotationPromptBinding.status == PUBLISHED,
            AnnotationPromptBinding.id != int(row.id),
        ).update({"status": SUPERSEDED, "updated_at": now}, synchronize_session=False)
        row.status = PUBLISHED
        row.published_by = current_user.id
        row.published_at = now
        row.updated_at = now
        db.add(row)
        db.flush()
    return _row_response(row)


def resolve_published_prompt_for_task(
    db: Session,
    task: Task,
    *,
    operation: str = DEFAULT_OPERATION,
    provider_key: str = DEFAULT_PROVIDER,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a prompt only when task and dataset contract signatures match.

    ``None`` is an explicit legacy fallback signal. Callers must record it in
    pre-annotation metadata rather than silently treating the fallback as a
    contract-bound prompt.
    """

    task_contract = annotation_contract.current_contract(db, int(task.id))
    dataset_row = None
    dataset_current_contract = None
    # Always inspect the logical dataset before trusting a task's persisted
    # contract pointer. Existing tasks can retain an older pointer after a
    # taxonomy edit; refreshing here keeps runtime lookup on the latest
    # dataset vocabulary even when no UI page was opened first.
    if getattr(task, "dataset_id", None):
        from labelu.internal.domain.models.dataset import Dataset

        dataset_row = (
            db.query(Dataset)
            .filter(
                Dataset.id == int(task.dataset_id),
                Dataset.deleted_at.is_(None),
            )
            .one_or_none()
        )
        if dataset_row is not None and isinstance(db, Session):
            with begin_transaction(db):
                dataset_current_contract = dataset_service.ensure_current_dataset_contract(
                    db,
                    dataset_row,
                    created_by=None,
                )
                db.refresh(dataset_row)
            # The refresh can create a new dataset revision and invalidate the
            # old task-linked prompt. Re-read the task contract after the
            # transaction boundary so callers do not use expired ORM state.
            task_contract = annotation_contract.current_contract(db, int(task.id))

    dataset_contract_id = (
        getattr(dataset_current_contract, "id", None)
        if dataset_current_contract is not None
        else None
    ) or (
        getattr(task_contract, "dataset_label_contract_id", None)
        if task_contract is not None
        else None
    ) or getattr(task, "dataset_label_contract_id", None)
    # A dataset-bound task created during a rolling contract migration may not
    # have a materialized task contract yet. Resolve the dataset pointer so
    # the runtime still uses the governed label vocabulary.
    if dataset_contract_id is None and dataset_row is not None:
        dataset_contract_id = getattr(dataset_row, "dataset_label_contract_id", None)
    if dataset_contract_id:
        dataset_contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == int(dataset_contract_id))
            .one_or_none()
        )
        if dataset_contract is None:
            return None
        # A stale task pointer must never let a prompt from another dataset
        # satisfy the lookup.  Signature equality alone is insufficient: two
        # independent datasets can intentionally use the same label set.
        task_dataset_id = getattr(task, "dataset_id", None)
        if task_dataset_id is not None and int(dataset_contract.dataset_id) != int(
            task_dataset_id
        ):
            return None
        # A SUBSET task has a different class signature and must not reuse a
        # prompt compiled for the full dataset contract.
        if task_contract is not None and (
            str(task_contract.label_set_signature)
            != str(dataset_contract.label_set_signature)
            or str(task_contract.class_order_signature)
            != str(dataset_contract.class_order_signature)
        ):
            return None
        base_filter = [
            AnnotationPromptBinding.dataset_label_contract_id
            == int(dataset_contract.id),
            AnnotationPromptBinding.contract_revision == int(dataset_contract.revision),
        ]
        contract_id = int(dataset_contract.id)
        contract_revision = int(dataset_contract.revision)
        label_set_signature = str(dataset_contract.label_set_signature)
        class_order_signature = str(dataset_contract.class_order_signature)
    elif task_contract is not None:
        base_filter = [
            AnnotationPromptBinding.task_annotation_contract_id
            == int(task_contract.id),
            AnnotationPromptBinding.contract_revision == int(task_contract.revision),
        ]
        contract_id = int(task_contract.id)
        contract_revision = int(task_contract.revision)
        label_set_signature = str(task_contract.label_set_signature)
        class_order_signature = str(task_contract.class_order_signature)
    else:
        return None

    query = db.query(AnnotationPromptBinding).filter(
        *base_filter,
        AnnotationPromptBinding.operation == str(operation),
        AnnotationPromptBinding.provider_key == str(provider_key),
        AnnotationPromptBinding.status == PUBLISHED,
    )
    rows = query.order_by(
        AnnotationPromptBinding.updated_at.desc(), AnnotationPromptBinding.id.desc()
    ).all()
    selected = None
    for row in rows:
        if model_name and row.model_name and str(row.model_name) == str(model_name):
            selected = row
            break
        if selected is None and not row.model_name:
            selected = row
    if selected is None:
        return None
    # Treat the published row as an executable artifact, not merely a lookup
    # pointer.  A direct database edit, a truncated migration, or a stale
    # prompt_spec must fail closed before any image is sent upstream.
    selected_spec = _json_object(selected.prompt_spec_json)
    selected_prompt = str(selected.effective_prompt or "")
    if _prompt_sha256(selected_prompt) != str(selected.prompt_sha256 or ""):
        return None
    try:
        expected_contract = dataset_contract if dataset_contract_id else task_contract
        spec_matches_contract = (
            str(selected_spec.get("operation") or "") == str(operation)
            and str(selected_spec.get("label_set_signature") or "")
            == label_set_signature
            and str(selected_spec.get("class_order_signature") or "")
            == class_order_signature
            and int(selected_spec.get("contract_id") or 0) == int(contract_id)
            and int(selected_spec.get("contract_revision") or 0)
            == int(contract_revision)
        )
        if spec_matches_contract and str(operation) == DEFAULT_OPERATION:
            spec_matches_contract = _prompt_spec_label_values(
                selected_spec
            ) == _contract_label_values(expected_contract)
    except (TypeError, ValueError):
        spec_matches_contract = False
    if not spec_matches_contract:
        return None
    try:
        _validate_effective_prompt(selected_prompt, selected_spec)
    except LabelUException:
        return None
    return {
        "id": int(selected.id),
        "contract_id": contract_id,
        "contract_revision": contract_revision,
        "label_set_signature": label_set_signature,
        "class_order_signature": class_order_signature,
        "operation": str(selected.operation),
        "provider_key": str(selected.provider_key),
        "model_name": selected.model_name,
        "profile_key": str(selected.profile_key),
        "profile_version": str(selected.profile_version),
        "prompt_sha256": str(selected.prompt_sha256),
        "effective_prompt": selected_prompt,
        "prompt_spec": _json_object(selected.prompt_spec_json),
        "status": str(selected.status),
    }
