import asyncio
import hashlib
import hmac
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from labelu.internal.application.command.rule import BindRuleCommand, CreateRuleTemplateCommand, CreateRuleVersionCommand, ReportRuleSignatureCommand, RuleTransitionCommand
from labelu.internal.application.service import algorithm as algorithm_service
from labelu.internal.application.service import rule as service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.rule_coordinates import (
    denormalize_rule_coordinates,
    normalize_rule_coordinates,
)
from labelu.internal.domain.models.project import Project, ProjectMember
from labelu.internal.domain.models.edge_agent import EdgeAgent
from labelu.internal.domain.models.rule import RuleBinding, RuleDeviceSignatureReport, RuleVersion, RuleVersionAudit
from labelu.internal.domain.models.user import User


# 规则中心依赖项目、设备发布和设备验签控制面，均已从一期主链退休。
# 该完整行为套件保留给二期，避免把历史项目维度重新当作一期验收条件。
pytestmark = pytest.mark.skip(
    reason="一期不开放项目绑定、规则发布和设备验签；二期隔离套件"
)


def _project(db, owner_id: int, name: str):
    row = Project(owner_id=owner_id, name=name, status="ACTIVE", created_by=owner_id, updated_by=owner_id)
    db.add(row); db.flush()
    db.add(ProjectMember(project_id=row.id, user_id=owner_id, role="owner", status="ACTIVE")); db.flush()
    db.commit()
    return row


def _agent(db, project_id: int, device_id: str, *, status: str = "REGISTERED"):
    db.add(
        EdgeAgent(
            agent_id=device_id,
            project_id=project_id,
            name=device_id,
            token_hash="x" * 64,
            status=status,
        )
    )
    db.commit()


