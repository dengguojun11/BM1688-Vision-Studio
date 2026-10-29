import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';

import request from '@/api/request';
import IngestionPage, { ServerPagedSelect } from './ingestion';

vi.mock('@/api/request', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}));

vi.mock('../project', () => ({
  usePlatformProject: () => ({ projectId: undefined, projectsQuery: { isLoading: false, data: [] } }),
}));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedPut = vi.mocked(request.put);

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPut.mockReset();
  localStorage.clear();
});

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({ matches: false, media: query, onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
}

function renderPage(getImplementation?: (url: string) => Promise<unknown>) {
  localStorage.setItem('token', 'test-token');
  if (getImplementation) mockedGet.mockImplementation(getImplementation as never);
  else mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);
  mockedPost.mockResolvedValue({ data: { data: { id: 42 } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><IngestionPage /></QueryClientProvider>);
}

describe('IngestionPage', () => {
  it('creates an account-scoped job with an idempotency key', async () => {
    renderPage();
    fireEvent.click(screen.getByText('新建接入作业'));
    fireEvent.change(screen.getByLabelText('筛选条件（JSON，可选）'), { target: { value: '{"prefix":"2026/09/"}' } });
    fireEvent.click(screen.getByRole('button', { name: '创建作业' }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith(
      '/v1/ingestion-jobs',
      expect.objectContaining({ source_type: 'UPLOAD', source_connection_id: undefined, selection: { prefix: '2026/09/' } }),
      { headers: { 'Idempotency-Key': expect.any(String) } },
    ));
    const payload = mockedPost.mock.calls.find(([url]) => url === '/v1/ingestion-jobs')?.[1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('project_id');
  });

  it('loads source connections visible to the current account', async () => {
    renderPage(async (url: string) => {
      if (url === '/v1/datasources') {
        return {
          data: {
            data: [{ id: 11, name: '城市 S3', type: 'S3', bucket: 'bucket-a' }],
            meta_data: { total: 1 },
          },
        } as never;
      }
      return { data: { data: [], meta_data: { total: 0 } } } as never;
    });

    fireEvent.click(screen.getByText('新建接入作业'));
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith(
      '/v1/datasources',
      { params: { page: 0, size: 20 } },
    ));
    const connection = screen.getByLabelText('数据源连接');
    expect(connection).toBeTruthy();
    await waitFor(() => expect(connection.closest('.ant-select')?.className || '').not.toContain('ant-select-disabled'));
  });

  it('bridges a completed legacy upload session into the created job', async () => {
    renderPage();
    fireEvent.click(screen.getByText('新建接入作业'));
    fireEvent.change(screen.getByLabelText('已完成上传会话 ID（可选）'), { target: { value: 'session-1' } });
    fireEvent.click(screen.getByRole('button', { name: '创建作业' }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith(
      '/v1/ingestion-jobs/42/assets/from-upload-session',
      { upload_session_id: 'session-1' },
    ));
  });

  it('uploads chunks and registers the resulting asset', async () => {
    renderPage(async (url: string) => {
      if (url === '/v1/ingestion-jobs') return { data: { data: [{ id: 42, name: '接入作业 42', status: 'QUEUED' }], meta_data: { total: 1 } } } as never;
      if (url === '/v1/tasks') return { data: { data: [{ id: 7, name: '任务 7', media_type: 'IMAGE', status: 'DRAFT' }], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });
    mockedPost
      .mockResolvedValueOnce({ data: { data: { session_id: 'session-1', total_chunks: 1, chunk_size: 8 * 1024 * 1024 } } } as never)
      .mockResolvedValueOnce({ data: { data: { status: 'COMPLETED' } } } as never)
      .mockResolvedValueOnce({ data: { data: { id: 99 } } } as never);
    mockedPut.mockResolvedValue({ data: { data: { received_chunks: [0] } } } as never);
    fireEvent.click(screen.getByText('上传媒体文件'));
    await waitFor(() => expect(screen.getByLabelText('接入作业')).toBeTruthy());
      await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/ingestion-jobs', expect.objectContaining({ params: expect.not.objectContaining({ project_id: expect.anything() }) })));
    const fileInput = document.querySelector('input[type="file"]');
    expect(fileInput).toBeTruthy();
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [new File(['abc'], 'frame.jpg', { type: 'image/jpeg' })] } });
    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));
    await waitFor(() => expect(mockedPut).toHaveBeenCalledWith('/v1/data-governance/upload-sessions/session-1/chunks/0', expect.any(FormData)));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/ingestion-jobs/42/assets/from-upload-session', { upload_session_id: 'session-1' }));
  });

  it('loads account-scoped media assets and renders scan state', async () => {
    renderPage(async (url: string) => {
      if (url === '/v1/ingestion-jobs') return { data: { data: [], meta_data: { total: 0 } } } as never;
      if (url === '/v1/media-assets') return { data: { data: [{ id: 9, media_type: 'IMAGE', scan_status: 'PASSED', quality_status: 'PENDING', lifecycle_status: 'ACTIVE', sha256: 'a'.repeat(64), metadata: { filename: 'frame.jpg' } }], meta_data: { total: 1 } } } as never;
      return { data: { data: [] } } as never;
    });

    await waitFor(() => expect(screen.getByText('frame.jpg')).toBeTruthy());
    expect(screen.getByText('PASSED')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith('/v1/media-assets', expect.objectContaining({ params: expect.objectContaining({ page: 0 }) }));
    expect(mockedGet).toHaveBeenCalledWith('/v1/media-assets', expect.objectContaining({ params: expect.not.objectContaining({ project_id: expect.anything() }) }));
  });

  it('searches media assets on the server and renders a no-result state', async () => {
    renderPage(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/v1/ingestion-jobs') return { data: { data: [], meta_data: { total: 0 } } } as never;
      if (url === '/v1/media-assets') {
        return config?.params?.q ? { data: { data: [], meta_data: { total: 0 } } } as never : { data: { data: [], meta_data: { total: 0 } } } as never;
      }
      return { data: { data: [] } } as never;
    });

    const input = await screen.findByRole('textbox', { name: '搜索媒体资产' });
    fireEvent.change(input, { target: { value: 'missing-frame' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith(
      '/v1/media-assets',
      expect.objectContaining({ params: expect.objectContaining({ q: 'missing-frame' }) }),
    ));
    expect(await screen.findByText('没有匹配的媒体资产')).toBeTruthy();
  });

  it('uses server search and pagination for large picker datasets', async () => {
    mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url !== '/v1/tasks') return { data: { data: [], meta_data: { total: 0 } } } as never;
      const page = Number(config?.params?.page || 0);
      const query = String(config?.params?.q || '');
      return {
        data: {
          data: [{ id: page + 1, name: query ? `搜索任务 ${query}` : `第 ${page + 1} 页任务`, status: 'DRAFT' }],
          meta_data: { total: query ? 1 : 21, page, size: 20 },
        },
      } as never;
    });
    const Harness = () => {
      const [value, setValue] = useState<number>();
      return (
        <ServerPagedSelect
          endpoint="/v1/tasks"
          queryKey="picker-test-tasks"
          ariaLabel="目标任务"
          value={value}
          onChange={setValue}
          labelForRow={(row) => String(row.name || row.id)}
        />
      );
    };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><Harness /></QueryClientProvider>);

    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      params: { page: 0, size: 20 },
    }));
    const searchInput = screen.getByRole('textbox', { name: '搜索目标任务' });
    fireEvent.change(searchInput, { target: { value: 'road' } });
    fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      params: { page: 0, q: 'road', size: 20 },
    }));

    fireEvent.change(searchInput, { target: { value: '' } });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      params: { page: 0, size: 20 },
    }));
    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/tasks', {
      params: { page: 1, size: 20 },
    }));
  });
});
