import { describe, expect, it } from 'vitest';

import { SampleState } from '@/api/types';

import { buildTaskStatusSamplesPath } from './taskStatusNavigation';

describe('buildTaskStatusSamplesPath', () => {
  it.each([
    [SampleState.NEW, 'NEW'],
    [SampleState.SKIPPED, 'SKIPPED'],
    [SampleState.INCOMPLETE, 'INCOMPLETE'],
    [SampleState.ABNORMAL, 'ABNORMAL'],
  ])('builds the filtered first-page route for %s', (state, queryState) => {
    expect(buildTaskStatusSamplesPath(148, state)).toBe(`/tasks/148?state=${queryState}&page=1&size=10`);
  });
});
