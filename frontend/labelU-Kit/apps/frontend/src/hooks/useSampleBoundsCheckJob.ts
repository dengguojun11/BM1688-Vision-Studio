import { useCallback, useEffect, useRef, useState } from 'react';
import { message } from 'antd';

import type { SampleBoundsCheckJobResponse } from '@/api/types';
import {
  createSampleBoundsCheckJob,
  getActiveSampleBoundsCheckJob,
  getSampleBoundsCheckJobStatus,
} from '@/api/services/samples';

interface UseSampleBoundsCheckJobOptions {
  taskId?: number;
  onCompleted?: (job: SampleBoundsCheckJobResponse) => void | Promise<void>;
}

export function useSampleBoundsCheckJob({ taskId, onCompleted }: UseSampleBoundsCheckJobOptions) {
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onCompletedRef = useRef(onCompleted);
  const [progress, setProgress] = useState<SampleBoundsCheckJobResponse | null>(null);
  const [isChecking, setIsChecking] = useState(false);

  useEffect(() => {
    onCompletedRef.current = onCompleted;
  }, [onCompleted]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const pollJob = useCallback(
    (jobId: number) => {
      stopPolling();
      pollTimerRef.current = setInterval(async () => {
        try {
          const statusResp = await getSampleBoundsCheckJobStatus(taskId!, jobId);
          const currentJob = statusResp.data;
          setProgress(currentJob);

          if (currentJob.status === 'COMPLETED' || currentJob.status === 'FAILED') {
            stopPolling();
            setIsChecking(false);
            if (currentJob.status === 'COMPLETED') {
              message.success(
                `完整性检查完成：不完整 ${currentJob.incomplete_count ?? 0} 个，异常 ${currentJob.abnormal_count} 个，失败 ${currentJob.failed_count} 个`,
              );
            } else {
              message.error(currentJob.error_message || '完整性检查失败');
            }
            await onCompletedRef.current?.(currentJob);
            setTimeout(() => setProgress(null), 3000);
          }
        } catch {
          stopPolling();
          setIsChecking(false);
          setProgress(null);
        }
      }, 2500);
    },
    [stopPolling, taskId],
  );

  const start = useCallback(async () => {
    if (!taskId) {
      return;
    }

    setIsChecking(true);
    try {
      const response = await createSampleBoundsCheckJob(taskId);
      const job = response.data;
      setProgress(job);
      message.success('完整性检查已开始');
      pollJob(job.id);
    } catch (error: any) {
      setIsChecking(false);
      const msg = error?.response?.data?.msg;
      message.error(msg || '完整性检查失败');
    }
  }, [pollJob, taskId]);

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  useEffect(() => {
    if (!taskId) {
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const response = await getActiveSampleBoundsCheckJob(taskId);
        if (cancelled || !response.data) {
          return;
        }
        setProgress(response.data);
        setIsChecking(true);
        pollJob(response.data.id);
      } catch {
        // 页面恢复进度失败时不打断主列表。
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pollJob, taskId]);

  return {
    progress,
    isChecking,
    start,
  };
}
