import type { LoaderFunctionArgs } from 'react-router';

import { sampleKey, taskKey } from '@/api/queryKeyFactories';
import {
  getTaskList,
  getTask,
  getTaskLabelImages,
  getAnnotationLabels,
  getTaskLabelSummary,
  type TaskLabelSummary,
} from '@/api/services/task';
import queryClient from '@/api/queryClient';
import { getSamples } from '@/api/services/samples';
import type {
  ListByApiV1TasksGetParams,
  ListByApiV1TasksTaskIdSamplesGetParams,
  SampleListResponse,
  TaskListResponseWithStatics,
  TaskResponseWithStatics,
} from '@/api/types';
import type { ToolsConfigState } from '@/types/toolConfig';
import { preAnnotationKey } from '@/api/queryKeyFactories/preAnnotation';
import { getPreAnnotationFiles } from '@/api/services/preAnnotations';

const TASK_LIST_STALE_TIME_MS = 30 * 1000;
const RECT_PRESET_SUMMARY_STALE_TIME_MS = 60 * 1000;

/** 标注页/完成页由 sampleLoader 或专用路由加载样本，避免 taskLoader 重复请求列表 */
function shouldSkipTaskListPreload(pathname: string): boolean {
  return /\/tasks\/\d+\/samples\/(\d+|finished)(\/|$)/.test(pathname);
}

export interface TasksLoaderResult {
  taskList: TaskListResponseWithStatics;
  /**
   * Sidebar aggregates are intentionally loaded by the page after the first
   * task list render.  They are useful for filtering, but should not block the
   * cards that users came to inspect.
   */
  labelSummary?: TaskLabelSummary;
  headers?: Record<string, string>;
}

export async function tasksLoader({ request }: LoaderFunctionArgs): Promise<TasksLoaderResult | null> {
  const url = new URL(request.url);
  const searchParams = new URLSearchParams(url.search);
  const queryParams = Object.fromEntries(searchParams.entries()) as ListByApiV1TasksGetParams;
  if (searchParams.has('clientId')) {
    return null;
  }

  const [taskListResponse, labelSummaryResponse] = await Promise.all([
    queryClient.fetchQuery({
      queryKey: taskKey.list(queryParams),
      queryFn: () => getTaskList(queryParams),
      staleTime: TASK_LIST_STALE_TIME_MS,
    }),
    queryClient.fetchQuery({
      queryKey: taskKey.labelSummary(),
      queryFn: async () => await getTaskLabelSummary(),
      staleTime: RECT_PRESET_SUMMARY_STALE_TIME_MS,
    }),
  ]);

  return {
    taskList: (taskListResponse as any)?.data,
    labelSummary: (labelSummaryResponse as any)?.data,
    headers: (taskListResponse as any)?.headers,
  };
}

export type TaskInLoader = Omit<TaskResponseWithStatics, 'config'> & {
  config: ToolsConfigState;
};

export interface TaskLoaderResult {
  samples?: SampleListResponse;
  task?: TaskInLoader;

  preAnnotations?: {
    sample_name: string;
    annotations: any;
  }[];
}

export interface TaskLabelImagesLoaderResult {
  labelImages: Awaited<ReturnType<typeof getTaskLabelImages>>;
  label?: Awaited<ReturnType<typeof getAnnotationLabels>>['data'][number];
}

export async function taskLabelImagesLoader({
  params,
  request,
}: LoaderFunctionArgs): Promise<TaskLabelImagesLoaderResult> {
  const url = new URL(request.url);
  const labelId = Number(params.labelId);
  const [labelImages, labels] = await Promise.all([
    getTaskLabelImages({
      label_id: labelId,
      page: Number(url.searchParams.get('page') || 0),
      size: Number(url.searchParams.get('size') || 24),
      q: url.searchParams.get('q') || undefined,
      dataset_category_id: url.searchParams.get('dataset_category_id')
        ? Number(url.searchParams.get('dataset_category_id'))
        : undefined,
      dataset_id: url.searchParams.get('dataset_id')
        ? Number(url.searchParams.get('dataset_id'))
        : undefined,
      uncategorized: url.searchParams.get('uncategorized') === 'true' || undefined,
    }),
    getAnnotationLabels(),
  ]);
  return {
    labelImages,
    label: labels.data.find((item) => item.id === labelId),
  };
}

