import type { DatasetOption } from '@/api/services/task';

export const TRAINING_ANALYSIS_DATASET_PAGE_SIZE = 20;

export function trainingAnalysisDatasetQuery(page: number, query: string) {
  return {
    page: Math.max(0, Number(page) || 0),
    size: TRAINING_ANALYSIS_DATASET_PAGE_SIZE,
    ...(query.trim() ? { q: query.trim() } : {}),
  };
}

export function mergeTrainingAnalysisDatasetOptions(pageItems: DatasetOption[], selected: DatasetOption | null) {
  const byId = new Map<number, DatasetOption>();
  [...pageItems, selected].forEach((item) => {
    if (item?.id) byId.set(Number(item.id), item);
  });
  return Array.from(byId.values());
}
