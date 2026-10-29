from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PAGE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"


def test_iteration_ai_patience_is_protected_before_auto_apply():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "function minimumAutomaticIterationPatience(epochs)" in source
    assert "function protectAutomaticIterationSuggestion(merged)" in source
    assert "var protection = protectAutomaticIterationSuggestion(merged);" in source
    assert 'srcLabel += " · patience 已保护为 " + protection.floor;' in source


def test_completed_epoch_cell_explains_normal_early_stopping():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'r.status === "COMPLETED" && r.train_stop' in source
    assert '"正常早停 · 最佳 " + String(stop.best_epoch)' in source
    assert 'class="epoch-progress-reason"' in source
