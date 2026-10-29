from labelu.internal.common.vision_volcengine import normalize_volcengine_ark_model_id


def test_strip_volcengine_prefix():
    assert (
        normalize_volcengine_ark_model_id("volcengine/doubao-seed-1-6-vision-250815")
        == "doubao-seed-1-6-vision-250815"
    )


def test_no_prefix_unchanged():
    assert normalize_volcengine_ark_model_id("doubao-seed-1-6-vision-250815") == (
        "doubao-seed-1-6-vision-250815"
    )


def test_empty():
    assert normalize_volcengine_ark_model_id("") == ""
    assert normalize_volcengine_ark_model_id(None) == ""
