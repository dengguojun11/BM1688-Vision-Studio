from __future__ import annotations

from labelu.internal.adapter.persistence import crud_model_version, crud_user
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.model_series import ModelSeries, ModelVariant
from labelu.internal.domain.models.model_version import ModelVersion


def test_next_branch_version_is_independent_for_each_variant(db):
    owner = crud_user.get_user_by_username(db, "test@example.com")
    assert owner is not None

    with begin_transaction(db):
        series = ModelSeries(
            owner_id=owner.id,
            name="branch-version-series",
            model_family="yolo_detection_v1",
            status="ACTIVE",
        )
        db.add(series)
        db.flush()
        first = ModelVariant(
            series_id=series.id,
            name="day",
            branch_key="a" * 64,
            selection_contract_json="{}",
            selection_contract_sha256="a" * 64,
            status="ACTIVE",
            created_by=owner.id,
        )
        second = ModelVariant(
            series_id=series.id,
            name="night",
            branch_key="b" * 64,
            selection_contract_json="{}",
            selection_contract_sha256="b" * 64,
            status="ACTIVE",
            created_by=owner.id,
        )
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                ModelVersion(
                    algorithm_id=1,
                    series_id=series.id,
                    variant_id=first.id,
                    model_key="branch-version-a-v1",
                    version_no=1,
                    branch_version_no=1,
                    display_name="day v1",
                    status="CANDIDATE",
                    created_by=owner.id,
                ),
                ModelVersion(
                    algorithm_id=1,
                    series_id=series.id,
                    variant_id=first.id,
                    model_key="branch-version-a-v2",
                    version_no=2,
                    branch_version_no=2,
                    display_name="day v2",
                    status="CANDIDATE",
                    created_by=owner.id,
                ),
                ModelVersion(
                    algorithm_id=1,
                    series_id=series.id,
                    variant_id=second.id,
                    model_key="branch-version-b-v1",
                    version_no=3,
                    branch_version_no=1,
                    display_name="night v1",
                    status="CANDIDATE",
                    created_by=owner.id,
                ),
            ]
        )
        db.flush()

    assert crud_model_version.next_branch_version_no(db, series.id, first.id) == 3
    assert crud_model_version.next_branch_version_no(db, series.id, second.id) == 2


def test_next_branch_version_accounts_for_legacy_rows_without_branch_number(db):
    owner = crud_user.get_user_by_username(db, "test@example.com")
    assert owner is not None

    with begin_transaction(db):
        series = ModelSeries(
            owner_id=owner.id,
            name="branch-version-legacy-series",
            model_family="yolo_detection_v1",
            status="ACTIVE",
        )
        variant = ModelVariant(
            series=series,
            name="legacy-branch",
            branch_key="c" * 64,
            selection_contract_json="{}",
            selection_contract_sha256="c" * 64,
            status="ACTIVE",
            created_by=owner.id,
        )
        db.add(variant)
        db.flush()
        db.add(
            ModelVersion(
                algorithm_id=1,
                series_id=series.id,
                variant_id=variant.id,
                model_key="branch-version-legacy-v-global-9",
                version_no=9,
                branch_version_no=None,
                display_name="legacy",
                status="CANDIDATE",
                created_by=owner.id,
            )
        )
        db.flush()

    # A legacy row occupies the branch even though it has no explicit local
    # sequence; the next materialized version must therefore be v2.
    assert crud_model_version.next_branch_version_no(db, series.id, variant.id) == 2

