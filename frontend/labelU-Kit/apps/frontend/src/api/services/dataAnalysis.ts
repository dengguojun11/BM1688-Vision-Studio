import request from '@/api/request';

export interface DataAnalysisSummary {
  total_images: number;
  labeled_images: number;
  labeled_rate: number;
  contract_count: number;
  label_count: number;
  combo_gap_count: number;
  label_gap_count: number;
  severe_gap_count: number;
  recommended_images: number;
  risk_level: string;
  target_mode: string;
  target_per_label: number;
}

export interface DataAnalysisState {
  state: string;
  count: number;
}

export interface DataAnalysisLabel {
  label_id: number;
  label_key: string;
  label_value: string;
  color?: string;
  count: number;
  target: number;
  coverage: number;
  gap: number;
  contract_names?: string[];
}

export interface DataAnalysisContractHealth {
  contract_id: number | null;
  contract_revision: number | null;
  contract_label: string;
  task_names: string[];
  task_count: number;
  image_count: number;
  label_count: number;
  label_target: number;
  label_coverage: number;
  combo_coverage: number;
  health_score: number;
  risk_level: string;
  gap_count: number;
  severe_gap_count: number;
  recommended_images: number;
}

export interface DataAnalysisDimensionValue {
  value: string;
  name_cn: string;
  total_count: number;
}

export interface DataAnalysisCell {
  actual: number;
  target: number;
  coverage: number;
  gap: number;
}

export interface DataAnalysisDimensionRow {
  contract_id: number | null;
  contract_label: string;
  task_names: string[];
  label_id: number;
  label_key: string;
  label_value: string;
  color?: string;
  label_count: number;
  label_target: number;
  cells: Record<string, DataAnalysisCell>;
}

export interface DataAnalysisDimension {
  field_key: string;
  name_cn: string;
  values: DataAnalysisDimensionValue[];
  rows: DataAnalysisDimensionRow[];
}

export interface DataAnalysisRecommendation {
  priority: string;
  contract_id: number | null;
  contract_label: string;
  task_names: string[];
  label_id: number;
  label_key: string;
  label_value: string;
  dimension_key: string;
  dimension_name: string;
  value: string;
  value_name: string;
  actual: number;
  target: number;
  gap: number;
  coverage: number;
}

export interface DataAnalysisGlobalToolValue {
  value: string;
  name_cn: string;
  count: number;
}

export interface DataAnalysisGlobalTool {
  field_key: string;
  name_cn: string;
  values: DataAnalysisGlobalToolValue[];
}

export interface DataAnalysisOverview {
  summary: DataAnalysisSummary;
  state_counts: DataAnalysisState[];
  label_distribution: DataAnalysisLabel[];
  contract_health: DataAnalysisContractHealth[];
  dimensions: DataAnalysisDimension[];
  recommendations: DataAnalysisRecommendation[];
  global_tools: DataAnalysisGlobalTool[];
}

export async function getDataAnalysisOverview(targetPerLabel = 0) {
  return await request.get('/v1/data-analysis/overview', {
    params: { target_per_label: targetPerLabel },
  });
}
