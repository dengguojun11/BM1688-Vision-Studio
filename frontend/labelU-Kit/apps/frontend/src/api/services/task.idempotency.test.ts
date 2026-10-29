import { beforeEach, describe, expect, it, vi } from 'vitest';

import request, { requestWithHeaders } from '@/api/request';
import { createTaskWithBasicConfig, getAccessibleDatasetsPage, getTaskList } from './task';

vi.mock('@/api/request', () => ({
  default: { post: vi.fn(), get: vi.fn() },
  requestWithHeaders: { get: vi.fn() },
}));

const mockedPost = vi.mocked(request.post);
const mockedDatasetGet = vi.mocked(request.get);
const mockedGet = vi.mocked(requestWithHeaders.get);

describe('task create idempotency contract', () => {
  beforeEach(() => {
    mockedPost.mockReset();
    mockedDatasetGet.mockReset();
    mockedGet.mockReset();
  });

  it('puts the retry key in Idempotency-Key and keeps the body unchanged', async () => {
    const payload = { name: '安全帽检测第一批', media_type: 'IMAGE' as never };
    mockedPost.mockResolvedValue({ data: { id: 7 } } as never);

    await createTaskWithBasicConfig(payload, 'task-key-1');
    await createTaskWithBasicConfig(payload, 'task-key-1');

    expect(mockedPost).toHaveBeenNthCalledWith(
      1,
      '/v1/tasks',
      payload,
      { headers: { 'Idempotency-Key': 'task-key-1' } },
    );
    expect(mockedPost).toHaveBeenNthCalledWith(
      2,
      '/v1/tasks',
      payload,
      { headers: { 'Idempotency-Key': 'task-key-1' } },
    );
  });

  it('forwards the task search query while converting the UI page to the API page', async () => {
    mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);

    await getTaskList({ page: 3, size: 16, q: '待复核' });

    expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      timeout: 0,
      params: { size: 16, page: 2, q: '待复核' },
    });
  });

  it('requests one server-side dataset page and preserves the response total', async () => {
    mockedDatasetGet.mockResolvedValue({
      data: {
        data: [{ id: 9, name: '道路数据集' }],
        meta_data: { total: 41, page: 2, size: 20 },
      },
    } as never);

    const result = await getAccessibleDatasetsPage({ page: 2, size: 20, q: '道路' });

    expect(mockedDatasetGet).toHaveBeenCalledWith('/v1/datasets', {
      params: { page: 2, size: 20, q: '道路' },
    });
    expect(result.data).toEqual([{ id: 9, name: '道路数据集' }]);
    expect(result.meta_data).toEqual({ total: 41, page: 2, size: 20 });
  });
});
