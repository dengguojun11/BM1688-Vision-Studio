import request, { requestWithHeaders } from '../request';
import { idempotencyRequestConfig } from '../idempotency';
import type {
  BasicConfigCommand,
  OkRespCommonDataResp,
  OkRespTaskResponse,
  TaskListResponseWithStatics,
  TaskListWithHeadersResponse,
  OkRespTaskResponseWithStatics,
  OkRespTaskRectPresetSummaryResponse,
  UpdateCommand,
  ListByApiV1TasksGetParams,
  OkRespAttachmentResponse,
  CreateApiV1TasksTaskIdAttachmentsPostParams,
  DeleteApiV1TasksTaskIdAttachmentsDeleteParams,
  AttachmentDeleteCommand,
  ListResponseWithMeta,
  UserResponse,
  OkResponse,
} from '../types';

export interface ApplyLabelPresetCommand {
  preset_id: number;
}

export interface AnnotationLabelOption {
  id: number;
  label_key: string;
  label_value: string;
  color: string;
  annotation_kind: string;
  status: string;
}

export interface ApplyTaskLabelsCommand {
  label_ids: number[];
  config?: string;
  tool_name?: string;
  scope_mode?: 'FULL' | 'SUBSET';
}

export async function getTask(
  taskId: number,
  options?: { compareAccessToken?: string; sampleId?: number },
): Promise<OkRespTaskResponseWithStatics> {
  return await request.get(`/v1/tasks/${taskId}`, {
    params: {
      task_id: taskId,
      ...(options?.compareAccessToken ? { compare_access_token: options.compareAccessToken } : {}),
      ...(options?.sampleId ? { sample_id: options.sampleId } : {}),
    },
  });
}

export async function getTaskCollaborators(taskId: number): Promise<ListResponseWithMeta<UserResponse>> {
  return await request.get(`/v1/tasks/${taskId}/collaborators`);
}

export async function removeCollaborator(taskId: number, userId: number) {
  return await request.delete(`/v1/tasks/${taskId}/collaborators/${userId}`);
}

export async function addCollaborator(taskId: number, userId: number) {
  return await request.post(`/v1/tasks/${taskId}/collaborators`, {
    user_id: userId,
  });
}

export async function batchAddCollaborators(taskId: number, userIds: number[]): Promise<OkResponse<UserResponse>> {
  return await request.post(`/v1/tasks/${taskId}/collaborators/batch_add`, {
    user_ids: userIds,
  });
}

export async function batchRemoveCollaborators(taskId: number, userIds: number[]): Promise<OkRespCommonDataResp> {
  return await request.post(`/v1/tasks/${taskId}/collaborators/batch_remove`, {
    user_ids: userIds,
  });
}

export async function createTaskWithBasicConfig(
  data: BasicConfigCommand,
  idempotencyKey?: string,
): Promise<OkRespTaskResponse> {
  const requestConfig = idempotencyRequestConfig(idempotencyKey);
  return requestConfig ? await request.post('/v1/tasks', data, requestConfig) : await request.post('/v1/tasks', data);
}

export async function uploadFile(
  params: CreateApiV1TasksTaskIdAttachmentsPostParams & {
    file: File;
  },
): Promise<OkRespAttachmentResponse> {
  const data = new FormData();

  if (params.file) {
    data.append('file', params.file);
  }

  return await request.post(`/v1/tasks/${params.task_id}/attachments`, data, {
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  });
}

export async function deleteFile(
  { task_id, ...restParams }: DeleteApiV1TasksTaskIdAttachmentsDeleteParams,
  body: AttachmentDeleteCommand,
): Promise<OkRespCommonDataResp> {
  return await request.delete(`/v1/tasks/${task_id}/attachments`, {
    params: restParams,
    data: body,
  });
}

export async function updateTaskConfig(taskId: number, taskConfig: UpdateCommand): Promise<OkRespTaskResponse> {
  return await request.patch(`/v1/tasks/${taskId}`, taskConfig);
}

export async function applyTaskLabelPreset(
  taskId: number,
  payload: ApplyLabelPresetCommand,
): Promise<OkRespTaskResponse> {
  return await request.post(`/v1/tasks/${taskId}/annotation/apply-label-preset`, payload);
}

export async function getAnnotationLabels(): Promise<{ data: AnnotationLabelOption[] }> {
  return await request.get('/v1/annotation-taxonomy/labels', {
    params: { include_disabled: false, size: 500 },
  });
}

