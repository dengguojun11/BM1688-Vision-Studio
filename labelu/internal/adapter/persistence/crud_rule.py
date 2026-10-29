from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern

from labelu.internal.domain.models.rule import (
    RuleBinding,
    RuleBindingAudit,
    RuleDeviceSignatureReport,
    RuleTemplate,
    RuleVersion,
    RuleVersionAudit,
)


def get_template(db: Session, template_id: int) -> RuleTemplate | None:
    return db.query(RuleTemplate).filter(RuleTemplate.id == int(template_id)).first()


def get_template_by_key(db: Session, project_id: int, key: str) -> RuleTemplate | None:
    return db.query(RuleTemplate).filter(RuleTemplate.project_id == int(project_id), RuleTemplate.template_key == key).first()


def list_templates(
    db: Session,
    project_id: int,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    query = db.query(RuleTemplate).filter(RuleTemplate.project_id == int(project_id))
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(RuleTemplate.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleTemplate.project_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleTemplate.template_key.ilike(pattern, escape=LIKE_ESCAPE),
                RuleTemplate.name.ilike(pattern, escape=LIKE_ESCAPE),
                RuleTemplate.algorithm_key.ilike(pattern, escape=LIKE_ESCAPE),
                RuleTemplate.description.ilike(pattern, escape=LIKE_ESCAPE),
                RuleTemplate.status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleTemplate.created_by, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    query = query.order_by(RuleTemplate.id.desc())
    if page is None or size is None:
        return query.all()
    page_value = max(0, int(page))
    size_value = max(1, int(size))
    total = query.order_by(None).count()
    return (
        query.offset(page_value * size_value).limit(size_value).all(),
        total,
    )


def get_version(db: Session, version_id: int) -> RuleVersion | None:
    return db.query(RuleVersion).filter(RuleVersion.id == int(version_id)).first()


def list_versions(
    db: Session,
    template_id: int,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    query = db.query(RuleVersion).filter(RuleVersion.template_id == int(template_id))
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(RuleVersion.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleVersion.template_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleVersion.version_no, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleVersion.schema_version.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersion.config_json.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersion.config_sha256.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersion.status.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersion.signing_payload_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleVersion.signature_algorithm.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersion.signature_key_id.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersion.server_signature_status.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleVersion.device_verification_status.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleVersion.created_by, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    query = query.order_by(RuleVersion.version_no.desc(), RuleVersion.id.desc())
    if page is None or size is None:
        return query.all()
    page_value = max(0, int(page))
    size_value = max(1, int(size))
    total = query.order_by(None).count()
    return (
        query.offset(page_value * size_value).limit(size_value).all(),
        total,
    )


def get_binding(db: Session, binding_id: int) -> RuleBinding | None:
    return db.query(RuleBinding).filter(RuleBinding.id == int(binding_id)).first()


def list_bindings(
    db: Session,
    project_id: int,
    device_id: str | None = None,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    query = db.query(RuleBinding).filter(RuleBinding.project_id == int(project_id))
    if device_id:
        query = query.filter(RuleBinding.device_id == device_id)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(RuleBinding.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleBinding.project_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleBinding.device_id.ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleBinding.rule_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleBinding.status.ilike(pattern, escape=LIKE_ESCAPE),
                RuleBinding.active_key.ilike(pattern, escape=LIKE_ESCAPE),
                RuleBinding.device_verification_status.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleBinding.last_device_report_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleBinding.created_by, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    query = query.order_by(RuleBinding.id.desc())
    if page is None or size is None:
        return query.all()
    page_value = max(0, int(page))
    size_value = max(1, int(size))
    total = query.order_by(None).count()
    return (
        query.offset(page_value * size_value).limit(size_value).all(),
        total,
    )


def active_for_device(db: Session, device_id: str) -> RuleBinding | None:
    return db.query(RuleBinding).filter(RuleBinding.device_id == device_id, RuleBinding.status == "ACTIVE").first()


def get_version_audit_by_key(db: Session, idempotency_key: str) -> RuleVersionAudit | None:
    return db.query(RuleVersionAudit).filter(RuleVersionAudit.idempotency_key == idempotency_key).first()


def list_binding_audits(
    db: Session,
    project_id: int,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    query = db.query(RuleBindingAudit).filter(
        RuleBindingAudit.project_id == int(project_id)
    )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(RuleBindingAudit.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleBindingAudit.binding_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleBindingAudit.project_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleBindingAudit.device_id.ilike(pattern, escape=LIKE_ESCAPE),
                RuleBindingAudit.operation.ilike(pattern, escape=LIKE_ESCAPE),
                RuleBindingAudit.from_status.ilike(pattern, escape=LIKE_ESCAPE),
                RuleBindingAudit.to_status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleBindingAudit.actor_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleBindingAudit.reason.ilike(pattern, escape=LIKE_ESCAPE),
                RuleBindingAudit.idempotency_key.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    query = query.order_by(RuleBindingAudit.id.desc())
    if page is None or size is None:
        return query.all()
    page_value = max(0, int(page))
    size_value = max(1, int(size))
    total = query.order_by(None).count()
    return (
        query.offset(page_value * size_value).limit(size_value).all(),
        total,
    )


def list_version_audits(
    db: Session,
    project_id: int,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    query = db.query(RuleVersionAudit).filter(
        RuleVersionAudit.project_id == int(project_id)
    )
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(RuleVersionAudit.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleVersionAudit.version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleVersionAudit.template_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleVersionAudit.project_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleVersionAudit.version_no, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleVersionAudit.operation.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersionAudit.from_status.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersionAudit.to_status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(RuleVersionAudit.actor_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleVersionAudit.reason.ilike(pattern, escape=LIKE_ESCAPE),
                RuleVersionAudit.idempotency_key.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    query = query.order_by(RuleVersionAudit.id.desc())
    if page is None or size is None:
        return query.all()
    page_value = max(0, int(page))
    size_value = max(1, int(size))
    total = query.order_by(None).count()
    return (
        query.offset(page_value * size_value).limit(size_value).all(),
        total,
    )


def get_device_signature_report_by_report_id(
    db: Session, report_id: str
) -> RuleDeviceSignatureReport | None:
    return (
        db.query(RuleDeviceSignatureReport)
        .filter(RuleDeviceSignatureReport.report_id == report_id)
        .first()
    )


def list_device_signature_reports(
    db: Session,
    project_id: int,
    device_id: str | None = None,
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
):
    query = db.query(RuleDeviceSignatureReport).filter(
        RuleDeviceSignatureReport.project_id == int(project_id)
    )
    if device_id:
        query = query.filter(RuleDeviceSignatureReport.device_id == device_id)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(RuleDeviceSignatureReport.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.report_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleDeviceSignatureReport.sequence, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleDeviceSignatureReport.project_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.device_id.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleDeviceSignatureReport.rule_binding_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(RuleDeviceSignatureReport.rule_version_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.payload_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.key_id.ilike(pattern, escape=LIKE_ESCAPE),
                RuleDeviceSignatureReport.key_state.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.reported_status.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.effective_status.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.failure_code.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                RuleDeviceSignatureReport.reason.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    query = query.order_by(RuleDeviceSignatureReport.id.desc())
    if page is None or size is None:
        return query.all()
    page_value = max(0, int(page))
    size_value = max(1, int(size))
    total = query.order_by(None).count()
    return (
        query.offset(page_value * size_value).limit(size_value).all(),
        total,
    )
