import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import EdgeValidationModal, { deviceReady, sampleHasRectangleAnnotation } from './edgeValidationModal';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname}{location.search}</span>;
}

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  vi.restoreAllMocks();
});

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

describe('edge validation modal', () => {
  it('does not dispatch to a busy device when availability is stale', () => {
    const device = {
      id: 'edge-1',
      validation_channel: 'ssh' as const,
      enabled: true,
      is_busy: true,
      available_for_dispatch: true,
      last_probe_status: 'ok',
      environment_status: 'ready',
      environment_report: { environment_ready: true },
    };
    expect(deviceReady(device)).toBe(false);
    expect(deviceReady({ ...device, is_busy: false })).toBe(true);
  });

  it('does not dispatch to a device while Agent bootstrap is active', () => {
    const device = {
      id: 'edge-bootstrap',
      validation_channel: 'ssh' as const,
      enabled: true,
      is_busy: false,
      available_for_dispatch: true,
      agent_bootstrap_status: 'WAITING_HEARTBEAT',
      last_probe_status: 'ok',
      environment_status: 'ready',
      environment_report: { environment_ready: true },
    };
    expect(deviceReady(device)).toBe(false);
  });

  it('does not dispatch to a Board Agent after bootstrap failure', () => {
    const device = {
      id: 'board-agent-failed',
      validation_channel: 'board_agent' as const,
      enabled: true,
      is_busy: false,
      available_for_dispatch: true,
      agent_bootstrap_status: 'FAILED',
      board_agent_status: 'online',
      board_agent_environment_ready: true,
      board_agent_last_heartbeat_at: '2026-10-25T11:59:00.000Z',
    };
    expect(deviceReady(device)).toBe(false);
  });

  it('detects stored rectangle annotations', () => {
    expect(sampleHasRectangleAnnotation({ id: 1, annotated_count: 1 })).toBe(true);
    expect(
      sampleHasRectangleAnnotation({
        id: 2,
        data: { result: JSON.stringify({ rectTool: { result: [{ id: 'box-1' }] } }) },
      }),
    ).toBe(true);
    expect(sampleHasRectangleAnnotation({ id: 3, data: { result: '{bad json' } })).toBe(false);
  });

  it('creates and starts a check using the default labeled samples', async () => {
    const completedJob = {
      id: 91,
      status: 'COMPLETED',
      mode: 'run_and_eval',
      execution_profile: 'quick',
      image_count: 1,
      labeled_image_count: 1,
      unlabeled_image_count: 0,
      edge_device_name: '本地边端一号',
      report_summary: '边端检查完成',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/1') {
        return {
          data: { id: 1, task_id: 2, task_name: '车辆检测', yolo_train_job_id: 3, status: 'COMPLETED' },
        } as never;
      }
      if (path === '/v1/edge-devices') {
        return {
          data: {
            devices: [
              {
                id: 'edge-1',
                name: '本地边端一号',
                enabled: true,
                validation_channel: 'ssh',
                last_probe_status: 'ok',
                environment_status: 'ready',
                environment_report: { environment_ready: true },
                available_for_dispatch: true,
              },
            ],
          },
        } as never;
      }
      if (path === '/v1/tasks/2/samples') {
        return {
          data: [
            {
              id: 10,
              inner_id: 1,
              state: 'DONE',
              annotated_count: 1,
              file: { filename: 'labeled.jpg' },
            },
            {
              id: 11,
              inner_id: 2,
              state: 'NEW',
              annotated_count: 0,
              file: { filename: 'unlabeled.jpg' },
            },
          ],
        } as never;
      }
      if (path === '/v1/yolo-edge-validation/jobs') return { data: { data: [completedJob] } } as never;
      if (path === '/v1/yolo-edge-validation/jobs/91') return { data: completedJob } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    mockedPost.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-edge-validation/jobs') {
        return { data: { ...completedJob, status: 'PENDING', edge_device_name: null } } as never;
      }
      if (path === '/v1/yolo-edge-validation/jobs/91/run') return { data: completedJob } as never;
      throw new Error(`unexpected POST ${path}`);
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeValidationModal open exportId={1} modelName="车辆检测 · v1" onClose={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('labeled.jpg')).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/已选 1 张，其中 1 张有人工拉框/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '创建并启动' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/yolo-edge-validation/jobs', {
        export_id: 1,
        task_id: 2,
        mode: 'run_and_eval',
        execution_profile: 'certification',
        sample_ids: [10],
        attachment_ids: [],
        external_attachments: [],
        thresholds: {},
        threshold_source: 'system_default',
        ai_thresholds: null,
      }),
    );
    expect(mockedPost).toHaveBeenCalledWith('/v1/yolo-edge-validation/jobs/91/run');
    expect(await screen.findByText('检查任务 #91：已完成')).toBeTruthy();
    expect(screen.getByText('本地边端一号')).toBeTruthy();
  });

  it('searches and paginates the device directory without changing global readiness', async () => {
    const device = {
      id: 'edge-board-01',
      name: '训练边端一号',
      enabled: true,
      validation_channel: 'board_agent',
      board_agent_status: 'online',
      board_agent_environment_ready: true,
      available_for_dispatch: true,
      is_busy: false,
    };
    mockedGet.mockImplementation(async (path: string, config?: { params?: Record<string, unknown> }) => {
      if (path === '/v1/yolo-bm1688-exports/1') {
        return {
          data: { id: 1, task_id: 2, task_name: '车辆检测', yolo_train_job_id: 3, status: 'COMPLETED' },
        } as never;
      }
      if (path === '/v1/edge-devices') {
        const params = config?.params || {};
        const searched = params.q === 'board';
        return {
          data: {
            devices: searched ? [device] : [device],
            total: searched ? 1 : 25,
            page: params.page,
            size: params.size,
          },
        } as never;
      }
      if (path === '/v1/tasks/2/samples') {
        return { data: [{ id: 10, annotated_count: 1, file: { filename: 'sample.jpg' } }] } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeValidationModal open embedded exportId={1} modelName="车辆检测 · v1" onClose={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const search = await screen.findByRole('textbox', { name: '搜索边端设备' });
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/edge-devices', {
        params: { page: 0, size: 20, q: undefined, status: 'all' },
      }),
    );
    expect(screen.getByText('共 25 台')).toBeTruthy();

    const nextPage = document.querySelector('.ant-pagination-next:not(.ant-pagination-disabled)');
    expect(nextPage).toBeTruthy();
    fireEvent.click(nextPage!);
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/edge-devices', {
        params: { page: 1, size: 20, q: undefined, status: 'all' },
      }),
    );

    fireEvent.change(search, { target: { value: '  board  ' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/edge-devices', {
        params: { page: 0, size: 20, q: 'board', status: 'all' },
      }),
    );
    await waitFor(() => expect(screen.getByText('搜索结果 1 台')).toBeTruthy());
    const createButton = screen.getByRole('button', { name: '创建并启动' }) as HTMLButtonElement;
    expect(createButton.disabled).toBe(false);
  });

  it('shows completed precision evidence even when the overall edge check failed', async () => {
    const failedJob = {
      id: 2,
      status: 'FAILED',
      mode: 'run_and_eval',
      execution_profile: 'quick',
      image_count: 10,
      labeled_image_count: 10,
      unlabeled_image_count: 0,
      edge_device_name: 'BM1688 边端一号',
      created_at: '2026-10-08T10:00:00',
      started_at: '2026-10-08T10:00:01',
      completed_at: '2026-10-08T10:01:23',
      sample_ids: [10],
      thresholds: { conf_thres: 0.35, nms_thres: 0.7, match_iou_thres: 0.5 },
      error_message: '曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）',
      progress: {
        stage: 'failed',
        message: '结果图生成失败',
        history: [
          { stage: 'ssh_connect', message: '边端设备连接成功', at: '2026-10-08T10:00:02' },
          { stage: 'evaluation', message: '三种模型精度计算完成', at: '2026-10-08T10:01:20' },
          { stage: 'failed', message: '结果图生成失败', at: '2026-10-08T10:01:23' },
        ],
      },
      result: {
        warnings: [
          {
            code: 'PLOT_DEPENDENCY_MISSING',
            message: '曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）',
          },
        ],
        execution: {
          warnings: [
            {
              code: 'PLOT_DEPENDENCY_MISSING',
              message: '曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）',
            },
          ],
        },
        execution_summary: {
          remote_exit_code: 1,
          model_cache_hit: true,
          files_pulled: 6,
          stage_durations_seconds: { total: 82.875 },
        },
        prediction_summary: { with_boxes: 9, total_boxes: 24 },
        validation_dimensions: {
          environment: { status: 'pass' },
          inference: { status: 'pass' },
          precision_evaluation: { status: 'computed' },
          class_coverage: {
            status: 'partial',
            covered_class_count: 3,
            total_class_count: 6,
            covered_classes: ['安全帽', '人员', '车辆'],
            missing_classes: ['反光衣', '烟火', '占道'],
          },
        },
        eval_models: {
          fp32: {
            status: 'ok',
            map50_95: 0.6133,
            map50: 0.7568,
            precision: 0.8421,
            recall: 0.7273,
            f1: 0.7805,
            images: 10,
          },
          fp16: {
            status: 'ok',
            map50_95: 0.6133,
            map50: 0.7568,
            precision: 0.8421,
            recall: 0.7273,
            f1: 0.7805,
            images: 10,
          },
          int8: {
            status: 'ok',
            map50_95: 0.5955,
            map50: 0.7539,
            precision: 0.7619,
            recall: 0.7273,
            f1: 0.7442,
            images: 10,
          },
        },
      },
    };

    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/1') {
        return {
          data: { id: 1, task_id: 2, task_name: '安全检测', yolo_train_job_id: 3, status: 'COMPLETED' },
        } as never;
      }
      if (path === '/v1/tasks/2/samples') {
        return { data: [{ id: 10, annotated_count: 2, file: { filename: 'check-10.jpg' } }] } as never;
      }
      if (path === '/v1/yolo-edge-validation/jobs') return { data: { data: [failedJob] } } as never;
      if (path === '/v1/yolo-edge-validation/jobs/2') return { data: failedJob } as never;
      throw new Error(`unexpected GET ${path}`);
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeValidationModal open exportId={1} modelName="安全检测 · v1" initialView="result" onClose={vi.fn()} />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('检查任务 #2：检查完成（质量门禁未通过）')).toBeTruthy();
    expect(screen.getByText(/任务 #2 · 检查完成（质量门禁未通过）/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '查看检查过程' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '查看检查结果' })).toBeTruthy();
    expect(screen.getAllByText('曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）')).toHaveLength(1);
    expect(screen.getByText('check-10.jpg')).toBeTruthy();
    expect(screen.getByText('三种模型精度计算完成')).toBeTruthy();
    expect(screen.getByText('标签覆盖 3/6')).toBeTruthy();
    expect(screen.getAllByText('FP32').length).toBeGreaterThan(0);
    expect(screen.getAllByText('FP16').length).toBeGreaterThan(0);
    expect(screen.getAllByText('INT8').length).toBeGreaterThan(0);
    expect(screen.getAllByText('61.33%').length).toBeGreaterThan(0);
    expect(screen.getByText('本次为快速检查，未开启性能数据采集。')).toBeTruthy();
    expect(screen.getAllByText('快速检查未采集').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '查看边端任务队列' }));
    expect(screen.getByTestId('location-probe').textContent).toBe('/edge-task-center?export_id=1&task_id=2');
  });

  it('switches to results inside the same embedded detail surface', async () => {
    const job = {
      id: 7,
      status: 'COMPLETED',
      mode: 'run_and_eval',
      execution_profile: 'certification',
      image_count: 1,
      labeled_image_count: 1,
      unlabeled_image_count: 0,
      edge_device_name: '本地边端一号',
      result: { eval_models: {} },
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/1') {
        return {
          data: { id: 1, task_id: 2, task_name: '车辆检测', yolo_train_job_id: 3, status: 'COMPLETED' },
        } as never;
      }
      if (path === '/v1/edge-devices') return { data: { devices: [] } } as never;
      if (path === '/v1/tasks/2/samples')
        return { data: [{ id: 10, annotated_count: 1, file: { filename: 'sample.jpg' } }] } as never;
      if (path === '/v1/yolo-edge-validation/jobs') return { data: { data: [job] } } as never;
      if (path === '/v1/yolo-edge-validation/jobs/7') return { data: job } as never;
      throw new Error(`unexpected GET ${path}`);
    });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <EdgeValidationModal open embedded exportId={1} modelName="车辆检测 · v1" onClose={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('新建边端检查')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '查看检查结果' }));
    expect(await screen.findByText('边端检查结果')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
