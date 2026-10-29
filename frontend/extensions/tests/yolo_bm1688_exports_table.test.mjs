import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const repoRoot = path.resolve(import.meta.dirname, "../../..");
const html = fs.readFileSync(
  path.join(repoRoot, "frontend/extensions/pages/yolo_bm1688_exports.html"),
  "utf8",
);

function requireMatch(pattern, description) {
  const match = html.match(pattern);
  assert.ok(match, `missing ${description}`);
  return match;
}

const head = requireMatch(
  /<table class="data-table">[\s\S]*?<thead>[\s\S]*?<tr>([\s\S]*?)<\/tr>/,
  "bm1688 exports table head",
)[1];
const rowTemplate = requireMatch(
  /async function loadTable\([^)]*\)[\s\S]*?tr\.innerHTML =([\s\S]*?)tr\.addEventListener\("click"/,
  "bm1688 exports row template",
)[1];
const opsTemplate = requireMatch(
  /var ops =([\s\S]*?)tr\.innerHTML =/,
  "bm1688 exports ops template",
)[1];

assert.equal((head.match(/<th/g) || []).length, 6);
assert.match(head, /记录与模型/);
assert.match(head, /当前阶段/);
assert.match(head, /当前结论 \/ 阻塞/);
assert.match(head, /产物/);
assert.match(head, /更新时间/);
assert.doesNotMatch(head, /验证是否通过/);

assert.match(rowTemplate, /buildExportRecordCell\(r, attempts\)/);
assert.match(rowTemplate, /buildExportWorkflowCell\(r\)/);
assert.match(rowTemplate, /buildExportVerdictCell\(r\)/);
assert.match(rowTemplate, /buildArtifactsCell\(r\)/);
assert.match(rowTemplate, /buildExportUpdatedCell\(r\)/);
assert.match(html, /function buildExportAttemptIndex\(rows\)/);
assert.match(html, /r\.current_verdict_label/);
assert.match(html, /id="exportDetailModal"/);
assert.match(html, /function syncExportSelectionUrl\(exportId\)/);
assert.match(html, /url\.searchParams\.set\("embedded", "1"\)/);
assert.match(html, /url\.searchParams\.delete\("export_id"\)/);
assert.match(html, /bmPageParams\.get\("from"\) === "publish"/);
assert.match(html, /id="bmPublishBlockerContext"/);
assert.match(html, /id="btnPublishContextBack"/);
assert.match(html, /function publishBlockerManualResultTab\(job\)/);
assert.match(html, /\["PENDING", "PROCESSING", "FAILED", "CANCELLED"\]/);
assert.match(html, /detailManualTab = publishBlockerManualResultTab\(job\)/);
assert.match(html, /function closeExportDetailModal\(\)[\s\S]*edgeManualFromPublish[\s\S]*returnToModelPublish\(\)/);
assert.match(html, /id="filterExportTaskName"/);
assert.match(html, /id="filterExportDatasetCategory"/);
assert.match(html, /id="filterExportStatus"/);
assert.match(html, /task_name=/);
assert.match(html, /dataset_category_id=/);
assert.match(html, /export_status=/);
assert.match(html, /function applyBm1688LayoutCopy\(\)/);
assert.match(html, /function enhanceDetailLayout\(\)/);
assert.match(html, /量化模型记录/);
assert.match(html, /配置入口在详情弹窗里的「配置快照」标签/);
assert.match(html, /#exportDetailModal\s*\{[\s\S]*align-items:\s*center;[\s\S]*justify-content:\s*center;/);
assert.match(html, /\.modal-dialog\.bm-detail-modal\s*\{[\s\S]*width:\s*min\(1180px, calc\(100vw - 64px\)\);[\s\S]*border-radius:\s*8px;/);
assert.match(opsTemplate, /data-act=\\"detail\\"[\s\S]*data-act=\\"eval\\"/);
assert.match(opsTemplate, /data-act=\\"steps\\"[\s\S]*data-act=\\"manual-edge\\"/);
assert.match(opsTemplate, /data-act=\\"delete\\"/);
assert.match(html, /td\.cell-ops\.ops-menu-open\s*\{\s*z-index:\s*40;/);
assert.match(
  html,
  /function closeOpsMoreMenus\(\)[\s\S]*classList\.remove\("ops-menu-open"\)/,
);
assert.match(
  html,
  /function toggleOpsMoreMenu\(btn\)[\s\S]*classList\.add\("ops-menu-open"\)/,
);
assert.match(html, /function deleteExport\(/);
assert.match(html, /name="edgeExecutionProfile" value="quick" checked/);
assert.match(html, /name="edgeExecutionProfile" value="certification"/);
assert.match(html, /execution_profile:\s*edgeManualExecutionProfileValue\(\)/);
assert.match(html, /job\.execution_profile \|\| "quick"/);
assert.match(html, /stage_durations_seconds/);
assert.match(html, /pt_same_batch_eval:\s*"PT 同批评估"/);
assert.match(html, /id="btnEvalSteps"/);
assert.match(html, /id="evalStepsModal"/);
assert.match(html, /function renderEvalStepsModal\(d\)/);
assert.match(html, /async function openEvalSteps\(exportId\)/);
assert.match(html, /data-act=\\"steps\\"/);
assert.match(html, /function resolveManualEdgeBmodelMetrics\(boardValidation\)/);
assert.match(html, /function resolveEffectiveBmodelComparison\(se, boardValidation\)/);
assert.doesNotMatch(
  html,
  /function resolveManualEdgeBmodelMetrics\(boardValidation\)[\s\S]*?report\.source !== "manual_edge_validation"/,
);
assert.match(html, /function hasEvalValue\(v\)[\s\S]*?Number\.isFinite/);
assert.match(
  html,
  /function fmt4\(v\)[\s\S]*?if\s*\(!hasEvalValue\(v\)\)\s*return "未计算"/,
);

const statusMetaSource = requireMatch(
  /(function edgeManualHistoryStatusMeta\(statusValue, edgeResultStatus\) \{[\s\S]*?\r?\n  \})\r?\n\r?\n  function edgeManualFormatHistoryMode/,
  "manual edge status mapping",
)[1];
const edgeManualHistoryStatusMeta = vm.runInNewContext(`(${statusMetaSource})`);

for (const [edgeResultStatus, expectedText, expectedClass] of [
  ["pass", "通过", "tag-ok"],
  ["fail", "未通过", "tag-no"],
  ["review", "需复核", "tag-warn"],
  ["result_pending", "结果待确认", "tag-warn"],
]) {
  const meta = edgeManualHistoryStatusMeta({
    status: "COMPLETED",
    edge_result_status: edgeResultStatus,
  });
  assert.equal(meta.text, expectedText, `${edgeResultStatus} status label`);
  assert.equal(meta.cls, expectedClass, `${edgeResultStatus} status class`);
}

const unknownMeta = edgeManualHistoryStatusMeta({ status: "COMPLETED" });
assert.equal(unknownMeta.text, "已完成，待确认");
assert.notEqual(unknownMeta.text, "通过");
