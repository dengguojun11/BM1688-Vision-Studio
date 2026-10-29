import { describe, expect, it } from 'vitest';

import { createIdempotencyKey, idempotencyRequestConfig } from './idempotency';

describe('idempotency request helpers', () => {
  it('creates a new key for each new create operation', () => {
    const first = createIdempotencyKey('training');
    const second = createIdempotencyKey('training');

    expect(first).toMatch(/^labelu-training-/);
    expect(second).toMatch(/^labelu-training-/);
    expect(second).not.toBe(first);
  });

  it('does not add a request header when a caller has no key', () => {
    expect(idempotencyRequestConfig()).toBeUndefined();
    expect(idempotencyRequestConfig('  ')).toBeUndefined();
  });
});
