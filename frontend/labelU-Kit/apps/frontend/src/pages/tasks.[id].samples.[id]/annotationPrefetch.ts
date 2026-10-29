import _ from 'lodash-es';
import { decodeImage, preloadImage } from '@labelu/image';

import queryClient from '@/api/queryClient';
import { sampleKey } from '@/api/queryKeyFactories';
import { preAnnotationKey } from '@/api/queryKeyFactories/preAnnotation';
import { getPreAnnotations } from '@/api/services/preAnnotations';
import type { SampleResponse } from '@/api/types';
import { getSample } from '@/api/services/samples';

import { shouldAutoLoadCanvasPreAnnotationsForSample } from './preAnnotationLoading';
import { createNearbySamplePrefetchTargets } from './sampleWindow';

export const ANNOTATION_PREFETCH_STALE_TIME_MS = 2 * 60 * 1000;
/** 生产翻页：只预取紧邻样本，避免切换时并发请求和解码过多 */
export const ANNOTATION_PREFETCH_RADIUS = 1;

const toAnnotationDisplayUrl = (url?: string | null) => {
  if (!url) {
    return url;
  }

  return url.includes('/tasks/attachment/') ? url.replace('/tasks/attachment/', '/tasks/annotation-display/') : url;
};

export const getSampleImageUrl = (target?: SampleResponse) =>
  _.get(target, 'file.annotation_display_url') ||
  _.get(target, 'data.file.annotation_display_url') ||
  toAnnotationDisplayUrl(_.get(target, 'file.url') || _.get(target, 'data.file.url'));

const getSampleFilename = (target?: SampleResponse) =>
  _.get(target, 'file.filename') || _.get(target, 'data.file.filename');

type SampleDetail = Awaited<ReturnType<typeof getSample>>;

const getSampleUpdatedAt = (detail?: SampleDetail) => {
  const value = detail?.data?.updated_at ?? (detail as any)?.updated_at;
  if (!value) {
    return undefined;
  }

  const timestamp = Date.parse(String(value));
  return Number.isFinite(timestamp) ? timestamp : undefined;
};

const chooseNewestSampleDetail = (current: SampleDetail | undefined, candidate: SampleDetail | undefined) => {
  if (!current) {
    return candidate;
  }
  if (!candidate) {
    return current;
  }

  const currentUpdatedAt = getSampleUpdatedAt(current);
  const candidateUpdatedAt = getSampleUpdatedAt(candidate);

  // A response without a version must not overwrite a versioned cache entry.
  if (candidateUpdatedAt === undefined) {
    return current;
  }
  if (currentUpdatedAt === undefined || candidateUpdatedAt >= currentUpdatedAt) {
    return candidate;
  }

  return current;
};

/** Write both detail-key variants while preventing an older prefetch response from winning. */
export const cacheSampleDetail = (sampleId: number, detail: SampleDetail) => {
  const cachedNumeric = queryClient.getQueryData<SampleDetail>(sampleKey.detail(sampleId));
  const cachedString = queryClient.getQueryData<SampleDetail>(sampleKey.detail(`${sampleId}`));
  const cached = chooseNewestSampleDetail(cachedNumeric, cachedString);
  const newest = chooseNewestSampleDetail(cached, detail) ?? detail;

  queryClient.setQueryData(sampleKey.detail(sampleId), newest);
  queryClient.setQueryData(sampleKey.detail(`${sampleId}`), newest);
  return newest;
};

export const hasAnnotationDisplayMetadata = (target?: SampleResponse | Awaited<ReturnType<typeof getSample>>['data']) => {
  const file = _.get(target, 'file') || _.get(target, 'data.file');

  return !!(
    _.get(file, 'annotation_display_url') ||
    _.get(file, 'original_width') ||
    _.get(file, 'original_height')
  );
};

const removeSampleDetailCache = (sampleId: number) => {
  queryClient.removeQueries({ queryKey: sampleKey.detail(sampleId), exact: true });
  queryClient.removeQueries({ queryKey: sampleKey.detail(`${sampleId}`), exact: true });
};

const buildOptimisticSampleDetail = (target?: SampleResponse) => {
  if (!target?.id) {
    return undefined;
  }

  const dataFile = _.get(target, 'data.file');
  const file = target.file || (_.isPlainObject(dataFile) ? dataFile : undefined);

  if (!file) {
    return undefined;
  }

  const mergedData = _.isPlainObject(target.data)
    ? {
        ...target.data,
        file: {
          ...(_.isPlainObject(dataFile) ? dataFile : {}),
          ...file,
        },
      }
    : {
        file,
      };

  return {
    data: {
      ...target,
      data: mergedData,
      file,
    },
  };
};

