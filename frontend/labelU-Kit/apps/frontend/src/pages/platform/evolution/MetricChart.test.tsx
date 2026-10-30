import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import MetricChart from './MetricChart';

afterEach(cleanup);

describe('MetricChart', () => {
  it('shows a recoverable error state when metric history cannot be read', () => {
    const onRetry = vi.fn();

    render(<MetricChart history={[]} error={new Error('offline')} onRetry={onRetry} />);

    expect(screen.getByRole('alert').textContent).toContain('逐轮指标读取失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('keeps an existing curve visible while a refresh fails', () => {
    render(<MetricChart history={[{ epoch: 1, map50_95: 0.4 }, { epoch: 2, map50_95: 0.5 }]} error={new Error('offline')} />);

    expect(screen.getByRole('img', { name: '模型逐轮精度曲线' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('最新指标同步失败');
  });
});
