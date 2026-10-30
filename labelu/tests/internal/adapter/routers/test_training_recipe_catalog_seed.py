from labelu.internal.common.config import settings
from labelu.internal.domain.models.annotation_taxonomy import TrainingRecipe


def test_legacy_training_recipe_catalog_seeds_plate_recipes(
    client, testuser_token_headers, db
):
    db.query(TrainingRecipe).filter(
        TrainingRecipe.recipe_key.in_(
            ("plate_detection_v1", "plate_recognition_v1")
        )
    ).delete(synchronize_session=False)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/training-recipes",
        headers=testuser_token_headers,
        params={"page": 0, "size": 500},
    )

    assert response.status_code == 200, response.text
    recipe_keys = {item["recipe_key"] for item in response.json()["data"]}
    assert {"plate_detection_v1", "plate_recognition_v1"} <= recipe_keys
