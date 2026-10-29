from types import SimpleNamespace

from fastapi.testclient import TestClient

from labelu.internal.common.algorithm_registry import (
    ClassificationAdapter,
    ContainerTrainingAdapter,
    CustomContainerAdapter,
    PoseAdapter,
    SegmentationAdapter,
    TrainingAdapter,
    YoloDetectionAdapter,
    algorithm_spec,
    adapter_capabilities,
    get_training_adapter,
)
from labelu.internal.application.service.algorithm import capability_state
from labelu.internal.common.training_worker import (
    ContainerTrainingWorker,
    PlaceholderTrainingWorker,
    TRAINING_PROTOCOL_VERSION,
    make_command,
    validate_command,
)


def test_algorithm_registry_requires_authentication(client: TestClient):
    assert client.get("/api/v1/algorithms").status_code == 403
    assert client.get("/api/v1/algorithm-groups").status_code == 403


def test_capability_state_separates_runtime_rules_from_quality_ready_models():
    rule = SimpleNamespace(
        status="ACTIVE", algorithm_kind="rule_template", training_adapter="rule_template", quality_gate_json=None
    )
    assert capability_state(rule, adapter_available=True, adapter_detail={"implemented": ["validate_runtime"]}) == (
        "RUNTIME_READY",
        False,
        False,
    )
    model = SimpleNamespace(
        status="ACTIVE", algorithm_kind="model", training_adapter="yolo_detection", quality_gate_json='{"map50": 0.5}'
    )
    assert capability_state(
        model,
        adapter_available=True,
        adapter_detail={"implemented": ["prepare_dataset", "train", "evaluate", "export", "validate_runtime"]},
    ) == ("QUALITY_READY", True, False)


