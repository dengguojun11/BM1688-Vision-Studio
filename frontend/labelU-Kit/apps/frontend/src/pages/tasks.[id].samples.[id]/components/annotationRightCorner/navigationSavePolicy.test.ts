import { describe, expect, it } from 'vitest';

import { SampleState } from '@/api/types';

import { shouldSaveBeforeSampleNavigation } from './navigationSavePolicy';

describe('shouldSaveBeforeSampleNavigation', () => {
  it('does not block navigation for an already skipped sample', () => {
    expect(shouldSaveBeforeSampleNavigation({ sampleState: SampleState.SKIPPED })).toBe(false);
  });

  it('saves a normal sample before navigation', () => {
    expect(shouldSaveBeforeSampleNavigation({ sampleState: SampleState.NEW, isCurrentUser: true })).toBe(true);
    expect(shouldSaveBeforeSampleNavigation({ sampleState: SampleState.DONE, isCurrentUser: true })).toBe(true);
  });

  it('does not save when the current user has no editing lock', () => {
    expect(shouldSaveBeforeSampleNavigation({ sampleState: SampleState.DONE, isCurrentUser: false })).toBe(false);
    expect(shouldSaveBeforeSampleNavigation({ sampleState: SampleState.DONE })).toBe(false);
  });

  it('does not save in preview or compare mode', () => {
    expect(shouldSaveBeforeSampleNavigation({ sampleState: SampleState.DONE, noSave: true, isCurrentUser: true })).toBe(false);
    expect(
      shouldSaveBeforeSampleNavigation({ sampleState: SampleState.DONE, isCompareAccessMode: true, isCurrentUser: true }),
    ).toBe(false);
  });
});
