import { describe, expect, it } from 'vitest';

import type { EvolutionNode } from '@/api/services/evolution';
import { automationCopy, formatMetric, isPassedStatus, mergeMetricHistory, metricHistoryAvailable, nodeProgress, pollingInterval, statusTone } from './cockpitUtils';

describe('evolution cockpit state helpers', () => {
  it('keeps automatic execution visibly disabled instead of implying activity', () => {
    expect(automationCopy({ status: 'DISABLED', training_enabled: false, plan_writes_enabled: false, auto_iteration_enabled: false }).label).toBe('自动进化未开启');
    expect(automationCopy().description).toContain('不会自动创建训练任务');
  });

  it('normalizes fractional and percentage progress', () => {
    const node = { status: 'PROCESSING', progress: { percent: 0.42 } } as EvolutionNode;
    expect(nodeProgress(node)).toBe(42);
    expect(nodeProgress({ ...node, progress: { percent: 63 } })).toBe(63);
    expect(nodeProgress({ ...node, progress: { current_epoch: 3, target_epochs: 8, percent: null } })).toBe(37.5);
  });

  it('merges incremental epochs without duplicates or stale ordering', () => {
    expect(mergeMetricHistory([{ epoch: 2, map50: 0.6 }, { epoch: 1, map50: 0.5 }], [{ epoch: 2, map50_95: 0.4 }, { epoch: 3, map50: 0.7 }])).toEqual([
      { epoch: 1, map50: 0.5 },
      { epoch: 2, map50: 0.6, map50_95: 0.4 },
      { epoch: 3, map50: 0.7 },
    ]);
  });

  it('uses calm polling while idle and never formats absent metrics as zero', () => {
    expect(pollingInterval('IDLE', 0)).toBe(30_000);
    expect(pollingInterval('RUNNING', 0)).toBe(8_000);
    expect(formatMetric(undefined)).toBe('—');
    expect(statusTone('FAILED')).toBe('danger');
  });

  it('treats the backend PASS gate value as a passed state', () => {
    expect(statusTone('PASS')).toBe('success');
    expect(isPassedStatus('PASS')).toBe(true);
  });

  it('keeps an existing metric chart visible when an incremental poll has no new rows', () => {
    expect(metricHistoryAvailable('live', 4, false)).toBe(true);
    expect(metricHistoryAvailable('live', 0, false)).toBe(false);
    expect(metricHistoryAvailable('snapshot', 0, false)).toBe(true);
  });
});
