import request from '../request';
import { idempotencyRequestConfig } from '../idempotency';

export type TrainingCreatePayload = Record<string, unknown>;

/** Create a label-dimension YOLO training job. */
export async function createLabelTrainingJob(
  payload: TrainingCreatePayload,
  idempotencyKey?: string,
) {
  const requestConfig = idempotencyRequestConfig(idempotencyKey);
  return requestConfig
    ? await request.post('/v1/yolo-training-tasks', payload, requestConfig)
    : await request.post('/v1/yolo-training-tasks', payload);
}

/** Create a task-dimension training job through the unified training API. */
export async function createTaskTrainingJob(
  payload: TrainingCreatePayload,
  idempotencyKey?: string,
) {
  const requestConfig = idempotencyRequestConfig(idempotencyKey);
  return requestConfig
    ? await request.post('/v1/training/jobs', payload, requestConfig)
    : await request.post('/v1/training/jobs', payload);
}
