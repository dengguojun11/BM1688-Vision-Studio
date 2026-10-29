import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const html = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_training.html"),
  "utf8",
);

assert.match(html, /data-job-tab="analysis"/);
assert.match(
  html,
  /data-job-panel="analysis"\s+tabindex="0"\s+aria-label="训练分析内容"/,
);
assert.match(
  html,
  /\[data-job-panel="analysis"\]\s*\{[^}]*overflow-y:\s*auto;/s,
);
assert.match(html, /id="jobTrainAnalysisStats"/);
assert.match(html, /id="jobTrainAnalysisCharts"/);
assert.match(html, /id="jobTrainAnalysisGallery"/);
assert.match(html, /id="jobTrainAnalysisComponentTabs"/);
assert.match(html, /function buildTrainAnalysisComponentTable\(job\)/);
assert.match(html, /function selectedTrainComponentAnalysis\(data\)/);
assert.match(html, /data-analysis-component/);
assert.match(html, /componentAnalysis\.preview_images/);
assert.match(html, /data\.component_analyses/);
assert.match(html, /function buildTrainAnalysisSparkline\(title, lines, options\)/);
assert.match(
  html,
  /api\("\/yolo-train-jobs\/" \+ jobId \+ "\/analysis"/,
);
assert.match(html, /apiBlob\(img\.url\)/);
assert.match(html, /URL\.revokeObjectURL/);
assert.match(html, /if \(tab === "analysis"\)/);
assert.match(html, /component-live-progress/);
assert.match(html, /componentProgress\.items/);
assert.match(html, /loadJobTrainAnalysis\(true\)/);
assert.match(html, /id="jobMetricContractMetrics"/);
assert.match(html, /id="jobTrainHealthSection"/);
assert.match(html, /id="jobTrainHealthStatus"/);
assert.match(html, /id="jobTrainHealthContent"/);
assert.match(html, /id="evalMetricSemantics"/);
assert.match(html, /id="gtPredModelIdentity"/);
assert.match(html, /function metricContractPanelHtml\(d, compact\)/);
assert.match(html, /best_pt_val_metrics/);
assert.match(html, /results_csv_best_epoch/);
assert.match(html, /历史作业最佳轮次/);
assert.match(html, /best\.pt 对应 results\.csv 最佳轮次，尚未独立复验/);
assert.match(html, /当前采用指标/);
assert.match(html, /health_diagnosis/);
assert.match(html, /train_cls_loss/);
assert.match(html, /val_cls_loss/);
assert.match(html, /train_dfl_loss/);
assert.match(html, /val_dfl_loss/);
