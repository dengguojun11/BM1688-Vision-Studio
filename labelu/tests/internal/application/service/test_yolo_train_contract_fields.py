from types import SimpleNamespace

import pytest

from labelu.internal.application.service import yolo_train
from labelu.internal.common.label_contract import class_order_signature


@pytest.mark.parametrize("job_type", ["plate_detection", "plate_recognition"])
def test_plate_job_contract_fields_use_runtime_class_for_legacy_rows(job_type):
    fields = yolo_train._job_contract_response_fields(
        SimpleNamespace(
            job_type=job_type,
            class_names_json=None,
            class_order_signature=None,
            label_set_signature=None,
            data_yaml_sha256=None,
            dataset_content_signature=None,
            training_recipe_key=None,
            training_recipe_version=None,
            runtime_profile=None,
            runtime_interface_version=None,
        )
    )

    assert fields["class_names"] == ["doubleEP"]
    assert fields["class_order_signature"] == class_order_signature(["doubleEP"])
    assert fields["label_set_signature"] is None


def test_standard_job_contract_fields_remain_persisted_values():
    fields = yolo_train._job_contract_response_fields(
        SimpleNamespace(
            job_type="yolo_detection",
            class_names_json='["person", "car"]',
            class_order_signature="persisted-order",
            label_set_signature="persisted-labels",
            data_yaml_sha256="yaml-sha",
            dataset_content_signature="content-sha",
            training_recipe_key="recipe",
            training_recipe_version=2,
            runtime_profile="runtime",
            runtime_interface_version=3,
        )
    )

    assert fields["class_names"] == ["person", "car"]
    assert fields["class_order_signature"] == "persisted-order"
    assert fields["label_set_signature"] == "persisted-labels"
