import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';

import { canViewWorkspaceEdgeQueue, EdgeQueueWorkspaceSummary } from './workspace';

const authState = vi.hoisted(() => ({
  value: {
    isSuccess: false,
    isTaskSuperuser: false,
    isLoading: true,
    isError: false,
  },
}));

vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));
vi.mock('@/hooks/useTaskSuperuser', () => ({ default: () => authState.value }));
vi.mock('../polling', () => ({ usePageVisible: () => true }));
vi.mock('../wallboard', () => ({
  useWallboard: () => ({
    summaryQuery: { isLoading: false, isFetching: false, data: undefined, error: undefined, refetch: vi.fn() },
    jobs: [],
    jobsTotal: 0,
    jobsPage: 0,
    jobsPageSize: 20,
    setJobsPage: vi.fn(),
    alerts: [],
    alertTotal: 0,
    alertsQuery: { isFetching: false, error: undefined },
    jobsQuery: { isFetching: false, error: undefined },
    refresh: vi.fn(),
  }),
  WorkbenchActivity: () => null,
}));
vi.mock('../WorkspaceSummaryPanel', () => ({ WorkspaceSummaryPanel: () => null }));
vi.mock('../evolution/EvolutionCockpit', () => ({ default: () => null }));

const mockedGet = vi.mocked(request.get);

function renderSummary() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EdgeQueueWorkspaceSummary />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  authState.value = {
    isSuccess: false,
    isTaskSuperuser: false,
    isLoading: true,
    isError: false,
  };
  mockedGet.mockReset();
});

afterEach(cleanup);

describe('workspace edge queue permission gate', () => {
  it('requires a resolved manager permission before enabling the queue query', () => {
    expect(canViewWorkspaceEdgeQueue({ isSuccess: false, isTaskSuperuser: true })).toBe(false);
    expect(canViewWorkspaceEdgeQueue({ isSuccess: true, isTaskSuperuser: false })).toBe(false);
    expect(canViewWorkspaceEdgeQueue({ isSuccess: true, isTaskSuperuser: true })).toBe(true);
  });

  it('does not request or render the administrator queue summary for annotators', async () => {
    authState.value = { isSuccess: true, isTaskSuperuser: false, isLoading: false, isError: false };

    renderSummary();

    await waitFor(() => expect(mockedGet).not.toHaveBeenCalled());
    expect(screen.queryByRole('region', { name: '边端执行摘要' })).toBeNull();
    expect(screen.queryByText('当前账号无权查看边端队列')).toBeNull();
  });

  it('requests and renders the queue summary after manager permission succeeds', async () => {
    authState.value = { isSuccess: true, isTaskSuperuser: true, isLoading: false, isError: false };
    mockedGet.mockResolvedValue({
      data: { counts: { queued: 2, total: 2 }, devices: [] },
    } as never);

    renderSummary();

    expect(await screen.findByRole('region', { name: '边端执行摘要' })).toBeTruthy();
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-edge-validation/queue/summary'));
    expect(screen.getAllByText('2 条').length).toBeGreaterThan(0);
  });
});
