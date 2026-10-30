import commonController from '@/utils/common';

import request from '../request';
import { getTask } from './task';
import {
  type AutoLabelCommand,
  type BatchAutoLabelCommand,
  type AutoLabelJobResponse,
  type CopySampleToTaskCommand,
  type PreannotateJobResponse,
  type PreannotateModelsResponse,
  type PreannotationCapabilitiesResponse,
  type ProviderPreannotateCommand,
  type SampleBoundsCheckJobResponse,
  type TagtoolPreannotateCommand,
  type TagtoolPreannotateModelResponse,
  type YoloPreannotateCommand,
  type YoloPreannotateModelResponse,
  ExportType,
  type DeleteApiV1TasksTaskIdDeleteParams,
  type DeleteSampleCommand,
  type GetApiV1TasksTaskIdSamplesSampleIdGetParams,
  type GetPreApiV1TasksTaskIdSamplesSampleIdPreGetParams,
  type ListByApiV1TasksTaskIdSamplesGetParams,
  type OkRespCommonDataResp,
  type OkRespCreateSampleResponse,
  type LabelDatasetImportJobResponse,
  type OkRespAutoLabelResponse,
  type OkRespSampleResponse,
  type PatchSampleCommand,
  type SampleData,
  type SampleListResponse,
  type SampleResponse,
  type UpdateApiV1TasksTaskIdSamplesSampleIdPatchParams,
} from '../types';

/** 大批量建样本时分批请求，避免单次请求超时 */
export const SAMPLE_CREATE_BATCH_SIZE = 80;

const SAMPLE_CREATE_TIMEOUT_MS = 3 * 60 * 1000;

export function normalizeSampleListParams(params: Omit<ListByApiV1TasksTaskIdSamplesGetParams, 'task_id'>) {
  const shouldUsePage =
    typeof params.page !== 'undefined' && typeof params.after === 'undefined' && typeof params.before === 'undefined';

  return {
    ...params,
    page: shouldUsePage ? params.page! - 1 : undefined,
  };
}

export async function createSamples(
  taskId: number,
  data: {
    file_id: number;
    data: SampleData;
  }[],
  options?: { timeout?: number },
): Promise<OkRespCreateSampleResponse> {
  return await request.post(`/v1/tasks/${taskId}/samples`, data, {
    timeout: options?.timeout ?? SAMPLE_CREATE_TIMEOUT_MS,
  });
}

export async function createSamplesInBatches(
  taskId: number,
  data: {
    file_id: number;
    data: SampleData;
  }[],
  options?: { batchSize?: number },
): Promise<OkRespCreateSampleResponse> {
  const batchSize = options?.batchSize ?? SAMPLE_CREATE_BATCH_SIZE;
  if (data.length <= batchSize) {
    return createSamples(taskId, data);
  }

  const allIds: number[] = [];
  for (let offset = 0; offset < data.length; offset += batchSize) {
    const chunk = data.slice(offset, offset + batchSize);
    const response = await createSamples(taskId, chunk);
    const ids = response?.data?.ids ?? [];
    if (ids.length !== chunk.length) {
      throw new Error(`createSamples batch mismatch: expected ${chunk.length} ids, got ${ids.length}`);
    }
    allIds.push(...ids);
  }

  return { data: { ids: allIds } };
}

export async function copySampleToTask(
  targetTaskId: number,
  body: CopySampleToTaskCommand,
): Promise<OkRespCreateSampleResponse> {
  return await request.post(`/v1/tasks/${targetTaskId}/samples/copy-from-sample`, body, {
    timeout: SAMPLE_CREATE_TIMEOUT_MS,
  });
}

export async function copySamplesByLabel(
  targetTaskId: number,
  labelId: number,
): Promise<{ data: LabelDatasetImportJobResponse }> {
  return await copySamplesByLabels(targetTaskId, [labelId]);
}

export async function copySamplesByLabels(
  targetTaskId: number,
  labelIds: number[],
): Promise<{ data: LabelDatasetImportJobResponse }> {
  return await request.post(`/v1/tasks/${targetTaskId}/samples/copy-by-label`, { label_ids: labelIds }, {
    timeout: 30_000,
  });
}

export async function getLabelDatasetImportJob(
  taskId: number,
  jobId: number,
): Promise<{ data: LabelDatasetImportJobResponse }> {
  return await request.get(`/v1/tasks/${taskId}/samples/copy-by-label/${jobId}`);
}

