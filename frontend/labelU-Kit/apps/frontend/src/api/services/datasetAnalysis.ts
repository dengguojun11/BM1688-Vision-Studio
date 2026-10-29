import request from '../request';

export interface DatasetDistributionItem {
  value: string;
  count: number;
  percentage: number;
}

export interface DatasetDimensionDistribution {
  key: string;
  label: string;
  total_images: number;
  covered_images: number;
  missing_images: number;
  items: DatasetDistributionItem[];
  expected_values: string[];
  missing_values: string[];
}

export interface DatasetLabelDistributionItem {
  label: string;
  image_count: number;
  instance_count: number;
  percentage: number;
  target_count: number;
  gap_count: number;
}

export interface DatasetAnalysisResponse {
  category_id?: number;
  category_key?: string;
  category_name?: string;
  dataset_id?: number | null;
  dataset_name?: string | null;
  generated_at: string;
  totals: {
    task_count: number;
    image_task_count: number;
    total_images: number;
    done_images: number;
    pending_images: number;
    tagged_images: number;
    labeled_images: number;
    label_instance_count: number;
  };
  dimensions: DatasetDimensionDistribution[];
  labels: DatasetLabelDistributionItem[];
  tasks: Array<{
    task_id: number;
    task_name: string;
    status?: string | null;
    source?: string | null;
    total_images: number;
    done_images: number;
    pending_images: number;
    labeled_images: number;
    tag_coverage: number;
  }>;
  recommendations: Array<{
    dimension: string;
    dimension_label: string;
    value: string;
    current_count: number;
    target_count: number;
    priority: 'critical' | 'warning' | 'ok' | string;
    message: string;
  }>;
}

export async function getDatasetAnalysis(
  datasetId: number,
  taskIds: number[] = [],
): Promise<{ data: DatasetAnalysisResponse }> {
  return await request.get('/v1/tasks/dataset-analysis', {
    params: { dataset_id: datasetId, ...(taskIds.length ? { task_ids: taskIds } : {}) },
    paramsSerializer: { indexes: null },
  });
}