def _signed_binding(db, monkeypatch, *, suffix: str = "device-report"):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, f"rules-{suffix}")
    device_id = f"{suffix}-agent"
    _agent(db, project.id, device_id)
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key=suffix,
                name="设备验签规则",
                algorithm_key="person_intrusion",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 2}
            ),
            user,
        )
    )
    monkeypatch.setattr(
        service.settings,
        "SEVEN_GAI_RULE_SIGNING_KEYS_JSON",
        '{"rule-key-1":"rule-test-secret"}',
    )
    signature = hmac.new(
        b"rule-test-secret",
        version.signing_payload_sha256.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    version = asyncio.run(
        service.publish_version(
            db,
            version.id,
            RuleTransitionCommand(
                signature={
                    "key_id": "rule-key-1",
                    "payload_sha256": version.signing_payload_sha256,
                    "signature": signature,
                }
            ),
            user,
        )
    )
    binding = asyncio.run(
        service.bind(
            db,
            project.id,
            device_id,
            BindRuleCommand(rule_version_id=version.id),
            user,
        )
    )
    return user, project, db.get(EdgeAgent, device_id), version, binding


def test_rule_center_validates_schema_and_isolates_projects(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-a")
    other = _project(db, user.id, "rules-b")
    _agent(db, project.id, "device-1")
    template = asyncio.run(service.create_template(db, CreateRuleTemplateCommand(project_id=project.id, template_key="intrusion", name="闯入", algorithm_key="person_intrusion"), user))
    with pytest.raises(Exception):
        asyncio.run(service.create_version(db, template.id, CreateRuleVersionCommand(config={"region_or_line": "r"}), user))
    version = asyncio.run(service.create_version(db, template.id, CreateRuleVersionCommand(config={"region_or_line": "r", "time_window": 3}), user))
    assert version.config_sha256
    published = asyncio.run(service.publish_version(db, version.id, RuleTransitionCommand(idempotency_key="publish-1", reason="首次发布"), user))
    assert published.status == "ACTIVE"
    replay = asyncio.run(service.publish_version(db, version.id, RuleTransitionCommand(idempotency_key="publish-1"), user))
    assert replay.id == published.id
    version_audits = db.query(RuleVersionAudit).filter(RuleVersionAudit.version_id == version.id).all()
    assert len(version_audits) == 1
    assert version_audits[0].from_status == "DRAFT"
    assert version_audits[0].to_status == "ACTIVE"
    with pytest.raises(ValueError):
        version_audits[0].reason = "tampered"
        db.flush()
    db.rollback()
    with pytest.raises(Exception):
        asyncio.run(service.bind(db, other.id, "device-1", BindRuleCommand(rule_version_id=version.id), user))


def test_rule_center_active_replacement_audit_and_idempotent_replay(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-replace")
    _agent(db, project.id, "device-1")
    template = asyncio.run(service.create_template(db, CreateRuleTemplateCommand(project_id=project.id, template_key="cross", name="越界", algorithm_key="boundary_crossing"), user))
    first = asyncio.run(service.create_version(db, template.id, CreateRuleVersionCommand(config={"region_or_line": "r", "time_window": {"seconds": 5}}), user))
    asyncio.run(service.publish_version(db, first.id, RuleTransitionCommand(), user))
    second = asyncio.run(service.create_version(db, template.id, CreateRuleVersionCommand(config={"region_or_line": "r2", "time_window": {"seconds": 10}}), user))
    asyncio.run(service.publish_version(db, second.id, RuleTransitionCommand(), user))
    binding = asyncio.run(service.bind(db, project.id, "device-1", BindRuleCommand(rule_version_id=first.id, idempotency_key="bind-1"), user))
    replay = asyncio.run(service.bind(db, project.id, "device-1", BindRuleCommand(rule_version_id=first.id, idempotency_key="bind-1"), user))
    assert replay.id == binding.id
    with pytest.raises(Exception):
        asyncio.run(service.bind(db, project.id, "device-1", BindRuleCommand(rule_version_id=second.id, idempotency_key="bind-1"), user))
    replacement = asyncio.run(service.bind(db, project.id, "device-1", BindRuleCommand(rule_version_id=second.id, idempotency_key="bind-2"), user))
    assert replacement.status == "ACTIVE"
    rows = db.query(RuleBinding).filter(RuleBinding.project_id == project.id, RuleBinding.device_id == "device-1").all()
    assert sum(row.status == "ACTIVE" for row in rows) == 1
    audits = asyncio.run(service.list_audits(db, project.id, user))
    assert [row.operation for row in audits].count("bind") == 2
    assert any(row.from_status == "ACTIVE" and row.to_status == "ACTIVE" for row in audits)


def test_rule_version_has_no_update_endpoint_and_unbind_is_idempotent(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-immutable")
    _agent(db, project.id, "device-2")
    template = asyncio.run(service.create_template(db, CreateRuleTemplateCommand(project_id=project.id, template_key="water", name="水域", algorithm_key="near_water_danger"), user))
    version = asyncio.run(service.create_version(db, template.id, CreateRuleVersionCommand(config={"region_or_line": "r", "time_window": 1}), user))
    asyncio.run(service.publish_version(db, version.id, RuleTransitionCommand(), user))
    binding = asyncio.run(service.bind(db, project.id, "device-2", BindRuleCommand(rule_version_id=version.id), user))
    unbound = asyncio.run(service.unbind(db, binding.id, RuleTransitionCommand(idempotency_key="unbind-1"), user))
    assert unbound.status == "INACTIVE"
    replay = asyncio.run(service.unbind(db, binding.id, RuleTransitionCommand(idempotency_key="unbind-1"), user))
    assert replay.status == "INACTIVE"
    row = db.query(RuleVersion).filter(RuleVersion.id == version.id).one()
    row.config_json = "{}"
    with pytest.raises(ValueError):
        db.flush()
    db.rollback()


def test_rule_center_strict_schema_rejects_invalid_types_and_extra_fields(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-schema-strict")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="strict",
                name="严格规则",
                algorithm_key="boundary_crossing",
            ),
            user,
        )
    )

    invalid_configs = (
        {"region_or_line": "line-a", "time_window": "5"},
        {
            "region_or_line": [{"x": 1, "y": 2}],
            "time_window": 5,
        },
        {
            "region_or_line": [
                [0, 0],
                [10, 10],
            ],
            "time_window": {"seconds": 5, "unexpected": True},
        },
        {
            "region_or_line": [
                [0, 0],
                [10, 10],
            ],
            "time_window": 0,
        },
    )
    for config in invalid_configs:
        with pytest.raises(LabelUException) as exc_info:
            asyncio.run(
                service.create_version(
                    db,
                    template.id,
                    CreateRuleVersionCommand(config=config),
                    user,
                )
            )
        assert exc_info.value.status_code == 422


def test_rule_center_schema_keeps_legacy_and_typed_forms_compatible(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-schema-compatible")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="compatible",
                name="兼容规则",
                algorithm_key="near_water_danger",
            ),
            user,
        )
    )
    legacy = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "legacy-region", "time_window": 3}
            ),
            user,
        )
    )
    typed = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={
                    "region_or_line": [{"x": 0, "y": 0}, [1920, 1080]],
                    "time_window": {"seconds": 3},
                }
            ),
            user,
        )
    )
    assert legacy.version_no == 1
    assert typed.version_no == 2


