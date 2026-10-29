import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import EdgeTaskCenter, {
  buildEdgeJobQueryParams,
  edgeTaskContextFromSearchParams,
  edgeTaskContextLabel,
  canCancelEdgeJob,
  canRetryEdgeJob,
  edgeJobSourceValue,
  edgeJobTiming,
  edgeSourceLabel,
  edgeQueueReasonLabel,
  edgeJobStatusLabel,
  edgeJobDisplayStatus,
  queueEtaBasisLabel,
  edgeQueuePollingInterval,
  formatDuration,
  hasActiveEdgeQueueSummary,
  isActiveEdgeJob,
  normalizeQueueSummary,
  queueDevicePoolSummary,
  queueMutationError,
} from './edgeTaskCenter';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

function LocationProbe() {
  const location = useLocation();
  return (
    <span data-testid="location-probe">
      {location.pathname}
      {location.search}
    </span>
  );
}

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
  vi.useRealTimers();
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  vi.restoreAllMocks();
});

describe('edge task center', () => {
  it('reads valid model/task scope from the URL and ignores invalid ids', () => {
    expect(edgeTaskContextFromSearchParams(new URLSearchParams('export_id=17&task_id=9'))).toEqual({
      exportId: 17,
      taskId: 9,
    });
    expect(edgeTaskContextFromSearchParams(new URLSearchParams('export_id=0&task_id=abc'))).toEqual({});
    expect(edgeTaskContextLabel({ exportId: 17, taskId: 9 })).toBe('边端模型导出 #17 · 所属任务 #9');
  });

  it('builds server-side filter parameters without sending empty filters', () => {
    expect(
      buildEdgeJobQueryParams({
        page: 2,
        size: 50,
        status: 'PROCESSING',
        deviceId: 'edge-a',
        sourceType: 'auto_training',
        keyword: '  car  ',
        activeOnly: true,
        exportId: 17,
        taskId: 9,
      }),
    ).toEqual({
      page: 2,
      size: 50,
      status: 'PROCESSING',
      device_id: 'edge-a',
      source_type: 'auto_training',
      q: 'car',
      active_only: 'true',
      export_id: 17,
      task_id: 9,
    });
    expect(
      buildEdgeJobQueryParams({ page: 0, size: 20, status: 'all', deviceId: 'all', sourceType: 'all', keyword: ' ' }),
    ).toEqual({ page: 0, size: 20 });
    expect(buildEdgeJobQueryParams({ page: 0, size: 20, activeOnly: false })).toEqual({ page: 0, size: 20 });
    expect(buildEdgeJobQueryParams({ page: 0, size: 20, keyword: 'legacy', q: 'canonical' })).toEqual({
      page: 0,
      size: 20,
      q: 'canonical',
    });
  });

  it('labels third-party queue jobs and keeps the source filter value available', () => {
    expect(edgeSourceLabel('third_party')).toBe('第三方任务');
    expect(
      buildEdgeJobQueryParams({
        page: 0,
        size: 20,
        status: 'all',
        deviceId: 'all',
        sourceType: 'third_party',
      }),
    ).toEqual({ page: 0, size: 20, source_type: 'third_party' });
  });

  it('maps pipeline and system origins to the backend filter fields', () => {
    expect(buildEdgeJobQueryParams({ page: 0, size: 20, sourceType: 'auto_quality' })).toEqual({
      page: 0,
      size: 20,
      pipeline_origin: 'AUTO_QUALITY',
    });
    expect(buildEdgeJobQueryParams({ page: 0, size: 20, sourceType: 'manual_diagnostic' })).toEqual({
      page: 0,
      size: 20,
      pipeline_origin: 'MANUAL_DIAGNOSTIC',
    });
    expect(buildEdgeJobQueryParams({ page: 0, size: 20, sourceType: 'evolution' })).toEqual({
      page: 0,
      size: 20,
      source_system: 'strategy_evolution',
    });
    expect(buildEdgeJobQueryParams({ page: 0, size: 20, sourceType: 'strategy_evolution' })).toEqual({
      page: 0,
      size: 20,
      source_system: 'strategy_evolution',
    });
  });

  it('uses the most specific persisted origin for queue labels', () => {
    expect(edgeSourceLabel('AUTO_QUALITY')).toBe('自动质量门禁');
    expect(edgeSourceLabel('MANUAL_DIAGNOSTIC')).toBe('手动诊断');
    expect(edgeSourceLabel('strategy_evolution')).toBe('进化流程');
    expect(
      edgeSourceLabel(
        edgeJobSourceValue({
          source_type: 'auto_training',
          source_system: 'labelu_auto_training',
          pipeline_origin: 'MANUAL_DIAGNOSTIC',
        }),
      ),
    ).toBe('手动诊断');
    expect(
      edgeSourceLabel(edgeJobSourceValue({ source_type: 'third_party', source_system: 'strategy_evolution' })),
    ).toBe('进化流程');
  });

  it('humanizes every scheduler queue reason exposed by the task center', () => {
    const reasons = {
      waiting_for_dispatch: '等待设备调度',
      waiting_for_idle_device: '在线设备均忙，等待空闲设备',
      waiting_for_online_device: '等待在线设备',
      waiting_for_environment_ready: '等待设备环境就绪',
      waiting_for_agent: '等待边端 Agent 上线',
      waiting_for_pack: '等待检查数据包',
      waiting_for_device: '等待可用设备',
      device_busy: '设备忙',
      no_online_device: '没有在线设备',
      environment_not_ready: '环境未就绪',
      agent_bootstrap_in_progress: 'Agent 引导进行中',
      agent_offline: 'Agent 未上线',
      preparing_pack: '检查数据准备中',
      packing: '检查数据打包中',
      pack_preparing: '检查数据准备中',
      pack_not_ready: '检查数据包尚未就绪',
      pack_failed: '检查数据准备失败',
      retry_scheduled: '等待重试调度',
      retrying: '等待重试',
      cancelled: '任务已取消',
      retry_backoff: '等待重试时间',
      execution_fencing_unavailable: '执行保护未就绪',
    };
    Object.entries(reasons).forEach(([reason, label]) => {
      expect(edgeQueueReasonLabel(reason)).toBe(label);
    });
  });

  it('normalizes wrapped queue summary data and humanizes duration/status', () => {
    const summary = normalizeQueueSummary({
      data: {
        counts: { queued: 12, processing: 2 },
        estimated_clear_seconds: 7260,
        devices: [{ id: 'edge-a', name: '设备 A', is_online: true, is_ready: true }],
      },
    });
    expect(summary.counts.queued).toBe(12);
    expect(summary.devices[0].device_id).toBe('edge-a');
    expect(formatDuration(summary.estimated_clear_seconds)).toBe('2 小时 1 分钟');
    expect(edgeJobStatusLabel('PROCESSING')).toBe('执行中');
    expect(edgeJobStatusLabel('BLOCKED')).toBe('已阻塞');
    expect(
      queueEtaBasisLabel({ eta_confidence: 'medium', eta_basis: '按 quick 历史中位数', completed_duration_samples: 4 }),
    ).toContain('历史样本：4 条');
  });

  it('keeps raw detail status and operator display status aligned with queue rows', () => {
    expect(edgeJobDisplayStatus({ status: 'PENDING', queue_reason: 'waiting_for_idle_device' })).toBe('BLOCKED');
    expect(edgeJobDisplayStatus({ status: 'PENDING', queue_reason: 'preparing_pack' })).toBe('PREPARING');
    expect(edgeJobDisplayStatus({ status: 'PROCESSING', queue_reason: null })).toBe('RUNNING');
    expect(
      edgeJobDisplayStatus({ status: 'PENDING', display_status: 'RETRYING', queue_reason: 'retry_scheduled' }),
    ).toBe('RETRYING');
  });

  it('keeps the device-pool denominator at zero when every device is offline', () => {
    expect(
      queueDevicePoolSummary([
        { device_id: 'edge-a', is_online: false, is_ready: false },
        { device_id: 'edge-b', is_online: false, is_ready: false },
      ]),
    ).toEqual({ ready: 0, online: 0, offline: 2 });
  });

  it('exposes queue wait, execution, and remaining estimates consistently', () => {
    expect(
      edgeJobTiming({ status: 'PROCESSING', waiting_seconds: 125, running_seconds: 3660, estimated_seconds: 900 }),
    ).toEqual({ waiting: '2 分钟', running: '1 小时 1 分钟', remaining: '15 分钟' });
    expect(
      edgeJobTiming({ status: 'COMPLETED', waiting_seconds: 10, running_seconds: 20, estimated_seconds: 0 }),
    ).toEqual({ waiting: '10 秒', running: '20 秒', remaining: '已结束' });
  });

  it('falls back to legacy timestamps when duration fields are absent', () => {
    const now = Date.parse('2026-10-25T10:10:00Z');
    expect(
      edgeJobTiming(
        {
          status: 'PROCESSING',
          created_at: '2026-10-25T10:00:00Z',
          started_at: '2026-10-25T10:04:00Z',
          estimated_seconds: null,
        },
        now,
      ),
    ).toEqual({ waiting: '4 分钟', running: '6 分钟', remaining: '暂无估算' });
    expect(
      edgeJobTiming(
        {
          status: 'COMPLETED',
          queued_at: '2026-10-25T10:00:00Z',
          started_at: '2026-10-25T10:04:00Z',
          completed_at: '2026-10-25T10:05:00Z',
          waiting_seconds: null,
          running_seconds: null,
          estimated_seconds: null,
        },
        now,
      ),
    ).toEqual({ waiting: '4 分钟', running: '1 分钟', remaining: '已结束' });
  });

  it('keeps paginated job polling active when another page has active work', () => {
    const terminalPage = { items: [{ id: 901, status: 'COMPLETED' }], total: 901 };
    const activeSummary = { counts: { active: 1, processing: 1 }, devices: [] };
    expect(hasActiveEdgeQueueSummary(activeSummary)).toBe(true);
    expect(edgeQueuePollingInterval(true, activeSummary)).toBe(5_000);
    expect(edgeQueuePollingInterval(true, { counts: { active: 0 }, devices: [] })).toBe(false);
    // The current page contents do not participate in the decision. This is
    // the cross-page case: page 2 is terminal-only while page 1 is running.
    expect(terminalPage.items.every((row) => !isActiveEdgeJob(row))).toBe(true);
    expect(edgeQueuePollingInterval(true, activeSummary)).toBe(5_000);
  });

  it('exposes queue actions only for statuses supported by the backend contract', () => {
    expect(canCancelEdgeJob({ status: 'PENDING' })).toBe(true);
    expect(canCancelEdgeJob({ status: 'QUEUED' })).toBe(true);
    expect(canCancelEdgeJob({ status: 'BLOCKED' })).toBe(true);
    expect(canCancelEdgeJob({ status: 'PREPARING' })).toBe(true);
    expect(canCancelEdgeJob({ status: 'RETRYING' })).toBe(true);
    expect(canCancelEdgeJob({ status: 'PROCESSING' })).toBe(false);
    expect(canRetryEdgeJob({ status: 'FAILED' })).toBe(true);
    expect(canRetryEdgeJob({ status: 'CANCELLED' })).toBe(true);
    expect(canRetryEdgeJob({ status: 'PARTIAL_COMPLETED' })).toBe(true);
    expect(canRetryEdgeJob({ status: 'PROCESSING' })).toBe(false);
  });

  it('turns a 409 mutation response into a refresh-oriented message', () => {
    expect(queueMutationError({ response: { status: 409 } }, 'cancel')).toContain('状态已变化');
    expect(queueMutationError({ response: { status: 409 } }, 'retry')).toContain('队列已刷新');
  });

  it('preserves a backend gate message for a 403 retry response', () => {
    expect(
      queueMutationError(
        { response: { status: 403, data: { msg: '自动训练计划一期暂未开放，历史记录仅供查看' } } },
        'retry',
      ),
    ).toContain('自动训练计划一期暂未开放');
    expect(queueMutationError({ response: { status: 403 } }, 'retry')).toBe('当前账号无权操作该边端任务');
  });

  it('renders paginated queue rows and opens a task detail drawer', async () => {
    const job = {
      id: 42,
      task_id: 7,
      task_name: '车辆检测',
      source_type: 'auto_training',
      status: 'PROCESSING',
      edge_device_id: 'edge-a',
      edge_device_name: '设备 A',
      queue_reason: 'device_busy',
      queue_position: 2,
      image_count: 20,
      progress: {
        stage: 'inference',
        message: 'FP16 预热 20/50',
        percent: 42,
        history: [{ stage: 'queued', message: '等待设备', at: '2026-10-25T10:00:00' }],
      },
      created_at: '2026-10-25T10:00:00',
      updated_at: '2026-10-25T10:04:00',
      waiting_seconds: 125,
      running_seconds: 3660,
      estimated_seconds: 900,
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-edge-validation/queue/summary') {
        return {
          data: {
            counts: { queued: 1, processing: 1, total: 2 },
            estimated_clear_seconds: 3600,
            devices: [
              {
                device_id: 'edge-a',
                name: '设备 A',
                is_online: true,
                is_ready: true,
                is_busy: true,
                current_job_id: 42,
                pending_count: 1,
              },
            ],
          },
        } as never;
      }
      if (path === '/v1/yolo-edge-validation/jobs/42') return { data: job } as never;
      if (path === '/v1/yolo-edge-validation/queue/jobs')
        return { data: { items: [job], total: 1, page: 0, size: 20 } } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeTaskCenter />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('统一 BM1688 边端验证任务中心')).toBeTruthy();
    expect(await screen.findByText('车辆检测')).toBeTruthy();
    expect(screen.getAllByText('设备 A').length).toBeGreaterThan(0);
    expect(screen.getAllByText('等待：2 分钟').length).toBeGreaterThan(0);
    expect(screen.getAllByText('执行：1 小时 1 分钟').length).toBeGreaterThan(0);
    expect(screen.getAllByText('预计剩余：15 分钟').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '查看任务 42 详情' }));
    expect(await screen.findByText('边端任务 #42')).toBeTruthy();
    expect(await screen.findByText('等待时长')).toBeTruthy();
    expect(screen.getByText('执行时长')).toBeTruthy();
    expect(screen.getByText('预计剩余')).toBeTruthy();
    expect(screen.getAllByText('FP16 预热 20/50').length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-edge-validation/queue/jobs', { params: { page: 0, size: 20 } }),
    );
    expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-edge-validation/queue/summary');
  });

  it('scopes list and summary requests to URL context and returns to the global queue', async () => {
    const scopedJob = {
      id: 52,
      export_id: 17,
      task_id: 9,
      task_name: '车辆检测',
      status: 'QUEUED',
      queue_reason: 'waiting_for_device',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-edge-validation/queue/summary') {
        return { data: { counts: { queued: 1, total: 1 }, devices: [] } } as never;
      }
      if (path === '/v1/yolo-edge-validation/queue/jobs') {
        return { data: { items: [scopedJob], total: 1, page: 0, size: 20 } } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/edge-task-center?export_id=17&task_id=9']}>
          <EdgeTaskCenter />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('heading', { name: '模型关联的边端任务' })).toBeTruthy();
    expect(screen.getByText('当前查看范围：边端模型导出 #17 · 所属任务 #9')).toBeTruthy();
    expect(await screen.findByText('车辆检测')).toBeTruthy();
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-edge-validation/queue/summary', {
        params: { export_id: 17, task_id: 9 },
      });
      expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-edge-validation/queue/jobs', {
        params: { page: 0, size: 20, export_id: 17, task_id: 9 },
      });
    });

    fireEvent.click(screen.getByRole('button', { name: /返回全局任务中心/ }));
    await waitFor(() => expect(screen.getByTestId('location-probe').textContent).toBe('/edge-task-center'));
  });

  it('refreshes a terminal-only page while the queue summary reports active work', async () => {
    vi.useFakeTimers();
    let summaryReads = 0;
    let jobReads = 0;
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-edge-validation/queue/summary') {
        summaryReads += 1;
        return { data: { counts: { active: 1, processing: 1, total: 901 }, devices: [] } } as never;
      }
      if (path === '/v1/yolo-edge-validation/queue/jobs') {
        jobReads += 1;
        return {
          data: {
            items: [{ id: 901, task_id: 9, task_name: '已完成历史任务', status: 'COMPLETED' }],
            total: 901,
            page: 1,
            size: 20,
          },
        } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeTaskCenter />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(summaryReads).toBe(1);
    expect(jobReads).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(summaryReads).toBeGreaterThanOrEqual(2);
    expect(jobReads).toBe(2);
  });

  it('requeues a retryable task and refreshes the queue', async () => {
    const failedJob = {
      id: 43,
      task_id: 8,
      task_name: '行人检测',
      source_type: 'manual',
      status: 'FAILED',
      error_message: '设备执行失败',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-edge-validation/queue/summary') {
        return { data: { counts: { failed: 1, total: 1 }, devices: [] } } as never;
      }
      if (path === '/v1/yolo-edge-validation/queue/jobs') {
        return { data: { items: [failedJob], total: 1 } } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });
    mockedPost.mockResolvedValue({ data: { job_id: 43, status: 'PENDING' } } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeTaskCenter />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const retryButton = await screen.findByRole('button', { name: '重试边端任务 43' });
    fireEvent.click(retryButton);
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/yolo-edge-validation/jobs/43/retry', {
        reason: '操作员重新排队',
      }),
    );
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(4));
  });

  it('confirms cancellation for a queued task before posting and refreshing', async () => {
    const queuedJob = {
      id: 44,
      task_id: 9,
      task_name: '车辆分类',
      source_type: 'manual',
      status: 'QUEUED',
      queue_reason: 'waiting_for_device',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-edge-validation/queue/summary') {
        return { data: { counts: { queued: 1, total: 1 }, devices: [] } } as never;
      }
      if (path === '/v1/yolo-edge-validation/queue/jobs') {
        return { data: { items: [queuedJob], total: 1 } } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });
    mockedPost.mockResolvedValue({ data: { job_id: 44, status: 'CANCELLED' } } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeTaskCenter />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '取消边端任务 44' }));
    expect(await screen.findByText('确认取消此边端任务？')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确认取消' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/yolo-edge-validation/jobs/44/cancel', {
        reason: '操作员取消',
      }),
    );
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(4));
  });
});
