import { beforeEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import { createLabelTrainingJob, createTaskTrainingJob } from './training';

vi.mock('@/api/request', () => ({
  default: { post: vi.fn() },
}));

const mockedPost = vi.mocked(request.post);

describe('training create idempotency contract', () => {
  beforeEach(() => mockedPost.mockReset());

  it('sends the same key in the header for label-dimension training', async () => {
    const payload = { selected_task_ids: [10], label_ids: [3], model_weights: 'yolo11n.pt' };
    mockedPost.mockResolvedValue({ data: { id: 101 } } as never);

    await createLabelTrainingJob(payload, 'label-key-1');
    await createLabelTrainingJob(payload, 'label-key-1');

    expect(mockedPost).toHaveBeenNthCalledWith(
      1,
      '/v1/yolo-training-tasks',
      payload,
      { headers: { 'Idempotency-Key': 'label-key-1' } },
    );
    expect(mockedPost).toHaveBeenNthCalledWith(
      2,
      '/v1/yolo-training-tasks',
      payload,
      { headers: { 'Idempotency-Key': 'label-key-1' } },
    );
  });

  it('sends the same key in the header for task-dimension training', async () => {
    const payload = { task_id: 10, algorithm_key: 'yolo_detection', model_weights: 'yolo11n.pt' };
    mockedPost.mockResolvedValue({ data: { id: 102 } } as never);

    await createTaskTrainingJob(payload, 'task-key-1');
    await createTaskTrainingJob(payload, 'task-key-1');

    expect(mockedPost).toHaveBeenNthCalledWith(
      1,
      '/v1/training/jobs',
      payload,
      { headers: { 'Idempotency-Key': 'task-key-1' } },
    );
    expect(mockedPost).toHaveBeenNthCalledWith(
      2,
      '/v1/training/jobs',
      payload,
      { headers: { 'Idempotency-Key': 'task-key-1' } },
    );
  });
});
