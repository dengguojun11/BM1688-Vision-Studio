from labelu.internal.application.command.training_task import (
    PreviewLabelDimensionTrainingTaskCommand,
)
from labelu.internal.application.service.training_task import (
    _increment_sample_condition_counts,
    _sample_matches_conditions,
)


def _result(**tags):
    return {
        "tagTool": {
            "result": [
                {"value": tags},
            ]
        }
    }


def test_sample_conditions_use_or_within_dimension_and_and_across_dimensions():
    result = _result(
        weather=["lightRain", "heavyRain"],
        lighting="heavyShadow",
    )

    matched, values = _sample_matches_conditions(
        result,
        {
            "weather": ["snow", "heavyRain"],
            "lighting": ["heavyShadow"],
        },
    )

    assert matched is True
    assert values == {"weather": "heavyRain", "lighting": "heavyShadow"}


def test_sample_conditions_require_every_selected_dimension():
    matched, values = _sample_matches_conditions(
        _result(weather="heavyRain", lighting="normal"),
        {"weather": ["heavyRain"], "lighting": ["heavyShadow"]},
    )

    assert matched is False
    assert values == {"weather": "heavyRain"}


def test_sample_conditions_command_normalizes_duplicate_values():
    command = PreviewLabelDimensionTrainingTaskCommand(
        label_ids=[1],
        sample_conditions={" weather ": [" heavyRain ", "heavyRain"]},
    )

    assert command.sample_conditions == {"weather": ["heavyRain"]}


def test_sample_condition_counts_count_each_image_once_per_option():
    counts = {}
    result = {
        "tagTool": {
            "result": [
                {"value": {"sceneType": ["backAlley", "backAlley"]}},
                {"value": {"lighting": "heavyShadow"}},
            ]
        }
    }

    _increment_sample_condition_counts(counts, result)
    _increment_sample_condition_counts(counts, _result(sceneType="urbanArterial"))

    assert counts == {
        "sceneType": {"backAlley": 1, "urbanArterial": 1},
        "lighting": {"heavyShadow": 1},
    }