def test_algorithm_registry_lists_seeded_yolo_capability(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    groups = client.get("/api/v1/algorithm-groups", headers=testuser_token_headers)
    assert groups.status_code == 200
    assert groups.json()["meta_data"] == {"total": 4, "page": 0, "size": 4}
    assert [row["group_key"] for row in groups.json()["data"]] == [
        "person_vehicle_detection",
        "person_behavior",
        "urban_management",
        "fire_environment",
    ]

    listed = client.get("/api/v1/algorithms", headers=testuser_token_headers)
    assert listed.status_code == 200
    assert listed.json()["meta_data"]["total"] == 36
    rows = listed.json()["data"]
    yolo = next(row for row in rows if row["algorithm_key"] == "yolo_detection")
    assert yolo["task_type"] == "object_detection"
    assert yolo["group_key"] == "person_vehicle_detection"
    assert yolo["training_adapter"] == "yolo_detection"
    assert yolo["required_tools"] == ["rectTool", "tagTool"]
    assert "bm1688" in yolo["supported_chips"]
    assert yolo["adapter_available"] is True
    assert yolo["capability_state"] == "RUNTIME_READY"
    assert yolo["trainable"] is True
    assert yolo["quality_ready"] is False
    assert yolo["publishable"] is False

    products = [row for row in rows if row.get("catalog_kind") == "product"]
    assert len(products) == 31
    assert sum(row["algorithm_kind"] == "rule_template" for row in products) == 3
    assert {row["algorithm_key"] for row in products} >= {
        "person_intrusion",
        "boundary_crossing",
        "near_water_danger",
    }
    rule = next(row for row in products if row["algorithm_key"] == "boundary_crossing")
    assert rule["runtime_adapter"] == "rule_engine"
    assert rule["rule_schema"]["version"] == "1"
    assert rule["trainable"] is False


def test_algorithm_groups_support_server_search_and_pagination(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    filtered = client.get(
        "/api/v1/algorithm-groups",
        params={"q": "人车", "page": 0, "size": 1},
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200, filtered.text
    payload = filtered.json()
    assert payload["meta_data"] == {"total": 1, "page": 0, "size": 1}
    assert payload["data"][0]["group_key"] == "person_vehicle_detection"

    empty_page = client.get(
        "/api/v1/algorithm-groups",
        params={"q": "人车", "page": 1, "size": 1},
        headers=testuser_token_headers,
    )
    assert empty_page.status_code == 200, empty_page.text
    assert empty_page.json()["meta_data"] == {"total": 1, "page": 1, "size": 0}
    assert empty_page.json()["data"] == []


def test_algorithm_registration_requires_a_registered_adapter_and_supports_custom_container(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
):
    groups = client.get(
        "/api/v1/algorithm-groups", headers=testadmin_token_headers
    )
    assert groups.status_code == 200, groups.text
    group_id = groups.json()["data"][0]["id"]
    payload = {
        "algorithm_key": "operator_custom_classifier",
        "name": "运营自定义分类器",
        "task_type": "image_classification",
        "algorithm_group_id": group_id,
        "training_adapter": "custom_container",
        "evaluation_adapter": "custom_container",
        "supported_chips": ["cuda"],
        "algorithm_kind": "model",
        "catalog_kind": "operator",
        "quality_gate": {"min_map50": 0.5},
    }
    created = client.post(
        "/api/v1/algorithms",
        json=payload,
        headers=testadmin_token_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["adapter_available"] is True
    assert created.json()["data"]["trainable"] is True
    assert created.json()["data"]["capability_state"] == "QUALITY_READY"
    assert created.json()["data"]["quality_ready"] is True
    assert created.json()["data"]["publishable"] is False

    invalid = client.post(
        "/api/v1/algorithms",
        json={**payload, "algorithm_key": "broken_operator_model", "training_adapter": "missing_adapter"},
        headers=testadmin_token_headers,
    )
    assert invalid.status_code == 400, invalid.text
    assert "training_adapter 未注册" in invalid.text


def test_algorithm_update_rejects_an_unregistered_adapter(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
):
    listed = client.get("/api/v1/algorithms", headers=testadmin_token_headers)
    assert listed.status_code == 200, listed.text
    yolo = next(item for item in listed.json()["data"] if item["algorithm_key"] == "yolo_detection")
    response = client.patch(
        f"/api/v1/algorithms/{yolo['id']}",
        json={"training_adapter": "missing_adapter"},
        headers=testadmin_token_headers,
    )
    assert response.status_code == 400, response.text
    assert "training_adapter 未注册" in response.text


def test_algorithm_and_provider_catalog_pages_return_stable_metadata(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    first = client.get(
        "/api/v1/algorithms", params={"page": 0, "size": 5}, headers=testuser_token_headers
    )
    second = client.get(
        "/api/v1/algorithms", params={"page": 1, "size": 5}, headers=testuser_token_headers
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["meta_data"] == {"total": 36, "page": 0, "size": 5}
    assert second.json()["meta_data"] == {"total": 36, "page": 1, "size": 5}
    assert first.json()["data"][0]["id"] != second.json()["data"][0]["id"]

    providers = client.get(
        "/api/v1/providers", params={"page": 0, "size": 2}, headers=testuser_token_headers
    )
    assert providers.status_code == 200
    assert providers.json()["meta_data"] == {"total": 6, "page": 0, "size": 2}
    assert len(providers.json()["data"]) == 2


def test_algorithm_detail_filter_and_capability_probe(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    filtered = client.get(
        "/api/v1/algorithms",
        params={
            "group_key": "person_vehicle_detection",
            "task_type": "object_detection",
        },
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200
    assert [row["algorithm_key"] for row in filtered.json()["data"]] == [
        "yolo_detection"
    ]

    detail = client.get(
        "/api/v1/algorithms/yolo_detection", headers=testuser_token_headers
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["default_recipe"] == "yolo_detection_v1"

    capability = client.get(
        "/api/v1/algorithms/yolo_detection/capabilities", headers=testuser_token_headers
    )
    assert capability.status_code == 200
    data = capability.json()["data"]
    assert data["available"] is True
    assert data["stages"] == [
        "prepare_dataset",
        "train",
        "evaluate",
        "export",
        "validate_runtime",
    ]
    assert data["implemented"] == data["stages"]

    missing = client.get(
        "/api/v1/algorithms/not_registered", headers=testuser_token_headers
    )
    assert missing.status_code == 404

    rule_capability = client.get(
        "/api/v1/algorithms/boundary_crossing/capabilities",
        headers=testuser_token_headers,
    )
    assert rule_capability.status_code == 200
    assert rule_capability.json()["data"]["execution_mode"] == "rule"
    assert rule_capability.json()["data"]["stages"] == ["validate_runtime"]


def test_algorithm_list_exposes_server_pagination_metadata(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.get(
        "/api/v1/algorithms",
        params={"page": 1, "size": 2},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2
    assert body["meta_data"] == {"total": 36, "page": 1, "size": 2}


def test_algorithm_spec_resolves_product_catalog_key():
    spec = algorithm_spec("boundary_crossing")
    assert spec is not None
    assert spec.algorithm_kind == "rule_template"


def test_yolo_adapter_delegates_without_owning_job_lifecycle():
    adapter = get_training_adapter("yolo_detection")
    assert isinstance(adapter, TrainingAdapter)
    assert isinstance(adapter, YoloDetectionAdapter)
    assert adapter.train({"task_id": 7}) == {
        "adapter_key": "yolo_detection",
        "stage": "train",
        "request": {"task_id": 7, "job_type": "yolo_detection"},
    }
    capabilities = adapter_capabilities("yolo_detection")
    assert capabilities["implemented"] == capabilities["stages"]


def test_model_family_adapters_share_container_contract_without_new_lifecycle():
    for key in ("classification", "segmentation", "pose", "custom_container"):
        adapter = get_training_adapter(key)
        assert isinstance(adapter, ContainerTrainingAdapter)
        assert adapter_capabilities(key)["execution_mode"] == "container"
        payload = adapter.prepare_dataset({"dataset_version_id": 12})
        assert payload["adapter_key"] == key
        assert payload["request"]["job_type"] == key
        try:
            adapter.train({"dataset_version_id": 12})
        except ValueError as exc:
            assert "container_image" in str(exc)
        else:  # pragma: no cover - contract must reject an incomplete command
            raise AssertionError("container adapter accepted a missing image")


def test_model_family_adapters_have_named_types_for_extension_points():
    assert isinstance(get_training_adapter("classification"), ClassificationAdapter)
    assert isinstance(get_training_adapter("segmentation"), SegmentationAdapter)
    assert isinstance(get_training_adapter("pose"), PoseAdapter)
    assert isinstance(get_training_adapter("custom_container"), CustomContainerAdapter)


def test_container_adapter_builds_versioned_worker_command():
    adapter = get_training_adapter("classification")
    envelope = adapter.train(
        {
            "job_id": 42,
            "dataset_version_id": 12,
            "container_image": "registry.example/classifier:1",
            "hyperparameters": {"epochs": 2},
            "command_id": "cmd-42",
        }
    )
    assert envelope["protocol_version"] == TRAINING_PROTOCOL_VERSION
    assert envelope["command"] == {
        "protocol_version": TRAINING_PROTOCOL_VERSION,
        "command_id": "cmd-42",
        "adapter_key": "classification",
        "execution_mode": "container",
        "stage": "train",
        "request": envelope["request"],
        "container": {"image": "registry.example/classifier:1"},
    }


def test_worker_command_preserves_positive_fencing_token():
    command = make_command(
        adapter_key="pose",
        stage="train",
        request={"dataset_version_id": 1},
        command_id="pose-fenced",
        container_image="pose:latest",
        fencing_token=12,
    )
    assert command["fencing_token"] == 12


def test_worker_protocol_rejects_invalid_stage_and_protocol():
    command = make_command(
        adapter_key="pose",
        stage="train",
        request={"dataset_version_id": 1},
        command_id="pose-1",
        container_image="pose:latest",
    )
    invalid_stage = dict(command, stage="unknown")
    try:
        validate_command(invalid_stage)
    except ValueError as exc:
        assert "unsupported training stage" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid stage accepted")
    invalid_protocol = dict(command, protocol_version="legacy.v0")
    try:
        validate_command(invalid_protocol)
    except ValueError as exc:
        assert "unsupported training protocol" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid protocol accepted")


def test_placeholder_worker_accepts_without_claiming_completion():
    command = make_command(
        adapter_key="segmentation",
        stage="prepare_dataset",
        request={"dataset_version_id": 9},
        command_id="seg-9",
    )
    result = PlaceholderTrainingWorker().execute(command)
    assert result["status"] == "ACCEPTED"
    assert result["command_id"] == "seg-9"
    assert result["artifacts"] == []

    # Queue consumers may pass the adapter compatibility envelope directly.
    wrapped = get_training_adapter("segmentation").prepare_dataset(
        {"dataset_version_id": 9, "command_id": "seg-9"}
    )
    assert PlaceholderTrainingWorker().execute(wrapped)["command_id"] == "seg-9"


def test_container_worker_builds_safe_argv_and_normalizes_runner_result():
    seen = []

    def runner(argv):
        seen.append(argv)
        return {"status": "SUCCEEDED", "metrics": {"accuracy": 0.9}}

    command = make_command(
        adapter_key="custom_container",
        stage="evaluate",
        request={"dataset_version_id": 3, "container_image": "custom:v2"},
        command_id="custom-3",
        container_image="custom:v2",
    )
    result = ContainerTrainingWorker(runner=runner).execute(command)
    assert result["status"] == "SUCCEEDED"
    assert result["metrics"] == {"accuracy": 0.9}
    assert seen[0][:7] == [
        "docker",
        "run",
        "--rm",
        "custom:v2",
        "--stage",
        "evaluate",
        "--command-id",
    ]
    assert "--request-json" in seen[0]
