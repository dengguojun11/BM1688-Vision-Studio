import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./pages/login', () => ({ default: () => null }));
import routes from './routes';
import RequireManager from './pages/platform/RequireManager';

describe('edge task center route access', () => {
  it('guards both edge task center aliases with RequireManager', () => {
    const rootRoute = routes.find((route) => route.path === '/');
    const edgeRoutes = (rootRoute?.children ?? []).filter((route) =>
      route.path === 'edge-task-center' || route.path === 'edge-tasks',
    );

    expect(edgeRoutes).toHaveLength(2);
    for (const route of edgeRoutes) {
      const element = route.element as ReactElement;
      expect(element.type).toBe(RequireManager);
      expect(element.props.children).toBeTruthy();
    }
  });
});
