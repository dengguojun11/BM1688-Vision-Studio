import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import NotificationCenter from './NotificationCenter';

vi.mock('@/api/request', () => ({
  default: {
    get: vi.fn(),
  },
}));

const mockedGet = vi.mocked(request.get);

function renderCenter() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationCenter />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  localStorage.clear();
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

describe('NotificationCenter', () => {
  it('loads account alerts without a project scope when opened and renders an empty state', async () => {
    localStorage.setItem('token', 'test-token');
    mockedGet.mockResolvedValue({ data: { data: [] } } as never);
    renderCenter();

    fireEvent.click(screen.getByRole('button', { name: '通知中心' }));
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());
    expect(await screen.findByText('暂无新通知')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith('/v1/workspace/alerts', {
      params: { page: 0, size: 10 },
    });
  });

  it('renders the server alert and refreshes on demand', async () => {
    localStorage.setItem('token', 'test-token');
    mockedGet.mockResolvedValue({ data: { data: [{ id: 1, title: '设备离线', message: '边端 A 已离线' }] } } as never);
    renderCenter();

    fireEvent.click(screen.getByRole('button', { name: '通知中心' }));
    expect(await screen.findByText('设备离线')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '刷新通知' }));
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
    expect(mockedGet).toHaveBeenNthCalledWith(1, '/v1/workspace/alerts', {
      params: { page: 0, size: 10 },
    });
  });

  it('submits a trimmed search and resets to the first server page', async () => {
    localStorage.setItem('token', 'test-token');
    mockedGet.mockResolvedValue({
      data: { data: [{ id: 3, title: '训练失败', message: '数据集清单异常' }], meta_data: { total: 1 } },
    } as never);
    renderCenter();

    fireEvent.click(screen.getByRole('button', { name: '通知中心' }));
    const input = await screen.findByRole('textbox', { name: '搜索通知' });
    fireEvent.change(input, { target: { value: '  清单异常  ' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    await waitFor(() =>
      expect(mockedGet).toHaveBeenLastCalledWith('/v1/workspace/alerts', {
        params: { page: 0, size: 10, q: '清单异常' },
      }),
    );
    expect(await screen.findByText('训练失败')).toBeTruthy();
  });

  it('clears the search and restores the unfiltered first page', async () => {
    localStorage.setItem('token', 'test-token');
    mockedGet
      .mockResolvedValueOnce({
        data: { data: [{ id: 4, title: '全部通知', message: '未筛选' }], meta_data: { total: 1 } },
      } as never)
      .mockResolvedValueOnce({
        data: { data: [{ id: 5, title: '筛选通知', message: '清单异常' }], meta_data: { total: 1 } },
      } as never);
    renderCenter();

    fireEvent.click(screen.getByRole('button', { name: '通知中心' }));
    const input = await screen.findByRole('textbox', { name: '搜索通知' });
    fireEvent.change(input, { target: { value: '清单异常' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await waitFor(() =>
      expect(mockedGet).toHaveBeenLastCalledWith('/v1/workspace/alerts', {
        params: { page: 0, size: 10, q: '清单异常' },
      }),
    );

    // Ant Design's clear icon emits the same empty change event; dispatch it
    // directly here so the test remains independent of the icon's DOM shape.
    fireEvent.change(input, { target: { value: '' } });
    await waitFor(() => expect(input.value).toBe(''));
    expect(await screen.findByText('全部通知')).toBeTruthy();
    expect(screen.queryByText('筛选通知')).toBeNull();
    // The initial unfiltered page is cached by React Query, so clearing the
    // query reuses it without issuing a redundant third request.
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('requests the next server page from the notification pager', async () => {
    localStorage.setItem('token', 'test-token');
    mockedGet.mockResolvedValue({
      data: { data: [{ id: 5, title: '历史告警', message: '第 1 页' }], meta_data: { total: 21 } },
    } as never);
    renderCenter();

    fireEvent.click(screen.getByRole('button', { name: '通知中心' }));
    expect(await screen.findByText('历史告警')).toBeTruthy();
    const nextPage = document.querySelector('.ant-pagination-next button');
    expect(nextPage).toBeTruthy();
    fireEvent.click(nextPage!);

    await waitFor(() =>
      expect(mockedGet).toHaveBeenLastCalledWith('/v1/workspace/alerts', {
        params: { page: 1, size: 10 },
      }),
    );
  });
});
