import codecs
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.security import get_password_hash
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_pre_annotation
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.application.service import pre_annotation as pre_annotation_service
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.tests.utils.utils import empty_task_upload, get_test_user


@pytest.mark.parametrize(
    ("filename", "payload", "sample_name"),
    [
        (
            "pre-annotation.json",
            [
                {
                    "fileName": "bird.jpg",
                    "url": "bird.jpg",
                    "result": json.dumps(
                        {"width": 100, "height": 80, "annotations": []}
                    ),
                }
            ],
            "bird.jpg",
        ),
        (
            "pre-annotation.jsonl",
            {"sample_name": "bird.jpg", "annotations": {}},
            "bird.jpg",
        ),
    ],
)
def test_read_pre_annotation_file_accepts_utf8_bom(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    payload: object,
    sample_name: str,
) -> None:
    if filename.endswith(".jsonl"):
        encoded = codecs.BOM_UTF8 + (json.dumps(payload) + "\n").encode("utf-8")
    else:
        encoded = codecs.BOM_UTF8 + json.dumps(payload).encode("utf-8")

    class InMemoryStorage:
        def exists(self, _path: str) -> bool:
            return True

        def read_text(self, _path: str, encoding: str = "utf-8") -> str:
            return encoded.decode(encoding)

    monkeypatch.setattr(
        pre_annotation_service,
        "get_storage_backend",
        lambda: InMemoryStorage(),
    )

    rows = pre_annotation_service.read_pre_annotation_file(
        TaskAttachment(filename=filename, path=filename)
    )

    assert len(rows) == 1
    actual_sample_name = (
        rows[0]["sample_name"] if filename.endswith(".jsonl") else rows[0]["fileName"]
    )
    assert actual_sample_name == sample_name
    if filename.endswith(".json"):
        assert rows[0]["result"]["width"] == 100


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


