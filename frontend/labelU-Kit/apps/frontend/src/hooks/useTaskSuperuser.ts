import { useEffect } from 'react';

import useMe from '@/hooks/useMe';
import { resolveIsTaskSuperuser } from '@/utils/taskSuperuser';

/** 任务超管判定：优先读 /users/me 的 is_task_superuser，旧版 API 对内置 admin 兜底 */
export default function useTaskSuperuser() {
  const meQuery = useMe();

  useEffect(() => {
    if (meQuery.isSuccess && meQuery.data && meQuery.data.is_task_superuser === undefined) {
      void meQuery.refetch();
    }
  }, [meQuery.isSuccess, meQuery.data, meQuery.refetch]);

  return {
    ...meQuery,
    isTaskSuperuser: resolveIsTaskSuperuser(meQuery.data),
  };
}
