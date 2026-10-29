import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { TrainingVersionComparisonDetail } from './ResourcePage';

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({ matches: false, media: query, onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
}

afterEach(cleanup);

describe('TrainingVersionComparisonDetail', () => {
  it('shows the real parent and authoritative metric deltas', () => {
    render(<TrainingVersionComparisonDetail payload={{
      available: true,
      status: 'mixed',
      previous_job_id: 17,
      current_job_id: 18,
      summary: '主指标 mAP50-95提升，其他指标存在取舍，建议结合精确率与召回率复核。',
      metrics: [
        { key: 'map50_95', label: 'mAP50-95', previous: 0.4, current: 0.45, delta: 0.05 },
        { key: 'recall', label: '召回率', previous: 0.75, current: 0.7, delta: -0.05 },
      ],
    }} />);

    expect(screen.getByText('与上一版本对比')).toBeTruthy();
    expect(screen.getByText(/上一版：训练作业 #17/)).toBeTruthy();
    expect(screen.getByText('部分提升 · 有取舍')).toBeTruthy();
    expect(screen.getByText('+5.00 pts')).toBeTruthy();
    expect(screen.getByText('-5.00 pts')).toBeTruthy();
    expect(screen.getByText('40.00%')).toBeTruthy();
    expect(screen.getByText('45.00%')).toBeTruthy();
  });

  it('does not invent a zero baseline for the first version', () => {
    render(<TrainingVersionComparisonDetail payload={{
      available: false,
      status: 'no_previous_version',
      current_job_id: 1,
      summary: '这是基础权重训练的首个版本，没有上一版可对比。',
      metrics: [],
    }} />);

    expect(screen.getByText('首个版本')).toBeTruthy();
    expect(screen.getByText(/没有上一版可对比/)).toBeTruthy();
    expect(screen.queryByText('0.00%')).toBeNull();
  });
});
