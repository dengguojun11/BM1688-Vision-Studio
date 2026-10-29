import { beforeEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import { getTaskLabelImages } from './task';

vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(request.get);

describe('task label image search contract', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({ data: [], meta_data: { total: 0, page: 0, size: 0 } } as never);
  });

  it('forwards a trimmed q while preserving label scope and pagination', async () => {
    await getTaskLabelImages({
      label_id: 7,
      page: 2,
      size: 12,
      q: '  camera-01  ',
      dataset_id: 9,
    });

    expect(mockedGet).toHaveBeenCalledWith('/v1/tasks/label-images', {
      params: {
        label_id: 7,
        page: 2,
        size: 12,
        q: 'camera-01',
        dataset_id: 9,
      },
    });
  });

  it('omits an empty q so the unfiltered endpoint remains compatible', async () => {
    await getTaskLabelImages({ label_id: 7, q: '   ' });

    expect(mockedGet).toHaveBeenCalledWith('/v1/tasks/label-images', {
      params: { label_id: 7, page: 0, size: 24 },
    });
  });
});