def test_rule_coordinates_round_trip_and_bounds():
    pixels = [{"x": 0, "y": 0}, [960, 540], {"x": 1920, "y": 1080}]
    envelope = normalize_rule_coordinates(pixels, width=1920, height=1080)
    assert envelope["coordinate_space"] == "normalized"
    assert envelope["points"][1] == {"x": 0.5, "y": 0.5}
    assert denormalize_rule_coordinates(envelope, width=1920, height=1080) == [
        {"x": 0.0, "y": 0.0},
        {"x": 960.0, "y": 540.0},
        {"x": 1920.0, "y": 1080.0},
    ]
    with pytest.raises(ValueError):
        normalize_rule_coordinates([[0, 0], [1921, 1]], width=1920, height=1080)
    with pytest.raises(ValueError):
        denormalize_rule_coordinates(
            {
                "coordinate_space": "normalized",
                "points": [[0, 0], [1.1, 1]],
            },
            width=1920,
            height=1080,
        )


def test_rule_publish_rejects_non_draft_state(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-publish-state")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="state",
                name="状态规则",
                algorithm_key="boundary_crossing",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 1}
            ),
            user,
        )
    )
    version_row = db.query(RuleVersion).filter(RuleVersion.id == version.id).one()
    version_row.status = "RETIRED"
    db.commit()
    assert db.query(RuleVersion).filter(RuleVersion.id == version.id).one().status == "RETIRED"
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(service.publish_version(db, version.id, RuleTransitionCommand(), user))
    assert exc_info.value.status_code == 409


def test_rule_binding_rejects_missing_cross_project_and_inactive_devices(db):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-device-state")
    other = _project(db, user.id, "rules-device-other")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="device-state",
                name="设备状态规则",
                algorithm_key="boundary_crossing",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 1}
            ),
            user,
        )
    )
    asyncio.run(service.publish_version(db, version.id, RuleTransitionCommand(), user))

    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.bind(
                db,
                project.id,
                "missing-device",
                BindRuleCommand(rule_version_id=version.id),
                user,
            )
        )
    assert exc_info.value.status_code == 404

    _agent(db, other.id, "cross-project-device")
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.bind(
                db,
                project.id,
                "cross-project-device",
                BindRuleCommand(rule_version_id=version.id),
                user,
            )
        )
    assert exc_info.value.status_code == 403

    _agent(db, project.id, "inactive-device")
    agent = db.get(EdgeAgent, "inactive-device")
    for blocked_status in ("DISABLED", "DELETED", "REVOKED"):
        agent.status = blocked_status
        agent.revoked_at = None
        db.commit()
        with pytest.raises(LabelUException) as exc_info:
            asyncio.run(
                service.bind(
                    db,
                    project.id,
                    agent.agent_id,
                    BindRuleCommand(rule_version_id=version.id),
                    user,
                )
            )
        assert exc_info.value.status_code == 409

    agent.status = "ONLINE"
    agent.revoked_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.rollback(
                db,
                project.id,
                agent.agent_id,
                BindRuleCommand(rule_version_id=version.id),
                user,
            )
        )
    assert exc_info.value.status_code == 409


