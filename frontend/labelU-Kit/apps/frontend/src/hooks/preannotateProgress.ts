import type { PreannotateJobResponse } from '@/api/types';

export function getYoloPreannotateCounts(job: PreannotateJobResponse) {
  const annotatedCount = job.annotated_count ?? job.success_count;
  const emptyCount = job.empty_count ?? Math.max(job.processed_count - annotatedCount - job.failed_count, 0);
  return { annotatedCount, emptyCount };
}

export function formatPreannotateProgressSummary(
  job: PreannotateJobResponse,
  type: 'yolo' | 'tagtool' | 'locateanything' | 'vlx_seek' | 'rex_omni' | 'ai_detection',
): string {
  if (type === 'yolo' || (type === 'ai_detection' && (job.annotated_count !== undefined || job.empty_count !== undefined))) {
    const { annotatedCount, emptyCount } = getYoloPreannotateCounts(job);
    return `已处理 ${job.processed_count}/${job.sample_count} 张；实际有框 ${annotatedCount} 张，零框 ${emptyCount} 张，失败 ${job.failed_count} 张`;
  }
  return `已处理 ${job.processed_count}/${job.sample_count} 个；成功 ${job.success_count} 个，失败 ${job.failed_count} 个`;
}
