from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Real
from typing import Any

from fastapi import status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_algorithm, crud_project, crud_rule
from labelu.internal.application.command.rule import (
    BindRuleCommand,
    CreateRuleTemplateCommand,
    CreateRuleVersionCommand,
    ReportRuleSignatureCommand,
    RuleSignatureCommand,
    RuleTransitionCommand,
)
from labelu.internal.application.response.rule import (
    RuleAuditResponse,
    RuleBindingResponse,
    RuleDeviceSignatureReportResponse,
    RuleTemplateResponse,
    RuleVersionAuditResponse,
    RuleVersionResponse,
)
from labelu.internal.common.algorithm_registry import algorithm_spec
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.phase_one_contract import (
    ensure_legacy_control_plane_retired,
)
from labelu.internal.common.config import settings
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.algorithm import AlgorithmDefinition
from labelu.internal.domain.models.edge_agent import EdgeAgent
from labelu.internal.domain.models.rule import (
    RuleBinding,
    RuleBindingAudit,
    RuleDeviceSignatureReport,
    RuleTemplate,
    RuleVersion,
    RuleVersionAudit,
)
from labelu.internal.domain.models.user import User


def _error(
    message: str,
    code=ErrorCode.CODE_30003_CLIENT_ERROR,
    http_status=status.HTTP_404_NOT_FOUND,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _project_access(db: Session, project_id: int, user: User, *, manage: bool = False):
    project = crud_project.get(db, project_id)
    if project is None:
        raise _error("项目不存在")
    if is_task_superuser(user) or project.owner_id == user.id:
        return project
    member = crud_project.get_member(db, project_id, user.id)
    if not member or member.status != "ACTIVE" or (manage and member.role != "admin"):
        raise _error(
            "无权访问该项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return project


def _template_payload(row: RuleTemplate) -> RuleTemplateResponse:
    return RuleTemplateResponse(
        id=row.id,
        project_id=row.project_id,
        template_key=row.template_key,
        name=row.name,
        algorithm_key=row.algorithm_key,
        description=row.description,
        status=row.status,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _version_payload(row: RuleVersion) -> RuleVersionResponse:
    return RuleVersionResponse(
        id=row.id,
        template_id=row.template_id,
        version_no=row.version_no,
        schema_version=row.schema_version,
        config=_loads(row.config_json),
        config_sha256=row.config_sha256,
        status=row.status,
        created_by=row.created_by,
        signing_payload_sha256=row.signing_payload_sha256,
        signature_algorithm=row.signature_algorithm,
        signature_key_id=row.signature_key_id,
        server_signature_status=row.server_signature_status or "NOT_PROVIDED",
        signature_verification_scope="SERVER_ONLY",
        device_verification_status=row.device_verification_status or "UNVERIFIED",
        signature_verified_at=row.signature_verified_at,
        created_at=row.created_at,
    )


def _binding_payload(row: RuleBinding) -> RuleBindingResponse:
    return RuleBindingResponse(
        id=row.id,
        project_id=row.project_id,
        device_id=row.device_id,
        rule_version_id=row.rule_version_id,
        status=row.status,
        created_by=row.created_by,
        device_verification_status=row.device_verification_status or "UNVERIFIED",
        device_report_sequence=row.device_report_sequence or 0,
        last_device_report_id=row.last_device_report_id,
        device_verified_at=row.device_verified_at,
        updated_at=row.updated_at,
        created_at=row.created_at,
    )


def _validate_config(algorithm_key: str, config: dict[str, Any]) -> None:
    spec = algorithm_spec(algorithm_key)
    if spec is None or spec.algorithm_kind != "rule_template":
        raise _error(
            "算法不是规则模板",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if not isinstance(config, dict):
        raise _error(
            "规则配置必须是对象",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    schema = spec.rule_schema or {}
    missing = [key for key in schema.get("requires", []) if not config.get(key)]
    if missing:
        raise _error(
            "规则配置缺少必填字段: " + ",".join(missing),
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    json_schema = schema.get("json_schema")
    if isinstance(json_schema, dict):
        try:
            _validate_json_schema(config, json_schema)
        except ValueError as exc:
            raise _error(
                f"规则配置 Schema 校验失败: {exc}",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            ) from exc


def _validate_json_schema(
    value: Any, schema: dict[str, Any], path: str = "config"
) -> None:
    """Validate the small JSON-Schema subset used by Registry rule schemas.

    Keeping this evaluator local avoids making ``jsonschema`` a mandatory
    runtime dependency while still enforcing the Registry's typed contract.
    """

    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list):
        matches = 0
        errors: list[str] = []
        for alternative in alternatives:
            try:
                _validate_json_schema(value, alternative, path)
                matches += 1
            except ValueError as exc:
                errors.append(str(exc))
        if matches != 1:
            raise ValueError(f"{path} 不匹配唯一 Schema 分支")
        return

    expected = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, Real) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    if expected in type_matches and not type_matches[expected]:
        raise ValueError(f"{path} 必须是 {expected}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path} 缺少字段: {','.join(map(str, missing))}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                raise ValueError(
                    f"{path} 包含未声明字段: {','.join(map(str, unknown))}"
                )
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                _validate_json_schema(value[key], child_schema, f"{path}.{key}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValueError(f"{path} 至少需要 {schema['minItems']} 项")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"{path} 最多允许 {schema['maxItems']} 项")
        child_schema = schema.get("items")
        if isinstance(child_schema, dict):
            for index, item in enumerate(value):
                _validate_json_schema(item, child_schema, f"{path}[{index}]")
    elif isinstance(value, str) and "minLength" in schema:
        if len(value) < int(schema["minLength"]):
            raise ValueError(f"{path} 不能为空")
    elif isinstance(value, Real) and not isinstance(value, bool):
        if "exclusiveMinimum" in schema and not float(value) > float(
            schema["exclusiveMinimum"]
        ):
            raise ValueError(f"{path} 必须大于 {schema['exclusiveMinimum']}")


def rule_signing_payload_sha256(
    *,
    project_id: int,
    template_id: int,
    version_no: int,
    schema_version: str,
    config_sha256: str,
) -> str:
    """Return the digest clients sign for the server-side rule contract.

    The signature is HMAC-SHA256 over this lowercase digest's ASCII bytes.
    Device-side signature verification is a separate HIL boundary.
    """

    canonical = json.dumps(
        {
            "contract": "7gai.rule-version.v1",
            "project_id": int(project_id),
            "template_id": int(template_id),
            "version_no": int(version_no),
            "schema_version": str(schema_version),
            "config_sha256": str(config_sha256).lower(),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class RuleSigningKey:
    secret: str
    status: str
    not_after: datetime | None = None
    rotation_grace_until: datetime | None = None


def _parse_key_time(value: Any, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise _error(
            f"规则签名密钥 {field_name} 必须是 ISO-8601 时间字符串",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error(
            f"规则签名密钥 {field_name} 不是合法 ISO-8601 时间",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rule_signing_keys() -> dict[str, RuleSigningKey]:
    raw = str(getattr(settings, "SEVEN_GAI_RULE_SIGNING_KEYS_JSON", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error(
            "规则签名 key map 配置不是合法 JSON",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    if not isinstance(parsed, dict):
        raise _error(
            "规则签名 key map 必须是 key_id 到密钥配置的对象映射",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    keys: dict[str, RuleSigningKey] = {}
    valid_states = {"ACTIVE", "ROTATING", "REVOKED", "EXPIRED"}
    for raw_key_id, value in parsed.items():
        if not isinstance(raw_key_id, str) or not raw_key_id.strip():
            raise _error(
                "规则签名 key_id 必须是非空字符串",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        key_id = raw_key_id.strip()
        if isinstance(value, str):
            secret = value
            key_state = "ACTIVE"
            not_after = None
            grace_until = None
        elif isinstance(value, dict):
            secret = value.get("secret")
            key_state = str(value.get("status") or "ACTIVE").strip().upper()
            not_after = _parse_key_time(value.get("not_after"), "not_after")
            grace_until = _parse_key_time(
                value.get("rotation_grace_until"), "rotation_grace_until"
            )
        else:
            secret = None
            key_state = ""
            not_after = None
            grace_until = None
        if not isinstance(secret, str) or not secret:
            raise _error(
                f"规则签名密钥 {key_id} 缺少非空 secret",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if key_state not in valid_states:
            raise _error(
                f"规则签名密钥 {key_id} 状态必须是 ACTIVE/ROTATING/REVOKED/EXPIRED",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if key_state == "ROTATING" and grace_until is None and not_after is None:
            raise _error(
                f"规则签名密钥 {key_id} 处于 ROTATING 时必须配置 rotation_grace_until 或 not_after",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        keys[key_id] = RuleSigningKey(
            secret=secret,
            status=key_state,
            not_after=not_after,
            rotation_grace_until=grace_until,
        )
    return keys


def _effective_key_state(key: RuleSigningKey | None, now: datetime) -> str:
    if key is None:
        return "EXPIRED"
    if key.status == "ACTIVE" and key.not_after is not None and now > key.not_after:
        return "EXPIRED"
    if key.status == "ROTATING":
        deadline = key.rotation_grace_until or key.not_after
        if deadline is None or now > deadline:
            return "EXPIRED"
    return key.status


def _device_key_state(
    key: RuleSigningKey | None, *, received_at: datetime, reported_at: datetime | None
) -> str:
    """Resolve device evidence against receipt time and rotation grace semantics."""
    if key is None:
        return "EXPIRED"
    if key.status == "ROTATING":
        deadline = key.rotation_grace_until or key.not_after
        device_time = reported_at or received_at
        if device_time.tzinfo is None:
            device_time = device_time.replace(tzinfo=timezone.utc)
        if deadline is not None and (
            received_at > deadline or device_time.astimezone(timezone.utc) > deadline
        ):
            return "EXPIRED"
        return "ROTATING"
    return _effective_key_state(key, received_at)


def _verified_signature_values(
    version: RuleVersion,
    template: RuleTemplate,
    signature: RuleSignatureCommand | None,
) -> dict[str, Any]:
    payload_sha256 = rule_signing_payload_sha256(
        project_id=template.project_id,
        template_id=template.id,
        version_no=version.version_no,
        schema_version=version.schema_version,
        config_sha256=version.config_sha256,
    )
    signature_required = bool(
        getattr(settings, "SEVEN_GAI_RULE_REQUIRE_SIGNATURE", False)
    )
    if signature is None:
        if signature_required:
            raise _error(
                "规则发布签名缺失",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        return {
            "signing_payload_sha256": payload_sha256,
            "server_signature_status": "NOT_PROVIDED",
            "device_verification_status": "UNVERIFIED",
        }
    if signature.payload_sha256.lower() != payload_sha256:
        raise _error(
            "规则签名 payload_sha256 与当前不可变版本不一致",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    keys = _rule_signing_keys()
    key = keys.get(signature.key_id)
    if key is None:
        raise _error(
            "规则签名 key_id 未配置",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if _effective_key_state(key, datetime.now(timezone.utc)) != "ACTIVE":
        raise _error(
            "规则发布仅允许使用 ACTIVE 签名密钥",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    expected = hmac.new(
        key.secret.encode("utf-8"), payload_sha256.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature.signature.lower(), expected):
        raise _error(
            "规则发布签名校验失败",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    return {
        "signing_payload_sha256": payload_sha256,
        "signature_algorithm": signature.algorithm,
        "signature_key_id": signature.key_id,
        "signature": signature.signature.lower(),
        "server_signature_status": "VERIFIED",
        # This service has no evidence that the target device performed the
        # same verification. HIL/Agent reporting must advance this separately.
        "device_verification_status": "UNVERIFIED",
        "signature_verified_at": datetime.now(timezone.utc),
    }


def _binding_agent(db: Session, project_id: int, device_id: str) -> EdgeAgent:
    agent = db.query(EdgeAgent).filter(EdgeAgent.agent_id == device_id).first()
    if agent is None:
        raise _error(
            "设备不存在", ErrorCode.CODE_30003_CLIENT_ERROR, status.HTTP_404_NOT_FOUND
        )
    if agent.project_id is None or int(agent.project_id) != int(project_id):
        raise _error(
            "设备不属于当前项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    agent_status = str(agent.status or "").strip().upper()
    if agent.revoked_at is not None or agent_status in {
        "DISABLED",
        "DELETED",
        "REVOKED",
    }:
        raise _error(
            f"设备状态 {agent_status or 'UNKNOWN'} 不允许绑定或回滚规则",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    return agent


async def create_template(
    db: Session, cmd: CreateRuleTemplateCommand, user: User
) -> RuleTemplateResponse:
    ensure_legacy_control_plane_retired()
    _project_access(db, cmd.project_id, user, manage=True)
    definition = crud_algorithm.get_definition(db, cmd.algorithm_key)
    if definition is None or (definition.algorithm_kind or "") != "rule_template":
        raise _error(
            "只能选择 Registry 中的 rule_template",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if crud_rule.get_template_by_key(db, cmd.project_id, cmd.template_key):
        raise _error(
            "规则模板 key 已存在",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    with begin_transaction(db):
        row = RuleTemplate(
            project_id=cmd.project_id,
            template_key=cmd.template_key,
            name=cmd.name,
            algorithm_key=cmd.algorithm_key,
            description=cmd.description,
            created_by=user.id,
        )
        db.add(row)
        db.flush()
    return _template_payload(row)


async def list_templates(
    db: Session,
    project_id: int,
    user: User,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    _project_access(db, project_id, user)
    result = crud_rule.list_templates(
        db, project_id, search=search, page=page, size=size
    )
    if page is None or size is None:
        return [_template_payload(row) for row in result]
    rows, total = result
    return [_template_payload(row) for row in rows], total


async def create_version(
    db: Session, template_id: int, cmd: CreateRuleVersionCommand, user: User
) -> RuleVersionResponse:
    ensure_legacy_control_plane_retired()
    template = crud_rule.get_template(db, template_id)
    if template is None:
        raise _error("规则模板不存在")
    _project_access(db, template.project_id, user, manage=True)
    _validate_config(template.algorithm_key, cmd.config)
    latest = crud_rule.list_versions(db, template_id)
    payload = json.dumps(
        cmd.config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    version_no = latest[0].version_no + 1 if latest else 1
    config_sha256 = hashlib.sha256(payload.encode()).hexdigest()
    signing_payload_sha256 = rule_signing_payload_sha256(
        project_id=template.project_id,
        template_id=template.id,
        version_no=version_no,
        schema_version=cmd.schema_version,
        config_sha256=config_sha256,
    )
    with begin_transaction(db):
        row = RuleVersion(
            template_id=template.id,
            version_no=version_no,
            schema_version=cmd.schema_version,
            config_json=payload,
            config_sha256=config_sha256,
            signing_payload_sha256=signing_payload_sha256,
            server_signature_status="NOT_PROVIDED",
            device_verification_status="UNVERIFIED",
            created_by=user.id,
        )
        db.add(row)
        db.flush()
    return _version_payload(row)


async def list_versions(
    db: Session,
    template_id: int,
    user: User,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    template = crud_rule.get_template(db, template_id)
    if template is None:
        raise _error("规则模板不存在")
    _project_access(db, template.project_id, user)
    result = crud_rule.list_versions(
        db, template_id, search=search, page=page, size=size
    )
    if page is None or size is None:
        return [_version_payload(row) for row in result]
    rows, total = result
    return [_version_payload(row) for row in rows], total


async def publish_version(
    db: Session, version_id: int, cmd: RuleTransitionCommand, user: User
) -> RuleVersionResponse:
    ensure_legacy_control_plane_retired()
    version = crud_rule.get_version(db, version_id)
    if version is None:
        raise _error("规则版本不存在")
    template = crud_rule.get_template(db, version.template_id)
    if template is None:
        raise _error("规则模板不存在")
    _project_access(db, template.project_id, user, manage=True)
    if cmd.idempotency_key:
        replay = crud_rule.get_version_audit_by_key(db, cmd.idempotency_key)
        if replay is not None:
            if (
                replay.version_id != version.id
                or replay.project_id != template.project_id
                or replay.operation != "publish"
            ):
                raise _error(
                    "幂等键已用于其他规则发布操作",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            return _version_payload(version)
    if version.status == "ACTIVE":
        return _version_payload(version)
    if version.status != "DRAFT":
        raise _error(
            "规则版本当前状态不可发布",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    old_status = version.status
    signature_values = _verified_signature_values(version, template, cmd.signature)
    with begin_transaction(db):
        # Compare-and-swap prevents two publishers from both observing DRAFT
        # and overwriting the version/audit history concurrently.
        changed = db.execute(
            update(RuleVersion)
            .where(RuleVersion.id == int(version.id), RuleVersion.status == "DRAFT")
            .values(status="ACTIVE", **signature_values)
        )
        if changed.rowcount != 1:
            raise _error(
                "规则版本已被其他请求发布或状态已变化",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        db.add(
            RuleVersionAudit(
                version_id=version.id,
                template_id=template.id,
                project_id=template.project_id,
                version_no=version.version_no,
                operation="publish",
                from_status=old_status,
                to_status="ACTIVE",
                actor_id=user.id,
                reason=cmd.reason,
                idempotency_key=cmd.idempotency_key,
            )
        )
        db.flush()
        db.refresh(version)
    return _version_payload(version)


def _audit_payload(row: RuleBindingAudit) -> RuleAuditResponse:
    return RuleAuditResponse(
        id=row.id,
        binding_id=row.binding_id,
        project_id=row.project_id,
        device_id=row.device_id,
        operation=row.operation,
        from_status=row.from_status,
        to_status=row.to_status,
        actor_id=row.actor_id,
        reason=row.reason,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
    )


async def bind(
    db: Session,
    project_id: int,
    device_id: str,
    cmd: BindRuleCommand,
    user: User,
    *,
    operation: str = "bind",
) -> RuleBindingResponse:
    ensure_legacy_control_plane_retired()
    _project_access(db, project_id, user, manage=True)
    _binding_agent(db, project_id, device_id)
    version = crud_rule.get_version(db, cmd.rule_version_id)
    if version is None or version.status != "ACTIVE":
        raise _error(
            "规则版本未发布",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    template = crud_rule.get_template(db, version.template_id)
    if template is None or template.project_id != project_id:
        raise _error(
            "规则版本不属于当前项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    existing = crud_rule.active_for_device(db, device_id)
    if existing is not None and existing.project_id != project_id:
        raise _error(
            "设备已有其他项目的 ACTIVE 规则",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_409_CONFLICT,
        )
    if cmd.idempotency_key:
        replay = (
            db.query(RuleBindingAudit)
            .filter(RuleBindingAudit.idempotency_key == cmd.idempotency_key)
            .first()
        )
        if replay is not None:
            if (
                replay.project_id != project_id
                or replay.device_id != device_id
                or replay.operation != operation
            ):
                raise _error(
                    "幂等键已用于其他规则操作",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            replay_binding = crud_rule.get_binding(db, replay.binding_id)
            if replay_binding is None or int(replay_binding.rule_version_id) != int(
                cmd.rule_version_id
            ):
                raise _error(
                    "幂等键对应的规则版本不一致",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            return _binding_payload(replay_binding)
    old_status = existing.status if existing else None
    existing_id = existing.id if existing else None
    try:
        with begin_transaction(db):
            if existing_id is not None:
                changed = db.execute(
                    update(RuleBinding)
                    .where(
                        RuleBinding.id == int(existing_id),
                        RuleBinding.status == "ACTIVE",
                        RuleBinding.active_key == device_id,
                    )
                    .values(status="INACTIVE", active_key=None)
                )
                if changed.rowcount != 1:
                    raise _error(
                        "设备规则绑定已被其他请求修改",
                        ErrorCode.CODE_30002_VALIDATION_ERROR,
                        status.HTTP_409_CONFLICT,
                    )
            row = RuleBinding(
                project_id=project_id,
                device_id=device_id,
                rule_version_id=version.id,
                status="ACTIVE",
                active_key=device_id,
                created_by=user.id,
            )
            db.add(row)
            db.flush()
            audit = RuleBindingAudit(
                binding_id=row.id,
                project_id=project_id,
                device_id=device_id,
                operation=operation,
                from_status=old_status,
                to_status="ACTIVE",
                actor_id=user.id,
                reason=cmd.reason,
                idempotency_key=cmd.idempotency_key,
            )
            db.add(audit)
    except IntegrityError as exc:
        db.rollback()
        if cmd.idempotency_key:
            replay = (
                db.query(RuleBindingAudit)
                .filter(RuleBindingAudit.idempotency_key == cmd.idempotency_key)
                .first()
            )
            if replay is not None:
                replay_binding = crud_rule.get_binding(db, replay.binding_id)
                if (
                    replay.project_id == project_id
                    and replay.device_id == device_id
                    and replay.operation == operation
                    and replay_binding is not None
                    and int(replay_binding.rule_version_id) == int(cmd.rule_version_id)
                ):
                    return _binding_payload(replay_binding)
        raise _error(
            "设备规则绑定发生并发冲突",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        ) from exc
    return _binding_payload(row)


def _device_report_payload(
    row: RuleDeviceSignatureReport,
) -> RuleDeviceSignatureReportResponse:
    return RuleDeviceSignatureReportResponse(
        id=row.id,
        report_id=row.report_id,
        sequence=row.sequence,
        project_id=row.project_id,
        device_id=row.device_id,
        rule_binding_id=row.rule_binding_id,
        rule_version_id=row.rule_version_id,
        payload_sha256=row.payload_sha256,
        key_id=row.key_id,
        key_state=row.key_state,
        reported_status=row.reported_status,
        effective_status=row.effective_status,
        failure_code=row.failure_code,
        reason=row.reason,
        reported_at=row.reported_at,
        received_at=row.received_at,
    )


def _same_device_report(
    row: RuleDeviceSignatureReport,
    *,
    agent: EdgeAgent,
    binding_id: int,
    cmd: ReportRuleSignatureCommand,
) -> bool:
    row_reported_at = row.reported_at
    cmd_reported_at = cmd.reported_at
    if row_reported_at is not None and row_reported_at.tzinfo is None:
        row_reported_at = row_reported_at.replace(tzinfo=timezone.utc)
    if cmd_reported_at is not None and cmd_reported_at.tzinfo is None:
        cmd_reported_at = cmd_reported_at.replace(tzinfo=timezone.utc)
    return (
        row.project_id == cmd.project_id
        and row.device_id == agent.agent_id
        and row.rule_binding_id == int(binding_id)
        and row.rule_version_id == cmd.rule_version_id
        and row.payload_sha256 == cmd.payload_sha256.lower()
        and row.key_id == cmd.key_id
        and row.sequence == cmd.sequence
        and row.reported_status == cmd.reported_status
        and row.failure_code == cmd.failure_code
        and row.reason == cmd.reason
        and row_reported_at == cmd_reported_at
    )


def _effective_device_report_status(reported_status: str, key_state: str) -> str:
    if reported_status == "REJECTED":
        return "REJECTED_DEVICE"
    if key_state == "ACTIVE":
        return "VERIFIED"
    if key_state == "ROTATING":
        return "VERIFIED_ROTATING_KEY"
    if key_state == "REVOKED":
        return "REJECTED_KEY_REVOKED"
    return "REJECTED_KEY_EXPIRED"


async def report_device_signature(
    db: Session,
    agent: EdgeAgent,
    binding_id: int,
    cmd: ReportRuleSignatureCommand,
) -> RuleDeviceSignatureReportResponse:
    ensure_legacy_control_plane_retired()
    agent_status = str(agent.status or "").strip().upper()
    if agent.revoked_at is not None or agent_status in {
        "DISABLED",
        "DELETED",
        "REVOKED",
    }:
        raise _error(
            "设备当前状态不允许回报规则验签结果",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    if agent.project_id is None or int(agent.project_id) != int(cmd.project_id):
        raise _error(
            "设备不属于回报中的项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )

    replay = crud_rule.get_device_signature_report_by_report_id(db, cmd.report_id)
    if replay is not None:
        if _same_device_report(replay, agent=agent, binding_id=binding_id, cmd=cmd):
            return _device_report_payload(replay)
        raise _error(
            "report_id 已用于其他设备验签回报",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )

    binding = crud_rule.get_binding(db, binding_id)
    if binding is None:
        raise _error("规则绑定不存在")
    if binding.project_id != cmd.project_id or binding.device_id != agent.agent_id:
        raise _error(
            "规则绑定不属于当前设备或项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    if binding.status != "ACTIVE" or binding.active_key != binding.device_id:
        raise _error(
            "只能回报当前 ACTIVE 规则绑定",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if int(binding.rule_version_id) != int(cmd.rule_version_id):
        raise _error(
            "回报规则版本不是设备当前绑定版本",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    version = crud_rule.get_version(db, cmd.rule_version_id)
    if (
        version is None
        or version.status != "ACTIVE"
        or version.server_signature_status != "VERIFIED"
        or not version.signing_payload_sha256
        or not version.signature_algorithm
        or not version.signature_key_id
        or not version.signature
    ):
        raise _error(
            "当前规则版本没有可供设备确认的服务端签名",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    template = crud_rule.get_template(db, version.template_id)
    if template is None or int(template.project_id) != int(cmd.project_id):
        raise _error(
            "回报规则版本不属于当前项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    if version.signing_payload_sha256 != cmd.payload_sha256.lower():
        raise _error(
            "回报 payload_sha256 与当前不可变规则版本不一致",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if version.signature_key_id != cmd.key_id:
        raise _error(
            "回报 key_id 与当前规则版本不一致",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )

    current_sequence = int(binding.device_report_sequence or 0)
    if cmd.sequence != current_sequence + 1:
        raise _error(
            "设备验签回报 sequence 必须严格递增且不能跳号",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    now = datetime.now(timezone.utc)
    key = _rule_signing_keys().get(cmd.key_id)
    key_state = _device_key_state(key, received_at=now, reported_at=cmd.reported_at)
    effective_status = _effective_device_report_status(cmd.reported_status, key_state)
    verified_at = now if effective_status.startswith("VERIFIED") else None

    binding_pk = int(binding.id)
    binding_version_id = int(binding.rule_version_id)
    project_id = int(binding.project_id)
    device_id = str(binding.device_id)
    try:
        with begin_transaction(db):
            changed = db.execute(
                update(RuleBinding)
                .where(
                    RuleBinding.id == binding_pk,
                    RuleBinding.project_id == project_id,
                    RuleBinding.device_id == device_id,
                    RuleBinding.rule_version_id == binding_version_id,
                    RuleBinding.status == "ACTIVE",
                    RuleBinding.active_key == device_id,
                    RuleBinding.device_report_sequence == current_sequence,
                )
                .values(
                    device_verification_status=effective_status,
                    device_report_sequence=cmd.sequence,
                    last_device_report_id=cmd.report_id,
                    device_verified_at=verified_at,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise _error(
                    "规则绑定已被其他请求修改",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            row = RuleDeviceSignatureReport(
                report_id=cmd.report_id,
                sequence=cmd.sequence,
                project_id=project_id,
                device_id=device_id,
                rule_binding_id=binding_pk,
                rule_version_id=binding_version_id,
                payload_sha256=cmd.payload_sha256.lower(),
                key_id=cmd.key_id,
                key_state=key_state,
                reported_status=cmd.reported_status,
                effective_status=effective_status,
                failure_code=cmd.failure_code,
                reason=cmd.reason,
                reported_at=cmd.reported_at,
                received_at=now,
            )
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        replay = crud_rule.get_device_signature_report_by_report_id(db, cmd.report_id)
        if replay is not None and _same_device_report(
            replay, agent=agent, binding_id=binding_id, cmd=cmd
        ):
            return _device_report_payload(replay)
        raise _error(
            "设备验签回报发生并发或重放冲突",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        ) from exc
    return _device_report_payload(row)


async def list_device_signature_reports(
    db: Session,
    project_id: int,
    device_id: str | None,
    user: User,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    _project_access(db, project_id, user)
    result = crud_rule.list_device_signature_reports(
        db,
        project_id,
        device_id,
        search=search,
        page=page,
        size=size,
    )
    if page is None or size is None:
        return [_device_report_payload(row) for row in result]
    rows, total = result
    return [_device_report_payload(row) for row in rows], total


async def rollback(
    db: Session, project_id: int, device_id: str, cmd: BindRuleCommand, user: User
) -> RuleBindingResponse:
    ensure_legacy_control_plane_retired()
    return await bind(db, project_id, device_id, cmd, user, operation="rollback")


async def unbind(
    db: Session, binding_id: int, cmd: RuleTransitionCommand, user: User
) -> RuleBindingResponse:
    ensure_legacy_control_plane_retired()
    row = crud_rule.get_binding(db, binding_id)
    if row is None:
        raise _error("规则绑定不存在")
    _project_access(db, row.project_id, user, manage=True)
    if cmd.idempotency_key:
        replay = (
            db.query(RuleBindingAudit)
            .filter(RuleBindingAudit.idempotency_key == cmd.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.binding_id != row.id or replay.operation != "unbind":
                raise _error(
                    "幂等键已用于其他规则操作",
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            return _binding_payload(row)
    if row.status == "INACTIVE":
        return _binding_payload(row)
    with begin_transaction(db):
        old = row.status
        row.status = "INACTIVE"
        row.active_key = None
        db.add(
            RuleBindingAudit(
                binding_id=row.id,
                project_id=row.project_id,
                device_id=row.device_id,
                operation="unbind",
                from_status=old,
                to_status="INACTIVE",
                actor_id=user.id,
                reason=cmd.reason,
                idempotency_key=cmd.idempotency_key,
            )
        )
    return _binding_payload(row)


async def list_bindings(
    db: Session,
    project_id: int,
    device_id: str | None,
    user: User,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    _project_access(db, project_id, user)
    result = crud_rule.list_bindings(
        db,
        project_id,
        device_id,
        search=search,
        page=page,
        size=size,
    )
    if page is None or size is None:
        return [_binding_payload(row) for row in result]
    rows, total = result
    return [_binding_payload(row) for row in rows], total


async def list_audits(
    db: Session,
    project_id: int,
    user: User,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    _project_access(db, project_id, user)
    result = crud_rule.list_binding_audits(
        db, project_id, search=search, page=page, size=size
    )
    if page is None or size is None:
        return [_audit_payload(row) for row in result]
    rows, total = result
    return [_audit_payload(row) for row in rows], total


def _version_audit_payload(row: RuleVersionAudit) -> RuleVersionAuditResponse:
    return RuleVersionAuditResponse(
        id=row.id,
        version_id=row.version_id,
        template_id=row.template_id,
        project_id=row.project_id,
        version_no=row.version_no,
        operation=row.operation,
        from_status=row.from_status,
        to_status=row.to_status,
        actor_id=row.actor_id,
        reason=row.reason,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
    )


async def list_version_audits(
    db: Session,
    project_id: int,
    user: User,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    _project_access(db, project_id, user)
    result = crud_rule.list_version_audits(
        db, project_id, search=search, page=page, size=size
    )
    if page is None or size is None:
        return [_version_audit_payload(row) for row in result]
    rows, total = result
    return [_version_audit_payload(row) for row in rows], total
