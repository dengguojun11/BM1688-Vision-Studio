import { describe, expect, it } from 'vitest';

import {
  calculateOnlineRate,
  evolutionErrorState,
  evolutionReviewErrorMessage,
  isEvolutionScreenPath,
  isWallboardPollingAllowed,
  isWorkspaceDataScreenPath,
  isWorkspaceRootPath,
  isWorkspaceWorkbenchPath,
  projectQueryKey,
  resolveProjectId,
  reviewReasonMissing,
  reviewReasonRequired,
  workspaceSummaryState,
} from './routing';

describe('platform project routing', () => {
  const projects = [{ id: 7, name: '城市治理' }, { id: 9, name: '交通治理' }];

  it('keeps an explicitly selected accessible project', () => {
    expect(resolveProjectId('9', projects)).toBe(9);
  });

  it('falls back instead of querying a project outside the accessible set', () => {
    expect(resolveProjectId('999', projects)).toBe(7);
    expect(resolveProjectId('not-a-number', projects)).toBe(7);
  });

  it('returns undefined when no accessible project exists', () => {
    expect(resolveProjectId('7', [])).toBeUndefined();
  });

  it('isolates project-scoped query caches, including optional filters', () => {
    expect(projectQueryKey('models', 7)).toEqual(['models', 7]);
    expect(projectQueryKey('edge-hard-examples', 9, 'PENDING')).toEqual([
      'edge-hard-examples',
      9,
      'PENDING',
    ]);
    expect(projectQueryKey('models', undefined)).toEqual(['models', null]);
  });

  it('requires a reason for destructive review transitions', () => {
    expect(reviewReasonRequired('REJECTED')).toBe(true);
    expect(reviewReasonRequired('INVALIDATED')).toBe(true);
    expect(reviewReasonRequired('ARBITRATION')).toBe(true);
    expect(reviewReasonRequired('IN_REVIEW')).toBe(false);
    expect(reviewReasonRequired('APPROVED')).toBe(false);
    expect(reviewReasonMissing('REJECTED', '   ')).toBe(true);
    expect(reviewReasonMissing('REJECTED', '已确认误检')).toBe(false);
    expect(reviewReasonMissing('APPROVED')).toBe(false);
  });

  it('calculates an online rate without treating unknown states as online', () => {
    expect(calculateOnlineRate([])).toBeNull();
    expect(calculateOnlineRate([{ status: 'ONLINE' }, { status: 'offline' }, { status: 'HEALTHY' }])).toBe(67);
    expect(calculateOnlineRate([{ status: null }, {}])).toBe(0);
  });

  it('only allows wallboard polling while visible and active', () => {
    expect(isWallboardPollingAllowed({ visible: true, hasActiveWork: true })).toBe(true);
    expect(isWallboardPollingAllowed({ visible: false, hasActiveWork: true })).toBe(false);
    expect(isWallboardPollingAllowed({ visible: true, hasActiveWork: false })).toBe(false);
  });

  it('maps evolution queue failures to recoverable UI states', () => {
    expect(evolutionErrorState(403)).toBe('forbidden');
    expect(evolutionErrorState(404)).toBe('missing');
    expect(evolutionErrorState(409)).toBe('conflict');
    expect(evolutionErrorState(500)).toBe('error');
    expect(evolutionErrorState(undefined, false)).toBe('offline');
    expect(evolutionReviewErrorMessage(409)).toContain('状态已变化');
    expect(evolutionReviewErrorMessage(undefined, false)).toContain('网络已断开');
  });

  it('does not report the workspace as healthy while its real summary failed', () => {
    expect(workspaceSummaryState({ loading: true, hasData: false })).toBe('loading');
    expect(workspaceSummaryState({ loading: false, hasData: true })).toBe('ready');
    expect(workspaceSummaryState({ loading: false, hasData: false, status: 403 })).toBe('forbidden');
    expect(workspaceSummaryState({ loading: false, hasData: false, status: 404 })).toBe('missing');
    expect(workspaceSummaryState({ loading: false, hasData: false, status: 409 })).toBe('conflict');
    expect(workspaceSummaryState({ loading: false, hasData: false, status: 204 })).toBe('empty');
    expect(workspaceSummaryState({ loading: false, hasData: false, online: false })).toBe('offline');
    expect(workspaceSummaryState({ loading: false, hasData: false, status: 500 })).toBe('error');
  });
});

describe('workspace destinations', () => {
  it('uses the root workspace as the data screen and keeps the workbench explicit', () => {
    expect(isWorkspaceRootPath('/workspace')).toBe(true);
    expect(isWorkspaceRootPath('/workspace/')).toBe(true);
    expect(isWorkspaceDataScreenPath('/workspace')).toBe(true);
    expect(isWorkspaceDataScreenPath('/workspace/evolution-screen/')).toBe(true);
    expect(isEvolutionScreenPath('/workspace/evolution-screen')).toBe(true);
    expect(isWorkspaceWorkbenchPath('/workspace/workbench')).toBe(true);
    expect(isWorkspaceDataScreenPath('/workspace/workbench')).toBe(false);
    expect(isEvolutionScreenPath('/workspace')).toBe(false);
  });
});
