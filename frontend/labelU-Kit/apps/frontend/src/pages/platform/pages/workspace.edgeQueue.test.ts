import { describe, expect, it } from 'vitest';

import { workspacePendingCount } from './workspace';

describe('workspace edge queue counters', () => {
  it('includes retrying work in the pending total', () => {
    expect(workspacePendingCount({ queued: 3, pending: 2, preparing: 1, retrying: 4 })).toBe(10);
  });
});
