import { describe, expect, it } from 'vitest';

import { resolveDeleteNavigationTarget } from './deleteNavigation';

describe('resolveDeleteNavigationTarget', () => {
  it('prefers the next loaded sample in the current window', () => {
    expect(
      resolveDeleteNavigationTarget({
        taskId: 90,
        samples: [{ id: 10 }, { id: 11 }, { id: 12 }],
        sampleIndex: 1,
      }),
    ).toEqual({
      path: '/tasks/90/samples/12',
      source: 'next',
    });
  });

  it('falls through to fetched next samples before previous ones', () => {
    expect(
      resolveDeleteNavigationTarget({
        taskId: 90,
        samples: [{ id: 10 }],
        sampleIndex: 0,
        nextFetchedSampleId: 13,
        previousFetchedSampleId: 9,
      }),
    ).toEqual({
      path: '/tasks/90/samples/13',
    });
  });

  it('uses the previous loaded sample when there is no next sample', () => {
    expect(
      resolveDeleteNavigationTarget({
        taskId: 90,
        samples: [{ id: 10 }, { id: 11 }],
        sampleIndex: 1,
      }),
    ).toEqual({
      path: '/tasks/90/samples/10',
      source: 'prev',
    });
  });

  it('uses the fetched previous sample when the current window has no neighbours', () => {
    expect(
      resolveDeleteNavigationTarget({
        taskId: 90,
        samples: [{ id: 11 }],
        sampleIndex: 0,
        previousFetchedSampleId: 10,
      }),
    ).toEqual({
      path: '/tasks/90/samples/10',
    });
  });

  it('falls back to the task sample list when no more samples are available', () => {
    expect(
      resolveDeleteNavigationTarget({
        taskId: 90,
        samples: [{ id: 11 }],
        sampleIndex: 0,
      }),
    ).toEqual({
      path: '/tasks/90',
    });
  });
});