export async function getActiveLabelDatasetImportJob(
  taskId: number,
): Promise<{ data: LabelDatasetImportJobResponse | null }> {
  return await request.get(`/v1/tasks/${taskId}/samples/copy-by-label/active`);
}

export async function getSamples({
  task_id,
  ...params
}: ListByApiV1TasksTaskIdSamplesGetParams): Promise<SampleListResponse> {
  return await request.get(`/v1/tasks/${task_id}/samples`, {
    params: normalizeSampleListParams(params),
  });
}

export async function getSample({
  task_id,
  sample_id,
  compare_access_token,
}: GetApiV1TasksTaskIdSamplesSampleIdGetParams & {
  compare_access_token?: string;
}): Promise<OkRespSampleResponse> {
  return await request.get(`/v1/tasks/${task_id}/samples/${sample_id}`, {
    params: compare_access_token ? { compare_access_token } : undefined,
  });
}

export async function updateSampleState(
  {
    task_id,
    sample_id,
    compare_access_token,
    ...params
  }: UpdateApiV1TasksTaskIdSamplesSampleIdPatchParams & { compare_access_token?: string },
  body: PatchSampleCommand,
): Promise<SampleResponse> {
  const sampleBody = body as PatchSampleCommand & { updated_at?: string };
  const { updated_at, ...patchBody } = sampleBody;
  return await request.patch(
    `/v1/tasks/${task_id}/samples/${sample_id}`,
    {
      ...patchBody,
      ...(patchBody.expected_updated_at || updated_at
        ? { expected_updated_at: patchBody.expected_updated_at ?? updated_at }
        : {}),
    },
    {
      params: {
        sample_id,
        ...params,
        ...(compare_access_token ? { compare_access_token } : {}),
      },
    },
  );
}

export async function updateSampleAnnotationResult(
  taskId: number,
  sampleId: number,
  data: SampleResponse,
  options?: { compareAccessToken?: string; taskContractRevision?: number | null },
): Promise<SampleResponse> {
  return await request.patch(
    `/v1/tasks/${taskId}/samples/${sampleId}`,
    {
      data: data.data,
      state: data.state,
      annotated_count: data.annotated_count,
      ...(data.updated_at ? { expected_updated_at: data.updated_at } : {}),
      ...(options?.taskContractRevision
        ? { expected_task_contract_revision: options.taskContractRevision }
        : {}),
    },
    {
      params: {
        sample_id: sampleId,
        ...(options?.compareAccessToken ? { compare_access_token: options.compareAccessToken } : {}),
      },
    },
  );
}

export async function autoLabelSample(
  taskId: number,
  sampleId: number,
  body: AutoLabelCommand = {},
  options?: { compareAccessToken?: string },
): Promise<OkRespAutoLabelResponse> {
  return await request.post(`/v1/tasks/${taskId}/samples/${sampleId}/auto_label`, body, {
    timeout: 5 * 60 * 1000,
    params: {
      sample_id: sampleId,
      ...(options?.compareAccessToken ? { compare_access_token: options.compareAccessToken } : {}),
    },
  });
}

export async function createAutoLabelJob(
  taskId: number,
  body: BatchAutoLabelCommand = {},
): Promise<{ data: AutoLabelJobResponse }> {
  return await request.post(`/v1/tasks/${taskId}/auto_label_job`, body);
}

export async function getAutoLabelJobStatus(taskId: number, jobId: number): Promise<{ data: AutoLabelJobResponse }> {
  return await request.get(`/v1/tasks/${taskId}/auto_label_job/${jobId}`);
}

export async function getActiveAutoLabelJob(taskId: number): Promise<{ data: AutoLabelJobResponse | null }> {
  return await request.get(`/v1/tasks/${taskId}/auto_label_jobs/active`);
}

export async function cancelAutoLabelJob(taskId: number, jobId: number): Promise<{ data: AutoLabelJobResponse }> {
  return await request.post(`/v1/tasks/${taskId}/auto_label_job/${jobId}/cancel`);
}

export async function createSampleBoundsCheckJob(taskId: number): Promise<{ data: SampleBoundsCheckJobResponse }> {
  return await request.post(`/v1/tasks/${taskId}/samples/bounds_check`);
}

