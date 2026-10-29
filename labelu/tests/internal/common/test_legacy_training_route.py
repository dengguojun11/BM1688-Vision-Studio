"""Legacy YOLO training entry-point compatibility contract.

The old page remains available for advanced panels while the 7GAI platform
training center is the canonical route.  These tests deliberately verify the
non-destructive migration signals instead of requiring a redirect: embedded
callers still need the existing recipe, auto-plan and model-library panels.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from labelu.main import app


def test_legacy_training_route_advertises_canonical_platform_entry() -> None:
    with TestClient(app) as client:
        response = client.get("/yolo-training")

    assert response.status_code == 200
    assert response.headers["deprecation"] == "true"
    assert response.headers["link"] == '</training>; rel="canonical"'
    assert response.headers["x-7gai-canonical-route"] == "/training"
    assert 'rel="canonical"' in response.text
    assert 'href="/training"' in response.text
    assert "这是旧版训练兼容入口" in response.text


def test_legacy_training_panel_stays_available_for_embedded_compatibility() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/yolo-training?embedded=1&panel=autoTrainingPlan",
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "location" not in response.headers
    assert 'id="navAutoTrainingPlan"' in response.text
    assert 'id="navModels"' in response.text
    # The notice is rendered for direct visits only; iframe callers should not
    # receive a second migration banner inside the platform shell.
    assert "body.labelu-embedded .legacy-convergence-notice { display: none; }" in response.text


def test_legacy_training_page_is_the_source_for_the_served_extension() -> None:
    page = Path("frontend/extensions/pages/yolo_training.html")
    assert page.is_file()
    html = page.read_text(encoding="utf-8")
    assert '<link rel="canonical" href="/training" />' in html
    assert 'data-canonical-training-link' in html
    assert 'href="/training"' in html
