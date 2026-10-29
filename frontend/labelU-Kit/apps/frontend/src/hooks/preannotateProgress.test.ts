import { describe, expect, it } from 'vitest';

import type { PreannotateJobResponse } from '@/api/types';
import { formatPreannotateProgressSummary, getYoloPreannotateCounts } from './preannotateProgress';

const job = {
  id: 8,
  task_id: 144,
  status: 'COMPLETED',
  sample_count: 643,
  processed_count: 643,
  success_count: 390,
  failed_count: 0,
  model_kind: 'imported',
  model_id: 1,
} satisfies PreannotateJobResponse;

describe('preannotation progress summary', () => {
  it('derives zero-box images when the API does not return empty_count', () => {
    expect(getYoloPreannotateCounts(job)).toEqual({ annotatedCount: 390, emptyCount: 253 });
  });

  it('shows processed, annotated, empty and failed image counts for YOLO', () => {
    expect(formatPreannotateProgressSummary(job, 'yolo')).toBe(
      '已处理 643/643 张；实际有框 390 张，零框 253 张，失败 0 张',
    );
  });
});
