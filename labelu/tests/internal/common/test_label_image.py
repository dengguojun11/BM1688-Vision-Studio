import json

from labelu.internal.application.command.sample import CopySamplesByLabelCommand
from labelu.internal.common.label_image import sample_data_with_only_label


def test_copy_command_normalizes_legacy_and_batch_ids():
    command = CopySamplesByLabelCommand(label_id=7, label_ids=[7, 8, 0])

    assert command.normalized_label_ids == [7, 8]


def test_sample_data_with_only_label_keeps_selected_rectangles():
    sample = {
        "result": json.dumps(
            {
                "rectTool": {
                    "result": [
                        {"id": "person", "label": "person", "x": 1},
                        {"id": "vehicle", "label": "vehicle", "x": 2},
                    ]
                },
                "tagTool": {"result": [{"label": "scene"}]},
            }
        ),
        "file": {"id": 12},
    }

    filtered = sample_data_with_only_label(sample, ["person"])

    assert filtered is not None
    result = json.loads(filtered["result"])
    assert result["rectTool"]["result"] == [{"id": "person", "label": "person", "x": 1}]
    assert result["tagTool"] == {"result": [{"label": "scene"}]}
