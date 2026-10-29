import { describe, expect, it, vi } from 'vitest';

import { SampleState } from '@/api/types';

import {
  getImageBackgroundSaveFingerprint,
  runImageBackgroundSaveOnce,
  shouldSkipUnchangedImageBackgroundSave,
} from './imageBackgroundSave';

const createSample = (state: SampleState, result: string, annotatedCount = 1, negative = false) =>
  ({
    inner_id: 1,
    is_pre_annotated: false,
    state,
    data: { result, ...(negative ? { training_sample_type: 'negative' } : {}) },
    file: {
      id: 1,
      url: '/sample.jpg',
      filename: 'sample.jpg',
    },
    annotated_count: annotatedCount,
  }) as any;

describe('imageBackgroundSave', () => {
  it('skips unchanged DONE image background saves', () => {
    const sample = createSample(SampleState.DONE, '{"width":100,"rectTool":{"result":[{"id":"a"}]}}');
    const payload = createSample(SampleState.DONE, '{"width":100,"rectTool":{"result":[{"id":"a"}]}}');

    expect(shouldSkipUnchangedImageBackgroundSave(sample, payload)).toBe(true);
  });

  it('does not skip NEW to DONE saves even when the result is unchanged', () => {
    const sample = createSample(SampleState.NEW, '{"width":100}');
    const payload = createSample(SampleState.DONE, '{"width":100}');

    expect(shouldSkipUnchangedImageBackgroundSave(sample, payload)).toBe(false);
  });

  it('does not skip changed DONE image background saves', () => {
    const sample = createSample(SampleState.DONE, '{"width":100}');
    const payload = createSample(SampleState.DONE, '{"width":100,"rectTool":{"result":[{"id":"a"}]}}');

    expect(shouldSkipUnchangedImageBackgroundSave(sample, payload)).toBe(false);
  });

  it('does not skip a changed negative-sample decision', () => {
    const sample = createSample(SampleState.DONE, '{"width":100}', 0, false);
    const payload = createSample(SampleState.DONE, '{"width":100}', 0, true);

    expect(shouldSkipUnchangedImageBackgroundSave(sample, payload)).toBe(false);
    expect(getImageBackgroundSaveFingerprint(1, sample)).not.toBe(getImageBackgroundSaveFingerprint(1, payload));
  });

  it('uses the sample id and payload to fingerprint inflight saves', () => {
    const payload = createSample(SampleState.DONE, '{"width":100}');

    expect(getImageBackgroundSaveFingerprint(1, payload)).toBe(getImageBackgroundSaveFingerprint(1, payload));
    expect(getImageBackgroundSaveFingerprint(1, payload)).not.toBe(getImageBackgroundSaveFingerprint(2, payload));
    expect(getImageBackgroundSaveFingerprint(1, payload)).not.toBe(
      getImageBackgroundSaveFingerprint(1, createSample(SampleState.DONE, '{"width":101}')),
    );
  });

  it('shares the same inflight image background save', async () => {
    const fingerprint = getImageBackgroundSaveFingerprint(1, createSample(SampleState.DONE, '{"width":100}'));
    let resolveRequest: (value: string) => void = () => {};
    const request = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          resolveRequest = resolve;
        }),
    );

    const first = runImageBackgroundSaveOnce(fingerprint, request);
    const second = runImageBackgroundSaveOnce(fingerprint, request);

    await Promise.resolve();
    expect(request).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);

    resolveRequest('saved');
    await expect(first).resolves.toBe('saved');
  });

  it('does not deduplicate different samples or payloads', async () => {
    const basePayload = createSample(SampleState.DONE, '{"width":100}');
    const changedPayload = createSample(SampleState.DONE, '{"width":101}');
    const baseFingerprint = getImageBackgroundSaveFingerprint(1, basePayload);
    const differentSampleFingerprint = getImageBackgroundSaveFingerprint(2, basePayload);
    const differentPayloadFingerprint = getImageBackgroundSaveFingerprint(1, changedPayload);
    const request = vi.fn(() => Promise.resolve('saved'));

    await Promise.all([
      runImageBackgroundSaveOnce(baseFingerprint, request),
      runImageBackgroundSaveOnce(differentSampleFingerprint, request),
      runImageBackgroundSaveOnce(differentPayloadFingerprint, request),
    ]);

    expect(request).toHaveBeenCalledTimes(3);
  });
});
