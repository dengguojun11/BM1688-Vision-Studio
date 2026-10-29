import {
  createCurrentFirstSampleWindow,
  createNearbySamplePrefetchTargets,
  createSampleWindow,
  getNextSampleCursor,
  getPreviousSampleCursor,
  prependSampleWindow,
  shouldPreserveSampleWindowForNavigation,
} from './sampleWindow';
import { describe, expect, it } from 'vitest';

describe('sampleWindow', () => {
  it('keeps the current sample near its real neighbours when inner ids have gaps', () => {
    const current = { id: 200, inner_id: 1000 };
    const previous = [
      { id: 190, inner_id: 10 },
      { id: 199, inner_id: 999 },
    ];
    const next = [
      { id: 201, inner_id: 1001 },
      { id: 202, inner_id: 1002 },
    ];

    expect(createSampleWindow(previous, current, next)).toEqual([
      { id: 190, inner_id: 10 },
      { id: 199, inner_id: 999 },
      { id: 200, inner_id: 1000 },
      { id: 201, inner_id: 1001 },
      { id: 202, inner_id: 1002 },
    ]);
  });

  it('deduplicates the current sample while preserving route sample details', () => {
    const current = { id: 200, inner_id: 1000 };

    expect(createSampleWindow([{ id: 199 }, { id: 200 }], current, [{ id: 200 }, { id: 201 }])).toEqual([
      { id: 199 },
      { id: 200, inner_id: 1000 },
      { id: 201 },
    ]);
  });

  it('uses sample ids as cursors instead of sparse inner ids', () => {
    const samples = [
      { id: 199, inner_id: 999 },
      { id: 200, inner_id: 1000 },
      { id: 500, inner_id: 9000 },
    ];

    expect(getNextSampleCursor(samples)).toBe(500);
  });

  it('uses the first sample id as the previous cursor', () => {
    expect(getPreviousSampleCursor([{ id: 199 }, { id: 200 }])).toBe(199);
  });

  it('creates a current-first window for annotation entry and sample switches', () => {
    const current = { id: 200, inner_id: 1000 };

    expect(createCurrentFirstSampleWindow(current, [{ id: 200 }, { id: 201 }, { id: 202 }])).toEqual([
      { id: 200, inner_id: 1000 },
      { id: 201 },
      { id: 202 },
    ]);
  });

  it('prepends previous samples without duplicating already loaded items', () => {
    expect(prependSampleWindow([{ id: 198 }, { id: 199 }, { id: 200 }], [{ id: 200 }, { id: 201 }])).toEqual([
      { id: 198 },
      { id: 199 },
      { id: 200 },
      { id: 201 },
    ]);
  });

  it('selects nearby samples around the current item for prefetching', () => {
    const samples = [
      { id: 1, file: { url: '/a.jpg' } },
      { id: 2, file: { url: '/b.jpg' } },
      { id: 3, file: { url: '/c.jpg' } },
      { id: 4, file: { url: '/d.jpg' } },
      { id: 5, file: { url: '/e.jpg' } },
    ];

    expect(createNearbySamplePrefetchTargets(samples, 3, 1)).toEqual([
      { id: 2, file: { url: '/b.jpg' } },
      { id: 4, file: { url: '/d.jpg' } },
    ]);
  });

  it('deduplicates and ignores invalid prefetch targets', () => {
    const samples = [
      { id: 1, file: { url: '/a.jpg' } },
      { id: 2 },
      { id: 3, file: { url: '/c.jpg' } },
      { id: 1, file: { url: '/a-duplicate.jpg' } },
      { id: 4, file: { url: '/d.jpg' } },
    ];

    expect(createNearbySamplePrefetchTargets(samples, 3, 3)).toEqual([
      { id: 1, file: { url: '/a.jpg' } },
      { id: 4, file: { url: '/d.jpg' } },
    ]);
  });

  it('preserves the current sample window only for list-driven navigation after initialization', () => {
    expect(shouldPreserveSampleWindowForNavigation('list', 201, 200)).toBe(true);
    expect(shouldPreserveSampleWindowForNavigation('next', 201, 200)).toBe(false);
    expect(shouldPreserveSampleWindowForNavigation(undefined, 201, 200)).toBe(false);
    expect(shouldPreserveSampleWindowForNavigation('list', 200, 200)).toBe(false);
    expect(shouldPreserveSampleWindowForNavigation('list', 201, undefined)).toBe(false);
  });
});
