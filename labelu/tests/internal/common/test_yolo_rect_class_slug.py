from labelu.internal.common.yolo_rect_class_slug import (
    build_rect_label_slug_aliases,
    looks_like_yolo_class_slug,
    normalize_rect_class_names_to_slugs,
    resolve_rect_label_slug,
)


def test_looks_like_yolo_class_slug():
    assert looks_like_yolo_class_slug("person_riding_bicycle")
    assert not looks_like_yolo_class_slug("骑行自行车的人")


def test_build_aliases_from_preset_items():
    items = [
        {"label_key": "骑行自行车的人", "label_value": "person_riding_bicycle"},
        {"label_key": "自行车", "label_value": "bicycle"},
    ]
    aliases = build_rect_label_slug_aliases(preset_label_items=items)
    assert aliases["骑行自行车的人"] == "person_riding_bicycle"
    assert aliases["person_riding_bicycle"] == "person_riding_bicycle"


def test_normalize_rect_class_names_to_slugs():
    aliases = {"骑行自行车的人": "person_riding_bicycle", "bicycle": "bicycle"}
    slugs, unresolved = normalize_rect_class_names_to_slugs(
        ["骑行自行车的人", "bicycle"],
        aliases,
    )
    assert slugs == ["person_riding_bicycle", "bicycle"]
    assert unresolved == []


def test_resolve_rect_label_slug_prefers_alias():
    aliases = {"骑行电动车的人": "person_riding_electricbike"}
    assert resolve_rect_label_slug("骑行电动车的人", aliases) == "person_riding_electricbike"
    assert resolve_rect_label_slug("person_riding_electricbike", aliases) == "person_riding_electricbike"