export async function getSampleBoundsCheckJobStatus(
  taskId: number,
  jobId: number,
): Promise<{ data: SampleBoundsCheckJobResponse }> {
  return await request.get(`/v1/tasks/${taskId}/samples/bounds_check/${jobId}`);
}

export async function getActiveSampleBoundsCheckJob(
  taskId: number,
): Promise<{ data: SampleBoundsCheckJobResponse | null }> {
  return await request.get(`/v1/tasks/${taskId}/samples/bounds_check/active`);
}

export async function getYoloPreannotateModels(
  taskId: number,
): Promise<{ data: PreannotateModelsResponse<YoloPreannotateModelResponse> }> {
  return await request.get(`/v1/tasks/${taskId}/yolo_preannotate/models`);
}

export async function createYoloPreannotateJob(
  taskId: number,
  body: YoloPreannotateCommand,
  sampleId?: number,
): Promise<{ data: PreannotateJobResponse }> {
  return await request.post(`/v1/tasks/${taskId}/yolo_preannotate_job`, body, {
    params: sampleId ? { sample_id: sampleId } : undefined,
  });
}

export async function getYoloPreannotateJobStatus(
  taskId: number,
  jobId: number,
): Promise<{ data: PreannotateJobResponse }> {
  return await request.get(`/v1/tasks/${taskId}/yolo_preannotate_job/${jobId}`);
}

export async function getActiveYoloPreannotateJob(taskId: number): Promise<{ data: PreannotateJobResponse | null }> {
  return await request.get(`/v1/tasks/${taskId}/yolo_preannotate_job/active`);
}

export async function getTagtoolPreannotateModels(
  taskId: number,
): Promise<{ data: PreannotateModelsResponse<TagtoolPreannotateModelResponse> }> {
  return await request.get(`/v1/tasks/${taskId}/tagtool_preannotate/models`);
}

export async function createTagtoolPreannotateJob(
  taskId: number,
  body: TagtoolPreannotateCommand,
  sampleId?: number,
): Promise<{ data: PreannotateJobResponse }> {
  return await request.post(`/v1/tasks/${taskId}/tagtool_preannotate_job`, body, {
    params: sampleId ? { sample_id: sampleId } : undefined,
  });
}

export async function getTagtoolPreannotateJobStatus(
  taskId: number,
  jobId: number,
): Promise<{ data: PreannotateJobResponse }> {
  return await request.get(`/v1/tasks/${taskId}/tagtool_preannotate_job/${jobId}`);
}

export async function getActiveTagtoolPreannotateJob(taskId: number): Promise<{ data: PreannotateJobResponse | null }> {
  return await request.get(`/v1/tasks/${taskId}/tagtool_preannotate_job/active`);
}

export async function getPreannotationCapabilities(
  taskId: number,
): Promise<{ data: PreannotationCapabilitiesResponse }> {
  return await request.get(`/v1/tasks/${taskId}/preannotation-capabilities`);
}

export type ProviderPreannotateType = 'locateanything' | 'vlx_seek' | 'rex_omni';

const PROVIDER_PREANNOTATE_PATH: Record<ProviderPreannotateType, string> = {
  locateanything: 'locateanything',
  vlx_seek: 'vlx_seek',
  rex_omni: 'rex_omni',
};

export async function createProviderPreannotateJob(
  taskId: number,
  provider: ProviderPreannotateType,
  body: ProviderPreannotateCommand = {},
): Promise<{ data: PreannotateJobResponse }> {
  return await request.post(`/v1/tasks/${taskId}/${PROVIDER_PREANNOTATE_PATH[provider]}_preannotate_job`, body);
}

export async function getProviderPreannotateJobStatus(
  taskId: number,
  provider: ProviderPreannotateType,
  jobId: number,
): Promise<{ data: PreannotateJobResponse }> {
  return await request.get(`/v1/tasks/${taskId}/${PROVIDER_PREANNOTATE_PATH[provider]}_preannotate_job/${jobId}`);
}

export async function getActiveProviderPreannotateJob(
  taskId: number,
  provider: ProviderPreannotateType,
): Promise<{ data: PreannotateJobResponse | null }> {
  return await request.get(`/v1/tasks/${taskId}/${PROVIDER_PREANNOTATE_PATH[provider]}_preannotate_job/active`);
}