export async function applyTaskLabels(taskId: number, payload: ApplyTaskLabelsCommand): Promise<OkRespTaskResponse> {
  return await request.post(`/v1/tasks/${taskId}/annotation/apply-labels`, payload);
}

export async function getTaskList({
  page,
  ...params
}: ListByApiV1TasksGetParams): Promise<TaskListWithHeadersResponse> {
  return await requestWithHeaders.get('/v1/tasks', {
    timeout: 0,
    params: {
      size: 16,
      page: page ? page - 1 : 0,
      ...params,
    },
  });
}

export async function getTaskRectPresetSummary(): Promise<OkRespTaskRectPresetSummaryResponse> {
  return await request.get('/v1/tasks/rect-preset-summary');
}

export interface TaskLabelSummaryItem {
  id: number;
  label_key: string;
  label_value: string;
  color: string;
  count: number;
}

export interface TaskLabelSummary {
  all: number;
  labels: TaskLabelSummaryItem[];
}

export interface DatasetCategoryOption {
  id: number;
  category_key: string;
  name: string;
  description?: string | null;
  status: 'active' | 'disabled' | string;
  reference_count: number;
  version: number;
}

export interface DatasetOption {
  id: number;
  name: string;
  dataset_category_id?: number | null;
  dataset_category_name?: string | null;
  dataset_label_contract_id?: number | null;
  dataset_label_contract_revision?: number | null;
  dataset_label_contract_status?: string | null;
  dataset_label_ids?: number[];
  dataset_labels?: Array<{
    label_id?: number;
    label_value?: string;
    label_key?: string;
    color?: string;
    annotation_kind?: string;
  }>;
}

export interface DatasetLabelContractOption {
  id: number;
  dataset_id: number;
  revision: number;
  annotation_kind: string;
  label_ids: number[];
  labels: Array<{
    label_id?: number;
    label_value?: string;
    label_key?: string;
    color?: string;
    annotation_kind?: string;
  }>;
  label_set_signature?: string;
  class_order_signature?: string;
  status: string;
}

export interface AccessibleDatasetListParams {
  page?: number;
  size?: number;
  q?: string;
}

/**
 * Fetch one server-side page of datasets for selectors and other lightweight
 * browsing surfaces. Keep this separate from getAccessibleDatasets(), whose
 * array return shape is used by legacy task-edit screens.
 */
export async function getAccessibleDatasetsPage(
  params: AccessibleDatasetListParams = {},
): Promise<ListResponseWithMeta<DatasetOption>> {
  const page = Math.max(0, Number(params.page ?? 0) || 0);
  const size = Math.max(1, Number(params.size ?? 20) || 20);
  const query = String(params.q || '').trim();
  const response = await request.get('/v1/datasets', {
    params: {
      page,
      size,
      ...(query ? { q: query } : {}),
    },
  });
  // request's interceptor returns the business envelope directly; the
  // fallback also keeps service tests and alternate Axios clients useful.
  const envelope = response?.data && !Array.isArray(response.data)
    && typeof response.data === 'object' && Object.prototype.hasOwnProperty.call(response.data, 'data')
    ? response.data
    : response;
  const data = Array.isArray(envelope?.data) ? envelope.data : [];
  const meta = envelope?.meta_data || (response as any)?.meta_data;
  return {
    data,
    meta_data: {
      total: Number(meta?.total ?? data.length),
      page: Number(meta?.page ?? page),
      size: Number(meta?.size ?? data.length),
    },
  };
}

export async function getAccessibleDataset(datasetId: number): Promise<DatasetOption | null> {
  const response = await request.get(`/v1/datasets/${datasetId}`);
  const envelope = response?.data && !Array.isArray(response.data)
    && typeof response.data === 'object' && Object.prototype.hasOwnProperty.call(response.data, 'data')
    ? response.data
    : response;
  const data = envelope?.data;
  return data && typeof data === 'object' ? data as DatasetOption : null;
}

export async function getAccessibleDatasets(): Promise<DatasetOption[]> {
  const response = await getAccessibleDatasetsPage({ page: 0, size: 500 });
  return response.data;
}

export async function getDatasetLabelContract(datasetId: number): Promise<DatasetLabelContractOption | null> {
  const response = await request.get(`/v1/datasets/${datasetId}/label-contracts/current`);
  const payload = response?.data;
  const data = payload && Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload;
  return data && typeof data === 'object' ? (data as DatasetLabelContractOption) : null;
}

export interface TaskDatasetCategorySummary {
  all: number;
  categorized: number;
  uncategorized: number;
  categories: Array<{
    id: number;
    category_key: string;
    name: string;
    status: string;
    count: number;
  }>;
}

