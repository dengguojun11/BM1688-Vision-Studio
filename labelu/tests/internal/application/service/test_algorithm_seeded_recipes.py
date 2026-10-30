from datetime import datetime

from labelu.internal.application.service import algorithm as algorithm_service
from labelu.internal.domain.models.annotation_taxonomy import TrainingRecipe


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
