import type { SampleState } from '@/api/types';

export function buildTaskStatusSamplesPath(taskId: number, state: SampleState) {
  return `/tasks/${taskId}?state=${state}&page=1&size=10`;
}
