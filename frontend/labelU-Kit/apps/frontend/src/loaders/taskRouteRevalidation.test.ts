import { describe, expect, it } from 'vitest';

import { shouldRevalidateTaskRoute } from './taskRouteRevalidation';

const check = (currentPath: string, nextPath: string, defaultShouldRevalidate = false) =>
  shouldRevalidateTaskRoute({
    currentUrl: new URL(currentPath, 'http://localhost'),
    nextUrl: new URL(nextPath, 'http://localhost'),
    defaultShouldRevalidate,
  });

describe('task route revalidation', () => {
  it('reloads task data when returning from a sample detail to the same task', () => {
    expect(check('/tasks/2/samples/62', '/tasks/2/')).toBe(true);
  });

  it('reloads task data when returning from the finished page', () => {
    expect(check('/tasks/2/samples/finished', '/tasks/2')).toBe(true);
  });

  it('does not add revalidation to unrelated route changes', () => {
    expect(check('/tasks/2/samples/62', '/tasks/3')).toBe(false);
    expect(check('/tasks/2/edit', '/tasks/2')).toBe(false);
  });

  it('preserves React Router default revalidation decisions', () => {
    expect(check('/tasks/2', '/tasks/2?page=2', true)).toBe(true);
  });
});
