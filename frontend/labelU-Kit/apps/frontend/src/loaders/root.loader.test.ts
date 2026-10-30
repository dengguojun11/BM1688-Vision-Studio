import { afterEach, describe, expect, it } from 'vitest';

import { rootLoader } from './root.loader';

afterEach(() => localStorage.clear());

describe('rootLoader visual preview boundary', () => {
  it('allows only the explicit cockpit preview without a token', async () => {
    window.IS_ONLINE = false;
    const result = await rootLoader({ request: new Request('http://localhost/workspace/evolution-screen?preview=1&fullscreen=1'), params: {} } as never);
    expect(result).toBeNull();
  });

  it('keeps the preview boundary when the cockpit URL has a trailing slash', async () => {
    window.IS_ONLINE = false;
    const result = await rootLoader({ request: new Request('http://localhost/workspace/evolution-screen/?preview=1'), params: {} } as never);
    expect(result).toBeNull();
  });

  it('keeps the normal workspace behind authentication', async () => {
    window.IS_ONLINE = false;
    const result = await rootLoader({ request: new Request('http://localhost/workspace'), params: {} } as never);
    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(302);
    expect((result as Response).headers.get('Location')).toBe('/login');
  });

  it('does not let the default data screen preview bypass authentication', async () => {
    window.IS_ONLINE = false;
    const result = await rootLoader({ request: new Request('http://localhost/workspace?preview=1'), params: {} } as never);
    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(302);
    expect((result as Response).headers.get('Location')).toBe('/login');
  });
});
