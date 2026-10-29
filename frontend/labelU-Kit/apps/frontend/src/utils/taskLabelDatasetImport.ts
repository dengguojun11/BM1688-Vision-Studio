export type TaskLabelDatasetImportStatus = 'PROCESSING' | 'COMPLETED' | 'FAILED';

export interface TaskLabelDatasetImportState {
  taskId: number;
  labelIds: number[];
  jobId?: number;
  status: TaskLabelDatasetImportStatus;
  importedCount?: number;
  totalCount?: number;
  processedCount?: number;
  errorMessage?: string;
  startedAt: number;
  finishedAt?: number;
}

const STORAGE_PREFIX = 'labelu:task-label-dataset-import:';
const TERMINAL_STATE_TTL_MS = 15 * 60 * 1000;

function storageKey(taskId: number) {
  return `${STORAGE_PREFIX}${taskId}`;
}

export function readTaskLabelDatasetImportState(taskId: number): TaskLabelDatasetImportState | null {
  if (typeof window === 'undefined' || taskId <= 0) {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(storageKey(taskId));
    if (!raw) {
      return null;
    }
    const state = JSON.parse(raw) as TaskLabelDatasetImportState;
    if (state.taskId !== taskId || !state.status || !Array.isArray(state.labelIds)) {
      return null;
    }
    if (state.finishedAt && Date.now() - state.finishedAt > TERMINAL_STATE_TTL_MS) {
      window.localStorage.removeItem(storageKey(taskId));
      return null;
    }
    return state;
  } catch {
    return null;
  }
}

export function writeTaskLabelDatasetImportState(state: TaskLabelDatasetImportState) {
  if (typeof window === 'undefined' || state.taskId <= 0) {
    return;
  }

  try {
    window.localStorage.setItem(storageKey(state.taskId), JSON.stringify(state));
  } catch {
    // A full or disabled localStorage must not block task navigation.
  }
}
