import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const html = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);
const observability = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/assets/yolo_training_observability.js"),
  "utf8",
);

function requireMatch(pattern, description) {
  const match = html.match(pattern);
  assert.ok(match, `missing ${description}`);
  return match;
}

const globalJobHead = requireMatch(
  /<table class="data-table" id="globalJobTable">[\s\S]*?<thead>[\s\S]*?<tr>([\s\S]*?)<\/tr>/,
  "global training job table head",
)[1];
const globalJobRowTemplate = requireMatch(
  /async function loadGlobalJobTable[\s\S]*?tr\.innerHTML =([\s\S]*?)tbody\.appendChild\(tr\);/,
  "global training job row template",
)[1];

assert.equal((globalJobHead.match(/<th>/g) || []).length, 12);
assert.match(globalJobHead, /训练达标判定/);
assert.match(globalJobHead, /创建者/);
assert.match(html, /function buildGlobalJobCreatorMeta\(r, createdText\)/);
assert.match(html, /function buildGlobalJobActionButtons\(r\)/);
assert.match(html, /function buildGlobalJobStatusCell\(r\)/);
assert.match(html, /function buildGlobalJobCreatorCell\(r\)/);
assert.match(html, /function buildGlobalJobCardHtml\(r, createdText, samples\)/);
assert.match(globalJobRowTemplate, /buildGlobalJobCardHtml\(r, ct, samples\)/);
assert.match(html, /cell-id/);
assert.match(html, /cell-task/);
assert.match(html, /cell-actions/);
assert.match(html, /function formatTrainingQualityCell\(row\)/);
assert.match(html, /btn-job-iterate/);
assert.match(html, /迭代/);
assert.match(html, /\.lu-sample-total-badge/);
assert.match(html, /formatTotalSampleCountCell\(r\)/);
assert.match(html, /formatEpochProgressCell\(r\)/);
assert.match(html, /train-row-id-pill/);
assert.match(html, /train-creator-cell/);
assert.match(html, /train-split-mono/);
assert.match(html, /async function openIterationTrainingFromJob/);
assert.match(html, /function applyIterationDraftToCreateModal/);
assert.match(html, /function autoApplyIterationSuggestion/);
assert.match(html, /function buildTrainParamCompareNote/);
assert.match(html, /function shouldShowTrainParamDiffRow/);
assert.match(html, /row-from-suggest td\.col-sug/);
assert.match(html, /row-changed td\.col-final/);
assert.match(html, /function ensureTrainedWeightSelectOption/);
assert.match(html, /ensureWeightCatalogLoaded\("trained"/);
assert.match(html, /本系统已训练/);
assert.match(html, /可续训/);
assert.match(
  html,
  /function filterTrainedModelsForRectPreset[\s\S]*?trainedModelMatchesRectPreset\(r, preset\)/,
);
assert.doesNotMatch(html, /function trainedModelValidationPassed/);
assert.match(html, /function bindValidationPassToggle/);
assert.match(html, /data-val-pass-toggle/);
assert.match(html, /job-overview-console-summary/);
assert.match(html, /job-overview-console-kpis/);
assert.match(html, /job-overview-console-risks/);
assert.match(html, /job-overview-console-facts/);
assert.match(html, /job-overview-console-timeline/);
assert.match(html, /function overviewConsoleMetricCard\(/);
assert.match(html, /grid\.innerHTML =\s*\n\s*'<div class="job-overview-console is-'/);
assert.match(html, /Val mAP50-95/);
assert.match(html, /Test mAP50-95/);
assert.match(html, /Test - Val/);
assert.match(html, /最佳 Epoch/);
assert.match(html, /样本总数/);
assert.match(html, /function overviewGtPredCompareState\(d\)/);
assert.match(html, /function overviewAiEvaluationState\(d\)/);
assert.match(html, /function overviewPostTrainingWorkflow\(d\)/);
assert.match(html, /var linkedJob = findGlobalJobRowById\(linkedJobId\)/);
assert.match(html, /openJobDetail\(savedT, savedJ, true/);
assert.match(html, /关联训练作业缺少有效的任务或作业编号/);
assert.doesNotMatch(html, /data-auto-open-task=.{0,120}row\.training_task_id \|\| ''/);
assert.match(html, /function trainingQualityPassed\(row\)/);
assert.match(html, /训练达标 · 验证待补齐/);
assert.match(html, /训练质量已达标 · 分析产物缺失/);
assert.match(html, /演示\/评估证据：不代表自动训练流水线已启用/);
assert.match(html, /六个关键节点 · 全部完成才形成分析闭环/);
assert.match(html, /overviewConsoleStage\("标注对比"/);
assert.match(html, /overviewConsoleStage\("AI 评估"/);
assert.match(html, /全维度报告 \+ 迭代建议/);
assert.doesNotMatch(html, /id="navEvalReports"/);
assert.match(html, /id="tabJobDetailEval"/);
assert.match(html, /训练与分析闭环已完成/);
assert.match(html, /job-overview-quality-grid/);
assert.match(html, /train-step-tree/);
assert.match(html, /train-step-subgrid/);
assert.match(html, /train-substep-checks/);
assert.match(html, /train-substep-check/);
assert.match(html, /train-substep-artifacts/);
assert.match(html, /step\.substeps/);
assert.match(html, /sub\.checks/);
assert.match(html, /step\.artifacts/);
assert.match(html, /sub\.artifacts/);
assert.match(html, /step\.root_path/);
assert.match(html, /步骤分解/);
assert.match(html, /保存位置/);
assert.doesNotMatch(html, /璁粌瀹屾垚鍚庡啓鍏?/);
assert.doesNotMatch(html, /寰呭啓鍏?/);
assert.match(html, /训练产物/);
assert.match(html, /需要优先处理/);
assert.match(html, /训练事实/);
assert.match(html, /LabeluTrainingObservability\.reset\(\)/);
assert.doesNotMatch(html, /LabeluTrainingObservability\.render\(d\)/);

const modelsHead = requireMatch(
  /<table class="data-table" id="modelsTable">[\s\S]*?<thead>[\s\S]*?<tr>([\s\S]*?)<\/tr>/,
  "trained models table head",
)[1];
const modelsRowTemplate = requireMatch(
  /async function loadModelsTable[\s\S]*?tr\.innerHTML =([\s\S]*?)tbody\.appendChild\(tr\);/,
  "trained models row template",
)[1];
const modelsLoader = requireMatch(
  /async function loadModelsTable[\s\S]*?function openBm1688Export/,
  "trained models loader",
)[0];
const importedModelsLoader = requireMatch(
  /async function loadImportedModelsTable[\s\S]*?async function downloadImportedModel/,
  "imported models loader",
)[0];
assert.equal((modelsHead.match(/<th>/g) || []).length, 7);
assert.match(modelsHead, /模型/);
assert.match(modelsHead, /场景与数据/);
assert.match(modelsHead, /Val \/ Test 性能/);
assert.match(modelsHead, /训练结论/);
assert.match(modelsHead, /BM1688 交付/);
assert.match(modelsRowTemplate, /formatModelIdentityCell\(r\)/);
assert.match(modelsRowTemplate, /formatModelScopeDataCell\(r\)/);
assert.match(modelsRowTemplate, /formatModelPerformanceCell\(r\)/);
assert.match(modelsRowTemplate, /formatModelTrainingGateCell\(r\)/);
assert.match(modelsRowTemplate, /formatModelBm1688CompileCell\(r\)/);
// A filtered/paged table response must not replace the full catalog consumed
// by the create/iteration weight picker.
assert.doesNotMatch(modelsLoader, /weightCatalogCache\.trained\s*=/);
assert.doesNotMatch(importedModelsLoader, /weightCatalogCache\.imported\s*=/);
assert.match(html, /id="filterModelQualityStatus"/);
assert.match(html, /id="filterModelSearch"/);
assert.match(html, /placeholder="搜索模型、作业、任务、数据集或分类"/);
assert.match(html, /id="filterJobSearch"/);
assert.match(html, /placeholder="搜索作业、任务、数据集、权重或 ID"/);
assert.match(
  html,
  /async function loadModelsTable[\s\S]*?filterModelSearch[\s\S]*?q \+= "&q="/,
);
assert.match(
  html,
  /function buildListQuery\(page\)[\s\S]*?filterJobSearch[\s\S]*?q \+= "&q="/,
);
assert.match(html, /id="sortModelsBy"/);
assert.match(html, /class="train-list-section-head models-list-header"/);
assert.match(html, /class="train-list-filters-compact models-filter-bar" id="modelsFiltersSystem"/);
assert.match(html, /class="models-filter-actions"/);
assert.match(html, /#viewModels \.models-filter-bar/);
assert.match(html, /grid-template-columns:[\s\S]*minmax\(180px, 1\.45fr\)/);
assert.match(html, /training_status=/);
assert.match(html, /sort_by=/);
assert.match(html, /function modelTrainingStatusCode\(row\)/);
assert.match(html, /function formatModelScopeDataCell\(row\)/);
assert.match(html, /function formatModelPerformanceCell\(row\)/);
assert.match(html, /function formatModelTrainingGateCell\(row\)/);
assert.match(html, /function formatModelBm1688CompileCell\(row\)/);
assert.match(html, /训练达标优先/);
assert.match(html, /PT 单图试跑/);
assert.doesNotMatch(modelsRowTemplate, /edge_validation_assessment/);
assert.doesNotMatch(modelsRowTemplate, /release_gate_assessment/);
assert.match(html, /Test - Val/);
assert.match(html, /meta\.summary/);
assert.match(html, /#modelsTable td\.cell-ops\.ops-menu-open\s*\{\s*z-index:\s*40;/);
assert.match(
  html,
  /function closeOpsMoreMenus[\s\S]*?\.cell-ops\.ops-menu-open[\s\S]*?classList\.remove\("ops-menu-open"\)/,
);
assert.match(
  html,
  /function toggleOpsMoreMenu[\s\S]*?var cell = wrap\.closest\("\.cell-ops"\);[\s\S]*?if \(willOpen && cell\) cell\.classList\.add\("ops-menu-open"\)/,
);

assert.match(html, /id="labelModeTaskWrap"/);
assert.match(html, /训练输出结构/);
assert.match(html, /可训练样本 ["'] \+ \(task\.done_count \|\| 0\) \+ ["'] 张 · 训练类别/);
assert.match(html, /使用车牌框和四个定位点训练 Pose 模型/);
const autoTrainingSeriesSelect = requireMatch(
  /<select id="autoTrainingSeriesType">([\s\S]*?)<\/select>/,
  "automatic training series selector",
)[1];
const evaluationSeriesSelect = requireMatch(
  /<select id="evaluationDatasetSeriesType">([\s\S]*?)<\/select>/,
  "evaluation dataset series selector",
)[1];
assert.match(autoTrainingSeriesSelect, /value="plate_detection"[^>]*>车牌框 \+ 四角点（仅定位）/);
assert.match(evaluationSeriesSelect, /value="plate_detection"[^>]*>车牌框 \+ 四角点（仅定位）/);
assert.doesNotMatch(autoTrainingSeriesSelect, /value="plate_recognition"/);
assert.doesNotMatch(evaluationSeriesSelect, /value="plate_recognition"/);
assert.match(html, /function validateNewTrainingSeries\(selectId\)/);
assert.match(html, /autoTrainingBasicValidation\(\)[\s\S]*validateNewTrainingSeries\("autoTrainingSeriesType"\)/);
assert.match(html, /createEvaluationDatasetFromEditor\(\)[\s\S]*validateNewTrainingSeries\("evaluationDatasetSeriesType"\)/);
assert.match(html, /autoGenerateEvaluationDatasetsFromEditor\(\)[\s\S]*validateNewTrainingSeries\("evaluationDatasetSeriesType"\)/);
assert.match(html, /历史复合车牌计划仅可查看，不能修改/);
assert.match(
  html,
  /function setCreateMode\(mode\)[\s\S]*if \(labelModeTaskWrap && !isLabelMode\) labelModeTaskWrap\.style\.display = "none"/,
);
assert.match(
  html,
  /loadSeq !== labelModeCandidateLoadSeq \|\| currentCreateMode !== CREATE_MODE_LABEL/,
);
assert.match(html, /function selectedPlateRecognitionPreset\(\)/);
assert.match(html, /payload\.selected_task_ids = collectSelectedLabelModeTaskIds\(\)/);
assert.match(
  html,
  /function buildLabelModePreviewPayload\(\)[\s\S]*payload\.selected_task_ids = collectSelectedLabelModeTaskIds\(\)/,
);
assert.match(
  html,
  /function loadLabelModePreview\(\)[\s\S]*buildLabelModePreviewPayload\(\)[\s\S]*\/yolo-training-tasks\/preview/,
);
assert.doesNotMatch(
  html,
  /function openIterationTrainingFromJob\(row\)[\s\S]*?payload\.selected_task_ids[\s\S]*?var presetFilterSel/,
);
assert.match(html, /复合车牌数据：正样本/);
assert.match(html, /function renderPlateComponentStatus\(d\)/);
assert.match(html, /function renderPlateComponentMetrics\(d\)/);
assert.match(html, /function isPlateRecognitionCreateMode\(\)/);
assert.match(html, /var PLATE_POSE_PRESET_WEIGHT = "yolov8n-pose\.pt"/);
assert.match(html, /YOLOv8 Pose，车牌框 \+ 四角点/);
assert.match(html, /componentProgress\.summary/);
assert.match(html, /Pose、OCR、颜色、类型四个组件均已完成/);
assert.match(html, /plate_pose_best\.pt/);
assert.match(html, /isPlateRecognitionJob\(row\)/);
assert.match(
  html,
  /var show = d && d\.status === "COMPLETED" && !isPlateJob\(d\)/,
);
assert.match(observability, /function isCompositePlateJob\(job\)/);
assert.match(observability, /function compositeSplitCounts\(dataset\)/);
assert.match(observability, /compositeAssessment\.warnings/);
assert.match(observability, /metricHtml\("组件进度"/);
assert.match(observability, /metricHtml\("OCR Test 准确率"/);
assert.match(observability, /composite \? "复合指标判定" : "指标验证"/);
assert.match(observability, /stageHtml\("PT 权重"/);
assert.match(observability, /metricHtml\("训练结论"/);
assert.doesNotMatch(observability, /\/yolo-bm1688-exports/);
assert.doesNotMatch(observability, /ONNX \/ BModel 导出/);
assert.doesNotMatch(observability, /BM1688 边端验证/);
assert.doesNotMatch(observability, /release_gate_assessment/);
assert.doesNotMatch(observability, /质量准入/);
assert.doesNotMatch(html, /id="btnDetailBm1688"/);
