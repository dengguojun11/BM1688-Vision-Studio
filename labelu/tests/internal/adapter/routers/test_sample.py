from pathlib import Path
import json
from datetime import datetime

from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.common.compare_sample_access_token import (
    create_compare_sample_access_token,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.adapter.persistence import crud_export_job
from labelu.internal.adapter.persistence import crud_auto_label_job
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.user import User
from labelu.internal.common.security import get_password_hash
from labelu.internal.common.plate_recognition_profile import (
    normalize_plate_recognition_task_config,
)
from labelu.tests.utils.utils import empty_task_upload


def _street_vendor_task_config() -> str:
    return json.dumps(
        {
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {"key": "蔬菜摊", "value": "streetVendor_vegetablet"},
                            {"key": "餐饮摊贩", "value": "streetVendor_catering"},
                            {"key": "衣服摊", "value": "streetVendor_clother"},
                            {"key": "水果摊", "value": "streetVendor_fruit"},
                            {"key": "其他摊贩", "value": "streetVendor_other"},
                            {"key": "箱子", "value": "object_plasticbasket"},
                            {"key": "篮子", "value": "object_basket"},
                            {"key": "水桶", "value": "object_bucket"},
                            {"key": "冰柜", "value": "object_cabinet"},
                            {"key": "桌子", "value": "object_table"},
                            {"key": "椅子", "value": "object_chair"},
                        ]
                    },
                }
            ]
        },
        ensure_ascii=False,
    )


