from labelu.internal.common.yolo_detection_geometry import (
    detection_class_names,
    detection_rects_for_result,
)


def _polygon_config():
    return {
        "tools": [
            {
                "tool": "polygonTool",
                "config": {
                    "lowerLimitPointNum": 5,
                    "upperLimitPointNum": 100,
                    "attributes": [{"value": "floating_mulchFilm"}],
                },
            }
        ]
    }


def test_polygon_is_normalized_to_detection_rectangle():
    rects = detection_rects_for_result(
        {
            "polygonTool": {
                "result": [
                    {
                        "label": "floating_mulchFilm",
                        "points": [
                            {"x": 30, "y": 20},
                            {"x": 10, "y": 40},
                            {"x": 50, "y": 60},
                            {"x": 70, "y": 30},
                            {"x": 30, "y": 20},
                        ],
                    }
                ]
            }
        },
        _polygon_config(),
        ["floating_mulchFilm"],
        image_width=100,
        image_height=100,
    )

    assert len(rects) == 1
    assert rects[0]["source_tool"] == "polygonTool"
    assert (rects[0]["x"], rects[0]["y"]) == (10, 20)
    assert (rects[0]["width"], rects[0]["height"]) == (60, 40)


def test_polygon_out_of_bounds_and_wrong_point_count_are_excluded():
    config = _polygon_config()
    result = {
        "polygonTool": {
            "result": [
                {
                    "label": "floating_mulchFilm",
                    "points": [{"x": 1, "y": 1}] * 4,
                },
                {
                    "label": "floating_mulchFilm",
                    "points": [
                        {"x": 10, "y": 10},
                        {"x": 20, "y": 10},
                        {"x": 20, "y": 20},
                        {"x": 10, "y": 20},
                        {"x": 120, "y": 20},
                    ],
                },
            ]
        }
    }

    assert (
        detection_rects_for_result(
            result,
            config,
            detection_class_names(config),
            image_width=100,
            image_height=100,
        )
        == []
    )


def test_polygon_boundary_rounding_error_is_clamped():
    rects = detection_rects_for_result(
        {
            "polygonTool": {
                "result": [
                    {
                        "label": "floating_mulchFilm",
                        "points": [
                            {"x": 10, "y": 10},
                            {"x": 100.0000000000002, "y": 10},
                            {"x": 100.0000000000002, "y": 100.0000000000002},
                            {"x": 10, "y": 100.0000000000002},
                            {"x": 10, "y": 10},
                        ],
                    }
                ]
            }
        },
        _polygon_config(),
        ["floating_mulchFilm"],
        image_width=100,
        image_height=100,
    )

    assert len(rects) == 1
    assert rects[0]["x"] == 10
    assert rects[0]["y"] == 10
    assert rects[0]["width"] == 90
    assert rects[0]["height"] == 90


def test_rect_tasks_keep_existing_rect_semantics():
    config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"value": "person"}]},
            }
        ]
    }
    rects = detection_rects_for_result(
        {
            "rectTool": {
                "result": [{"label": "person", "x": 1, "y": 2, "width": 3, "height": 4}]
            }
        },
        config,
        ["person"],
    )
    assert [
        (item["x"], item["y"], item["width"], item["height"]) for item in rects
    ] == [(1, 2, 3, 4)]
