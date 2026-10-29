"""HTTP contract tests for model-series and branch lineage APIs."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def _api(path: str) -> str:
    return f"{settings.API_V1_STR}{path}"


def _contract(*, weather: str = "rain", dataset_version_id: int | None = None) -> dict:
    contract = {
        "schema_version": 1,
        "dataset_scope": {"dataset_id": 7},
        "mode": "label",
        "task_ids": [11, 12],
        "selected_label_ids": [3, 5],
        "sample_conditions": [{"dimension": "weather", "values": [weather]}],
        "annotation_contract": {"class_order": ["car", "person"]},
        "recipe": {"model_family": "yolo_detection_v1"},
    }
    if dataset_version_id is not None:
        contract["dataset_scope"]["dataset_version_id"] = dataset_version_id
    return contract


def _create_series(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str | None = None,
    description: str = "道路目标检测系列",
):
    name = name or f"道路目标-{uuid4().hex[:10]}"
    response = client.post(
        _api("/model-series"),
        json={"name": name, "description": description, "model_family": "yolo"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _create_user_and_headers(client: TestClient) -> dict[str, str]:
    headers, _ = _create_user_and_headers_with_username(client)
    return headers


def _create_user_and_headers_with_username(
    client: TestClient,
) -> tuple[dict[str, str], str]:
    username = f"model-series-outsider-{uuid4().hex[:12]}@example.com"
    password = "User@123"
    # Creating through the API keeps this helper aligned with the production
    # account lifecycle while the direct row lookup is intentionally avoided.
    signup = client.post(
        _api("/users/signup"),
        json={"username": username, "password": password},
    )
    assert signup.status_code == 201, signup.text
    login = client.post(
        _api("/users/login"),
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": login.json()["data"]["token"]}, username


def _seed_algorithm(db: Session) -> int:
    suffix = uuid4().hex[:10]
    group = AlgorithmGroup(
        group_key=f"model_series_test_group_{suffix}",
        name="模型系列测试算法组",
        status="ACTIVE",
        sort_order=1,
    )
    db.add(group)
    db.flush()
    algorithm = AlgorithmDefinition(
        algorithm_key=f"model_series_test_algorithm_{suffix}",
        name="模型系列测试算法",
        task_type="object_detection",
        algorithm_group_id=group.id,
        required_tools_json="[]",
        required_attributes_json="[]",
        training_adapter="yolo_detection",
        evaluation_adapter="yolo_detection",
        supported_chips_json='["BM1688"]',
        status="ACTIVE",
    )
    db.add(algorithm)
    db.commit()
    db.refresh(algorithm)
    return int(algorithm.id)


def test_create_duplicate_and_search_series(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    series = _create_series(client, testuser_token_headers, name="道路目标系列-搜索")

    duplicate = client.post(
        _api("/model-series"),
        json={"name": series["name"]},
        headers=testuser_token_headers,
    )
    assert duplicate.status_code == 409, duplicate.text

    listing = client.get(
        _api("/model-series"),
        params={"q": "目标系列-搜", "page": 0, "size": 10},
        headers=testuser_token_headers,
    )
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert payload["meta_data"]["total"] == 1
    assert payload["meta_data"]["page"] == 0
    assert payload["data"][0]["id"] == series["id"]
    assert payload["data"][0]["variant_count"] == 0


def test_resolve_is_idempotent_and_contract_change_creates_branch(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    first = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(weather="rain"),
            "series_name": "道路目标迭代系列",
            "variant_name": "白天下雨",
        },
        headers=testuser_token_headers,
    )
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]
    assert first_data["series_name"] == "道路目标迭代系列"
    assert first_data["variant_name"] == "白天下雨"
    assert len(first_data["branch_key"]) == 64
    assert len(first_data["selection_contract_sha256"]) == 64
    assert first_data["inheritance_mode"] == "from_scratch"

    repeated = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(weather="rain"),
            "series_name": "道路目标迭代系列",
        },
        headers=testuser_token_headers,
    )
    assert repeated.status_code == 200, repeated.text
    repeated_data = repeated.json()["data"]
    assert repeated_data["series_id"] == first_data["series_id"]
    assert repeated_data["variant_id"] == first_data["variant_id"]

    changed = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(weather="fog"),
            "series_name": "道路目标迭代系列",
        },
        headers=testuser_token_headers,
    )
    assert changed.status_code == 200, changed.text
    changed_data = changed.json()["data"]
    assert changed_data["series_id"] == first_data["series_id"]
    assert changed_data["variant_id"] != first_data["variant_id"]
    assert changed_data["branch_key"] != first_data["branch_key"]

    detail = client.get(
        _api(f"/model-series/{first_data['series_id']}"),
        headers=testuser_token_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["variant_count"] == 2


def test_resolve_rejects_stale_branch_key_when_snapshot_is_bound(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(dataset_version_id=42),
            "branch_key": "0" * 64,
            "series_name": "道路目标快照系列",
        },
        headers=testuser_token_headers,
    )
    assert response.status_code == 409, response.text
    assert "branch_key" in response.text


def test_resolve_reuses_branch_when_only_dataset_snapshot_changes(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    first = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(dataset_version_id=101),
            "series_name": "道路目标快照迭代系列",
        },
        headers=testuser_token_headers,
    )
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]

    refreshed = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(dataset_version_id=102),
            "series_name": "道路目标快照迭代系列",
        },
        headers=testuser_token_headers,
    )
    assert refreshed.status_code == 200, refreshed.text
    refreshed_data = refreshed.json()["data"]

    assert refreshed_data["series_id"] == first_data["series_id"]
    assert refreshed_data["variant_id"] == first_data["variant_id"]
    assert refreshed_data["branch_key"] == first_data["branch_key"]
    assert (
        refreshed_data["selection_contract_sha256"]
        != first_data["selection_contract_sha256"]
    )


def test_series_visibility_is_owner_scoped(
    client: TestClient,
    db: Session,
    testuser_token_headers: dict[str, str],
):
    series = _create_series(client, testuser_token_headers, name="仅所有者可见系列")
    outsider_headers = _create_user_and_headers(client)

    listing = client.get(_api("/model-series"), headers=outsider_headers)
    assert listing.status_code == 200, listing.text
    assert all(item["id"] != series["id"] for item in listing.json()["data"])

    detail = client.get(_api(f"/model-series/{series['id']}"), headers=outsider_headers)
    assert detail.status_code == 404, detail.text

    resolve = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(),
            "series_id": series["id"],
            "create_if_missing": False,
        },
        headers=outsider_headers,
    )
    assert resolve.status_code == 403, resolve.text


def test_parent_model_requires_visibility(
    client: TestClient,
    db: Session,
    testuser_token_headers: dict[str, str],
):
    series = _create_series(client, testuser_token_headers, name="父模型权限系列")
    owner = db.query(User).filter(User.username == "test@example.com").one()
    algorithm_id = _seed_algorithm(db)
    parent = ModelVersion(
        algorithm_id=algorithm_id,
        series_id=series["id"],
        model_key=f"model-series-parent-{uuid4().hex}",
        version_no=1,
        display_name="父模型 v1",
        status="CANDIDATE",
        created_by=owner.id,
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    outsider_headers = _create_user_and_headers(client)
    denied = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(),
            "parent_model_version_id": parent.id,
            "inheritance_mode": "fine_tune",
            "series_name": "外部父模型尝试",
        },
        headers=outsider_headers,
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(),
            "parent_model_version_id": parent.id,
            "inheritance_mode": "fine_tune",
        },
        headers=testuser_token_headers,
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["parent_model_version_id"] == parent.id
    assert allowed.json()["data"]["inheritance_mode"] == "fine_tune"


def test_task_collaborator_can_iterate_authorized_parent_model_in_same_series(
    client: TestClient,
    db: Session,
    testuser_token_headers: dict[str, str],
):
    """A task collaborator may use a completed parent model, but not fork its family."""

    series = _create_series(client, testuser_token_headers, name="协同父模型系列")
    owner = db.query(User).filter(User.username == "test@example.com").one()
    collaborator_headers, collaborator_username = (
        _create_user_and_headers_with_username(client)
    )
    collaborator = db.query(User).filter(User.username == collaborator_username).one()
    algorithm_id = _seed_algorithm(db)

    task = Task(
        name=f"协同迭代任务-{uuid4().hex[:8]}",
        description="model-series collaborator permission test",
        media_type="IMAGE",
        status="CONFIGURED",
        created_by=owner.id,
        updated_by=owner.id,
        source_type="internal",
    )
    db.add(task)
    db.flush()
    db.add(TaskCollaborator(task_id=task.id, user_id=collaborator.id))

    parent_job = YoloTrainJob(
        task_id=task.id,
        created_by=owner.id,
        series_id=series["id"],
        status=YoloTrainStatus.COMPLETED.value,
        job_type="yolo_detection",
        idempotency_key=f"model-series-parent-job-{uuid4().hex}",
    )
    db.add(parent_job)
    db.flush()
    parent_model = ModelVersion(
        algorithm_id=algorithm_id,
        series_id=series["id"],
        model_key=f"model-series-collab-parent-{uuid4().hex}",
        version_no=1,
        display_name="协同父模型 v1",
        status="CANDIDATE",
        created_by=owner.id,
        training_job_id=parent_job.id,
    )
    db.add(parent_model)
    db.commit()
    db.refresh(parent_model)

    resolve = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(weather="fog"),
            "parent_model_version_id": parent_model.id,
            "inheritance_mode": "fine_tune",
        },
        headers=collaborator_headers,
    )
    assert resolve.status_code == 200, resolve.text
    data = resolve.json()["data"]
    assert data["series_id"] == series["id"]
    assert data["parent_model_version_id"] == parent_model.id
    assert data["inheritance_mode"] == "fine_tune"

    mismatched_job = YoloTrainJob(
        task_id=task.id,
        created_by=owner.id,
        series_id=series["id"],
        status=YoloTrainStatus.COMPLETED.value,
        job_type="yolo_detection",
        idempotency_key=f"model-series-mismatched-job-{uuid4().hex}",
    )
    db.add(mismatched_job)
    db.commit()
    mismatch = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(weather="hail"),
            "parent_model_version_id": parent_model.id,
            "parent_training_job_id": mismatched_job.id,
            "inheritance_mode": "fine_tune",
        },
        headers=collaborator_headers,
    )
    assert mismatch.status_code == 409, mismatch.text
    assert "父模型与父训练作业不一致" in mismatch.text

    other_series = _create_series(
        client, testuser_token_headers, name="协同父模型其他系列"
    )
    cross_series = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(weather="clear"),
            "series_id": other_series["id"],
            "parent_model_version_id": parent_model.id,
            "inheritance_mode": "fine_tune",
        },
        headers=collaborator_headers,
    )
    assert cross_series.status_code == 409, cross_series.text


def test_resolve_rejects_invalid_inheritance_mode(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.post(
        _api("/model-series/resolve"),
        json={
            "selection_contract": _contract(),
            "inheritance_mode": "not-a-mode",
        },
        headers=testuser_token_headers,
    )
    assert response.status_code == 400, response.text
    assert "inheritance_mode" in response.text
