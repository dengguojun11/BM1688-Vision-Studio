import { describe, expect, it, vi, beforeEach } from 'vitest';

const queryStore = vi.hoisted(() => new Map<string, unknown>());

vi.mock('@/api/queryClient', () => ({
  default: {
    clear: () => queryStore.clear(),
    getQueryData: (key: unknown) => queryStore.get(JSON.stringify(key)),
    setQueryData: (key: unknown, value: unknown) => queryStore.set(JSON.stringify(key), value),
    removeQueries: ({ queryKey }: { queryKey: unknown }) => queryStore.delete(JSON.stringify(queryKey)),
    prefetchQuery: vi.fn(),
  },
}));

vi.mock('@labelu/image', () => ({
  decodeImage: vi.fn(),
  preloadImage: vi.fn(),
}));

vi.mock('@/api/services/preAnnotations', () => ({
  getPreAnnotations: vi.fn(),
}));

vi.mock('@/api/services/samples', () => ({
  getSample: vi.fn(),
}));

import queryClient from '@/api/queryClient';
import { sampleKey } from '@/api/queryKeyFactories';
import { SampleState } from '@/api/types';

import { cacheSampleDetail, warmSampleDetailCacheFromWindow } from './annotationPrefetch';

const createSample = () =>
  ({
    id: 12,
    inner_id: 12,
    state: SampleState.DONE,
    annotated_count: 2,
    is_pre_annotated: false,
    is_yolo_pre_annotated: false,
    data: {
      result: '{"width":100}',
      file: {
        id: 21,
        filename: 'demo.jpg',
        url: '/api/v1/tasks/attachment/demo.jpg',
      },
    },
    file: {
      id: 21,
      filename: 'demo.jpg',
      url: '/api/v1/tasks/attachment/demo.jpg',
      annotation_display_url: '/api/v1/tasks/annotation-display/demo.jpg',
      original_width: 1920,
      original_height: 1080,
    },
  }) as any;

describe('annotationPrefetch', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.restoreAllMocks();
  });

  it('warms sample detail cache from the current sample window data', () => {
    const sample = createSample();

    warmSampleDetailCacheFromWindow(sample);

    expect(queryClient.getQueryData(sampleKey.detail(sample.id))).toEqual({
      data: {
        ...sample,
        data: {
          ...sample.data,
          file: {
            ...sample.data.file,
            ...sample.file,
          },
        },
        file: sample.file,
      },
    });
    expect(queryClient.getQueryData(sampleKey.detail(String(sample.id)))).toEqual({
      data: {
        ...sample,
        data: {
          ...sample.data,
          file: {
            ...sample.data.file,
            ...sample.file,
          },
        },
        file: sample.file,
      },
    });
  });

  it('does not overwrite an existing richer cached detail', () => {
    const sample = createSample();
    const existing = {
      data: {
        ...sample,
        file: {
          ...sample.file,
          annotation_display_url: '/cached.jpg',
        },
      },
    };

    queryClient.setQueryData(sampleKey.detail(sample.id), existing);

    warmSampleDetailCacheFromWindow({
      ...sample,
      file: {
        ...sample.file,
        annotation_display_url: '/from-window.jpg',
      },
    } as any);

    expect(queryClient.getQueryData(sampleKey.detail(sample.id))).toEqual(existing);
  });

  it('does not let an older prefetch response overwrite a newer saved detail', () => {
    const sample = createSample();
    const newer = {
      data: {
        ...sample,
        updated_at: '2026-09-11T10:00:00.000Z',
        data: { ...sample.data, result: '{"width":100,"rectTool":{"result":[]}}' },
      },
    };
    const older = {
      data: {
        ...sample,
        updated_at: '2026-09-11T09:00:00.000Z',
        data: { ...sample.data, result: '{"width":100,"rectTool":{"result":[{"id":"old"}]}}' },
      },
    };

    queryClient.setQueryData(sampleKey.detail(sample.id), newer);
    cacheSampleDetail(sample.id, older as any);

    expect(queryClient.getQueryData(sampleKey.detail(sample.id))).toEqual(newer);
    expect(queryClient.getQueryData(sampleKey.detail(String(sample.id)))).toEqual(newer);
  });

  it('does not warm detail cache from window data without annotation display metadata', () => {
    const sample = createSample();
    const listOnlySample = {
      ...sample,
      file: {
        id: sample.file.id,
        filename: sample.file.filename,
        url: sample.file.url,
      },
      data: {
        ...sample.data,
        file: {
          id: sample.file.id,
          filename: sample.file.filename,
          url: sample.file.url,
        },
      },
    };

    warmSampleDetailCacheFromWindow(listOnlySample as any);

    expect(queryClient.getQueryData(sampleKey.detail(sample.id))).toBeUndefined();
    expect(queryClient.getQueryData(sampleKey.detail(String(sample.id)))).toBeUndefined();
  });
});
