import type { LoaderFunctionArgs } from 'react-router';

import queryClient from '@/api/queryClient';
import { sampleKey } from '@/api/queryKeyFactories';
import { getSample } from '@/api/services/samples';
import { preAnnotationKey } from '@/api/queryKeyFactories/preAnnotation';
import { getPreAnnotations } from '@/api/services/preAnnotations';
import { shouldAutoLoadCanvasPreAnnotationsForSample } from '@/pages/tasks.[id].samples.[id]/preAnnotationLoading';
import { hasAnnotationDisplayMetadata } from '@/pages/tasks.[id].samples.[id]/annotationPrefetch';

export const SAMPLE_LOADER_STALE_TIME_MS = 2 * 60 * 1000;

export async function sampleLoader({ params, request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const compareAccessToken = url.searchParams.get('compare_access_token') || undefined;
  const queryKey = sampleKey.detail(params.sampleId!);

  const result: {
    sample: Awaited<ReturnType<typeof getSample>> | undefined;
    preAnnotation: Awaited<ReturnType<typeof getPreAnnotations>> | undefined;
  } = {
    sample: undefined,
    preAnnotation: undefined,
  };

  const cachedSample = queryClient.getQueryData<Awaited<ReturnType<typeof getSample>>>(queryKey);

  if (cachedSample && hasAnnotationDisplayMetadata(cachedSample.data)) {
    result.sample = cachedSample;
  } else {
    result.sample = await getSample({
      sample_id: +params.sampleId!,
      task_id: +params.taskId!,
      ...(compareAccessToken ? { compare_access_token: compareAccessToken } : {}),
    });
    queryClient.setQueryData(queryKey, result.sample);
    queryClient.setQueryData(sampleKey.detail(+params.sampleId!), result.sample);
  }

  const sampleName = result?.sample?.data?.file?.filename;

  if (!sampleName || !shouldAutoLoadCanvasPreAnnotationsForSample(result.sample?.data)) {
    return result;
  }

  if (compareAccessToken) {
    return result;
  }

  const preAnnotationQueryKey = preAnnotationKey.listWithSampleName({
    task_id: +params.taskId!,
    sample_name: sampleName,
    canvas_only: true,
  });

  result.preAnnotation = queryClient.getQueryData(preAnnotationQueryKey);

  void queryClient.prefetchQuery({
    queryKey: preAnnotationQueryKey,
    queryFn: () =>
      getPreAnnotations({
        task_id: +params.taskId!,
        sample_name: sampleName,
        canvas_only: true,
      }),
    staleTime: SAMPLE_LOADER_STALE_TIME_MS,
  });

  return result;
}