def test_rule_publish_server_signature_contract_and_device_boundary(db, monkeypatch):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-signature")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="signed",
                name="签名规则",
                algorithm_key="person_intrusion",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 2}
            ),
            user,
        )
    )
    assert version.signing_payload_sha256
    secret = "rule-test-secret"
    monkeypatch.setattr(
        service.settings,
        "SEVEN_GAI_RULE_SIGNING_KEYS_JSON",
        '{"rule-key-1":"rule-test-secret"}',
    )
    monkeypatch.setattr(service.settings, "SEVEN_GAI_RULE_REQUIRE_SIGNATURE", True)
    signature = hmac.new(
        secret.encode("utf-8"),
        version.signing_payload_sha256.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    published = asyncio.run(
        service.publish_version(
            db,
            version.id,
            RuleTransitionCommand(
                signature={
                    "algorithm": "HMAC-SHA256",
                    "key_id": "rule-key-1",
                    "payload_sha256": version.signing_payload_sha256,
                    "signature": signature,
                }
            ),
            user,
        )
    )
    assert published.status == "ACTIVE"
    assert published.signature_algorithm == "HMAC-SHA256"
    assert published.signature_key_id == "rule-key-1"
    assert published.server_signature_status == "VERIFIED"
    assert published.signature_verification_scope == "SERVER_ONLY"
    assert published.device_verification_status == "UNVERIFIED"
    assert published.signature_verified_at is not None


def test_rule_publish_rejects_missing_or_invalid_required_signature(db, monkeypatch):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-signature-reject")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="signature-reject",
                name="签名拒绝规则",
                algorithm_key="near_water_danger",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 2}
            ),
            user,
        )
    )
    monkeypatch.setattr(
        service.settings,
        "SEVEN_GAI_RULE_SIGNING_KEYS_JSON",
        '{"rule-key-1":"rule-test-secret"}',
    )
    monkeypatch.setattr(service.settings, "SEVEN_GAI_RULE_REQUIRE_SIGNATURE", True)
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.publish_version(db, version.id, RuleTransitionCommand(), user)
        )
    assert exc_info.value.status_code == 409
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.publish_version(
                db,
                version.id,
                RuleTransitionCommand(
                    signature={
                        "algorithm": "HMAC-SHA256",
                        "key_id": "rule-key-1",
                        "payload_sha256": version.signing_payload_sha256,
                        "signature": "0" * 64,
                    }
                ),
                user,
            )
        )
    assert exc_info.value.status_code == 409
    assert db.get(RuleVersion, version.id).status == "DRAFT"


def test_rule_binding_integrity_race_is_normalized_to_conflict(db, monkeypatch):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-binding-race")
    _agent(db, project.id, "race-device")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="race",
                name="并发绑定规则",
                algorithm_key="boundary_crossing",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 1}
            ),
            user,
        )
    )
    asyncio.run(service.publish_version(db, version.id, RuleTransitionCommand(), user))
    original_flush = db.flush

    def conflicting_flush(*args, **kwargs):
        if any(isinstance(row, RuleBinding) for row in db.new):
            raise IntegrityError("rule binding unique race", {}, RuntimeError("race"))
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", conflicting_flush)
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.bind(
                db,
                project.id,
                "race-device",
                BindRuleCommand(rule_version_id=version.id),
                user,
            )
        )
    assert exc_info.value.status_code == 409


def test_rule_binding_stale_active_cas_is_normalized_to_conflict(db, monkeypatch):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-binding-cas")
    _agent(db, project.id, "cas-device")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="cas",
                name="CAS 绑定规则",
                algorithm_key="boundary_crossing",
            ),
            user,
        )
    )
    first = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "first", "time_window": 1}
            ),
            user,
        )
    )
    second = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "second", "time_window": 1}
            ),
            user,
        )
    )
    asyncio.run(service.publish_version(db, first.id, RuleTransitionCommand(), user))
    asyncio.run(service.publish_version(db, second.id, RuleTransitionCommand(), user))
    active = asyncio.run(
        service.bind(
            db,
            project.id,
            "cas-device",
            BindRuleCommand(rule_version_id=first.id),
            user,
        )
    )
    original_execute = db.execute

    class LostCasResult:
        rowcount = 0

    def lost_cas(statement, *args, **kwargs):
        if (
            getattr(statement, "is_update", False)
            and getattr(getattr(statement, "table", None), "name", None)
            == "rule_binding"
        ):
            return LostCasResult()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", lost_cas)
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.bind(
                db,
                project.id,
                "cas-device",
                BindRuleCommand(rule_version_id=second.id),
                user,
            )
        )
    assert exc_info.value.status_code == 409
    current = db.query(RuleBinding).filter(RuleBinding.id == active.id).one()
    assert current.status == "ACTIVE"
    assert current.rule_version_id == first.id


def test_device_signature_ack_updates_binding_and_is_idempotent(db, monkeypatch):
    user, project, agent, version, binding = _signed_binding(db, monkeypatch)
    cmd = ReportRuleSignatureCommand(
        project_id=project.id,
        rule_version_id=version.id,
        payload_sha256=version.signing_payload_sha256,
        key_id="rule-key-1",
        report_id="report-device-0001",
        sequence=1,
        reported_status="ACK",
    )
    report = asyncio.run(
        service.report_device_signature(db, agent, binding.id, cmd)
    )
    assert report.key_state == "ACTIVE"
    assert report.effective_status == "VERIFIED"
    replay = asyncio.run(
        service.report_device_signature(db, agent, binding.id, cmd)
    )
    assert replay.id == report.id
    row = db.get(RuleBinding, binding.id)
    assert row.device_verification_status == "VERIFIED"
    assert row.device_report_sequence == 1
    assert row.last_device_report_id == cmd.report_id
    assert row.device_verified_at is not None
    assert db.get(RuleVersion, version.id).device_verification_status == "UNVERIFIED"
    reports = asyncio.run(
        service.list_device_signature_reports(db, project.id, agent.agent_id, user)
    )
    assert [item.report_id for item in reports] == [cmd.report_id]


