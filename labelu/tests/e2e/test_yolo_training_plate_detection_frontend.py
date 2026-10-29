from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_PAGE = REPO_ROOT / "frontend" / "extensions" / "pages" / "yolo_training.html"


def test_create_modal_keeps_legacy_plate_target_compatibility_explicit():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'name="plateTrainingTarget" value="plate_detection" />' in source
    assert 'name="plateTrainingTarget" value="plate_recognition" checked' in source
    assert "车牌检测" in source
    assert "完整车牌识别" in source
    assert "历史兼容，不可新建" in source
    assert "历史复合车牌预设不能创建一期新训练，请使用普通目标检测矩形框标签" in source
    assert "历史复合车牌训练仅用于兼容查看，不能创建一期新训练" in source
    assert "车牌框 + 四角点（仅兼容查看，不能创建一期新训练）" in source


def test_plate_training_target_is_submitted_and_locked_for_iteration():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert (
        source.count("body.plate_training_target = selectedPlateTrainingTarget()") == 3
    )
    assert "currentCreateMode === CREATE_MODE_TASK" in source
    assert (
        'String(taskRecipe.recipe_key || "").trim() === "plate_recognition_v1"'
        in source
    )
    assert (
        'plateTrainingTarget: isPlateDetectionJob(row) ? "plate_detection" : "plate_recognition"'
        in source
    )
    assert "input.disabled = lockTarget" in source
    assert (
        source.count("plateIterationSourceJobId: isPlateJob(row) ? row.id : null") == 2
    )
    assert (
        source.count(
            'plateTrainingTarget: isPlateDetectionJob(row) ? "plate_detection" : "plate_recognition"'
        )
        == 2
    )


def test_plate_detection_detail_uses_single_model_artifacts():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert '? [["pose", "车牌检测与四角定位", "plate_detection_best.pt"]]' in source
    assert 'buildOverviewActionButton("download", "下载 best.pt"' in source
    assert 'isPlateDetectionJob(row) ? "单模型 Pose 训练"' in source
    assert "if (!isPlateJob(d)) {" in source


def test_plate_profile_defaults_to_pose_bootstrap_weight():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'var plateDefaultWeight = "preset:" + PLATE_POSE_PRESET_WEIGHT;' in source
    assert 'String(want || "").indexOf("trained:") !== 0' in source
    assert "sel.value = plateDefaultWeight;" in source


def test_strategy_stats_does_not_reference_training_detail_locals():
    source = TRAINING_PAGE.read_text(encoding="utf-8")
    start = source.index("function updateStrategyTaskStats(rows)")
    end = source.index("function renderStrategyTaskTable()", start)
    function_source = source[start:end]

    assert "isPlateRecognitionJob(d)" not in function_source
    assert "pl.textContent" not in function_source
    assert "running += 1" in function_source


def test_phase_one_hides_new_composite_plate_entry_but_keeps_legacy_iteration_copy():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert 'opt.disabled = legacyPlate' in source
    assert 'cb.disabled = legacyPlate' in source
    assert "历史兼容，不可新建" in source
    assert "历史复合车牌预设不能创建一期新训练，请使用普通目标检测矩形框标签" in source
    assert "历史复合车牌结构：车牌框 + 四角点（仅兼容查看，不能创建一期新训练）" in source
    assert "历史复合车牌训练仅用于兼容查看，不能创建一期新训练" in source


def test_plate_component_progress_uses_compact_table_copy():
    source = TRAINING_PAGE.read_text(encoding="utf-8")
    start = source.index("function formatPlateEpochProgressCell(r)")
    end = source.index("function formatEpochProgressCell(r)", start)
    function_source = source[start:end]

    assert 'String(itemCurrent) + " / " + String(itemTarget)' in function_source
    assert 'compactText = "组件 " + position + "/" + total' in function_source
    assert 'compactText += " · " + item.current_epoch + " / " + item.target_epochs' in function_source
    assert "formatEpochProgressBar(componentProgress.percent, compactText, componentSummary, true)" in function_source
    assert "componentSummary + '</span></div>'" not in function_source
    assert "#globalJobTable .epoch-progress-text.is-component" in source
    assert "-webkit-line-clamp: 2" in source
    assert "td.cell-epoch-progress .epoch-progress" in source
    assert "td:nth-child(6) { width: 160px; }" in source


def test_training_progress_adjacent_columns_do_not_overflow():
    source = TRAINING_PAGE.read_text(encoding="utf-8")

    assert "min-width: 1480px" in source
    assert "td:nth-child(7) { width: 132px; }" in source
    assert "td:nth-child(8) { width: 128px; }" in source
    assert "#globalJobTable td.cell-epoch-progress," in source
    assert "#globalJobTable td.cell-sample-total," in source
    assert "#globalJobTable td.cell-split {" in source
    assert "#globalJobTable .lu-sample-total-cell" in source
