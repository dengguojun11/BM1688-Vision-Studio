import asyncio
import base64
import json
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from labelu.internal.application.command.annotation_prompt_trial import (
    AnnotationPromptTrialCommand,
)
from labelu.internal.application.service import annotation_prompt as prompt_service
from labelu.internal.application.service import annotation_prompt_trial as trial_service
from labelu.internal.application.service import vision_llm_chain
from labelu.internal.common.error_code import LabelUException


def _image_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (100, 80), (30, 40, 50)).save(stream, format="PNG")
    return stream.getvalue()


def _contract():
    return SimpleNamespace(
        id=17,
        dataset_id=9,
        revision=2,
        annotation_kind="bbox",
        labels_snapshot_json=json.dumps(
            [
                {
                    "label_value": "person",
                    "label_key": "人员",
                    "description": "可见的人体目标",
                }
            ],
            ensure_ascii=False,
        ),
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
    )


def test_data_uri_download_supports_uploaded_image():
    raw = _image_bytes()
    uri = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    decoded, mime = vision_llm_chain._download_image(uri)
    assert decoded == raw
    assert mime == "image/png"


def test_prompt_row_integrity_rejects_tampered_hash_and_contract():
    contract = _contract()
    row = SimpleNamespace(
        label_set_signature=contract.label_set_signature,
        class_order_signature=contract.class_order_signature,
        effective_prompt="prompt",
        prompt_sha256="0" * 64,
        prompt_spec_json=json.dumps(
            {
                "contract_id": contract.id,
                "contract_revision": contract.revision,
                "label_set_signature": contract.label_set_signature,
                "class_order_signature": contract.class_order_signature,
            }
        ),
    )
    try:
        trial_service._assert_prompt_row_integrity(row, contract)
    except LabelUException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("tampered prompt hash must fail closed")


def test_trial_calls_model_and_does_not_persist(monkeypatch):
    contract = _contract()
    captured = {}

    monkeypatch.setattr(
        prompt_service,
        "_contract_for_revision",
        lambda *_args, **_kwargs: (SimpleNamespace(id=9), contract),
    )
    monkeypatch.setattr(trial_service, "_latest_prompt_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(vision_llm_chain, "_image_dimensions", lambda _raw: (100, 80))
    monkeypatch.setattr(
        vision_llm_chain,
        "_prepare_image_for_inference",
        lambda raw, *, max_side=None: (raw, (50, 40)),
    )

    def fake_call(payload, *, provider_key):
        captured["payload"] = payload
        captured["provider_key"] = provider_key
        return {
            "model": "trial-model",
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 1, "y": 2, "width": 20, "height": 30},
                    "score": 0.91,
                }
            ],
        }

    monkeypatch.setattr(vision_llm_chain, "call_vision_llm_chain", fake_call)
    _, generated, _ = prompt_service._build_prompt_spec(
        contract,
        operation="bbox_detection",
        provider_key="volcengine",
        model_name="trial-model",
        profile_key="annotation_bbox_v1",
        profile_version="1",
        image_width=100,
        image_height=80,
    )
    override = generated + "\n只输出契约内目标。"
    command = AnnotationPromptTrialCommand(
        prompt_override=override,
        operation="bbox_detection",
        provider_key="volcengine",
        model_name="trial-model",
    )
    result = asyncio.run(
        trial_service.run_trial(
            object(),
            9,
            2,
            _image_bytes(),
            SimpleNamespace(id=1),
            command,
            image_content_type="image/png",
            image_name="trial.png",
        )
    )

    assert result.status == "COMPLETED"
    assert result.image_width == 100
    assert result.image_height == 80
    assert result.model_image_width == 50
    assert result.normalized_result["annotations"]["rectTool"]["result"][0]["label"] == "person"
    assert result.effective_prompt == override
    assert captured["provider_key"] == "bbox_detection"
    assert captured["payload"]["image_url"].startswith("data:image/png;base64,")
    assert captured["payload"]["contract_prompt"]["contract_revision"] == 2
