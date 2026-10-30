export type ProjectRoute = { id?: number | string };

/** Build stable cache keys for resources whose records are scoped to a project. */
export function projectQueryKey(resource: string, projectId?: number, ...parts: unknown[]) {
  return [resource, projectId ?? null, ...parts] as const;
}

function normalizedPath(pathname: string) {
  return pathname.replace(/\/+$/, '') || '/';
}

/** Treat a trailing slash as the same route so direct links keep the cockpit isolated. */
export function isEvolutionScreenPath(pathname: string) {
  return normalizedPath(pathname) === '/workspace/evolution-screen';
}

/** The clean default entry for the read-only model evolution data screen. */
export function isWorkspaceDataScreenPath(pathname: string) {
  const normalized = normalizedPath(pathname);
  return normalized === '/workspace' || isEvolutionScreenPath(normalized);
}

/** The operational workbench remains available as a deliberately named route. */
export function isWorkspaceWorkbenchPath(pathname: string) {
  return normalizedPath(pathname) === '/workspace/workbench';
}

/** The root workspace path is the data screen and opens without the platform shell. */
export function isWorkspaceRootPath(pathname: string) {
  return normalizedPath(pathname) === '/workspace';
}

export function reviewReasonRequired(status: string) {
  return status === 'REJECTED' || status === 'INVALIDATED' || status === 'ARBITRATION';
}

export function reviewReasonMissing(status: string, reason?: string | null) {
  return reviewReasonRequired(status) && !String(reason ?? '').trim();
}

const ONLINE_AGENT_STATUSES = new Set(['ONLINE', 'ACTIVE', 'HEALTHY']);

/** Return a whole-number online percentage, or null when no devices exist. */
export function calculateOnlineRate(agents: Array<{ status?: string | null }> = []) {
  if (!agents.length) return null;
  const online = agents.filter((agent) => ONLINE_AGENT_STATUSES.has(String(agent.status ?? '').toUpperCase())).length;
  return Math.round((online / agents.length) * 100);
}

/** Keep wallboard polling bounded to visible pages with work still in progress. */
export function isWallboardPollingAllowed({ visible, hasActiveWork }: { visible: boolean; hasActiveWork: boolean }) {
  return visible && hasActiveWork;
}

export type EvolutionErrorState = 'forbidden' | 'missing' | 'conflict' | 'offline' | 'error';

export function evolutionErrorState(status?: number, online = true): EvolutionErrorState {
  if (!online) return 'offline';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'missing';
  if (status === 409) return 'conflict';
  return 'error';
}

export function evolutionReviewErrorMessage(status?: number, online = true) {
  const state = evolutionErrorState(status, online);
  return state === 'forbidden'
    ? '无权限提交难例审核'
    : state === 'missing'
    ? '难例已不存在，请刷新队列'
    : state === 'conflict'
    ? '难例状态已变化，请刷新后重试'
    : state === 'offline'
    ? '当前网络已断开，审核未提交'
    : '审核提交失败，请稍后重试';
}

export function resolveProjectId(
  param: string | null,
  projects?: ProjectRoute[],
): number | undefined {
  const requested = param ? Number(param) : NaN;
  if (
    Number.isInteger(requested) &&
    requested > 0 &&
    projects?.some((project) => Number(project.id) === requested)
  ) {
    return requested;
  }

  const firstId = projects?.[0]?.id;
  return firstId == null ? undefined : Number(firstId);
}

export type WorkspaceSummaryState = 'loading' | 'ready' | 'forbidden' | 'missing' | 'conflict' | 'offline' | 'empty' | 'error';

export function workspaceSummaryState({
  loading,
  hasData,
  status,
  online = true,
}: {
  loading: boolean;
  hasData: boolean;
  status?: number;
  online?: boolean;
}): WorkspaceSummaryState {
  if (loading) return 'loading';
  if (hasData) return 'ready';
  if (!online) return 'offline';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'missing';
  if (status === 409) return 'conflict';
  if (status === 204) return 'empty';
  return 'error';
}
