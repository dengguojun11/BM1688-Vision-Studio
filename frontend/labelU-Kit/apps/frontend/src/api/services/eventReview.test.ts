import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '../request';
import { getEventReviewList } from './eventReview';

vi.mock('../request', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(request.get);

afterEach(() => {
  mockedGet.mockReset();
});

describe('event review list search contract', () => {
  it('sends canonical q and maps the legacy keyword alias', async () => {
    mockedGet.mockResolvedValue({ data: [], meta_data: { total: 0, page: 0, size: 0 } } as never);

    await getEventReviewList({ page: 2, size: 20, q: '  helmet  ' });
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/event-review', {
      params: { page: 2, size: 20, q: 'helmet' },
    });

    await getEventReviewList({ page: 0, size: 10, keyword: 'legacy alarm' });
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/event-review', {
      params: { page: 0, size: 10, q: 'legacy alarm' },
    });
  });

  it('gives q precedence when both search aliases are provided', async () => {
    mockedGet.mockResolvedValue({ data: [], meta_data: { total: 0, page: 0, size: 0 } } as never);

    await getEventReviewList({ q: 'new', keyword: 'old' });
    expect(mockedGet).toHaveBeenCalledWith('/v1/event-review', { params: { q: 'new' } });
  });
});
