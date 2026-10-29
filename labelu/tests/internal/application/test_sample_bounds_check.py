import json

from labelu.internal.application.service.sample import check_sample_annotations_out_of_bounds
from labelu.internal.domain.models.sample import TaskSample


def _sample_with_result(result: dict) -> TaskSample:
    return TaskSample(data=json.dumps({"result": json.dumps(result)}))


def test_rect_inside_bounds_is_normal():
    sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "rectTool": {"result": [{"x": 10, "y": 20, "width": 30, "height": 40}]},
        }
    )

    assert check_sample_annotations_out_of_bounds(sample) == (False, None)


def test_rect_negative_coordinate_is_abnormal():
    sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "rectTool": {"result": [{"x": -1, "y": 20, "width": 30, "height": 40}]},
        }
    )

    assert check_sample_annotations_out_of_bounds(sample) == (True, None)


def test_rect_exceeding_image_size_is_abnormal():
    sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "rectTool": {"result": [{"x": 80, "y": 20, "width": 30, "height": 40}]},
        }
    )

    assert check_sample_annotations_out_of_bounds(sample) == (True, None)


def test_points_in_polygon_line_and_point_are_checked():
    polygon_sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "polygonTool": {"result": [{"points": [{"x": 10, "y": 10}, {"x": 101, "y": 10}]}]},
        }
    )
    line_sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "lineTool": {"result": [{"pointList": [{"x": 10, "y": 10}, {"x": 20, "y": -2}]}]},
        }
    )
    point_sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "pointTool": {"result": [{"x": 10, "y": 81}]},
        }
    )

    assert check_sample_annotations_out_of_bounds(polygon_sample) == (True, None)
    assert check_sample_annotations_out_of_bounds(line_sample) == (True, None)
    assert check_sample_annotations_out_of_bounds(point_sample) == (True, None)


def test_duplicate_annotation_orders_do_not_make_valid_rects_abnormal():
    sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "rectTool": {
                "result": [
                    {"order": 1, "x": 10, "y": 20, "width": 30, "height": 40},
                    {"order": 1, "x": 50, "y": 20, "width": 30, "height": 40},
                ]
            },
        }
    )

    assert check_sample_annotations_out_of_bounds(sample) == (False, None)


def test_duplicate_placeholder_orders_do_not_make_valid_rects_abnormal():
    sample = _sample_with_result(
        {
            "width": 100,
            "height": 80,
            "rectTool": {
                "result": [
                    {"order": 0, "x": 10, "y": 20, "width": 30, "height": 40},
                    {"order": 0, "x": 50, "y": 20, "width": 30, "height": 40},
                ]
            },
        }
    )

    assert check_sample_annotations_out_of_bounds(sample) == (False, None)


def test_missing_dimensions_returns_error_without_abnormal_state():
    sample = _sample_with_result(
        {
            "rectTool": {"result": [{"x": 10, "y": 20, "width": 30, "height": 40}]},
        }
    )

    is_abnormal, error = check_sample_annotations_out_of_bounds(sample)

    assert is_abnormal is False
    assert error == "missing image width or height"


def test_bad_annotation_json_returns_error_without_abnormal_state():
    sample = TaskSample(data=json.dumps({"result": "{"}))

    is_abnormal, error = check_sample_annotations_out_of_bounds(sample)

    assert is_abnormal is False
    assert error and error.startswith("invalid annotation JSON")
