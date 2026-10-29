from labelu.internal.adapter.persistence import crud_rule
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.rule import (
    RuleBinding,
    RuleBindingAudit,
    RuleTemplate,
    RuleVersion,
    RuleVersionAudit,
)


def _project(db, name: str) -> Project:
    row = Project(
        owner_id=1,
        name=name,
        status="ACTIVE",
        created_by=1,
        updated_by=1,
    )
    db.add(row)
    db.flush()
    return row


def test_rule_collections_support_literal_search_and_server_pagination(db):
    project = _project(db, "rule-search-project")
    first = RuleTemplate(
        project_id=project.id,
        template_key="intrusion",
        name="人员闯入",
        algorithm_key="person_intrusion",
        description="100% precision",
        status="ACTIVE",
        created_by=1,
    )
    second = RuleTemplate(
        project_id=project.id,
        template_key="water",
        name="水域告警",
        algorithm_key="near_water_danger",
        description="普通规则",
        status="ACTIVE",
        created_by=1,
    )
    db.add_all([first, second])
    db.flush()

    rows, total = crud_rule.list_templates(
        db, project.id, page=0, size=1
    )
    assert total == 2
    assert len(rows) == 1
    assert rows[0].id == second.id

    rows, total = crud_rule.list_templates(db, project.id, search="100%", page=0, size=20)
    assert total == 1
    assert [row.template_key for row in rows] == ["intrusion"]

    version_one = RuleVersion(
        template_id=first.id,
        version_no=1,
        schema_version="v1",
        config_json='{"region_or_line":"gate"}',
        config_sha256="a" * 64,
        status="DRAFT",
        created_by=1,
    )
    version_two = RuleVersion(
        template_id=first.id,
        version_no=2,
        schema_version="v2",
        config_json='{"region_or_line":"yard"}',
        config_sha256="b" * 64,
        status="ACTIVE",
        created_by=1,
    )
    db.add_all([version_one, version_two])
    db.flush()
    rows, total = crud_rule.list_versions(db, first.id, search="yard", page=0, size=20)
    assert total == 1
    assert [row.version_no for row in rows] == [2]

    binding = RuleBinding(
        project_id=project.id,
        device_id="edge-alpha",
        rule_version_id=version_two.id,
        status="ACTIVE",
        active_key="edge-alpha",
        created_by=1,
    )
    db.add(binding)
    db.flush()
    audit = RuleBindingAudit(
        binding_id=binding.id,
        project_id=project.id,
        device_id="edge-alpha",
        operation="bind",
        from_status=None,
        to_status="ACTIVE",
        actor_id=1,
        reason="initial",
        idempotency_key="rule-search-audit",
    )
    version_audit = RuleVersionAudit(
        version_id=version_two.id,
        template_id=first.id,
        project_id=project.id,
        version_no=2,
        operation="publish",
        from_status="DRAFT",
        to_status="ACTIVE",
        actor_id=1,
        reason="release",
        idempotency_key="rule-search-version-audit",
    )
    db.add_all([audit, version_audit])
    db.flush()

    rows, total = crud_rule.list_bindings(db, project.id, search="alpha", page=0, size=20)
    assert total == 1
    assert rows[0].device_id == "edge-alpha"
    rows, total = crud_rule.list_binding_audits(db, project.id, search="initial", page=0, size=20)
    assert total == 1
    assert rows[0].reason == "initial"
    rows, total = crud_rule.list_version_audits(db, project.id, search="release", page=0, size=20)
    assert total == 1
    assert rows[0].version_no == 2

