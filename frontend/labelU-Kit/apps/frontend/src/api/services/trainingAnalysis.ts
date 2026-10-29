import request, { requestWithHeaders } from '../request';

export interface YoloTrainJobListRow {
  id: number;
  task_id: number;
  task_name?: string | null;
  status: string;
  model_weights?: string | null;
  epochs?: number | null;
  actual_epochs?: number | null;
  imgsz?: number | null;
  train_sample_count?: number | null;
  val_sample_count?: number | null;
  test_sample_count?: number | null;
  total_sample_count?: number | null;
  runtime_status?: string | null;
  is_restartable?: boolean;
  hyperparams?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface YoloTrainJobDetail extends YoloTrainJobListRow {
  current_epoch?: number | null;
  progress_snapshot?: string | null;
  train_phase?: string | null;
  train_phase_label?: string | null;
  train_phase_detail?: string | null;
  current_step_key?: string | null;
  train_steps?: Array<{
    key: string;
    label: string;
    status: string;
    summary?: string | null;
    detail?: string | null;
    order?: number | null;
  }> | null;
  error_message?: string | null;
  weights_path?: string | null;
  component_progress?: {
    completed: number;
    total: number;
    percent: number;
    current_component?: string | null;
    current_component_label?: string | null;
    summary?: string | null;
    items?: YoloTrainComponentProgressItem[];
  } | null;
}

export interface YoloTrainComponentProgressItem {
  key: string;
  label: string;
  status: string;
  order?: number | null;
  current_epoch?: number | null;
  target_epochs?: number | null;
  percent?: number | null;
  progress_snapshot?: string | null;
  phase?: string | null;
  updated_at?: number | null;
}

export interface YoloTrainAnalysisPoint {
  epoch: number;
  value: number;
}

export interface YoloTrainAnalysisLogLine {
  time: string;
  message: string;
}

export interface YoloTrainAnalysisPreviewImage {
  asset_id: string;
  filename: string;
  kind?: 'training_summary' | 'confusion_matrix' | 'metric_curve' | 'validation_preview' | 'label_distribution' | 'prediction' | 'other';
  url: string;
}

export interface YoloHyperparamField {
  key: string;
  label: string;
  type: 'int' | 'float' | 'bool' | 'str' | string;
  hint?: string;
  default?: unknown;
  official_default?: unknown;
}

export interface YoloTrainClassPerformance {
  index: number;
  label: string;
  map50?: number | null;
  map50_95?: number | null;
  precision?: number | null;
  recall?: number | null;
  test_map50?: number | null;
  test_map50_95?: number | null;
  test_precision?: number | null;
  test_recall?: number | null;
}

export interface YoloTrainComponentAnalysis {
  key: 'pose' | 'ocr' | 'color' | 'plate_type' | string;
  label: string;
  status: string;
  model_filename: string;
  model_available: boolean;
  train_sample_count?: number | null;
  final_loss?: number | null;
  current_epoch: number;
  target_epochs: number;
  percent?: number;
  progress_snapshot?: string;
  phase?: string;
  metrics: Record<string, unknown>;
  series: Record<string, YoloTrainAnalysisPoint[]>;
  preview_images: YoloTrainAnalysisPreviewImage[];
  analysis_source: {
    key: string;
    relative_path: string;
  };
}

export interface YoloTrainAnalysisResponse {
  job: YoloTrainJobDetail;
  progress: {
    current_epoch: number;
    target_epochs: number;
    percent: number;
    snapshot?: string;
  };
  series: Record<string, YoloTrainAnalysisPoint[]>;
  status_cards: Record<string, { label: string; value?: number | string | null; total?: number | null; unit?: string; seconds?: number | null; used_mb?: number; total_mb?: number; train?: number; val?: number; test?: number }>;
  logs: YoloTrainAnalysisLogLine[];
  preview_images: YoloTrainAnalysisPreviewImage[];
  component_analyses?: YoloTrainComponentAnalysis[];
  analysis_source?: {
    key: string;
    relative_path: string;
  };
  class_performance?: YoloTrainClassPerformance[];
  hyperparam_fields?: YoloHyperparamField[];
  runtime_editable_hyperparam_keys?: string[];
  pending_runtime_hyperparams?: { hyperparams?: Record<string, unknown>; requested_at?: string; restart_after_epoch?: boolean } | null;
  updated_at: string;
}

export interface YoloTrainJobsResponse {
  meta_data: { total: number; page: number; size: number };
  data: YoloTrainJobListRow[];
}

export async function getYoloTrainJobs(params?: {
  page?: number;
  size?: number;
  status?: string;
  task_name?: string;
}): Promise<YoloTrainJobsResponse> {
  return await request.get('/v1/yolo-train-jobs', { params });
}

export async function getYoloTrainAnalysis(jobId: number): Promise<{ data: YoloTrainAnalysisResponse }> {
  return await request.get(`/v1/yolo-train-jobs/${jobId}/analysis`);
}

export async function pauseYoloTrainJob(taskId: number, jobId: number): Promise<{ data: YoloTrainJobDetail }> {
  return await request.post(`/v1/tasks/${taskId}/yolo-train-jobs/${jobId}/pause`);
}

export async function restartYoloTrainJob(taskId: number, jobId: number): Promise<{ data: YoloTrainJobDetail }> {
  return await request.post(`/v1/tasks/${taskId}/yolo-train-jobs/${jobId}/restart`);
}

export async function patchYoloTrainRuntimeHyperparams(
  taskId: number,
  jobId: number,
  hyperparams: Record<string, unknown>,
): Promise<{ data: YoloTrainJobDetail }> {
  return await request.patch(`/v1/tasks/${taskId}/yolo-train-jobs/${jobId}/runtime-hyperparams`, { hyperparams });
}

export async function getYoloTrainAnalysisAsset(url: string): Promise<Blob> {
  const res = await requestWithHeaders.get(url.replace(/^\/api/, ''), { responseType: 'blob' });
  return res.data;
}
