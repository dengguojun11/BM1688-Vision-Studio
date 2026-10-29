import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import TrainingAiReportAction, { latestReportsByProvider, recommendedTrainParams } from './TrainingAiReport';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
});

function renderAction(job: Record<string, unknown> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const row = {
    id: 42,
    task_id: 7,
    status: 'COMPLETED',
    ai_eval_full_status: 'COMPLETED',
    ai_eval_next_status: 'COMPLETED',
    ...job,
  };
  return render(
    <QueryClientProvider client={queryClient}>
      <TrainingAiReportAction row={row} />
    </QueryClientProvider>,
  );
}

function mockReports(job: Record<string, unknown>, reports: Array<Record<string, unknown>>) {
  mockedGet.mockImplementation(async (path: string) => {
    if (path === '/v1/tasks/7/yolo-train-jobs/42') return { data: { data: job } } as never;
    if (path === '/v1/tasks/7/yolo-train-jobs/42/evaluate-reports') return { data: { data: reports } } as never;
    throw new Error(`unexpected GET ${path}`);
  });
}

describe('TrainingAiReportAction', () => {
  it('shows full analysis and structured next-iteration parameters from the job detail menu', async () => {
    const job = {
      id: 42,
      task_id: 7,
      status: 'COMPLETED',
      ai_eval_full_status: 'COMPLETED',
      ai_eval_next_status: 'COMPLETED',
    };
    mockReports(job, [
      { id: 1, provider_key: 'volcengine', report_kind: 'full_analysis', body: '训练曲线稳定，best.pt 独立验证有效。' },
      {
        id: 2,
        provider_key: 'volcengine',
        report_kind: 'next_iteration',
        body: '建议降低学习率并复核难例。',
        suggested_params: '{"recommended_train_params":{"epochs":120,"lr0":0.005,"patience":30}}',
      },
    ]);
    mockedPost.mockResolvedValue({ data: { data: { accepted: true } } } as never);
    renderAction({ ai_eval_full_status: undefined, ai_eval_next_status: undefined });

    fireEvent.click(screen.getByRole('button', { name: 'AI报告分析' }));
    expect(await screen.findByText('训练曲线稳定，best.pt 独立验证有效。')).toBeTruthy();
    expect(screen.getByText('火山方舟')).toBeTruthy();
    expect(screen.getAllByText('分析完成')).toHaveLength(2);

    fireEvent.click(screen.getByRole('tab', { name: /下一轮参数建议/ }));
    expect(await screen.findByText('建议降低学习率并复核难例。')).toBeTruthy();
    expect(screen.getByText('lr0')).toBeTruthy();
    expect(screen.getByText('0.005')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新分析' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/tasks/7/yolo-train-jobs/42/evaluate', {
        report_kind: 'next_iteration',
      }),
    );
  });

  it('shows the failure reason and allows a failed report to be analyzed again', async () => {
    const job = {
      id: 42,
      task_id: 7,
      status: 'COMPLETED',
      ai_eval_full_status: 'FAILED',
      ai_eval_next_status: 'PENDING',
    };
    mockReports(job, [
      {
        id: 3,
        provider_key: 'volcengine',
        report_kind: 'full_analysis',
        error_message: 'The read operation timed out',
      },
    ]);
    renderAction(job);

    fireEvent.click(screen.getByRole('button', { name: 'AI报告分析' }));
    expect(await screen.findByText('The read operation timed out')).toBeTruthy();
    expect(screen.getAllByText('分析失败').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '重新分析' })).toBeTruthy();
  });

  it('uses only the latest provider report and safely parses recommended parameters', () => {
    expect(
      latestReportsByProvider(
        [
          { id: 1, provider_key: 'volcengine', report_kind: 'full_analysis', body: '旧报告' },
          { id: 2, provider_key: 'volcengine', report_kind: 'full_analysis', body: '新报告' },
        ] as any,
        'full_analysis',
      ).map((report) => report.id),
    ).toEqual([2]);
    expect(recommendedTrainParams('{"recommended_train_params":{"epochs":80}}')).toEqual({ epochs: 80 });
    expect(recommendedTrainParams('not-json')).toBeNull();
  });
});
