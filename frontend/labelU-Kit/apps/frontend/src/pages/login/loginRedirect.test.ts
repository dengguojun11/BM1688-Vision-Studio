import { describe, expect, it } from 'vitest';

import { normalizeRedirect } from './loginRedirect';

describe('normalizeRedirect', () => {
  it('opens the 7GAI workspace by default', () => {
    expect(normalizeRedirect(null)).toBe('/workspace');
    expect(normalizeRedirect('')).toBe('/workspace');
  });

  it('preserves safe in-platform redirects', () => {
    expect(normalizeRedirect('/training')).toBe('/training');
    expect(normalizeRedirect('/projects/1')).toBe('/projects/1');
  });

  it('rejects external and login-loop redirects', () => {
    expect(normalizeRedirect('https://example.com')).toBe('/workspace');
    expect(normalizeRedirect('/login?next=/training')).toBe('/workspace');
  });
});