export async function taskLoader({ params, request }: LoaderFunctionArgs) {
  const result: TaskLoaderResult = {
    samples: undefined,
    task: undefined,
    preAnnotations: undefined,
  };

  // taskId 为 0 时，表示新建任务
  if (!params?.taskId || params.taskId === '0') {
    return result;
  }

  const url = new URL(request.url);
  const pathname = url.pathname;
  const navigationStartedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const logNavigationStage = (stage: string, details: Record<string, unknown> = {}) => {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    // eslint-disable-next-line no-console
    console.info(
      `[LabelU][task-navigation] ${stage} ${JSON.stringify({
        task_id: Number(params.taskId),
        pathname,
        duration_ms: Math.round(now - navigationStartedAt),
        ...details,
      })}`,
    );
  };
  logNavigationStage('load.start');
  const searchParams = new URLSearchParams(url.search);
  const compareAccessToken = searchParams.get('compare_access_token') || undefined;
  const compareSampleId = compareAccessToken && params.sampleId ? +params.sampleId : undefined;
  const queryParams = {
    task_id: +params.taskId,
    ...Object.fromEntries(searchParams.entries()),
  } as ListByApiV1TasksTaskIdSamplesGetParams;

  if (!compareAccessToken && !shouldSkipTaskListPreload(pathname)) {
    // task page
    if (params.taskId && !params.sampleId && !queryParams.size) {
      queryParams.size = 10;
    }

    const sampleQueryKey = sampleKey.list(queryParams);

    result.samples = await queryClient.fetchQuery({
      queryKey: sampleQueryKey,
      queryFn: () => getSamples(queryParams),
    });
    logNavigationStage('load.samples.complete', {
      sample_count: result.samples?.data?.length ?? 0,
      sample_total: result.samples?.meta_data?.total ?? result.samples?.data?.length ?? 0,
    });

    if (searchParams.get('isNew') !== 'true' && !searchParams.has('state')) {
      const preAnnotationQueryParams = { ...queryParams };
      delete preAnnotationQueryParams.sort;
      const preAnnotationQueryKey = preAnnotationKey.list(preAnnotationQueryParams);

      result.preAnnotations = await queryClient.fetchQuery({
        queryKey: preAnnotationQueryKey,
        queryFn: () => getPreAnnotationFiles(preAnnotationQueryParams),
      });
      const preannotationFileCount = (result.preAnnotations as any)?.data?.length ?? result.preAnnotations?.length ?? 0;
      logNavigationStage('load.preannotations.complete', {
        preannotation_file_count: preannotationFileCount,
      });
    }
  } else {
    logNavigationStage('load.samples.skipped', {
      reason: compareAccessToken ? 'compare_access' : 'sample_route_or_state_filter',
    });
  }

  const taskDetail = await queryClient.fetchQuery({
    queryKey: taskKey.detail(compareAccessToken ? `${params.taskId}-compare-${params.sampleId}` : params.taskId!),
    queryFn: () =>
      getTask(+params.taskId!, {
        compareAccessToken,
        sampleId: compareSampleId,
      }),
  });

  if (taskDetail?.data) {
    result.task = {
      ...taskDetail.data,
      config: taskDetail.data.config ? JSON.parse(taskDetail.data.config) : null,
    };
  }
  logNavigationStage('load.task.complete', {
    task_name: result.task?.name ?? null,
    has_config: Boolean(result.task?.config),
  });

  const navigationFinishedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const preannotationFileCount = (result.preAnnotations as any)?.data?.length ?? result.preAnnotations?.length ?? 0;
  // eslint-disable-next-line no-console
  console.info(
    `[LabelU][task-navigation] load.complete ${JSON.stringify({
      task_id: Number(params.taskId),
      pathname,
      duration_ms: Math.round(navigationFinishedAt - navigationStartedAt),
      sample_count: result.samples?.data?.length ?? 0,
      preannotation_file_count: preannotationFileCount,
    })}`,
  );

  return result;
}
