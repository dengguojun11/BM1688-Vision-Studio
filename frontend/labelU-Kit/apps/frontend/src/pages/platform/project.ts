import { useQuery } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';

import request from '@/api/request';
import { resolveProjectId } from './routing';
import { listData } from './shared';

export interface PlatformProjectQueryOptions {
  /** Enable/disable the request while keeping the legacy boolean call shape. */
  enabled?: boolean;
  /** Zero-based server page. */
  page?: number;
  /** Server page size (the API accepts 1..500). */
  size?: number;
  /** Submitted server-side search text. */
  q?: string;
  /** Friendly alias for callers that use the service-layer name. */
  search?: string;
}

export type PlatformProjectQueryArg = boolean | PlatformProjectQueryOptions;

export interface PlatformProjectListRequest {
  page: number;
  size: number;
  q?: string;
}

function asPage(value: number | undefined): number {
  return Number.isInteger(value) && Number(value) >= 0 ? Number(value) : 0;
}

function asSize(value: number | undefined): number {
  if (!Number.isInteger(value)) return 100;
  return Math.min(500, Math.max(1, Number(value)));
}

/** Keep query construction in one place so every project consumer uses q/page/size consistently. */
export function buildPlatformProjectRequest(options: PlatformProjectQueryOptions = {}): PlatformProjectListRequest {
  const search = String(options.q ?? options.search ?? '').trim();
  return {
    page: asPage(options.page),
    size: asSize(options.size),
    ...(search ? { q: search } : {}),
  };
}

/** Fetch one server page while accepting both raw and nested API envelopes in tests/legacy clients. */
export async function fetchPlatformProjects(
  options: PlatformProjectQueryOptions = {},
): Promise<ReturnType<typeof listData>> {
  const params = buildPlatformProjectRequest(options);
  const response = await request.get('/v1/projects', { params });
  return listData(response?.data ?? response);
}

export function usePlatformProject(enabledOrOptions: PlatformProjectQueryArg = true) {
  const location = useLocation();
  const options: PlatformProjectQueryOptions =
    typeof enabledOrOptions === 'boolean' ? { enabled: enabledOrOptions } : enabledOrOptions;
  const params = buildPlatformProjectRequest(options);
  const enabled = options.enabled ?? true;
  const projectsQuery = useQuery({
    queryKey: ['projects', 'accessible', params.page, params.size, params.q ?? ''],
    queryFn: () => fetchPlatformProjects(params),
    enabled: enabled && Boolean(localStorage.getItem('token')),
    staleTime: 60_000,
  });
  const projectParam = new URLSearchParams(location.search).get('project_id');
  const projectId = resolveProjectId(projectParam, projectsQuery.data);

  return {
    location,
    projectId,
    projectsQuery,
    projectSearch: params.q ?? '',
    projectPage: params.page,
    projectPageSize: params.size,
  };
}
