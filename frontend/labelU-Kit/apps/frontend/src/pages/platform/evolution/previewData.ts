import type { EvolutionLineage, EvolutionOverview, TrainingMetricHistory } from '@/api/services/evolution';

/** Explicit visual-preview fixtures. Never used unless ?preview=1 is present. */
const decision = {
  id: 'preview-decision', report_id: 1, lineage_id: 'job-chain:1', job_id: 2,
  decision_type: '参数优化', actor_type: 'AI' as const, provider_key: 'quality-policy', status: 'COMPLETED',
  summary: '第 2 代模型召回率持续提升。AI 建议保持当前数据契约，将训练轮次调整至 120，并在完成后执行冠军挑战。',
  suggested_params: { epochs: 120 }, created_at: '2026-10-24T10:00:00',
};

const nodes: EvolutionLineage['nodes'] = [
  { id: 'job:1', lineage_id: 'job-chain:1', round_no: 1, job_id: 1, name: '占道经营 · 初始基线', status: 'COMPLETED', stage: 'PUBLISH', stage_status: 'PASSED', metrics: { map50_95: 0.621, map50: 0.864, precision: 0.88, recall: 0.81 }, dataset_version_id: 11, quality_gate: { training: 'PASSED', generalization: 'PASSED', conversion: 'PASSED', edge: 'PASSED', release: 'PASSED' }, model: { id: 1, model_key: 'occupancy-yolo', display_name: '占道经营检测 V1', version_no: 1, status: 'PUBLISHED' } },
  { id: 'job:2', lineage_id: 'job-chain:1', round_no: 2, job_id: 2, parent_job_id: 1, name: '占道经营 · 自主进化第 2 代', status: 'PROCESSING', stage: 'TRAINING', stage_status: 'RUNNING', progress: { current_epoch: 72, target_epochs: 120, percent: 60 }, metrics: { map50_95: 0.684, map50: 0.901 }, dataset_version_id: 12, quality_gate: { training: 'RUNNING', generalization: 'PENDING', conversion: 'PENDING', edge: 'PENDING', release: 'PENDING' }, decision, execution_mode: 'container', train_node_id: 'GPU-NODE-01' },
];

export const previewLineage: EvolutionLineage = {
  id: 'job-chain:1', source_type: 'JOB_CHAIN', name: '占道经营智能检测', series_type: '城市治理 · YOLO', status: 'RUNNING',
  round_count: 2, model_count: 1, current_job_id: 2, champion_job_id: 1,
  latest_metrics: nodes![1].metrics, best_metrics: nodes![1].metrics,
  metric_delta: { metric: 'map50_95', before: 0.621, after: 0.684, delta: 0.063 },
  latest_decision: decision, nodes, decisions: [decision], updated_at: '2026-10-24T10:00:00',
};

export const previewOverview: EvolutionOverview = {
  generated_at: '2026-10-24T10:00:00',
  summary: { lineage_count: 1, active_round_count: 1, active_training_count: 1, model_count: 2, published_model_count: 1, pending_intervention_count: 0, autonomous_decision_count: 1 },
  automation: { training_enabled: true, plan_writes_enabled: true, auto_iteration_enabled: true, status: 'ENABLED', mode: 'PLAN' },
  stages: ['难例回流', '数据治理', '冻结快照', '模型训练', '质量评估', 'AI 决策', '冠军挑战', '边端转换', '真机验证', '模型发布'].map((label, index) => ({
    key: `STAGE_${index}`, label, count: 2, active_count: index === 3 ? 1 : 0, failed_count: 0, waiting_count: index > 6 ? 1 : 0,
    status: index < 3 || (index > 3 && index < 6) ? 'PASSED' : index === 3 ? 'RUNNING' : 'WAITING',
  })),
  lineages: [{ ...previewLineage, nodes: undefined, decisions: undefined }],
  active_jobs: [nodes![1]], decisions: [decision],
  quality_gates: [
    { gate: 'training', pass_count: 1, fail_count: 0, pending_count: 1, status: 'RUNNING' },
    { gate: 'generalization', pass_count: 1, fail_count: 0, pending_count: 1, status: 'WAITING' },
    { gate: 'conversion', pass_count: 1, fail_count: 0, pending_count: 1, status: 'WAITING' },
    { gate: 'edge', pass_count: 1, fail_count: 0, pending_count: 1, status: 'WAITING' },
    { gate: 'release', pass_count: 1, fail_count: 0, pending_count: 1, status: 'WAITING' },
  ],
  events: [
    { id: 'preview-e1', event_type: 'AI', lineage_id: previewLineage.id, title: 'AI 完成第 2 代中期质量分析', summary: decision.summary, status: 'COMPLETED', actor_type: 'AI', occurred_at: decision.created_at },
    { id: 'preview-e2', event_type: 'TRAIN', lineage_id: previewLineage.id, title: '自主训练进入 Epoch 72', summary: 'GPU-NODE-01 · mAP@50:95 达到 68.4%', status: 'RUNNING', actor_type: 'SYSTEM', occurred_at: '2026-10-24T09:58:00' },
    { id: 'preview-e3', event_type: 'DATA', lineage_id: previewLineage.id, title: '难例快照已冻结', summary: '数据集版本 #12 · 数据契约校验通过', status: 'COMPLETED', actor_type: 'SYSTEM', occurred_at: '2026-10-24T09:00:00' },
  ],
};

export const previewMetrics: TrainingMetricHistory = {
  job_id: 2, available: true, has_more: false, next_after_epoch: 70,
  history: Array.from({ length: 24 }, (_, index) => ({
    epoch: index * 3 + 1,
    map50_95: 0.31 + index * 0.014 + Math.sin(index / 2) * 0.012,
    map50: 0.58 + index * 0.012 + Math.sin(index / 3) * 0.01,
    train_box_loss: 1.2 * Math.exp(-index / 10) + 0.16,
    val_box_loss: 1.3 * Math.exp(-index / 11) + 0.2,
  })),
};
