from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from sqlalchemy.exc import IntegrityError

from labelu.internal.application.command.dataset import (
    CreateDatasetVersionFromTasksCommand,
)
from labelu.internal.application.service import dataset as dataset_service
from labelu.internal.common.storage import get_storage_backend
from labelu.internal.domain.models.dataset import DatasetVersion

from labelu.tests.internal.application.service.test_dataset_version_label_subset import (
    _seed_two_label_task,
)


def _create_command(task_id: int) -> CreateDatasetVersionFromTasksCommand:
    return CreateDatasetVersionFromTasksCommand(source_task_ids=[int(task_id)])


@pytest.mark.parametrize("auto_freeze", [True, False])
def test_task_export_retries_version_number_conflict_and_recovers_session(
    db, monkeypatch, auto_freeze
):
    user, task, dataset, labels = _seed_two_label_task(db)
    if not auto_freeze:
        task.status = "FINISHED"
    db.commit()

    original_create = dataset_service.crud_dataset.create_version
    create_calls = {"count": 0}

    def flaky_create(session, row):
        create_calls["count"] += 1
        if create_calls["count"] == 1:
            raise IntegrityError(
                "uq_dataset_version_number",
                {},
                RuntimeError("duplicate dataset version number"),
            )
        return original_create(session, row)

    version_numbers = iter([1, 2])
    monkeypatch.setattr(
        dataset_service.crud_dataset,
        "next_version_no",
        lambda _db, _dataset_id: next(version_numbers),
    )
    monkeypatch.setattr(
        dataset_service.crud_dataset, "create_version", flaky_create
    )

    response = asyncio.run(
        dataset_service.create_version_from_tasks(
            db,
            int(dataset.id),
            _create_command(task.id),
            user,
            auto_freeze=auto_freeze,
            selected_label_ids=[int(labels[0].id)],
        )
    )

    assert response.version_no == 2
    assert create_calls["count"] == 2
    row = db.query(DatasetVersion).filter(DatasetVersion.id == response.id).one()
    assert row.version_no == 2
    # A failed flush must not poison the Session for the caller.
    assert db.query(DatasetVersion).count() == 1
    manifest = get_storage_backend().read_text(row.manifest_rel_path).encode("utf-8")
    assert hashlib.sha256(manifest).hexdigest() == row.manifest_sha256


@pytest.mark.parametrize("auto_freeze", [True, False])
def test_distinct_task_export_digests_do_not_overwrite_manifest_on_version_race(
    db, monkeypatch, auto_freeze
):
    user, task, dataset, labels = _seed_two_label_task(db)
    if not auto_freeze:
        task.status = "FINISHED"
    db.commit()

    # First request gets v1.  The second request deliberately gets the same
    # candidate, loses the unique version-number race, then gets v2.
    version_numbers = iter([1, 1, 2])
    monkeypatch.setattr(
        dataset_service.crud_dataset,
        "next_version_no",
        lambda _db, _dataset_id: next(version_numbers),
    )

    first = asyncio.run(
        dataset_service.create_version_from_tasks(
            db,
            int(dataset.id),
            _create_command(task.id),
            user,
            auto_freeze=auto_freeze,
            selected_label_ids=[],
        )
    )
    first_text = get_storage_backend().read_text(first.manifest_rel_path)

    second = asyncio.run(
        dataset_service.create_version_from_tasks(
            db,
            int(dataset.id),
            _create_command(task.id),
            user,
            auto_freeze=auto_freeze,
            selected_label_ids=[int(labels[0].id)],
        )
    )

    assert first.id != second.id
    assert {first.version_no, second.version_no} == {1, 2}
    assert first.manifest_rel_path != second.manifest_rel_path
    assert get_storage_backend().read_text(first.manifest_rel_path) == first_text
    second_text = get_storage_backend().read_text(second.manifest_rel_path)
    assert hashlib.sha256(first_text.encode("utf-8")).hexdigest() == first.manifest_sha256
    assert hashlib.sha256(second_text.encode("utf-8")).hexdigest() == second.manifest_sha256
    assert json.loads(first_text)["selected_label_ids"] != json.loads(second_text)[
        "selected_label_ids"
    ]

