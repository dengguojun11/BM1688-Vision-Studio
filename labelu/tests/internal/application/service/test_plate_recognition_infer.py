from labelu.internal.application.service.plate_recognition_infer import (
    _apply_guarded_consensus,
)


def test_guarded_consensus_matches_bm1688_ocr_selection_contract():
    candidates = [
        {"text": "123456", "confidence": 0.95, "crop_ratio": 0.45},
        {"text": "1234S6", "confidence": 0.96, "crop_ratio": 0.50},
        {"text": "123456", "confidence": 0.94, "crop_ratio": 0.55},
    ]

    selected = _apply_guarded_consensus(
        candidates[1],
        candidates,
        preferred_length=6,
        anchor_ratio=0.5,
        preserve_confidence=0.99,
        anchor_group_min_confidence=0.9,
        non_anchor_group_min_confidence=0.94,
    )

    assert selected["text"] == "123456"
    assert selected["confidence"] == 0.95
    assert selected["crop_ratio"] == "guarded_consensus"
