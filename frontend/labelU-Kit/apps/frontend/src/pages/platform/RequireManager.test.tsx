import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import request from '@/api/request';
import RequireManager from './RequireManager';

vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));

const mockedGet = vi.mocked(request.get);

function renderGuard() {
  window.localStorage.setItem('token', 'test-token');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/admin']}>
        <Routes>
          <Route path="/admin" element={<RequireManager><div>管理内容</div></RequireManager>} />
          <Route path="/workspace" element={<div>工作台</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  window.localStorage.removeItem('token');
});

describe('RequireManager', () => {
  it('redirects annotators before rendering management content', async () => {
    mockedGet.mockResolvedValue({ data: { id: 2, username: 'annotator@example.com', is_task_superuser: false } } as never);
    renderGuard();
    await waitFor(() => expect(screen.getByText('工作台')).toBeTruthy());
    expect(screen.queryByText('管理内容')).toBeNull();
  });

  it('renders management content for managers', async () => {
    mockedGet.mockResolvedValue({ data: { id: 1, username: 'manager@example.com', is_task_superuser: true } } as never);
    renderGuard();
    await waitFor(() => expect(screen.getByText('管理内容')).toBeTruthy());
  });

  it('fails closed when the current-user request fails', async () => {
    mockedGet.mockRejectedValue(new Error('network')); 
    renderGuard();
    await waitFor(() => expect(screen.getAllByText('当前账号没有系统管理权限').length).toBeGreaterThan(0));
    expect(screen.queryByText('管理内容')).toBeNull();
  });
});
