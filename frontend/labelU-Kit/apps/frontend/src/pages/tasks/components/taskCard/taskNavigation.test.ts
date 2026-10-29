import { describe, expect, it } from 'vitest';

import { isTaskNavigationPending } from './taskNavigation';

describe('isTaskNavigationPending', () => {
  it('marks only the card matching the active task navigation as busy', () => {
    expect(isTaskNavigationPending('loading', '/tasks/97', 97)).toBe(true);
    expect(isTaskNavigationPending('loading', '/tasks/97', 98)).toBe(false);
  });

  it('clears the busy state when navigation returns to idle', () => {
    expect(isTaskNavigationPending('idle', '/tasks/97', 97)).toBe(false);
  });

  it('does not mark unsaved task cards as busy', () => {
    expect(isTaskNavigationPending('submitting', '/tasks/97', undefined)).toBe(false);
  });
});