class TestClassTaskSampleRouter:
    def test_auto_label_job_status_requires_task_access(
        self, client: TestClient, db: Session
    ) -> None:
        owner = crud_user.get_user_by_username(db=db, username="test@example.com")
        outsider_name = "auto-label-job-outsider@example.com"
        with begin_transaction(db):
            outsider = crud_user.get_user_by_username(db=db, username=outsider_name)
            if not outsider:
                outsider = crud_user.create(
                    db=db,
                    user=User(
                        username=outsider_name,
                        hashed_password=get_password_hash("test@123"),
                    ),
                )
            task = crud_task.create(
                db=db,
                task=Task(name="private auto-label task", created_by=owner.id, updated_by=owner.id),
            )
            job = crud_auto_label_job.create(
                db=db,
                task_id=task.id,
                user_id=owner.id,
                sample_count=1,
                filter_by_labels=True,
            )
            # Keep scalar identifiers before the shared test session is cleaned
            # or expires ORM instances at the transaction boundary.
            task_id = task.id
            job_id = job.id

        login = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": outsider_name, "password": "test@123"},
        )
        headers = {"Authorization": login.json()["data"]["token"]}
        response = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/auto_label_job/{job_id}",
            headers=headers,
        )
        assert response.status_code == 403

    def test_create_sample_successful(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        data = [{"file_id": 1, "data": {}}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        db.expire_all()
        updated_task = crud_task.get(db=db, task_id=task.id)
        json = r.json()
        assert r.status_code == 201
        assert len(json["data"]["ids"]) == 1
        assert updated_task.status == "IMPORTED"

    def test_create_sample_idempotent_by_file_id(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        data = [{"file_id": 42, "data": {}}]
        url = f"{settings.API_V1_STR}/tasks/{task.id}/samples"

        first = client.post(url, headers=testuser_token_headers, json=data)
        second = client.post(url, headers=testuser_token_headers, json=data)

        assert first.status_code == 201
        assert second.status_code == 201
        first_id = first.json()["data"]["ids"][0]
        second_id = second.json()["data"]["ids"][0]
        assert first_id == second_id

        db.expire_all()
        assert crud_sample.count(db=db, task_id=task.id) == 1

    def test_create_sample_task_status_not_draft(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                    status="CONFIGURED",
                ),
            )
        data = [{"file_id": 1, "data": {}}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        db.expire_all()
        updated_task = crud_task.get(db=db, task_id=task.id)
        json = r.json()
        assert r.status_code == 201
        assert len(json["data"]["ids"]) == 1
        assert updated_task.status == "CONFIGURED"

    def test_create_sample_task_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = [{"file_id": 1, "data": {}}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/0/samples",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 50002

    def test_sample_list_by_page(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                name="name",
                description="description",
                tips="tips",
                created_by=0,
                updated_by=0,
            ),
        )
        samples = [
            TaskSample(
                task_id=1,
                file_id=12,
                created_by=current_user.id,
                updated_by=current_user.id,
                data="{}",
            )
            for i in range(14)
        ]
        with begin_transaction(db):
            crud_sample.batch(
                db=db,
                samples=samples,
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 10
        assert json["data"][0]["id"] == 1
        assert json["meta_data"]["total"] == 14

    def test_sample_list_searches_filename_and_inner_id(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="searchable task",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            attachments = [
                TaskAttachment(
                    task_id=task.id,
                    filename="night-road.jpg",
                    path="night-road.jpg",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
                TaskAttachment(
                    task_id=task.id,
                    filename="day-road.jpg",
                    path="day-road.jpg",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            ]
            db.add_all(attachments)
            db.flush()
            samples = [
                TaskSample(
                    task_id=task.id,
                    inner_id=index,
                    file_id=attachment.id,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    data="{}",
                )
                for index, attachment in enumerate(attachments, start=1)
            ]
            crud_sample.batch(db=db, samples=samples)
            task_id = task.id
            inner_id = samples[1].inner_id

        by_filename = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/samples",
            headers=testuser_token_headers,
            params={"page": 0, "size": 20, "q": "night-road"},
        )
        assert by_filename.status_code == 200
        assert [row["file"]["filename"] for row in by_filename.json()["data"]] == [
            "night-road.jpg"
        ]
        assert by_filename.json()["meta_data"]["total"] == 1

        by_inner_id = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/samples",
            headers=testuser_token_headers,
            params={"page": 0, "size": 20, "q": str(inner_id)},
        )
        assert by_inner_id.status_code == 200
        assert [row["inner_id"] for row in by_inner_id.json()["data"]] == [inner_id]
        assert by_inner_id.json()["meta_data"]["total"] == 1

    def test_sample_list_searches_id_without_active_attachment(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        """ID search must still find samples whose attachment is unavailable."""
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="sample without attachment",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            attachment = TaskAttachment(
                task_id=task.id,
                filename="removed.jpg",
                path="removed.jpg",
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            db.add(attachment)
            db.flush()
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        inner_id=901,
                        file_id=None,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    ),
                    TaskSample(
                        task_id=task.id,
                        inner_id=902,
                        file_id=attachment.id,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    ),
                ],
            )
            attachment.deleted_at = datetime.now()
            db.flush()
            task_id = task.id
            no_attachment_id = samples[0].id
            deleted_attachment_id = samples[1].id

        for sample_id in (no_attachment_id, deleted_attachment_id):
            response = client.get(
                f"{settings.API_V1_STR}/tasks/{task_id}/samples",
                headers=testuser_token_headers,
                params={"page": 0, "size": 20, "q": str(sample_id)},
            )
            assert response.status_code == 200
            body = response.json()
            assert [row["id"] for row in body["data"]] == [sample_id]
            assert body["meta_data"]["total"] == 1

        assert crud_sample.count(
            db=db, task_id=task_id, search=str(no_attachment_id)
        ) == 1
        assert crud_sample.count(
            db=db, task_id=task_id, search=str(deleted_attachment_id)
        ) == 1

    def test_sample_list_by_before(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                name="name",
                description="description",
                tips="tips",
                created_by=0,
                updated_by=0,
            ),
        )
        samples = [
            TaskSample(
                task_id=1,
                file_id=1,
                created_by=current_user.id,
                updated_by=current_user.id,
                data="{}",
            )
            for i in range(14)
        ]
        with begin_transaction(db):
            crud_sample.batch(
                db=db,
                samples=samples,
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            params={"before": 13, "size": 10},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 10
        assert json["data"][0]["id"] == 3
        assert json["data"][-1]["id"] == 12
        assert json["meta_data"]["total"] == 14

    def test_sample_list_by_after(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        samples = [
            TaskSample(
                task_id=1,
                file_id=1,
                created_by=current_user.id,
                updated_by=current_user.id,
                data="{}",
            )
            for i in range(14)
        ]
        with begin_transaction(db):
            crud_sample.batch(
                db=db,
                samples=samples,
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            params={"after": 5, "size": 10},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 9
        assert json["data"][0]["id"] == 6
        assert json["data"][8]["id"] == 14
        assert json["meta_data"]["total"] == 14

    def test_sample_list_with_sort(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        samples = [
            TaskSample(
                task_id=task.id,
                file_id=1,
                created_by=current_user.id,
                updated_by=current_user.id,
                data="{}",
                annotated_count=i,
            )
            for i in range(14)
        ]
        with begin_transaction(db):
            crud_sample.batch(
                db=db,
                samples=samples,
            )

        # get actual sample IDs for assertions
        sample_ids = sorted([s.id for s in samples])
        after_id = sample_ids[4]  # 5th sample (index 4)

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            params={"sort": "annotated_count:desc", "after": after_id, "size": 10},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 9
        # first item should have highest annotated_count (sorted desc)
        assert json["data"][0]["annotated_count"] >= json["data"][1]["annotated_count"]
        assert json["meta_data"]["total"] == 14

    def test_sample_list_by_params_error(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{1}/samples",
            headers=testuser_token_headers,
            params={"after": 1, "before": 1, "page": 1, "size": 10},
        )

        # check
        assert r.status_code == 422

    def test_sample_list_by_sort_error(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            params={"sort": "", "page": 0, "size": 10},
        )

        # check
        assert r.status_code == 422

    def test_sample_get(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )
        
        print(samples)

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 200

    def test_sample_get_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/0",
            headers=testuser_token_headers,
        )

        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 55001

    def test_sample_patch(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=1,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        # run
        data = {
            "data": {"key1": "value1", "key2": [{"key3": "value3"}]},
            "annotated_count": 1,
        }
        r = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert json["data"]["state"] == "DONE"

    def test_sample_patch_rejects_stale_version(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        version = datetime(2026, 7, 28, 10, 0, 0)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="versioned-sample",
                    description="description",
                    created_by=0,
                    updated_by=0,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        updated_at=version,
                        data="{}",
                    )
                ],
            )

        response = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json={
                "data": {"result": "{}"},
                "annotated_count": 1,
                "expected_updated_at": "2026-07-28T09:59:59",
            },
        )

        assert response.status_code == 409
        db.expire_all()
        saved = crud_sample.get(db=db, sample_id=samples[0].id)
        assert saved.data == "{}"
        assert saved.updated_at == version

        accepted = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json={
                "data": {"result": "{}"},
                "annotated_count": 0,
                "expected_updated_at": version.isoformat(),
            },
        )
        assert accepted.status_code == 200

    def test_sample_patch_normalizes_legacy_rect_labels(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="street-vendor",
                    description="description",
                    tips="tips",
                    config=_street_vendor_task_config(),
                    media_type="IMAGE",
                    created_by=0,
                    updated_by=0,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        result_payload = {
            "width": 100,
            "height": 80,
            "rotate": 0,
            "rectTool": {
                "toolName": "rectTool",
                "result": [
                    {
                        "id": "box-1",
                        "x": 1,
                        "y": 2,
                        "width": 10,
                        "height": 12,
                        "label": "streetVendor_vegetable",
                    },
                    {
                        "id": "box-2",
                        "x": 11,
                        "y": 12,
                        "width": 8,
                        "height": 9,
                        "label": "object_bauket",
                    },
                ],
            },
        }

        r = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json={
                "data": {"result": json.dumps(result_payload, ensure_ascii=False)},
                "annotated_count": 2,
            },
        )

        body = r.json()
        assert r.status_code == 200
        stored = json.loads(body["data"]["data"]["result"])
        assert [item["label"] for item in stored["rectTool"]["result"]] == [
            "streetVendor_vegetablet",
            "object_bucket",
        ]

    def test_sample_patch_rejects_unknown_rect_labels(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="street-vendor",
                    description="description",
                    tips="tips",
                    config=_street_vendor_task_config(),
                    media_type="IMAGE",
                    created_by=0,
                    updated_by=0,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        result_payload = {
            "width": 100,
            "height": 80,
            "rotate": 0,
            "rectTool": {
                "toolName": "rectTool",
                "result": [
                    {
                        "id": "box-1",
                        "x": 1,
                        "y": 2,
                        "width": 10,
                        "height": 12,
                        "label": "streetVendor_unknown",
                    }
                ],
            },
        }

        r = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json={
                "data": {"result": json.dumps(result_payload, ensure_ascii=False)},
                "annotated_count": 1,
            },
        )

        body = r.json()
        assert r.status_code == 400
        assert body["err_code"] == 30002
        assert "streetVendor_unknown" in body["msg"]

    def test_sample_patch_normalizes_stale_plate_tokens(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="plate task",
                    config=json.dumps(
                        normalize_plate_recognition_task_config({}),
                        ensure_ascii=False,
                    ),
                    media_type="IMAGE",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        result_payload = {
            "rectTool": {
                "result": [
                    {
                        "label": "doebleEP",
                        "attributes": {
                            "plateNumber": "A12345",
                            "color": ["green"],
                            " plateType": [" greenPlate"],
                        },
                    }
                ]
            },
            "polygonTool": {"result": [{"label": " platePoint"}]},
        }
        response = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json={
                "data": {"result": json.dumps(result_payload, ensure_ascii=False)},
                "annotated_count": 2,
            },
        )

        assert response.status_code == 200
        stored = json.loads(response.json()["data"]["data"]["result"])
        rect = stored["rectTool"]["result"][0]
        assert rect["label"] == "doubleEP"
        assert rect["attributes"]["plateType"] == ["greenPlate"]
        assert stored["polygonTool"]["result"][0]["label"] == "platePoint"

    def test_sample_patch_skip(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=1,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        # run
        data = {"state": "SKIPPED"}
        r = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{samples[0].id}",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert json["data"]["state"] == "SKIPPED"

    def test_sample_patch_task_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data

        # run
        data = {"state": "SKIPPED"}
        r = client.patch(
            f"{settings.API_V1_STR}/tasks/0/samples/0",
            headers=testuser_token_headers,
            json=data,
        )
        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 50002

    def test_sample_patch_task_sample_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )

        # run
        data = {"state": "SKIPPED"}
        r = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/0",
            headers=testuser_token_headers,
            json=data,
        )
        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 55001

    def test_sample_get_and_patch_with_compare_access_token_for_outsider(
        self, client: TestClient, db: Session
    ) -> None:
        owner = crud_user.get_user_by_username(db=db, username="test@example.com")
        outsider_name = "compare-sample-outsider@example.com"
        with begin_transaction(db):
            outsider = crud_user.get_user_by_username(db=db, username=outsider_name)
            if not outsider:
                outsider = crud_user.create(
                    db=db,
                    user=User(
                        username=outsider_name,
                        hashed_password=get_password_hash("test@123"),
                    ),
                )
            task = crud_task.create(
                db=db,
                task=Task(
                    name="compare-sample-task",
                    description="",
                    tips="",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=owner.id,
                        updated_by=owner.id,
                        data="{}",
                    ),
                    TaskSample(
                        task_id=task.id,
                        file_id=2,
                        created_by=owner.id,
                        updated_by=owner.id,
                        data="{}",
                    ),
                ],
            )
        target_sample = samples[0]
        other_sample = samples[1]
        token = create_compare_sample_access_token(task.id, target_sample.id)
        login = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": outsider_name, "password": "test@123"},
        )
        headers = {"Authorization": login.json()["data"]["token"]}

        denied = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{target_sample.id}",
            headers=headers,
        )
        allowed = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{target_sample.id}",
            headers=headers,
            params={"compare_access_token": token},
        )
        denied_other = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{other_sample.id}",
            headers=headers,
            params={"compare_access_token": token},
        )
        patched = client.patch(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/{target_sample.id}",
            headers=headers,
            params={"compare_access_token": token},
            json={"data": {"result": "{}"}, "annotated_count": 1},
        )

        assert denied.status_code == 403
        assert allowed.status_code == 200
        assert denied_other.status_code == 403
        assert patched.status_code == 200
        assert patched.json()["data"]["state"] == "DONE"

    def test_sample_delete(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
        with begin_transaction(db):
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        # run
        data = {"sample_ids": [samples[0].id]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        assert r.status_code == 200

    def test_collaborator_can_delete_sample(
        self, client: TestClient, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        collaborator_username = "sample-delete-collaborator@example.com"
        with begin_transaction(db):
            collaborator = crud_user.get_user_by_username(
                db=db, username=collaborator_username
            )
            if not collaborator:
                collaborator = crud_user.create(
                    db=db,
                    user=User(
                        username=collaborator_username,
                        hashed_password=get_password_hash("test@123"),
                    ),
                )
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            task.collaborators.append(collaborator)
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        login = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": collaborator_username, "password": "test@123"},
        )
        headers = {"Authorization": login.json()["data"]["token"]}
        r = client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=headers,
            json={"sample_ids": [samples[0].id]},
        )

        assert r.status_code == 200
        db.expire_all()
        assert crud_sample.count(db=db, task_id=task.id) == 0

    def test_sample_delete_requires_task_access(
        self, client: TestClient, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        other_username = "sample-delete-outsider@example.com"
        with begin_transaction(db):
            other = crud_user.get_user_by_username(db=db, username=other_username)
            if not other:
                other = crud_user.create(
                    db=db,
                    user=User(
                        username=other_username,
                        hashed_password=get_password_hash("test@123"),
                    ),
                )
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        login = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": other_username, "password": "test@123"},
        )
        headers = {"Authorization": login.json()["data"]["token"]}
        r = client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=headers,
            json={"sample_ids": [samples[0].id]},
        )

        assert r.status_code == 403
        assert r.json()["err_code"] == 30001
        db.expire_all()
        assert crud_sample.count(db=db, task_id=task.id) == 1

    def test_sample_delete_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=0,
                    updated_by=0,
                ),
            )

        # run
        data = {"sample_ids": [1, 2]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/samples",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        assert r.status_code == 403
        assert r.json()["err_code"] == 30001

    def test_export_sample(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    config='{"tools":[{"tool":"rectTool","config":{"isShowCursor":false,"showConfirm":false,"skipWhileNoDependencies":false,"drawOutsideTarget":false,"copyBackwardResult":false,"minWidth":1,"attributeConfigurable":true,"textConfigurable":true,"textCheckType":4,"customFormat":"","attributes":[{"key":"rectTool","value":"rectTool"}]}},{"tool":"pointTool","config":{"upperLimit":10,"isShowCursor":false,"attributeConfigurable":true,"copyBackwardResult":false,"textConfigurable":true,"textCheckType":0,"customFormat":"","attributes":[{"key":"pointTool","value":"pointTool"}]}},{"tool":"polygonTool","config":{"isShowCursor":false,"lineType":0,"lineColor":0,"drawOutsideTarget":false,"edgeAdsorption":true,"copyBackwardResult":false,"attributeConfigurable":true,"textConfigurable":true,"textCheckType":0,"customFormat":"","attributes":[{"key":"polygonTool","value":"polygonTool"}],"lowerLimitPointNum":"4","upperLimitPointNum":100}},{"tool":"lineTool","config":{"isShowCursor":false,"lineType":0,"lineColor":1,"edgeAdsorption":true,"outOfTarget":true,"copyBackwardResult":false,"attributeConfigurable":true,"textConfigurable":true,"textCheckType":4,"customFormat":"^[\s\S]{1,3}$","lowerLimitPointNum":4,"upperLimitPointNum":"","attributes":[{"key":"lineTool","value":"lineTool"}]}},{"tool":"tagTool"},{"tool":"textTool"}],"tagList":[{"key":"类别1","value":"class1","isMulti":true,"subSelected":[{"key":"选项1","value":"option1","isDefault":true},{"key":"选项2","value":"option2","isDefault":false}]},{"key":"类别2","value":"class2","isMulti":true,"subSelected":[{"key":"a选项1","value":"aoption1","isDefault":true},{"key":"a选项2","value":"aoption2","isDefault":false}]}],"attributes":[{"key":"RT","value":"RT"}],"textConfig":[{"label":"我的描述","key":"描述的关键","required":true,"default":"","maxLength":200},{"label":"我的描述1","key":"描述的关键1","required":true,"default":"","maxLength":200}],"fileInfo":{"type":"img","list":[{"id":1,"url":"/src/img/example/bear6.webp","result":"[]"}]},"commonAttributeConfigurable":true}',
                    created_by=0,
                    updated_by=0,
                ),
            )
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data='{"result": "{\"width\":1256,\"height\":647,\"valid\":true,\"rotate\":0,\"rectTool\":{\"toolName\":\"rectTool\",\"result\":[{\"x\":76.7636304422194,\"y\":86.75077939093666,\"width\":156.47058823529457,\"height\":86.47058823529437,\"label\":\"RT\",\"valid\":true,\"isVisible\":true,\"id\":\"J3eK0yr6\",\"sourceID\":\"\",\"textAttribute\":\"\",\"order\":1}]}}","urls": {"42": "http://localhost:8000/api/v1/tasks/attachment/upload/6/1/d9c34a05-screen.png"},"fileNames": {"42": ""}}',
                        annotated_count=1,
                        state="DONE",
                    )
                ],
            )

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/export?export_type=JSON",
            headers=testuser_token_headers,
            json={"sample_ids": [samples[0].id]},
        )

        # check - async export returns job info
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert data["data"]["status"] == "PENDING"
        assert data["data"]["sample_count"] == 1
        assert "id" in data["data"]

    def test_export_includes_all_states(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        """Verify NEW, DONE, SKIPPED samples are all included in export job."""

        # prepare data
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    config='{"tools":[{"tool":"rectTool","config":{"attributes":[{"key":"RT","value":"RT"}]}}],"tagList":[],"attributes":[],"textConfig":[],"fileInfo":{"type":"img","list":[]},"commonAttributeConfigurable":true}',
                    created_by=0,
                    updated_by=0,
                ),
            )
            result_data = '{"result": "{\"width\":100,\"height\":100,\"valid\":true,\"rotate\":0,\"rectTool\":{\"toolName\":\"rectTool\",\"result\":[{\"x\":10,\"y\":10,\"width\":50,\"height\":50,\"label\":\"RT\",\"valid\":true,\"isVisible\":true,\"id\":\"test1\",\"order\":1}]}}"}'
            samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data=result_data,
                        annotated_count=1,
                        state="DONE",
                    ),
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data=result_data,
                        annotated_count=0,
                        state="NEW",
                    ),
                    TaskSample(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data=result_data,
                        annotated_count=0,
                        state="SKIPPED",
                    ),
                ],
            )

        sample_ids = [s.id for s in samples]

        # run - create export job
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/export?export_type=JSON",
            headers=testuser_token_headers,
            json={"sample_ids": sample_ids},
        )

        # check - all 3 samples should be counted
        assert r.status_code == 200
        data = r.json()
        assert data["data"]["sample_count"] == 3


    def test_export_status_reports_skipped_count(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="status task",
                    description="description",
                    tips="tips",
                    config='{"tools":[{"tool":"rectTool","config":{"attributes":[{"key":"RT","value":"RT"}]}}],"tagList":[],"attributes":[],"textConfig":[],"fileInfo":{"type":"img","list":[]},"commonAttributeConfigurable":true}',
                    created_by=0,
                    updated_by=0,
                ),
            )
            job = crud_export_job.create(
                db=db,
                task_id=task.id,
                user_id=current_user.id,
                export_type="JSON",
                sample_ids=[1, 2, 3],
            )
            crud_export_job.update_status(
                db=db,
                job=job,
                status="COMPLETED",
                processed_count=1,
                file_path="/tmp/result.json",
            )

        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/samples/export/{job.id}",
            headers=testuser_token_headers,
        )

        assert r.status_code == 200
        data = r.json()["data"]
        assert data["sample_count"] == 3
        assert data["processed_count"] == 1
        assert data["skipped_count"] == 2
        assert "warning_message" in data and data["warning_message"]

    def test_sample_get_merges_attachment_into_data_for_pre_annotation_filename(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        """前端用 data.file.filename 拉预标注；仅 file_id、无 data.file 的样本须由接口补全。"""
        task = client.post(
            f"{settings.API_V1_STR}/tasks",
            headers=testuser_token_headers,
            json={"name": "preann_filename", "description": "", "tips": ""},
        )
        assert task.status_code == 201
        task_id = task.json()["data"]["id"]
        empty_task_upload(task_id, "preann_merge.png")
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            up = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": ("preann_merge.png", f, "image/png")},
            )
        assert up.status_code == 201
        fid = up.json()["data"]["id"]
        fn = up.json()["data"]["filename"]

        cr = client.post(
            f"{settings.API_V1_STR}/tasks/{task_id}/samples",
            headers=testuser_token_headers,
            json=[{"file_id": fid}],
        )
        assert cr.status_code == 201
        sid = cr.json()["data"]["ids"][0]

        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/samples/{sid}",
            headers=testuser_token_headers,
        )
        assert r.status_code == 200
        body = r.json()["data"]
        assert isinstance(body["data"], dict)
        assert body["data"]["file"]["filename"] == fn

    def test_copy_sample_to_task_only_copies_original_image(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
        with begin_transaction(db):
            src_task = crud_task.create(
                db=db,
                task=Task(
                    name="src-copy",
                    description="",
                    tips="",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            target_task = crud_task.create(
                db=db,
                task=Task(
                    name="target-copy",
                    description="",
                    tips="",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
        src_task_id = src_task.id
        target_task_id = target_task.id

        empty_task_upload(src_task_id, "copy-source.png")
        empty_task_upload(target_task_id, "copy-source.png")

        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            upload_resp = client.post(
                f"{settings.API_V1_STR}/tasks/{src_task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": ("copy-source.png", f, "image/png")},
            )
        assert upload_resp.status_code == 201
        source_file_id = upload_resp.json()["data"]["id"]

        with begin_transaction(db):
            source_samples = crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=src_task_id,
                        file_id=source_file_id,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data='{"result":"{\\"rectTool\\":{\\"result\\":[{\\"label\\":\\"garbage\\"}]}}"}',
                        annotated_count=1,
                        state="DONE",
                    )
                ],
            )
        source_sample_id = source_samples[0].id

        copy_resp = client.post(
            f"{settings.API_V1_STR}/tasks/{target_task_id}/samples/copy-from-sample",
            headers=testuser_token_headers,
            json={
                "source_task_id": src_task_id,
                "source_sample_id": source_sample_id,
            },
        )
        assert copy_resp.status_code == 201
        copied_sample_id = copy_resp.json()["data"]["ids"][0]

        source_detail = client.get(
            f"{settings.API_V1_STR}/tasks/{src_task_id}/samples/{source_sample_id}",
            headers=testuser_token_headers,
        )
        copied_detail = client.get(
            f"{settings.API_V1_STR}/tasks/{target_task_id}/samples/{copied_sample_id}",
            headers=testuser_token_headers,
        )
        assert source_detail.status_code == 200
        assert copied_detail.status_code == 200

        source_body = source_detail.json()["data"]
        copied_body = copied_detail.json()["data"]

        assert copied_body["id"] != source_body["id"]
        assert copied_body["file"]["id"] != source_body["file"]["id"]
        assert copied_body["file"]["filename"].startswith("copy-source")
        assert copied_body["state"] == "NEW"
        assert copied_body["annotated_count"] == 0
        assert isinstance(copied_body["data"], dict)
        assert copied_body["data"].get("result") in (None, "")
        assert copied_body["data"]["file"]["id"] == copied_body["file"]["id"]
        assert copied_body["file"]["url"]
