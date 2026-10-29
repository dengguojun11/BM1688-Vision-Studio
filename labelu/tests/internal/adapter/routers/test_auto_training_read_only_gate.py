from __future__ import annotations

import pytest

from labelu.internal.common.config import settings
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingRound,
)


API = settings.API_V1_STR

WRITE_CASES = [
    ("patch", f"{API}/auto-training-settings", {}),
    ("post", f"{API}/auto-training-plans/preview", {}),
    ("post", f"{API}/auto-training-plans", {}),
    ("patch", f"{API}/auto-training-plans/1", {}),
    ("post", f"{API}/auto-training-plans/1/submit", {}),
    ("post", f"{API}/auto-training-plans/1/approve", {}),
    ("post", f"{API}/auto-training-plans/1/pause", {}),
    ("post", f"{API}/auto-training-plans/1/resume", {}),
    ("post", f"{API}/auto-training-plans/1/archive", {}),
    ("post", f"{API}/auto-training-plans/1/scan-preview", None),
    ("post", f"{API}/auto-training-plans/1/reconcile", None),
    ("post", f"{API}/auto-training-plans/1/rounds/preview", None),
    ("post", f"{API}/auto-training-plans/1/rounds", {}),
    ("post", f"{API}/auto-training-rounds/1/cancel", {}),
    ("post", f"{API}/auto-training-rounds/1/retry-stage", {}),
    ("post", f"{API}/auto-training-rounds/1/promote-to-bm1688", {}),
    ("post", f"{API}/auto-training-rounds/1/start-edge-validation", {}),
    ("post", f"{API}/auto-training-rounds/1/quality-evidence", {}),
    ("post", f"{API}/auto-training-rounds/1/publish", {}),
    ("post", f"{API}/samples/1/training-review", {}),
    ("post", f"{API}/training-reviews/1/invalidate", {}),
    ("post", f"{API}/training-evaluation-datasets", {}),
    ("post", f"{API}/training-evaluation-datasets/auto-generate", {}),
    ("patch", f"{API}/training-evaluation-datasets/1", {}),
    ("post", f"{API}/training-evaluation-datasets/1/submit", {}),
    ("post", f"{API}/training-evaluation-datasets/1/approve", {}),
    ("put", f"{API}/training-evaluation-datasets/1/samples", {}),
    ("post", f"{API}/auto-training-evaluation-jobs/1/claim", {}),
    ("post", f"{API}/yolo-quality-policies/default/submit-approval", {}),
    ("post", f"{API}/quality-policy-approvals/submit-current", {}),
    ("post", f"{API}/yolo-quality-policies/default/approve", {}),
]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    WRITE_CASES,
)
def test_phase_one_blocks_every_automatic_training_write_surface(
    client,
    testadmin_token_headers,
    db,
    monkeypatch,
    method,
    path,
    payload,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    before = (
        db.query(AutoTrainingPlan).count(),
        db.query(AutoTrainingRound).count(),
        db.query(AutoTrainingOutbox).count(),
    )

    response = client.request(
        method,
        path,
        headers=testadmin_token_headers,
        json=payload,
    )

    assert response.status_code == 403, response.text
    assert response.json() == {
        "msg": "自动训练计划一期暂未开放，历史记录仅供查看",
        "err_code": 57122,
    }
    db.expire_all()
    assert (
        db.query(AutoTrainingPlan).count(),
        db.query(AutoTrainingRound).count(),
        db.query(AutoTrainingOutbox).count(),
    ) == before


def test_phase_one_keeps_automatic_training_history_readable(
    client,
    testadmin_token_headers,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    response = client.get(
        f"{API}/auto-training-plans",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text


def test_phase_one_hides_automatic_training_history_from_annotation_users(
    client,
    testuser_token_headers,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    response = client.get(
        f"{API}/auto-training-plans",
        headers=testuser_token_headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["err_code"] == 30001


def test_phase_one_write_gate_authenticates_before_disclosing_feature_state(
    client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    response = client.post(f"{API}/auto-training-plans", json={})

    assert response.status_code == 401, response.text
