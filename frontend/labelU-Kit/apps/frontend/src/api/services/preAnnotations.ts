import request from '../request';
import { uploadFile } from './task';
import type {
  DeleteApiV1TasksTaskIdDeleteParams,
  ListByApiV1TasksTaskIdSamplesGetParams,
  OkRespCommonDataResp,
  OkRespCreateSampleResponse,
  AttachmentListResponse,
  DeletePreAnnotationFileParams,
  PreAnnotationListResponse,
} from '../types';

export async function createPreAnnotations(
  taskId: number,
  data: {
    file_id: number;
  }[],
): Promise<OkRespCreateSampleResponse> {
  return await request.post(`/v1/tasks/${taskId}/pre_annotations`, data);
}

/** Upload a pre-annotation file and register it as task pre-annotation data. */
export async function uploadPreAnnotationFile(taskId: number, file: File) {
  const attachment = await uploadFile({ task_id: taskId, file });
  if (!attachment.data?.id) {
    throw new Error('预标注文件上传后未返回附件 ID');
  }
  const created = await createPreAnnotations(taskId, [{ file_id: attachment.data.id }]);
  return { attachment: attachment.data, created: created.data };
}

export async function getPreAnnotationFiles({
  task_id,
  ...params
}: ListByApiV1TasksTaskIdSamplesGetParams & {
  sample_name?: string;
}): Promise<AttachmentListResponse> {
  return await request.get(`/v1/tasks/${task_id}/pre_annotations/files`, {
    params: {
      ...params,
      page: typeof params.page === 'undefined' ? 0 : params.page - 1,
    },
  });
}

export async function getPreAnnotations({
  task_id,
  ...params
}: ListByApiV1TasksTaskIdSamplesGetParams & {
  sample_name?: string;
  canvas_only?: boolean;
}): Promise<PreAnnotationListResponse> {
  return await request.get(`/v1/tasks/${task_id}/pre_annotations`, {
    params: {
      ...params,
      page: typeof params.page === 'undefined' ? 0 : params.page - 1,
    },
  });
}

export async function deletePreAnnotations(
  { task_id }: DeleteApiV1TasksTaskIdDeleteParams,
  body: {
    pre_annotation_ids: number[];
  },
): Promise<OkRespCommonDataResp> {
  return await request.delete(`/v1/tasks/${task_id}/pre_annotations`, {
    data: body,
  });
}

export async function deletePreAnnotationFile({
  task_id,
  file_id,
}: DeletePreAnnotationFileParams): Promise<OkRespCommonDataResp> {
  return await request.delete(`/v1/tasks/${task_id}/pre_annotations/files/${file_id}`);
}
