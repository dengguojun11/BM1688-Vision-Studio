import type { SampleResponse } from '@/api/types';
import { SampleState } from '@/api/types';

export type ImageBackgroundSavePayload = Pick<SampleResponse, 'data' | 'state' | 'annotated_count'>;

const normalizeResult = (value: unknown) => {
  if (typeof value !== 'string') {
    return value == null ? '' : JSON.stringify(value);
  }

  try {
    return JSON.stringify(JSON.parse(value));
  } catch {
    return value;
  }
};

export const getImageBackgroundSaveFingerprint = (sampleId: number, payload: ImageBackgroundSavePayload) =>
  JSON.stringify({
    sampleId,
    result: normalizeResult(payload.data?.result),
    training_sample_type: payload.data?.training_sample_type || '',
    state: payload.state,
    annotated_count: payload.annotated_count,
  });

const imageBackgroundSaveRequests = new Map<string, Promise<unknown>>();

export const runImageBackgroundSaveOnce = <T>(fingerprint: string, request: () => Promise<T>) => {
  const existing = imageBackgroundSaveRequests.get(fingerprint);
  if (existing) {
    return existing as Promise<T>;
  }

  const requestPromise = Promise.resolve().then(request);
  const trackedPromise = requestPromise.finally(() => {
    if (imageBackgroundSaveRequests.get(fingerprint) === trackedPromise) {
      imageBackgroundSaveRequests.delete(fingerprint);
    }
  });

  imageBackgroundSaveRequests.set(fingerprint, trackedPromise);
  return trackedPromise;
};

export const shouldSkipUnchangedImageBackgroundSave = (
  currentSample: SampleResponse | undefined,
  payload: ImageBackgroundSavePayload,
) => {
  if (!currentSample || currentSample.state !== SampleState.DONE || payload.state !== SampleState.DONE) {
    return false;
  }

  return (
    normalizeResult(currentSample.data?.result) === normalizeResult(payload.data?.result) &&
    (currentSample.data?.training_sample_type || '') === (payload.data?.training_sample_type || '') &&
    currentSample.state === payload.state &&
    currentSample.annotated_count === payload.annotated_count
  );
};
