import { beforeEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import { getDataSources } from './datasource';

vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(request.get);

describe('data source list service', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({ data: [], meta_data: { total: 0 } } as never);
  });

  it('forwards q and converts the UI page to the API page', async () => {
    await getDataSources({ page: 3, size: 16, q: 'media bucket' });

    expect(mockedGet).toHaveBeenCalledWith('/v1/datasources', {
      params: { page: 2, size: 16, q: 'media bucket' },
    });
  });
});
