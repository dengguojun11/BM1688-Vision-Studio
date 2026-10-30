import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import WorkspacePage, { isEvolutionScreenPath, isWorkspaceDataScreenPath } from './workspace';

const mockedUseWallboard = vi.hoisted(() => vi.fn());

vi.mock('../evolution/EvolutionCockpit', () => ({
  default: () => <div>独立进化驾驶舱</div>,
}));

vi.mock('../wallboard', () => ({
  useWallboard: () => mockedUseWallboard(),
  WorkbenchActivity: () => <div>最近运行</div>,
}));

vi.mock('../WorkspaceSummaryPanel', () => ({
  WorkspaceSummaryPanel: () => <div>工作台摘要</div>,
}));

vi.mock('../polling', () => ({ usePageVisible: () => true }));
vi.mock('@/api/request', () => ({ default: { get: vi.fn() } }));
vi.mock('@/hooks/useTaskSuperuser', () => ({
  default: () => ({ isSuccess: true, isTaskSuperuser: false, isLoading: false, isError: false }),
}));

function renderPage(pathname: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[pathname]}>
        <WorkspacePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedUseWallboard.mockReturnValue({
    summaryQuery: { isLoading: false, isFetching: false, data: undefined, error: undefined, refetch: vi.fn() },
    jobs: [], jobsTotal: 0, jobsPage: 0, jobsPageSize: 10, setJobsPage: vi.fn(),
    alerts: [], alertTotal: 0, activitySearchDraft: '', setActivitySearchDraft: vi.fn(),
    applyActivitySearch: vi.fn(), alertsQuery: { isFetching: false, error: undefined },
    jobsQuery: { isFetching: false, error: undefined }, refresh: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkspacePage route separation', () => {
  it('recognizes the root and dedicated data screen paths', () => {
    expect(isEvolutionScreenPath('/workspace/evolution-screen')).toBe(true);
    expect(isEvolutionScreenPath('/workspace/evolution-screen/')).toBe(true);
    expect(isEvolutionScreenPath('/workspace')).toBe(false);
    expect(isWorkspaceDataScreenPath('/workspace')).toBe(true);
    expect(isWorkspaceDataScreenPath('/workspace/workbench')).toBe(false);
  });

  it.each(['/workspace', '/workspace/evolution-screen?fullscreen=1'])('renders the evolution cockpit on %s', (path) => {
    renderPage(path);

    expect(screen.getByText('独立进化驾驶舱')).toBeTruthy();
    expect(mockedUseWallboard).not.toHaveBeenCalled();
  });

  it('keeps the ordinary workbench behind its explicit route', () => {
    renderPage('/workspace/workbench');

    expect(screen.getByText('工作台摘要')).toBeTruthy();
    expect(screen.getByText('最近运行')).toBeTruthy();
    expect(screen.queryByText('独立进化驾驶舱')).toBeNull();
    expect(mockedUseWallboard).toHaveBeenCalledTimes(1);
  });
});
