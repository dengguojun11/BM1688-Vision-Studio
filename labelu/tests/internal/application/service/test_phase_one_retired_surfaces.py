import asyncio
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.rule import CreateRuleTemplateCommand
from labelu.internal.application.service import rule as rule_service
from labelu.internal.common.error_code import LabelUException


def test_rule_center_write_surface_is_retired_in_phase_one(db):
    with pytest.raises(LabelUException) as caught:
        asyncio.run(
            rule_service.create_template(
                db,
                CreateRuleTemplateCommand(
                    project_id=1,
                    template_key="phase-one-retired",
                    name="一期退休规则",
                    algorithm_key="person_intrusion",
                ),
                SimpleNamespace(id=1),
            )
        )
    assert caught.value.status_code == 403