export const warmSampleDetailCacheFromWindow = (target?: SampleResponse) => {
  if (!target?.id) {
    return;
  }

  const existing =
    queryClient.getQueryData<Awaited<ReturnType<typeof getSample>>>(sampleKey.detail(target.id)) ||
    queryClient.getQueryData<Awaited<ReturnType<typeof getSample>>>(sampleKey.detail(`${target.id}`));

  if (existing?.data?.file?.annotation_display_url || existing?.data?.file?.original_width) {
    cacheSampleDetail(target.id, existing);
    return;
  }

  if (existing && !hasAnnotationDisplayMetadata(existing.data)) {
    removeSampleDetailCache(target.id);
  }

  if (!hasAnnotationDisplayMetadata(target)) {
    return;
  }

  const optimisticDetail = buildOptimisticSampleDetail(target);

  if (optimisticDetail) {
    cacheSampleDetail(target.id, optimisticDetail as SampleDetail);
  }
};

export const prefetchSampleDetailAndPreAnnotations = (taskId: number, target?: SampleResponse) => {
  const sampleId = target?.id;

  if (!sampleId) {
    return;
  }

  warmSampleDetailCacheFromWindow(target);

  void queryClient
    .prefetchQuery({
      queryKey: sampleKey.detail(`${sampleId}`),
      queryFn: () =>
        getSample({
          task_id: taskId,
          sample_id: sampleId,
        }),
      staleTime: ANNOTATION_PREFETCH_STALE_TIME_MS,
    })
    .then(() => {
      const prefetchedSample =
        queryClient.getQueryData<SampleDetail>(sampleKey.detail(`${sampleId}`)) ||
        queryClient.getQueryData<SampleDetail>(sampleKey.detail(sampleId));

      if (!prefetchedSample?.data) {
        return undefined;
      }

      const cachedSample = cacheSampleDetail(sampleId, prefetchedSample);

      const sampleName = cachedSample?.data?.file?.filename || getSampleFilename(target);
      const resolvedSample = cachedSample?.data || target;

      if (!sampleName || !shouldAutoLoadCanvasPreAnnotationsForSample(resolvedSample)) {
        return undefined;
      }

      return queryClient.prefetchQuery({
        queryKey: preAnnotationKey.listWithSampleName({
          task_id: taskId,
          sample_name: sampleName,
          canvas_only: true,
        }),
        queryFn: () =>
          getPreAnnotations({
            task_id: taskId,
            sample_name: sampleName,
            canvas_only: true,
          }),
        staleTime: ANNOTATION_PREFETCH_STALE_TIME_MS,
      });
    })
    .catch(() => undefined);
};

const prefetchSampleImage = (
  target: SampleResponse | undefined,
  currentIndex: number,
  targetIndex: number,
) => {
  const url = getSampleImageUrl(target);

  if (!url) {
    return;
  }

  preloadImage(url);

  const distance = Math.abs(targetIndex - currentIndex);
  const isImmediateNext = targetIndex === currentIndex + 1;
  const priority = isImmediateNext && distance === 1 ? 'high' : distance <= 1 ? 'auto' : 'low';

  void decodeImage(url, priority);
};

/** 当前页前后 radius 张：仅预下载展示图，详情和预标注等用户动作发生时再请求。 */
export const prewarmAdjacentSamples = (
  taskId: number,
  samples: SampleResponse[],
  currentSampleId?: number,
  radius = ANNOTATION_PREFETCH_RADIUS,
) => {
  if (typeof currentSampleId !== 'number') {
    return;
  }

  const currentIndex = samples.findIndex((item) => item.id === currentSampleId);

  if (currentIndex < 0) {
    return;
  }

  const targets = createNearbySamplePrefetchTargets(samples, currentSampleId, radius);

  targets.forEach((target) => {
    const targetIndex = samples.findIndex((item) => item.id === target.id);
    prefetchSampleImage(target, currentIndex, targetIndex);
  });
};

/** 悬停/快捷键「下一张」前：高优先级预取紧邻下一张 */
export const prefetchUpcomingSample = (
  taskId: number,
  samples: SampleResponse[],
  currentSampleIndex: number,
) => {
  if (currentSampleIndex < 0) {
    return;
  }

  const nextSample = samples[currentSampleIndex + 1];

  if (!nextSample) {
    return;
  }

  prefetchSampleDetailAndPreAnnotations(taskId, nextSample);
  prefetchSampleImage(nextSample, currentSampleIndex, currentSampleIndex + 1);
};