@pytest.mark.parametrize(
    ("key_config", "expected_key_state", "expected_status"),
    (
        (
            '{"rule-key-1":{"secret":"rule-test-secret","status":"REVOKED"}}',
            "REVOKED",
            "REJECTED_KEY_REVOKED",
        ),
        (
            '{"rule-key-1":{"secret":"rule-test-secret","status":"EXPIRED"}}',
            "EXPIRED",
            "REJECTED_KEY_EXPIRED",
        ),
        (
            '{"rule-key-1":{"secret":"rule-test-secret","status":"ROTATING","rotation_grace_until":"2999-01-01T00:00:00Z"}}',
            "ROTATING",
            "VERIFIED_ROTATING_KEY",
        ),
        (
            '{"rule-key-1":{"secret":"rule-test-secret","status":"ROTATING","rotation_grace_until":"2000-01-01T00:00:00Z"}}',
            "EXPIRED",
            "REJECTED_KEY_EXPIRED",
        ),
    ),
)
def test_device_ack_respects_key_lifecycle(
    db, monkeypatch, key_config, expected_key_state, expected_status
):
    _, project, agent, version, binding = _signed_binding(
        db, monkeypatch, suffix="key-lifecycle"
    )
    monkeypatch.setattr(
        service.settings, "SEVEN_GAI_RULE_SIGNING_KEYS_JSON", key_config
    )
    report = asyncio.run(
        service.report_device_signature(
            db,
            agent,
            binding.id,
            ReportRuleSignatureCommand(
                project_id=project.id,
                rule_version_id=version.id,
                payload_sha256=version.signing_payload_sha256,
                key_id="rule-key-1",
                report_id="report-key-state-0001",
                sequence=1,
                reported_status="ACK",
            ),
        )
    )
    assert report.key_state == expected_key_state
    assert report.effective_status == expected_status
    assert db.get(RuleBinding, binding.id).device_verification_status == expected_status


def test_device_signature_report_rejects_forgery_replay_and_sequence_gap(
    db, monkeypatch
):
    _, project, agent, version, binding = _signed_binding(
        db, monkeypatch, suffix="report-guards"
    )
    base = {
        "project_id": project.id,
        "rule_version_id": version.id,
        "payload_sha256": version.signing_payload_sha256,
        "key_id": "rule-key-1",
        "reported_status": "ACK",
    }
    invalid_payloads = (
        {**base, "report_id": "wrong-hash-0001", "sequence": 1, "payload_sha256": "0" * 64},
        {**base, "report_id": "wrong-key-0001", "sequence": 1, "key_id": "other-key"},
        {**base, "report_id": "sequence-gap-0001", "sequence": 2},
    )
    for payload in invalid_payloads:
        with pytest.raises(LabelUException) as exc_info:
            asyncio.run(
                service.report_device_signature(
                    db, agent, binding.id, ReportRuleSignatureCommand(**payload)
                )
            )
        assert exc_info.value.status_code == 409
    first = ReportRuleSignatureCommand(
        **base, report_id="replay-guard-0001", sequence=1
    )
    asyncio.run(service.report_device_signature(db, agent, binding.id, first))
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.report_device_signature(
                db,
                agent,
                binding.id,
                first.model_copy(update={"reason": "tampered"}),
            )
        )
    assert exc_info.value.status_code == 409
    assert db.query(RuleDeviceSignatureReport).count() == 1


