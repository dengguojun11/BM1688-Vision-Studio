import request from '@/api/request';

export interface DataAnalysisSummary {
  total_images: number;
  labeled_images: number;
  contract_count: number;
  label_count: number;
  gap_count: number;
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
}

export interface DataAnalysisContractLabel extends DataAnalysisLabel {
  target: number;
  coverage: number;
  gap: number;
}

export interface DataAnalysisContract {
  contract_id: number | null;
  contract_revision: number | null;
  task_id: number;
  task_name: string;
  image_count: number;
  labels: DataAnalysisContractLabel[];
  gap_count: number;
  coverage: number;
}

export interface DataAnalysisGap {
  contract_id: number | null;
  task_id: number;
  task_name: string;
  label_id: number;
  label_key: string;
  label_value: string;
  count: number;
  target: number;
  gap: number;
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
  contracts: DataAnalysisContract[];
  gaps: DataAnalysisGap[];
  global_tools: DataAnalysisGlobalTool[];
}

export async function getDataAnalysisOverview(targetPerLabel: number) {
  return await request.get('/v1/data-analysis/overview', {
    params: { target_per_label: targetPerLabel },
  });
}
