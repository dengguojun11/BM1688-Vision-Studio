import request from '../request';

export type EvolutionStatus = 'RUNNING' | 'COMPLETED' | 'ATTENTION' | 'IDLE';
export type StageStatus = 'NOT_STARTED' | 'RUNNING' | 'WAITING' | 'PASSED' | 'FAILED' | 'BLOCKED' | 'SKIPPED';

export interface TrainingMetrics {
  epoch?: number;
  map50_95?: number;
  map50?: number;
  precision?: number;
  recall?: number;
  fitness?: number;
  train_box_loss?: number;
  val_box_loss?: number;
}

export interface EvolutionDecision {
  id: string;
  report_id: number;
  lineage_id: string;
  job_id: number;
  decision_type: string;
  actor_type: 'AI' | 'SYSTEM' | 'HUMAN';
  provider_key: string;
  status: string;
  summary: string;
  suggested_params?: Record<string, unknown> | null;
  created_at?: string | null;
}

export interface ModelSummary {
  id: number;
  model_key: string;
  display_name: string;
  version_no: number;
  status: string;
  chip_type?: string | null;
  precision?: string | null;
}

export interface EvolutionNode {
  id: string;
  lineage_id: string;
  round_id?: number | null;
  round_no: number;
  job_id?: number | null;
  name: string;
  status: string;
  stage: string;
  stage_status: StageStatus;
  progress?: { current_epoch?: number | null; target_epochs?: number | null; percent?: number | null } | null;
  metrics: TrainingMetrics;
  dataset_version_id?: number | null;
  model?: ModelSummary | null;
  quality_gate: Record<string, string>;
  decision?: EvolutionDecision | null;
  parent_job_id?: number | null;
  execution_mode?: string | null;
  train_node_id?: string | null;
  failure_reason?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  lineage_name?: string;
}

export interface EvolutionLineage {
  id: string;
  source_type: 'PLAN' | 'JOB_CHAIN';
  name: string;
  series_type: string;
  status: EvolutionStatus;
  round_count: number;
  model_count: number;
  current_job_id?: number | null;
  current_round_id?: number | null;
  best_job_id?: number | null;
  champion_job_id?: number | null;
  champion?: ModelSummary | null;
  latest_metrics: TrainingMetrics;
  best_metrics: TrainingMetrics;
  metric_delta?: { metric: string; before: number; after: number; delta: number } | null;
  latest_decision?: EvolutionDecision | null;
  updated_at?: string | null;
  nodes?: EvolutionNode[];
  decisions?: EvolutionDecision[];
}

export interface EvolutionOverview {
  generated_at: string;
  summary: {
    lineage_count: number;
    active_round_count: number;
    active_training_count: number;
    model_count: number;
    published_model_count: number;
    pending_intervention_count: number;
    autonomous_decision_count: number;
  };
  automation: {
    training_enabled: boolean;
    plan_writes_enabled: boolean;
    auto_iteration_enabled: boolean;
    status: 'ENABLED' | 'PARTIAL' | 'DISABLED';
    mode: 'PLAN' | 'JOB_CHAIN' | 'MANUAL';
  };
  stages: Array<{
    key: string;
    label: string;
    count: number;
    active_count: number;
    failed_count: number;
    waiting_count: number;
    status: StageStatus;
  }>;
  lineages: EvolutionLineage[];
  active_jobs: EvolutionNode[];
  decisions: EvolutionDecision[];
  quality_gates: Array<{ gate: string; pass_count: number; fail_count: number; pending_count: number; status: string }>;
  events: Array<{
    id: string;
    event_type: string;
    lineage_id: string;
    job_id?: number | null;
    title: string;
    summary: string;
    status: string;
    actor_type: string;
    occurred_at?: string | null;
  }>;
}

export interface TrainingMetricHistory {
  job_id: number;
  available: boolean;
  next_after_epoch?: number | null;
  has_more: boolean;
  history: TrainingMetrics[];
}

function unwrap<T>(response: { data?: { data?: T } & T }): T {
  return (response.data?.data ?? response.data) as T;
}

export async function getEvolutionOverview() {
  return unwrap<EvolutionOverview>(await request.get('/v1/evolution/screen/overview'));
}

export async function getEvolutionLineage(lineageId: string) {
  return unwrap<EvolutionLineage>(await request.get(`/v1/evolution/lineages/${encodeURIComponent(lineageId)}`));
}

export async function getTrainingMetrics(jobId: number, afterEpoch?: number | null) {
  return unwrap<TrainingMetricHistory>(await request.get(`/v1/training/jobs/${jobId}/metrics`, {
    params: { after_epoch: afterEpoch ?? undefined, limit: 5000 },
  }));
}
