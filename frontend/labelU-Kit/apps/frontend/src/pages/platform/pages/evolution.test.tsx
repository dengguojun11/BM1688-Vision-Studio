import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { message } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import EvolutionPage, { humanizeImportFailureReason, summarizeImportGroups } from './evolution';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

let isTaskSuperuser = true;
vi.mock('@/hooks/useTaskSuperuser', () => ({ default: () => ({ isTaskSuperuser }) }));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedDelete = vi.mocked(request.delete);

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({ matches: false, media: query, onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
}

beforeEach(() => {
  isTaskSuperuser = true;
});

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedDelete.mockReset();
  vi.restoreAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><EvolutionPage /></QueryClientProvider>);
}

describe('EvolutionPage terminal reviews', () => {
  it('counts each expert-feedback aggregation group once', () => {
    const summary = summarizeImportGroups([
      { package_id: 'pkg-20', aggregation_key: 'same-group', valid_sample_count: 20, status: 'WAITING_FOR_SAMPLES' },
      { package_id: 'pkg-22', aggregation_key: 'same-group', valid_sample_count: 42, status: 'WAITING_FOR_SAMPLES' },
      { package_id: 'pkg-8', aggregation_key: 'same-group', valid_sample_count: 50, status: 'COMPLETED' },
      { package_id: 'pkg-other', aggregation_key: 'other-group', valid_sample_count: 3, status: 'WAITING_FOR_SAMPLES' },
    ]);

    expect(summary.groups).toHaveLength(2);
    expect(summary.total).toBe(53);
    expect(summary.ready).toBe(1);
  });

  it('opens on the expert feedback import list so received packages are visible', async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [{ package_id: 'pkg-default', dataset_date: '2026-08-03', modelId: '536', valid_sample_count: 3, required_sample_count: 50, status_label: '等待更多图片' }] } } as never;
      return { data: { data: [] } } as never;
    });

    renderPage();

    expect(await screen.findByText('pkg-default')).toBeTruthy();
    expect(screen.getByRole('tab', { name: '专家回流任务' }).getAttribute('aria-selected')).toBe('true');
  });

  it('renders expert feedback imports returned with the public modelId field', async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [{ package_id: 'pkg-1', dataset_date: '2026-08-03', modelId: '536', valid_sample_count: 3, required_sample_count: 50, status_label: '等待更多图片' }] } } as never;
      return { data: { data: [] } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '专家回流任务' }));

    expect(await screen.findByText('pkg-1')).toBeTruthy();
    expect(screen.getByText('536')).toBeTruthy();
    expect(screen.getByText('3 / 50')).toBeTruthy();
  });

  it('shows the persisted failure reason for a failed expert feedback import', async () => {
    const failureReason = 'Connect timeout: HTTPSConnectionPool(host="minio", port=9000)';
    const humanizedReason = '连接第三方存储服务超时，请检查服务地址和网络后重试';
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') {
        return {
          data: {
            data: [{
              package_id: 'pkg-failed',
              dataset_date: '2026-08-03',
              modelId: '536',
              status: 'FAILED',
              status_label: '失败',
              status_detail: `导入失败：${failureReason}`,
              last_error: failureReason,
            }],
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderPage();

    expect(await screen.findByText('pkg-failed')).toBeTruthy();
    expect(screen.getByText(humanizedReason)).toBeTruthy();
    expect(screen.getByTitle(`原因：${humanizedReason}；技术详情：${failureReason}`)).toBeTruthy();
  });

  it('skips a generic failure message when a specific status detail is available', async () => {
    const failureReason = 'S3 元数据读取超时';
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') {
        return {
          data: {
            data: [{
              package_id: 'pkg-specific-detail',
              status: 'FAILED',
              status_label: '失败',
              last_error: '导入失败',
              status_detail: `导入失败：${failureReason}`,
              message: '导入失败',
            }],
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderPage();

    expect(await screen.findByText('pkg-specific-detail')).toBeTruthy();
    expect(screen.getByText('读取第三方数据超时，请检查网络或存储服务后重试')).toBeTruthy();
    expect(screen.getByTitle(`原因：读取第三方数据超时，请检查网络或存储服务后重试；技术详情：导入失败：${failureReason}`)).toBeTruthy();
  });

  it('falls back to the import message when a failed row has no error column', async () => {
    const failureMessage = '第三方清单缺少 dataset_date 字段';
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') {
        return {
          data: {
            data: [{
              package_id: 'pkg-message-failed',
              status: 'ERROR',
              status_label: '导入失败',
              success: false,
              message: failureMessage,
            }],
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderPage();

    expect(await screen.findByText('pkg-message-failed')).toBeTruthy();
    expect(screen.getByText('第三方数据清单缺少必填信息，请补齐后重新提交')).toBeTruthy();
    expect(screen.getByTitle(`原因：第三方数据清单缺少必填信息，请补齐后重新提交；技术详情：${failureMessage}`)).toBeTruthy();
  });

  it('humanizes common expert feedback import failures while preserving the raw error', () => {
    const cases = {
      '403 Forbidden: Access Denied': '第三方存储访问被拒绝，请检查访问凭据和权限',
      'NoSuchKey: metadata.json does not exist': '找不到第三方数据文件，请确认对象路径和文件是否已上传',
      'metadata field dataset_date is required': '第三方数据清单缺少必填信息，请补齐后重新提交',
      'invalid JSON in metadata.json': '第三方数据清单格式不正确，请检查 metadata 文件',
    };
    Object.entries(cases).forEach(([raw, summary]) => {
      expect(humanizeImportFailureReason(raw)).toBe(summary);
    });
  });

  it('lets platform managers re-execute a failed import and refreshes the list', async () => {
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);
    const failedRow = {
      package_id: 'pkg-failed-retry',
      import_job_id: 42,
      status: 'FAILED',
      status_label: '失败',
      status_detail: '导入失败：metadata.json 读取超时',
      last_error: 'metadata.json 读取超时',
    };
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [failedRow], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockResolvedValue({ data: { data: { package_id: 'pkg-failed-retry-2', import_job_id: 43, status: 'PENDING' } } } as never);

    renderPage();
    expect(await screen.findByText('pkg-failed-retry')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重新执行$/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('原数据包：pkg-failed-retry')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '重新执行' }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/evolution/imports/42/retry', {}));
    await waitFor(() => expect(successNotice).toHaveBeenCalledWith('已提交重新执行，新数据包：pkg-failed-retry-2'));
    expect(mockedGet.mock.calls.filter(([url]) => url === '/v1/evolution/imports').length).toBeGreaterThan(2);
  });

  it('shows a readable conflict when a failed import was already re-executed', async () => {
    const errorNotice = vi.spyOn(message, 'error').mockImplementation(() => ({}) as never);
    const failedRow = {
      package_id: 'pkg-already-retried',
      import_job_id: 44,
      status: 'FAILED',
      status_label: '失败',
      last_error: '连接超时',
    };
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [failedRow], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockRejectedValue({ response: { status: 409, data: { msg: '该失败任务已提交重试，请勿重复操作' } } });

    renderPage();
    expect(await screen.findByText('pkg-already-retried')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重新执行$/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '重新执行' }));

    await waitFor(() => expect(errorNotice).toHaveBeenCalledWith('该失败任务已提交重试，请勿重复操作'));
  });

  it('prefers the backend retry message when an existing result is reused', async () => {
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);
    const failedRow = {
      package_id: 'pkg-completed-retry',
      import_job_id: 48,
      status: 'FAILED',
      status_label: '失败',
      last_error: '连接超时',
    };
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [failedRow], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockResolvedValue({
      data: {
        data: {
          package_id: 'pkg-completed-retry-1',
          import_job_id: 49,
          status: 'COMPLETED',
          message: '已有成功的重试结果，无需重复执行',
        },
      },
    } as never);

    renderPage();
    expect(await screen.findByText('pkg-completed-retry')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重新执行$/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '重新执行' }));

    await waitFor(() => expect(successNotice).toHaveBeenCalledWith('已有成功的重试结果，无需重复执行（数据包：pkg-completed-retry-1）'));
  });

  it('lets platform managers archive a failed import and displays the backend message', async () => {
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);
    const failedRow = {
      package_id: 'pkg-failed-archive',
      import_job_id: 46,
      status: 'FAILED',
      status_label: '失败',
      last_error: 'metadata.json 读取超时',
    };
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [failedRow], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedDelete.mockResolvedValue({ data: { data: { archived: true, message: '失败任务已归档，原始失败原因仍保留在历史记录中' } } } as never);

    renderPage();
    expect(await screen.findByText('pkg-failed-archive')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /删除失败记录$/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('如需再次创建，请使用新的数据包编号，不能复用原来的 package_id。')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '删除失败记录' }));

    await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith('/v1/evolution/imports/46'));
    await waitFor(() => expect(successNotice).toHaveBeenCalledWith('失败任务已归档，原始失败原因仍保留在历史记录中'));
    expect(mockedGet.mock.calls.filter(([url]) => url === '/v1/evolution/imports').length).toBeGreaterThan(2);
  });

  it('shows the backend message when archiving a failed import is rejected', async () => {
    const errorNotice = vi.spyOn(message, 'error').mockImplementation(() => ({}) as never);
    const failedRow = {
      package_id: 'pkg-archive-conflict',
      import_job_id: 47,
      status: 'FAILED',
      status_label: '失败',
      last_error: '连接超时',
    };
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [failedRow], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedDelete.mockRejectedValue({ response: { status: 409, data: { msg: '该失败任务已有重试任务，完成后再归档' } } });

    renderPage();
    expect(await screen.findByText('pkg-archive-conflict')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /删除失败记录$/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '删除失败记录' }));

    await waitFor(() => expect(errorNotice).toHaveBeenCalledWith('该失败任务已有重试任务，完成后再归档'));
  });

  it('does not expose the re-execute action to non-manager accounts', async () => {
    isTaskSuperuser = false;
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') return { data: { data: [{ package_id: 'pkg-readonly', import_job_id: 45, status: 'FAILED', status_label: '失败', last_error: '连接超时' }] } } as never;
      return { data: { data: [] } } as never;
    });

    renderPage();
    expect(await screen.findByText('pkg-readonly')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '重新执行' })).toBeNull();
    expect(screen.queryByRole('button', { name: /删除失败记录$/ })).toBeNull();
  });

  it('searches and paginates expert feedback imports on the server', async () => {
    const importCalls = () => mockedGet.mock.calls.filter(([url]) => url === '/v1/evolution/imports');
    mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/evolution/imports') {
        const params = config?.params || {};
        const searched = params.q === 'pkg-beta';
        return {
          data: {
            data: searched
              ? [{ package_id: 'pkg-beta', modelId: 'model-2', valid_sample_count: 4, required_sample_count: 50, status_label: '已完成' }]
              : [{ package_id: 'pkg-alpha', modelId: 'model-1', valid_sample_count: 2, required_sample_count: 50, status_label: '等待更多图片' }],
            meta_data: { total: searched ? 1 : 21, page: params.page, size: params.size },
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderPage();
    const searchInput = await screen.findByRole('textbox', { name: '搜索专家回流任务' });
    await waitFor(() => expect(importCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.size === 20 && params?.q === undefined;
    })).toBe(true));

    fireEvent.change(searchInput, { target: { value: 'pkg-beta' } });
    fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(importCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.size === 20 && params?.q === 'pkg-beta';
    })).toBe(true));
    expect(await screen.findByText('pkg-beta')).toBeTruthy();

    fireEvent.change(searchInput, { target: { value: '' } });
    await waitFor(() => expect(importCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.size === 20 && params?.q === undefined;
    })).toBe(true));
  });

  it('keeps rejected history read-only and blocks duplicate submission', async () => {
    const row = {
      id: 9,
      device_id: 'local-edge-01',
      hard_example_type: 'LOW_CONFIDENCE',
      confidence: 24,
      fingerprint: 'fp-9',
      asset_uri: 'local/demo-9.jpg',
      review_status: 'REJECTED',
      review_reason: '误报样本',
      training_status: 'COMPLETED',
    };
    mockedGet.mockImplementation(async (url: string) => ({ data: url.includes('/hard-examples/9') ? row : { data: [row] } }) as never);

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '难例审核' }));
    fireEvent.click(await screen.findByRole('button', { name: /查看/ }));

    expect(await screen.findByText('该难例已完成审核，仅可查看历史结果。')).toBeTruthy();
    expect((screen.getByRole('combobox', { name: '审核决策' }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('textbox', { name: '审核理由' }) as HTMLTextAreaElement).disabled).toBe(true);
    const closeButton = screen.getByRole('button', { name: /关/ }) as HTMLButtonElement;
    expect(closeButton.disabled).toBe(false);

    fireEvent.click(closeButton);
    await waitFor(() => expect(mockedPost).not.toHaveBeenCalled());
  });

  it('uses the global evolution scope without sending a project parameter', async () => {
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/edge-feedback/hard-examples') return { data: { data: [] } } as never;
      if (url === '/v1/active-learning/clusters/search') return { data: { data: [] } } as never;
      if (url === '/v1/workflows') return { data: { data: [] } } as never;
      if (url === '/v1/datasets') return { data: { data: [] } } as never;
      return { data: { data: [] } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '回流编排' }));
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/active-learning/clusters/search', expect.objectContaining({ params: expect.objectContaining({ page: 0, size: 20 }) })));

    for (const [, config] of mockedGet.mock.calls) {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      expect(params?.project_id).toBeUndefined();
    }
    expect(screen.getByText('主动学习编排')).toBeTruthy();
    expect(screen.getByRole('button', { name: /重建聚类/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /生成训练批次/ })).toBeTruthy();
  });

  it('searches and paginates feedback clusters on the server', async () => {
    const clusterCalls = () => mockedGet.mock.calls.filter(([url]) => url === '/v1/active-learning/clusters/search');
    mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/v1/active-learning/clusters/search') {
        const query = config?.params || {};
        const searched = query.q === 'edge-42';
        return {
          data: {
            data: searched
              ? [{ id: 42, cluster_key: 'cluster-42', device_id: 'edge-42', status: 'READY' }]
              : [{ id: 1, cluster_key: 'cluster-1', device_id: 'edge-1', status: 'READY' }],
            meta_data: { total: searched ? 1 : 21, page: query.page, size: query.size },
          },
        } as never;
      }
      if (url === '/v1/workflows') return { data: { data: [] } } as never;
      if (url === '/v1/dataset-versions') return { data: { data: [] } } as never;
      return { data: { data: [] } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '回流编排' }));
    await waitFor(() => expect(clusterCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.size === 20 && params?.q === undefined;
    })).toBe(true));

    const searchInput = await screen.findByRole('textbox', { name: '搜索反馈聚类' });
    fireEvent.change(searchInput, { target: { value: 'edge-42' } });
    fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(clusterCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.size === 20 && params?.q === 'edge-42';
    })).toBe(true));
    expect(await screen.findByText('cluster-42')).toBeTruthy();

    fireEvent.change(searchInput, { target: { value: '' } });
    await waitFor(() => expect(clusterCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.q === undefined;
    })).toBe(true));
    fireEvent.click(await screen.findByTitle('2'));
    await waitFor(() => expect(clusterCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 1 && params?.size === 20;
    })).toBe(true));
  });

  it('searches and paginates associated retraining workflows on the server', async () => {
    const workflowCalls = () => mockedGet.mock.calls.filter(([url]) => url === '/v1/workflows');
    mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/v1/active-learning/clusters/search') return { data: { data: [] } } as never;
      if (url === '/v1/datasets') return { data: { data: [] } } as never;
      if (url === '/v1/workflows') {
        const query = config?.params || {};
        const searched = query.q === 'workflow-42';
        return {
          data: {
            data: searched ? [{ id: 42, workflow_type: 'expert_feedback', status: 'RUNNING', current_step: 'train' }] : [{ id: 1, workflow_type: 'expert_feedback', status: 'PENDING', current_step: 'review' }],
            meta_data: { total: searched ? 1 : 21, page: query.page, size: query.size },
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '回流编排' }));

    await waitFor(() => expect(workflowCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0
        && params?.size === 20
        && params?.workflow_type === 'expert_feedback'
        && params?.q === undefined;
    })).toBe(true));
    const searchInput = await screen.findByRole('textbox', { name: '搜索重训工作流' });
    fireEvent.change(searchInput, { target: { value: 'workflow-42' } });
    fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });

    await waitFor(() => expect(workflowCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0
        && params?.size === 20
        && params?.workflow_type === 'expert_feedback'
        && params?.q === 'workflow-42';
    })).toBe(true));
    expect(await screen.findByText('#42')).toBeTruthy();

    fireEvent.change(searchInput, { target: { value: '' } });
    await waitFor(() => expect(workflowCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0
        && params?.size === 20
        && params?.workflow_type === 'expert_feedback'
        && params?.q === undefined;
    })).toBe(true));

    fireEvent.click(await screen.findByTitle('2'));
    await waitFor(() => expect(workflowCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 1
        && params?.size === 20
        && params?.workflow_type === 'expert_feedback'
        && params?.q === undefined;
    })).toBe(true));
  });

  it('searches and paginates active-learning dataset versions on the server', async () => {
    const versionCalls = () => mockedGet.mock.calls.filter(([url]) => url === '/v1/dataset-versions');
    mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/v1/active-learning/clusters/search') return { data: { data: [] } } as never;
      if (url === '/v1/workflows') return { data: { data: [] } } as never;
      if (url === '/v1/dataset-versions') {
        const query = config?.params || {};
        const searched = query.q === '回流数据集';
        return {
          data: {
            data: searched
              ? [{ id: 42, dataset_name: '回流数据集', version_no: 3, status: 'READY_TO_REVIEW', metadata_json: 'active_learning_batch' }]
              : [{ id: 1, dataset_name: '默认数据集', version_no: 1, status: 'FROZEN', metadata_json: 'active_learning_batch' }],
            meta_data: { total: searched ? 1 : 21, page: query.page, size: query.size },
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '回流编排' }));

    await waitFor(() => expect(versionCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0
        && params?.size === 20
        && params?.active_learning_only === true
        && params?.q === undefined;
    })).toBe(true));

    const searchInput = await screen.findByRole('textbox', { name: '搜索数据集版本' });
    fireEvent.change(searchInput, { target: { value: '回流数据集' } });
    fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(versionCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0
        && params?.size === 20
        && params?.active_learning_only === true
        && params?.q === '回流数据集';
    })).toBe(true));
    expect(await screen.findByText('回流数据集')).toBeTruthy();

    fireEvent.change(searchInput, { target: { value: '' } });
    await waitFor(() => expect(versionCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 0 && params?.active_learning_only === true && params?.q === undefined;
    })).toBe(true));

    fireEvent.click(await screen.findByTitle('2'));
    await waitFor(() => expect(versionCalls().some(([, config]) => {
      const params = (config as { params?: Record<string, unknown> } | undefined)?.params;
      return params?.page === 1 && params?.size === 20 && params?.active_learning_only === true;
    })).toBe(true));
  });

  it('keeps dataset version freeze on the existing endpoint and refreshes the list', async () => {
    let versionRequestCount = 0;
    mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/v1/active-learning/clusters/search') return { data: { data: [] } } as never;
      if (url === '/v1/workflows') return { data: { data: [] } } as never;
      if (url === '/v1/dataset-versions') {
        versionRequestCount += 1;
        return {
          data: {
            data: [{ id: 42, dataset_name: '回流数据集', version_no: 3, status: 'READY_TO_REVIEW', metadata_json: 'active_learning_batch' }],
            meta_data: { total: 1, page: config?.params?.page, size: config?.params?.size },
          },
        } as never;
      }
      return { data: { data: [] } } as never;
    });
    mockedPost.mockResolvedValue({ data: { id: 42, status: 'FROZEN' } } as never);

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '回流编排' }));
    const freezeButton = await screen.findByRole('button', { name: /校验并冻结/ });
    fireEvent.click(freezeButton);

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/dataset-versions/42/freeze'));
    await waitFor(() => expect(versionRequestCount).toBeGreaterThan(1));
  });

  it('does not expose global review submission to annotation users', async () => {
    isTaskSuperuser = false;
    const row = {
      id: 10,
      device_id: 'edge-02',
      hard_example_type: 'LOW_CONFIDENCE',
      confidence: 31,
      fingerprint: 'fp-10',
      asset_uri: 'third-party/problem-10.jpg',
      review_status: 'PENDING',
      training_status: 'READY',
    };
    mockedGet.mockImplementation(async (url: string) => ({ data: url.includes('/hard-examples/10') ? row : { data: [row] } }) as never);

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '难例审核' }));
    fireEvent.click(await screen.findByRole('button', { name: /查看/ }));

    expect(await screen.findByText('全局难例由管理人员审核，当前账号仅可查看。')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '提交审核' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '进化链路' })).toBeNull();
    expect((screen.getByRole('combobox', { name: '审核决策' }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /关/ }));
    await waitFor(() => expect(mockedPost).not.toHaveBeenCalled());
  });

  it('locks all pipeline write controls while one operation is pending', async () => {
    let finishRebuild!: (value: unknown) => void;
    mockedGet.mockImplementation(async (url: string) => {
      if (url === '/v1/active-learning/clusters/search') return { data: { data: [{ id: 7, cluster_key: 'cluster-7', status: 'READY' }], meta_data: { total: 1, page: 0, size: 20 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost.mockImplementation(() => new Promise((resolve) => { finishRebuild = resolve; }) as never);

    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '回流编排' }));
    const rebuildButton = await screen.findByRole('button', { name: /重建聚类/ });
    const createButton = await screen.findByRole('button', { name: /生成训练批次/ });

    fireEvent.click(rebuildButton);
    await waitFor(() => expect((rebuildButton as HTMLButtonElement).disabled).toBe(true));
    expect((createButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(rebuildButton);
    expect(mockedPost).toHaveBeenCalledTimes(1);

    finishRebuild({ data: [] });
    await waitFor(() => expect((rebuildButton as HTMLButtonElement).disabled).toBe(false));
  });
});

