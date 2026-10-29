import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { WorkspaceSummaryPanel } from './WorkspaceSummaryPanel';
import { isWallboardPollingAllowed } from './routing';

afterEach(cleanup);

const emptySummary = {
  task_count: 0,
  pending_annotation_task_count: 0,
  model_count: 0,
  published_model_count: 0,
  download_disabled_model_count: 0,
  active_training_count: 0,
  failed_training_count: 0,
  pending_feedback_count: 0,
};

describe('WorkspaceSummaryPanel', () => {
  it('announces loading without reporting a healthy platform', () => {
    render(<WorkspaceSummaryPanel state="loading" onRetry={vi.fn()} />);

    expect(screen.getByRole('status', { name: '正在加载工作台摘要' })).toBeTruthy();
    expect(screen.queryByText('基础服务正常')).toBeNull();
  });

  it.each([
    ['forbidden', '无权限查看工作台摘要'],
    ['missing', '工作台摘要接口不存在'],
    ['conflict', '工作台摘要版本冲突'],
    ['empty', '暂无工作台数据'],
  ] as const)('renders the %s failure and provides a working retry action', (state, message) => {
    const onRetry = vi.fn();
    render(<WorkspaceSummaryPanel state={state} onRetry={onRetry} />);

    expect(screen.getByText(message)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('基础服务正常')).toBeNull();
  });

  it('renders a real all-zero summary as empty counts and a terminal synced state', () => {
    render(<WorkspaceSummaryPanel state="ready" summary={emptySummary} onRetry={vi.fn()} />);

    expect(screen.getByRole('region', { name: '工作台摘要' })).toBeTruthy();
    expect(screen.getAllByText('0')).toHaveLength(2);
    expect(screen.getAllByText('0 / 0')).toHaveLength(3);
    expect(screen.getByText('已同步')).toBeTruthy();
    expect(screen.queryByText('刷新中')).toBeNull();
    expect(isWallboardPollingAllowed({ visible: true, hasActiveWork: emptySummary.active_training_count > 0 })).toBe(false);
  });
});