def test_device_signature_report_enforces_project_and_append_only(db, monkeypatch):
    _, project, agent, version, binding = _signed_binding(
        db, monkeypatch, suffix="report-scope"
    )
    other = _project(db, 1, "report-scope-other")
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.report_device_signature(
                db,
                agent,
                binding.id,
                ReportRuleSignatureCommand(
                    project_id=other.id,
                    rule_version_id=version.id,
                    payload_sha256=version.signing_payload_sha256,
                    key_id="rule-key-1",
                    report_id="cross-project-0001",
                    sequence=1,
                    reported_status="ACK",
                ),
            )
        )
    assert exc_info.value.status_code == 403
    valid = ReportRuleSignatureCommand(
        project_id=project.id,
        rule_version_id=version.id,
        payload_sha256=version.signing_payload_sha256,
        key_id="rule-key-1",
        report_id="append-only-0001",
        sequence=1,
        reported_status="REJECTED",
        failure_code="DEVICE_SIGNATURE_INVALID",
    )
    asyncio.run(service.report_device_signature(db, agent, binding.id, valid))
    row = db.query(RuleDeviceSignatureReport).one()
    assert row.effective_status == "REJECTED_DEVICE"
    assert db.get(RuleBinding, binding.id).device_verification_status == "REJECTED_DEVICE"
    row.reason = "tampered"
    with pytest.raises(ValueError):
        db.flush()
    db.rollback()


def test_device_signature_report_cas_conflict_is_rejected(db, monkeypatch):
    _, project, agent, version, binding = _signed_binding(
        db, monkeypatch, suffix="report-cas"
    )
    original_execute = db.execute

    class LostCasResult:
        rowcount = 0

    def lost_cas(statement, *args, **kwargs):
        if (
            getattr(statement, "is_update", False)
            and getattr(getattr(statement, "table", None), "name", None)
            == "rule_binding"
        ):
            return LostCasResult()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", lost_cas)
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.report_device_signature(
                db,
                agent,
                binding.id,
                ReportRuleSignatureCommand(
                    project_id=project.id,
                    rule_version_id=version.id,
                    payload_sha256=version.signing_payload_sha256,
                    key_id="rule-key-1",
                    report_id="report-cas-0001",
                    sequence=1,
                    reported_status="ACK",
                ),
            )
        )
    assert exc_info.value.status_code == 409
    assert db.query(RuleDeviceSignatureReport).count() == 0


def test_device_signature_report_rejects_server_unverified_version(db, monkeypatch):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-unverified-device-report")
    _agent(db, project.id, "unverified-agent")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="unverified-device-report",
                name="未签名规则",
                algorithm_key="person_intrusion",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 2}
            ),
            user,
        )
    )
    version = asyncio.run(
        service.publish_version(db, version.id, RuleTransitionCommand(), user)
    )
    binding = asyncio.run(
        service.bind(
            db,
            project.id,
            "unverified-agent",
            BindRuleCommand(rule_version_id=version.id),
            user,
        )
    )
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.report_device_signature(
                db,
                db.get(EdgeAgent, "unverified-agent"),
                binding.id,
                ReportRuleSignatureCommand(
                    project_id=project.id,
                    rule_version_id=version.id,
                    payload_sha256=version.signing_payload_sha256,
                    key_id="rule-key-1",
                    report_id="unsigned-version-0001",
                    sequence=1,
                    reported_status="ACK",
                ),
            )
        )
    assert exc_info.value.status_code == 409
    assert db.query(RuleDeviceSignatureReport).count() == 0


def test_rule_publish_rejects_non_active_signing_key(db, monkeypatch):
    user = db.query(User).filter(User.id == 1).one()
    algorithm_service.ensure_seeded(db)
    project = _project(db, user.id, "rules-signing-key-state")
    template = asyncio.run(
        service.create_template(
            db,
            CreateRuleTemplateCommand(
                project_id=project.id,
                template_key="signing-key-state",
                name="密钥状态",
                algorithm_key="person_intrusion",
            ),
            user,
        )
    )
    version = asyncio.run(
        service.create_version(
            db,
            template.id,
            CreateRuleVersionCommand(
                config={"region_or_line": "region", "time_window": 2}
            ),
            user,
        )
    )
    monkeypatch.setattr(
        service.settings,
        "SEVEN_GAI_RULE_SIGNING_KEYS_JSON",
        '{"rule-key-1":{"secret":"rule-test-secret","status":"ROTATING","rotation_grace_until":"2999-01-01T00:00:00Z"}}',
    )
    signature = hmac.new(
        b"rule-test-secret",
        version.signing_payload_sha256.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    with pytest.raises(LabelUException) as exc_info:
        asyncio.run(
            service.publish_version(
                db,
                version.id,
                RuleTransitionCommand(
                    signature={
                        "key_id": "rule-key-1",
                        "payload_sha256": version.signing_payload_sha256,
                        "signature": signature,
                    }
                ),
                user,
            )
        )
    assert exc_info.value.status_code == 409
