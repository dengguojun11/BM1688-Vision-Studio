import type { UserResponse } from '@/api/types';
import { SAMPLE_LOCKING_ENABLED, type TaskSampleUser } from '@/hooks/useSampleWs';

export function getCurrentEditingUser(
  currentSampleConns: TaskSampleUser[] | undefined,
  me: UserResponse | undefined,
  taskId: string | number | undefined,
  sampleId: string | number | undefined,
): TaskSampleUser | undefined {
  const lockedEditor = currentSampleConns?.[0];

  if (lockedEditor || SAMPLE_LOCKING_ENABLED || !me?.id || !taskId || !sampleId) {
    return lockedEditor;
  }

  return {
    user_id: me.id,
    username: me.username ?? '',
    task_id: Number(taskId),
    sample_id: Number(sampleId),
  };
}
