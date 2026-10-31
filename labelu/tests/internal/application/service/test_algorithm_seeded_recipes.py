import json
from datetime import datetime

from labelu.internal.application.service import algorithm as algorithm_service
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    LabelPreset,
    LabelPresetItem,
    TrainingRecipe,
)


def test_ensure_seeded_restores_plate_training_recipes_for_legacy_databases(db):
    db.query(TrainingRecipe).filter(
        TrainingRecipe.recipe_key.in_(("plate_detection_v1", "plate_recognition_v1"))
    ).delete(synchronize_session=False)
    db.commit()

    algorithm_service.ensure_seeded(db)
    algorithm_service.ensure_seeded(db)

    rows = (
        db.query(TrainingRecipe)
        .filter(
            TrainingRecipe.recipe_key.in_(
                ("plate_detection_v1", "plate_recognition_v1")
            ),
            TrainingRecipe.version == 1,
        )
        .order_by(TrainingRecipe.recipe_key)
        .all()
    )
    assert [(row.recipe_key, row.job_type, row.model_family) for row in rows] == [
        ("plate_detection_v1", "plate_detection", "plate_detection"),
        ("plate_recognition_v1", "plate_recognition", "plate_recognition"),
    ]
    assert rows[0].component_contract_json == (
        '{"geometry":["rect","four_corners"],"attributes":[]}'
    )


def test_ensure_seeded_does_not_duplicate_soft_deleted_plate_recipe(db):
    algorithm_service.ensure_seeded(db)
    row = (
        db.query(TrainingRecipe)
        .filter(
            TrainingRecipe.recipe_key == "plate_detection_v1",
            TrainingRecipe.version == 1,
        )
        .one()
    )
    row.status = "disabled"
    row.deleted_at = datetime.now()
    db.commit()

    algorithm_service.ensure_seeded(db)

    rows = (
        db.query(TrainingRecipe)
        .filter(
            TrainingRecipe.recipe_key == "plate_detection_v1",
            TrainingRecipe.version == 1,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].id == row.id
    assert rows[0].status == "disabled"
    assert rows[0].deleted_at is not None


def test_ensure_seeded_restores_missing_plate_detection_preset(db):
    preset = (
        db.query(LabelPreset).filter(LabelPreset.slug == "plateDetection").one_or_none()
    )
    if preset is not None:
        db.query(LabelPresetItem).filter(
            LabelPresetItem.preset_id == int(preset.id)
        ).delete(synchronize_session=False)
        db.delete(preset)
        db.commit()

    algorithm_service.ensure_seeded(db)
    algorithm_service.ensure_seeded(db)

    rows = db.query(LabelPreset).filter(LabelPreset.slug == "plateDetection").all()
    assert len(rows) == 1
    assert rows[0].training_profile == "plate_detection_v1"
    assert rows[0].training_profile_config == (
        '{"profile":"plate_detection_v1","rect_label":"doubleEP",'
        '"polygon_label":"platePoint","attributes":{}}'
    )
    items = (
        db.query(LabelPresetItem)
        .filter(LabelPresetItem.preset_id == int(rows[0].id))
        .all()
    )
    assert [
        (item.label_key, item.label_value, item.annotation_kind, item.color)
        for item in items
    ] == [
        ("车牌框", "doubleEP", "bbox", "#ff6600"),
        ("车牌四角点", "platePoint", "polygon", "#00aaee"),
    ]


def test_ensure_seeded_does_not_resurrect_soft_deleted_plate_detection_preset(db):
    algorithm_service.ensure_seeded(db)
    preset = db.query(LabelPreset).filter(LabelPreset.slug == "plateDetection").one()
    preset_id = int(preset.id)
    preset.deleted_at = datetime.now()
    db.commit()

    algorithm_service.ensure_seeded(db)

    rows = db.query(LabelPreset).filter(LabelPreset.slug == "plateDetection").all()
    assert len(rows) == 1
    assert int(rows[0].id) == preset_id
    assert rows[0].deleted_at is not None


def test_ensure_seeded_repairs_plate_detection_geometry_contract(db):
    algorithm_service.ensure_seeded(db)
    preset = db.query(LabelPreset).filter(LabelPreset.slug == "plateDetection").one()
    point_label = (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.normalized_label_value == "platepoint")
        .one()
    )
    point_item = (
        db.query(LabelPresetItem)
        .filter(
            LabelPresetItem.preset_id == int(preset.id),
            LabelPresetItem.label_value == "platePoint",
        )
        .one()
    )
    point_label.annotation_kind = "bbox"
    point_item.annotation_kind = "bbox"
    preset.training_profile = "plate_recognition_v1"
    preset.training_profile_config = json.dumps({"attributes": {"plateNumber": {"type": "ocr"}}})
    db.commit()

    algorithm_service.ensure_seeded(db)

    db.refresh(preset)
    db.refresh(point_label)
    db.refresh(point_item)
    assert preset.training_profile == "plate_detection_v1"
    assert json.loads(preset.training_profile_config) == {
        "profile": "plate_detection_v1",
        "rect_label": "doubleEP",
        "polygon_label": "platePoint",
        "attributes": {},
    }
    assert point_label.annotation_kind == "polygon"
    assert point_item.annotation_kind == "polygon"