class TestClassTaskPreAnnotationRouter:
    def test_pre_annotation_requires_task_access(
        self, client: TestClient, db: Session
    ) -> None:
        owner = get_test_user(db)
        outsider_name = "pre-annotation-outsider@example.com"
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
                    name="private pre-annotation task",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            row = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        sample_name="sample.jpg",
                        data="{}",
                        created_by=owner.id,
                        updated_by=owner.id,
                    )
                ],
            )[0]
            # Keep scalar identifiers before the shared test session is cleaned
            # by another test; ORM instances may be expired/detached after the
            # transaction boundary.
            task_id = task.id
            pre_annotation_id = row.id

        login = client.post(
            f"{settings.API_V1_STR}/users/login",
            json={"username": outsider_name, "password": "test@123"},
        )
        headers = {"Authorization": login.json()["data"]["token"]}

        assert client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/pre_annotations",
            headers=headers,
            params={"page": 0, "size": 10},
        ).status_code == 403
        assert client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/pre_annotations/{pre_annotation_id}",
            headers=headers,
        ).status_code == 403
        assert client.request(
            "delete",
            f"{settings.API_V1_STR}/tasks/{task_id}/pre_annotations",
            headers=headers,
            json={"pre_annotation_ids": [pre_annotation_id]},
        ).status_code == 403

    def test_create_pre_annotation_rejects_cross_task_attachment(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        owner = get_test_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(name="pre-annotation target", created_by=owner.id, updated_by=owner.id),
            )
            other_task = crud_task.create(
                db=db,
                task=Task(name="pre-annotation source", created_by=owner.id, updated_by=owner.id),
            )
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    task_id=other_task.id,
                    filename="annotations.jsonl",
                    path="missing-annotations.jsonl",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )

        response = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=[{"file_id": attachment.id}],
        )
        assert response.status_code == 404

    def test_create_pre_annotation_successful(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
            
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        
        # prepare data
        empty_task_upload(task.id, "test.jsonl")
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
            
        data = [{"file_id": jsonl.json()["data"]["id"]}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=data,
        )

        json = r.json()
        assert r.status_code == 201
        assert len(json["data"]["ids"]) == 1

    def test_create_pre_annotation_normalizes_legacy_rect_labels(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        db: Session,
        tmp_path: Path,
    ) -> None:
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="street-vendor",
                    description="description",
                    tips="tips",
                    config=_street_vendor_task_config(),
                    media_type="IMAGE",
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )

        payload = {
            "sample_name": "legacy.jpg",
            "result": {
                "width": 100,
                "height": 80,
                "rotate": 0,
                "annotations": [
                    {
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
                    }
                ],
            },
        }
        src = tmp_path / "legacy.jsonl"
        src.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

        with src.open("rb") as f:
            uploaded = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={
                    "file": (
                        f"legacy-{uuid.uuid4().hex}.jsonl",
                        f,
                        "application/jsonl",
                    )
                },
            )
        assert uploaded.status_code == 201

        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=[{"file_id": uploaded.json()["data"]["id"]}],
        )
        assert r.status_code == 201

        rows = crud_pre_annotation.list_by_task_id_and_owner_id_and_sample_name(
            db=db,
            task_id=task.id,
            sample_name="legacy.jpg",
        )
        assert len(rows) == 1
        stored = json.loads(rows[0].data)
        rects = stored["result"]["annotations"][0]["result"]
        assert [item["label"] for item in rects] == [
            "streetVendor_vegetablet",
            "object_bucket",
        ]

    def test_create_pre_annotation_rejects_unknown_rect_labels(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        db: Session,
        tmp_path: Path,
    ) -> None:
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="street-vendor",
                    description="description",
                    tips="tips",
                    config=_street_vendor_task_config(),
                    media_type="IMAGE",
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )

        payload = {
            "sample_name": "bad.jpg",
            "result": {
                "width": 100,
                "height": 80,
                "rotate": 0,
                "annotations": [
                    {
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
                    }
                ],
            },
        }
        src = tmp_path / "bad.jsonl"
        src.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

        with src.open("rb") as f:
            uploaded = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={
                    "file": (
                        f"bad-{uuid.uuid4().hex}.jsonl",
                        f,
                        "application/jsonl",
                    )
                },
            )
        assert uploaded.status_code == 201

        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=[{"file_id": uploaded.json()["data"]["id"]}],
        )
        body = r.json()
        assert r.status_code == 400
        assert body["err_code"] == 30002
        assert "streetVendor_unknown" in body["msg"]
        
    def test_create_pre_annotation_same_sample_name_overwrites_uploaded_rows(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
            
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="name",
                    description="description",
                    tips="tips",
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        
        empty_task_upload(task.id, "test.jsonl")
        
        # prepare data
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            res = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
            
        data = [{"file_id": res.json()["data"]["id"]}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=data,
        )
        
        empty_task_upload(task.id, "test.jsonl")
        
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            res = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
            
        data = [{"file_id": res.json()["data"]["id"]}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=data,
        )

        assert r.status_code == 201

        rows = crud_pre_annotation.list_by_task_id_and_owner_id_and_sample_name(
            db=db,
            task_id=task.id,
            sample_name="test.png",
        )
        assert len(rows) == 1
        assert rows[0].file_id == res.json()["data"]["id"]

    def test_create_sample_task_not_found(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data
        data = [{"file_id": 1, "data": {}}]

        # run
        r = client.post(
            f"{settings.API_V1_STR}/tasks/0/pre_annotations",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        json = r.json()
        assert r.status_code == 404
        assert json["err_code"] == 50002

    def test_pre_annotation_list_by_page(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        
        empty_task_upload(task.id, "test.jsonl")
        
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        
        pre_annotations = [
            TaskPreAnnotation(
                task_id=task.id,
                file_id=jsonl.json()["data"]["id"],
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            for i in range(14)
        ]
        
        with begin_transaction(db):
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=pre_annotations,
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 10
        assert json["data"][0]["id"] == 1
        assert json["meta_data"]["total"] == 14

    def test_pre_annotation_list_with_sample_name(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        
        empty_task_upload(task.id, "test.png")
        empty_task_upload(task.id, "test.jsonl")
        # upload sample file
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            img = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        # upload jsonl file
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        
        samples = [
            TaskSample(
                task_id=task.id,
                file_id=img.json()["data"]["id"],
                created_by=current_user.id,
                updated_by=current_user.id,
                data="{}",
            )
        ]
        with begin_transaction(db):
            crud_sample.batch(
                db=db,
                samples=samples,
            )
        
        pre_annotations = [
            TaskPreAnnotation(
                task_id=task.id,
                file_id=jsonl.json()["data"]["id"],
                sample_name=img.json()["data"]["filename"],
                created_by=current_user.id,
                updated_by=current_user.id,
            )
        ]
        with begin_transaction(db):
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=pre_annotations,
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": img.json()['data']['filename'], "page": 0},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert json["data"][0]["id"] == 1
        assert json["meta_data"]["total"] == 1

    def test_pre_annotation_canvas_only_returns_canvas_row(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="canvas only task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    filename="pre.json",
                    path="pre.json",
                    url="/pre.json",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        with begin_transaction(db):
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=attachment.id,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"file_upload"}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"ai_generated"}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                ],
            )

        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "test.png", "page": 0, "canvas_only": True},
        )

        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 1
        assert json["data"][0]["id"] == pre_annotations[0].id
        assert json["meta_data"]["total"] == 1

    def test_pre_annotation_canvas_only_uses_latest_generated_without_upload(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="canvas generated task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"ai_generated"}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"yolo_generated"}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                ],
            )

        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "test.png", "page": 0, "canvas_only": True},
        )

        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 1
        assert json["data"][0]["id"] == pre_annotations[1].id
        assert json["meta_data"]["total"] == 1

    def test_pre_annotation_canvas_only_merges_qwen_global_tags_into_detection_row(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="canvas mixed providers task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        with begin_transaction(db):
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data=(
                            '{"annotations":{"tagTool":{"toolName":"tagTool",'
                            '"result":[{"id":"tag-1","type":"tag",'
                            '"value":{"sceneType":["urbanArterial"]}}]}},'
                            '"meta":{"source_type":"ai_generated"}}'
                        ),
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data=(
                            '{"annotations":{"rectTool":{"toolName":"rectTool",'
                            '"result":[{"id":"box-1","label":"car"}]}},'
                            '"meta":{"source_type":"vlx_seek_generated",'
                            '"apply_to_canvas_overwrite":true,'
                            '"preserve_global_annotations":true}}'
                        ),
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                ],
            )

        response = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "test.png", "page": 0, "canvas_only": True},
        )

        body = response.json()
        assert response.status_code == 200
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == pre_annotations[1].id
        merged = json.loads(body["data"][0]["data"])
        assert len(merged["annotations"]["rectTool"]["result"]) == 1
        assert merged["annotations"]["tagTool"]["result"][0]["value"] == {
            "sceneType": ["urbanArterial"]
        }

    def test_pre_annotation_canvas_only_prefers_explicit_yolo_over_upload(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="canvas yolo overwrite task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    filename="pre.json",
                    path="pre.json",
                    url="/pre.json",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        with begin_transaction(db):
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=attachment.id,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"file_upload"}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data=(
                            '{"meta":{"source_type":"yolo_generated",'
                            '"apply_to_canvas_overwrite":true},"annotations":{}}'
                        ),
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                ],
            )

        response = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "test.png", "page": 0, "canvas_only": True},
        )

        body = response.json()
        assert response.status_code == 200
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == pre_annotations[1].id
        assert body["meta_data"]["total"] == 1

    def test_pre_annotation_canvas_only_prefers_geometry_over_newer_tag_only_generated(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="canvas geometry task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"experiment_dataset_metadata"},"annotations":{"rectTool":{"toolName":"rectTool","result":[{"id":"box-1"}]}}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=None,
                        sample_name="test.png",
                        data='{"meta":{"source_type":"ai_generated","include_geometry_tools":false},"annotations":{"tagTool":{"toolName":"tagTool","result":[{"id":"tag-1"}]}}}',
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                ],
            )

        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "test.png", "page": 0, "canvas_only": True},
        )

        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 1
        assert json["data"][0]["id"] == pre_annotations[0].id
        assert json["meta_data"]["total"] == 1

    def test_pre_annotation_list_with_sample_name_not_found(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        empty_task_upload(task.id, "test.png")
        # upload sample file
        with Path("labelu/tests/data/test.png").open(mode="rb") as f:
            img = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        # upload jsonl file
        empty_task_upload(task.id, "test.jsonl")
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        
        samples = [
            TaskSample(
                task_id=task.id,
                file_id=img.json()["data"]["id"],
                created_by=current_user.id,
                updated_by=current_user.id,
                data="{}",
            )
        ]
        with begin_transaction(db):
            crud_sample.batch(
                db=db,
                samples=samples,
            )
        
        pre_annotations = [
            TaskPreAnnotation(
                task_id=task.id,
                file_id=jsonl.json()["data"]["id"],
                created_by=current_user.id,
                updated_by=current_user.id,
            )
        ]
        with begin_transaction(db):
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=pre_annotations,
            )

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "asdasd.png", "page": 0},
        )

        # check
        json = r.json()
        assert r.status_code == 200
        assert len(json["data"]) == 0
    
    def test_pre_annotations_list_by_params_error(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:

        # prepare data

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{1}/pre_annotations",
            headers=testuser_token_headers,
            params={"after": 1, "before": 1, "page": 1, "size": 10},
        )

        # check
        assert r.status_code == 422

    def test_sample_list_by_sample_name_error(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"sample_name": "", "page": 0, "size": 10},
        )

        # check
        assert r.status_code == 422

    def test_pre_annotation_get(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        with begin_transaction(db):
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    )
                ],
            )
        
        assert pre_annotations[0] is not None

        # run
        r = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/{pre_annotations[0].id}",
            headers=testuser_token_headers,
        )

        # check
        assert r.status_code == 200

    def test_pre_annotation_delete(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        with begin_transaction(db):
            pre_annotations = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=1,
                        file_id=1,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        data="{}",
                    )
                ],
            )

        # run
        data = {"pre_annotation_ids": [pre_annotations[0].id]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        assert r.status_code == 200

    def test_pre_annotations_delete_not_found(
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
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )

        # run
        data = {"pre_annotation_ids": [1, 2]}
        r = client.request("delete",
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=data,
        )

        # check
        assert r.status_code == 200

    def test_pre_annotation_files_list(
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
                    name="list files task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
        
        # 上传预注释文件
        empty_task_upload(task.id, "test.jsonl")
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl1 = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        
        # 为文件创建预注释
        client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=[{"file_id": jsonl1.json()["data"]["id"]}],
        )

        # 测试 page 列表
        r1 = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10},
        )

        # 检查
        json1 = r1.json()
        assert r1.status_code == 200
        assert len(json1["data"]) == 1
        assert json1["meta_data"]["total"] == 1
        
        # 测试 before 列表
        r2 = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"before": json1["data"][0]["id"], "size": 10},
        )
        
        # 检查
        json2 = r2.json()
        assert r2.status_code == 200
        assert len(json2["data"]) == 0
        assert json2["meta_data"]["total"] == 0
        
        # 测试 after 列表
        r3 = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"after": json1["data"][0]["id"], "size": 10},
        )
        
        # 检查
        json3 = r3.json()
        assert r3.status_code == 200
        assert len(json3["data"]) == 0  # 没有更多的文件了
        
        # 测试排序
        r4 = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10, "sort": "created_at:desc"},
        )
        
        # 检查
        json4 = r4.json()
        assert r4.status_code == 200
        assert len(json4["data"]) == 1
        # 检查降序排序（最新的文件应该在前面）
        # assert json4["data"][0]["id"] > json4["data"][1]["id"]

    def test_pre_annotation_files_list_searches_filename_and_id(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        owner = get_test_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="search pre-annotation files",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            attachments = [
                crud_attachment.create(
                    db=db,
                    attachment=TaskAttachment(
                        task_id=task.id,
                        filename="night-road.jsonl",
                        url="/attachments/night-road.jsonl",
                        path="night-road.jsonl",
                        created_by=owner.id,
                        updated_by=owner.id,
                    ),
                ),
                crud_attachment.create(
                    db=db,
                    attachment=TaskAttachment(
                        task_id=task.id,
                        filename="day-road.jsonl",
                        url="/attachments/day-road.jsonl",
                        path="day-road.jsonl",
                        created_by=owner.id,
                        updated_by=owner.id,
                    ),
                ),
            ]
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=attachment.id,
                        created_by=owner.id,
                        updated_by=owner.id,
                    )
                    for attachment in attachments
                ],
            )
            task_id = task.id
            second_attachment_id = attachments[1].id

        by_filename = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 20, "q": "night-road"},
        )
        assert by_filename.status_code == 200
        assert [row["filename"] for row in by_filename.json()["data"]] == [
            "night-road.jsonl"
        ]
        assert by_filename.json()["meta_data"]["total"] == 1

        by_id = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 20, "q": str(second_attachment_id)},
        )
        assert by_id.status_code == 200
        assert [row["id"] for row in by_id.json()["data"]] == [second_attachment_id]
        assert by_id.json()["meta_data"]["total"] == 1

    def test_pre_annotation_list_searches_sample_filename_and_id(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        owner = get_test_user(db)
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="search pre-annotation rows",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    task_id=task.id,
                    filename="night-road.jsonl",
                    url="/attachments/night-road.jsonl",
                    path="night-road.jsonl",
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            rows = crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task.id,
                        file_id=attachment.id,
                        sample_name="night-road-alpha.jpg",
                        created_by=owner.id,
                        updated_by=owner.id,
                        data="{}",
                    ),
                    TaskPreAnnotation(
                        task_id=task.id,
                        sample_name="day-road-beta.jpg",
                        created_by=owner.id,
                        updated_by=owner.id,
                        data="{}",
                    ),
                ],
            )
            first_row_id = rows[0].id

        by_sample = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"page": 0, "size": 20, "q": "night-road"},
        )
        assert by_sample.status_code == 200, by_sample.text
        assert [row["id"] for row in by_sample.json()["data"]] == [first_row_id]
        assert by_sample.json()["meta_data"]["total"] == 1

        by_id = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            params={"page": 0, "size": 20, "q": str(first_row_id)},
        )
        assert by_id.status_code == 200, by_id.text
        assert [row["id"] for row in by_id.json()["data"]] == [first_row_id]
        
    def test_superuser_lists_pre_annotation_files_from_other_users(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        testadmin_token_headers: dict,
        db: Session,
    ) -> None:
        current_user = crud_user.get_user_by_username(
            db=db, username="test@example.com"
        )
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="admin lists files task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )

        empty_task_upload(task.id, "test.jsonl")
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            uploaded = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        assert uploaded.status_code == 201

        created = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=[{"file_id": uploaded.json()["data"]["id"]}],
        )
        assert created.status_code == 201

        response = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testadmin_token_headers,
            params={"page": 0, "size": 10},
        )

        assert response.status_code == 200
        assert response.json()["meta_data"]["total"] == 1
        assert response.json()["data"][0]["id"] == uploaded.json()["data"]["id"]

        deleted = client.delete(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files/"
            f"{uploaded.json()['data']['id']}",
            headers=testadmin_token_headers,
        )
        assert deleted.status_code == 200
        assert deleted.json()["data"]["ok"] is True

        after_delete = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testadmin_token_headers,
            params={"page": 0, "size": 10},
        )
        assert after_delete.status_code == 200
        assert after_delete.json()["data"] == []

    def test_pre_annotation_files_list_with_errors(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # 测试不存在的任务
        r = client.get(
            f"{settings.API_V1_STR}/tasks/9999/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10},
        )
        
        # 检查
        assert r.status_code == 200
        
        # 测试无效的排序规则
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="invalid sort task",
                    description="description",
                    tips="tips",
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        
        r2 = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10, "sort": "invalid:desc"},
        )
        
        # 检查 - 应该返回验证错误
        assert r2.status_code == 422

    def test_delete_pre_annotation_file(
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
                    name="delete file task",
                    description="description",
                    tips="tips",
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
        
        # 上传预注释文件
        empty_task_upload(task.id, "test.jsonl")
        with Path("labelu/tests/data/test.jsonl").open(mode="rb") as f:
            jsonl = client.post(
                f"{settings.API_V1_STR}/tasks/{task.id}/attachments",
                headers=testuser_token_headers,
                files={"file": f},
            )
        
        file_id = jsonl.json()["data"]["id"]
        
        # 为文件创建预注释
        pre_resp = client.post(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations",
            headers=testuser_token_headers,
            json=[{"file_id": file_id}],
        )
        
        # 删除预注释文件
        r = client.delete(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files/{file_id}",
            headers=testuser_token_headers,
        )
        
        # 检查
        json = r.json()
        assert r.status_code == 200
        assert json["data"]["ok"] == True
        
        # 确认文件已被删除
        files_resp = client.get(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files",
            headers=testuser_token_headers,
            params={"page": 0, "size": 10},
        )
        
        assert len(files_resp.json()["data"]) == 0
        
    def test_delete_pre_annotation_file_errors(
        self, client: TestClient, testuser_token_headers: dict, db: Session
    ) -> None:
        # 测试不存在的任务
        r1 = client.delete(
            f"{settings.API_V1_STR}/tasks/9999/pre_annotations/files/1",
            headers=testuser_token_headers,
        )
        
        # 检查
        assert r1.status_code == 404
        
        # 测试不存在的文件
        with begin_transaction(db):
            task = crud_task.create(
                db=db,
                task=Task(
                    name="not found file task",
                    description="description",
                    tips="tips",
                    created_by=get_test_user(db).id,
                    updated_by=get_test_user(db).id,
                ),
            )
        
        r2 = client.delete(
            f"{settings.API_V1_STR}/tasks/{task.id}/pre_annotations/files/9999",
            headers=testuser_token_headers,
        )
        
        # 检查
        assert r2.status_code == 404
