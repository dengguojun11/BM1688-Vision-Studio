from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PAGE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"


def test_plate_jobs_expose_working_pause_and_resume_actions():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'btnPause.style.display = running ? "inline-block" : "none"' in source
    assert "btnRestart.disabled = !canRestartJob(d)" in source
    assert "canPauseJob(r.status) && !isJobStopped(r)" in source
    assert "canRestartJob(r)" in source
    assert "最近可用的 checkpoint" in source
    assert "running && !composite" not in source
    assert "canRestartJob(d) && !composite" not in source


def test_completed_plate_jobs_expose_component_iteration_action():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert (
        'if (btnIter) btnIter.style.display = completed ? "inline-block" : "none"'
        in source
    )
    assert (
        '(r.status === "COMPLETED"\n        ? \'<button type="button" class="btn btn-job-iterate"'
        in source
    )
    assert (
        "plateIterationSourceJobId: isPlateJob(row) ? row.id : null"
        in source
    )
    assert (
        "body.iteration_source_job_id = weightPayload.iteration_source_job_id" in source
    )
    assert 'value: plateIterationValue' in source
    assert 'sel.value = plateIterationValue' in source
    assert '"（Pose / OCR / 颜色 / 类型）"' in source
    assert (
        "rowTrainingMode === CREATE_MODE_LABEL || rowTrainingMode === CREATE_MODE_TASK || isPlateJob(row)"
        in source
    )