export async function outputSample(taskId: number, sampleIds: number[], activeTxt: ExportType) {
  // 1. Create export job
  const jobRes = await request.post(
    `/v1/tasks/${taskId}/samples/export`,
    { sample_ids: sampleIds },
    { params: { export_type: activeTxt } },
  );

  const jobId = jobRes.data.id;

  // 2. Poll until completed
  let job = jobRes.data;
  while (job.status !== 'COMPLETED' && job.status !== 'FAILED') {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const statusRes = await request.get(`/v1/tasks/${taskId}/samples/export/${jobId}`);
    job = statusRes.data;
  }

  if (job.status === 'FAILED') {
    commonController.notificationErrorMessage({ message: job.error_message || 'Export failed' }, 3);
    return;
  }

  // 3. Download the exported file
  const blob = await request.get(`/v1/tasks/${taskId}/samples/export/${jobId}/download`, {
    responseType: 'blob',
  });

  const taskRes = await getTask(taskId);
  let filename = taskRes.data.name || 'export';

  switch (activeTxt) {
    case ExportType.JSON:
    case ExportType.COCO:
      filename += '.json';
      break;
    case ExportType.XML:
      filename += '.xml';
      break;
    default:
      filename += '.zip';
      break;
  }

  // The shared Axios interceptor unwraps successful responses to ``data``;
  // the export endpoint explicitly returns a Blob when responseType is set.
  const url = window.URL.createObjectURL(blob as unknown as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  window.URL.revokeObjectURL(url);
}

export async function outputSamples(taskId: number, activeTxt: ExportType) {
  const samplesRes = await getSamples({ task_id: taskId, page: 1, size: 100000 });
  const sampleIdArrays = samplesRes.data;
  const sampleIds = [];

  for (const sample of sampleIdArrays) {
    sampleIds.push(sample.id!);
  }

  if (sampleIds.length === 0) {
    commonController.notificationErrorMessage({ message: 'No samples to export' }, 1);
    return;
  }

  await outputSample(taskId, sampleIds, activeTxt);

  return true;
}

export async function deleteSamples(
  { task_id }: DeleteApiV1TasksTaskIdDeleteParams,
  body: DeleteSampleCommand,
): Promise<OkRespCommonDataResp> {
  return await request.delete(`/v1/tasks/${task_id}/samples`, {
    data: body,
  });
}

export async function getPreSample({
  sample_id,
  task_id,
}: GetPreApiV1TasksTaskIdSamplesSampleIdPreGetParams): Promise<OkRespSampleResponse> {
  return await request.get(`/v1/tasks/${task_id}/samples/${sample_id}/pre`);
}

/**
 * 当前图片 AI 目标检测的运行过程快照。
 * 后端只在单图 AI 检测成功后写入，旧数据可能没有 process 明细。
 */
export type AiProcessCoordinateSpace = 'normalized_0_1000' | 'pixels' | string;

export interface AiProcessItem {
  index: number;
  tool?: string | null;
  reason?: string | null;
}

export interface AiProcessBox {
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  score?: number | string | null;
}

export interface AiProcessContractPrompt {
  prompt_binding_id?: number | null;
  prompt_sha256?: string | null;
  contract_id?: number | null;
  contract_revision?: number | null;
}

export interface AiProcessDetail {
  provider?: string | null;
  model?: string | null;
  endpoint_host?: string | null;
  request_protocol?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  model_image_width?: number | null;
  model_image_height?: number | null;
  coordinate_space?: AiProcessCoordinateSpace | null;
  coordinate_scale?: number[] | null;
  latency_ms?: number | null;
  instruction?: string | null;
  raw_response_text?: string | null;
  box_count?: number | null;
  dropped_items?: AiProcessItem[] | null;
  repaired_items?: AiProcessItem[] | null;
  contract_prompt?: AiProcessContractPrompt | null;
  boxes?: AiProcessBox[] | null;
}

export interface AiProcessResponse {
  sample_id: number;
  pre_annotation_id?: number | null;
  created_at?: string | null;
  process: AiProcessDetail | null;
}

export async function getAiProcess(
  taskId: number,
  sampleId: number,
): Promise<{ data: AiProcessResponse }> {
  return await request.get(`/v1/tasks/${taskId}/samples/${sampleId}/ai_process`);
}
