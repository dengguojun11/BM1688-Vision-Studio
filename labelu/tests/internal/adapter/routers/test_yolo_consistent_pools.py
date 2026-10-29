from __future__ import annotations

import json

from labelu.internal.adapter.persistence import crud_task, crud_user
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus


def test_consistent_pool_list_returns_source_category_to_admin_only(
    client,
    db,
    testadmin_token_headers,
    testuser_token_headers,
):
    with begin_transaction(db):
        admin = crud_user.get_user_by_username(db=db, username="admin@123.com")
        category = DatasetCategory(
            category_key="road_parking",
            name="非机动车违停",
            status="active",
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(category)
        db.flush()
        pool = crud_task.create(
            db=db,
            task=Task(
                name="非机动车违停-训练筛选一致",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.CONFIGURED.value,
                config=json.dumps(
                    {
                        "consistent_pool": {
                            "kind": "train_filtered_consistent",
                            "category": "非机动车违停",
                            "label_set_signature": "a" * 64,
                            "class_names": ["person", "electric_bike"],
                            "dataset_category_scope": f"category:{category.id}",
                            "dataset_category_id": int(category.id),
                            "dataset_category_key": category.category_key,
                        }
                    },
                    ensure_ascii=False,
                ),
                created_by=admin.id,
                updated_by=admin.id,
                source_type="consistent_pool",
                source_system="labelu",
                source_external_task_id="a" * 64,
                dataset_category_id=int(category.id),
            ),
        )
        pool_id = int(pool.id)
        category_id = int(category.id)

    admin_response = client.get(
        f"{settings.API_V1_STR}/yolo-train/consistent-pools",
        headers=testadmin_token_headers,
    )
    assert admin_response.status_code == 200, admin_response.text
    admin_rows = admin_response.json()["data"]
    assert admin_rows == [
        {
            "category": "非机动车违停",
            "category_scope_key": f"category:{category_id}",
            "dataset_category_id": category_id,
            "dataset_category_key": "road_parking",
            "dataset_category_name": "非机动车违停",
            "task_id": pool_id,
            "task_name": "非机动车违停-训练筛选一致",
            "sample_count": 0,
            "label_set_signature": "a" * 64,
            "class_names": ["person", "electric_bike"],
            "admin_only": True,
            "updated_at": admin_rows[0]["updated_at"],
            "created_at": admin_rows[0]["created_at"],
        }
    ]

    regular_response = client.get(
        f"{settings.API_V1_STR}/yolo-train/consistent-pools",
        headers=testuser_token_headers,
    )
    assert regular_response.status_code == 200, regular_response.text
    assert regular_response.json()["data"] == []
