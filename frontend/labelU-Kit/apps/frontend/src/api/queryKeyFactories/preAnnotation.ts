import type { ListByApiV1TasksTaskIdSamplesGetParams } from '../types';

// 预标注
export const preAnnotationKey = {
  all: ['preAnnotationKey'] as const,
  lists: () => [...preAnnotationKey.all, 'list'] as const,
  list: (
    filter: ListByApiV1TasksTaskIdSamplesGetParams & {
      sample_name?: string;
      canvas_only?: boolean;
    },
  ) => [...preAnnotationKey.lists(), filter] as const,
  listWithSampleName: (
    filter: ListByApiV1TasksTaskIdSamplesGetParams & {
      sample_name?: string;
      canvas_only?: boolean;
    },
  ) => [...preAnnotationKey.all, 'queryList', filter] as const,
  details: () => [...preAnnotationKey.all, 'details'] as const,
  detail: (id: string | number) => [...preAnnotationKey.details(), id] as const,
};