export async function getDatasetCategories(params?: {
  page?: number;
  size?: number;
  q?: string;
  status?: string;
}): Promise<ListResponseWithMeta<DatasetCategoryOption>> {
  return await request.get('/v1/annotation-taxonomy/dataset-categories', {
    params: { page: 0, size: 200, include_disabled: false, ...(params || {}) },
  });
}

export async function getTaskDatasetCategorySummary(): Promise<{ data: TaskDatasetCategorySummary }> {
  return await request.get('/v1/tasks/dataset-category-summary');
}

export async function batchAssignTaskDatasetCategory(
  task_ids: number[],
  dataset_category_id: number | null,
): Promise<OkResponse<unknown>> {
  return await request.patch('/v1/tasks/batch-dataset-category', {
    task_ids,
    dataset_category_id,
  });
}

export async function getTaskLabelSummary(params?: {
  dataset_category_id?: number;
  dataset_id?: number;
  uncategorized?: boolean;
}): Promise<{ data: TaskLabelSummary }> {
  return await request.get('/v1/tasks/label-summary', { params });
}

export interface TaskImageSearchHit {
  sample_id: number;
  task_id: number;
  task_name: string;
  filename: string;
  state?: string | null;
  url?: string | null;
  thumbnail_url?: string | null;
}

export interface TaskLabelImageHit {
  sample_id: number;
  task_id: number;
  task_name: string;
  filename: string;
  state?: string | null;
  url?: string | null;
  thumbnail_url?: string | null;
  width?: number | null;
  height?: number | null;
  annotations: Array<{
    id?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    label?: string;
    color?: string;
    [key: string]: unknown;
  }>;
}

export async function searchTaskImages(params: {
  q: string;
  page?: number;
  size?: number;
  rect_preset_slug?: string;
  label_ids?: number | number[];
}): Promise<ListResponseWithMeta<TaskImageSearchHit>> {
  const query: Record<string, string | number> = {
    q: params.q,
    page: params.page ?? 0,
    size: params.size ?? 24,
  };
  if (params.rect_preset_slug) {
    query.rect_preset_slug = params.rect_preset_slug;
  }
  if (params.label_ids) {
    query.label_ids = Array.isArray(params.label_ids) ? params.label_ids[0] : params.label_ids;
  }
  return await request.get('/v1/tasks/image-search', { params: query });
}

export async function getTaskLabelImages(params: {
  label_id: number;
  page?: number;
  size?: number;
  q?: string;
  dataset_category_id?: number;
  dataset_id?: number;
  uncategorized?: boolean;
}): Promise<ListResponseWithMeta<TaskLabelImageHit>> {
  return await request.get('/v1/tasks/label-images', {
    params: {
      label_id: params.label_id,
      page: params.page ?? 0,
      size: params.size ?? 24,
      ...(params.q?.trim() ? { q: params.q.trim() } : {}),
      ...(params.dataset_category_id ? { dataset_category_id: params.dataset_category_id } : {}),
      ...(params.dataset_id ? { dataset_id: params.dataset_id } : {}),
      ...(params.uncategorized ? { uncategorized: true } : {}),
    },
  });
}

export async function deleteTask(taskId: number): Promise<OkRespCommonDataResp> {
  return await request.delete(`/v1/tasks/${taskId}`, {
    params: {
      task_id: taskId,
    },
  });
}

export interface AnnotationDailyUserRow {
  user_id: number;
  username: string;
  stat_date: string;
  done_count: number;
}

export interface AnnotationSampleTimingRow {
  sample_id: number;
  task_id: number;
  task_name?: string | null;
  filename?: string | null;
  user_id: number;
  username: string;
  duration_seconds: number;
  annotated_at: string;
  annotated_count: number;
}

export interface AnnotationStatsDashboard {
  summary: {
    total_done: number;
    total_annotators: number;
    avg_duration_seconds?: number | null;
    today_done: number;
    date_from: string;
    date_to: string;
  };
  daily_by_user: AnnotationDailyUserRow[];
  sample_timings: AnnotationSampleTimingRow[];
  sample_timings_total: number;
  duration_note: string;
}

export async function getAnnotationStatsDashboard(params?: {
  date_from?: string;
  date_to?: string;
  task_id?: number;
  sample_page?: number;
  sample_size?: number;
}): Promise<{ data: AnnotationStatsDashboard }> {
  return await request.get('/v1/tasks/annotation-stats/dashboard', { params });
}
