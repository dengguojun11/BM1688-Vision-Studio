import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import { buildPlatformProjectRequest, fetchPlatformProjects } from './project';

vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(request.get);

afterEach(() => {
  mockedGet.mockReset();
});

describe('platform project search contract', () => {
  it('normalizes q and keeps server pagination bounded', () => {
    expect(buildPlatformProjectRequest({ q: '  道路巡检  ', page: 2, size: 20 })).toEqual({
      page: 2,
      size: 20,
      q: '道路巡检',
    });
    expect(buildPlatformProjectRequest({ search: '  ', page: -1, size: 9999 })).toEqual({
      page: 0,
      size: 500,
    });
  });

  it('requests q/page/size and reads the standard nested response envelope', async () => {
    mockedGet.mockResolvedValue({
      data: {
        data: [{ id: 7, name: '道路巡检' }],
        meta_data: { total: 1, page: 1, size: 1 },
      },
    } as never);

    await expect(fetchPlatformProjects({ q: '道路', page: 1, size: 1 })).resolves.toEqual([
      { id: 7, name: '道路巡检' },
    ]);
    expect(mockedGet).toHaveBeenCalledWith('/v1/projects', {
      params: { page: 1, size: 1, q: '道路' },
    });
  });
});
