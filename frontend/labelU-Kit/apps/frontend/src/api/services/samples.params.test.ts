import { normalizeSampleListParams } from './samples';
import { getSamples } from './samples';
import request from '@/api/request';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(request.get);

describe('sample list params', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('converts one-based UI pages to zero-based API pages', () => {
    expect(normalizeSampleListParams({ page: 3 })).toEqual({ page: 2 });
  });

  it('does not add page when using an after cursor', () => {
    expect(normalizeSampleListParams({ after: 12, size: 40 } as any)).toEqual({
      after: 12,
      size: 40,
      page: undefined,
    });
  });

  it('does not add page when using a before cursor', () => {
    expect(normalizeSampleListParams({ before: 12, size: 20 } as any)).toEqual({
      before: 12,
      size: 20,
      page: undefined,
    });
  });

  it('forwards the server-side search keyword with one-based page conversion', async () => {
    mockedGet.mockResolvedValue({ data: [], meta_data: { total: 0 } } as never);

    await getSamples({ task_id: 9, page: 3, size: 20, q: 'road-01' });

    expect(mockedGet).toHaveBeenCalledWith('/v1/tasks/9/samples', {
      params: { page: 2, size: 20, q: 'road-01' },
    });
  });
});
